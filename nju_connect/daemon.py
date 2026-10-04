"""The background supervisor: zju-connect only while off campus, plus ruleset updates."""

import os
import signal
import subprocess
import sys
import threading
import time

from . import paths
from .config import load_config, load_settings, socks_address
from .network import instance_problems, on_campus, port_in_use, vpn_healthy
from .ruleset import update_ruleset
from .util import log, notify
from .zju import NEEDS_INPUT, require_fetch_resource, session_mtime


class Supervisor:
    QUICK_EXIT = 120          # an exit sooner than this counts as a failed start
    STARTUP_GRACE = 30        # let zju-connect log in before health checks/updates
    HEALTH_FAILURES = 3
    MAX_BACKOFF = 30 * 60

    def __init__(self, settings, config):
        self.settings = settings
        self.config = config
        self.socks = socks_address(config)
        self.update_interval = int(settings["daemon"]["ruleset_interval"])
        self.proc = None
        self.started_at = 0.0
        self.failures = 0
        self.retry_at = 0.0
        self.notified = False
        self.warned_external = False
        self.health_failures = 0
        self.next_update = 0.0
        self.campus = None
        self.needs_input = False
        self.login_mark = None      # session mtime when a manual login became necessary
        self.waiting_for_login = False

    def start(self):
        log("Off campus: starting zju-connect")
        self.needs_input = False
        self.proc = subprocess.Popen([paths.zju_connect_binary(), "-config", str(paths.CONFIG_TOML)],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, start_new_session=True)
        threading.Thread(target=self.pump, args=(self.proc,), daemon=True).start()
        self.started_at = time.monotonic()
        self.health_failures = 0
        self.next_update = self.started_at + self.STARTUP_GRACE

    def pump(self, proc):
        """Forward zju-connect's output to the journal and spot SMS prompts."""
        for raw in proc.stdout:
            line = raw.decode(errors="replace")
            sys.stdout.write(line)
            sys.stdout.flush()
            if NEEDS_INPUT.search(line):
                self.needs_input = True

    def require_login(self, why):
        """Stop retrying (each try may send an SMS) until the user logs in again."""
        self.login_mark = session_mtime()
        notify(f"{why} Run `nju-connect login` in a terminal; the service resumes by itself afterwards.")

    def login_pending(self):
        if self.login_mark is None and session_mtime() is not None:
            if self.waiting_for_login:
                log("Session found; connecting")
                self.waiting_for_login = False
            return False
        if session_mtime() is None:
            if not self.waiting_for_login:
                self.waiting_for_login = True
                log("No saved session yet; waiting for `nju-connect login`")
            return True
        if session_mtime() != self.login_mark:
            log("New session found; connecting again")
            self.login_mark, self.waiting_for_login = None, False
            self.failures, self.retry_at = 0, 0.0
            return False
        return True

    def stop(self, why):
        if not self.proc:
            return
        log(f"Stopping zju-connect: {why}")
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(self.proc.pid, signal.SIGKILL)
            self.proc.wait()
        except ProcessLookupError:
            pass
        self.proc = None

    def reap(self, now):
        if not self.proc or self.proc.poll() is None:
            return
        ran = now - self.started_at
        log(f"zju-connect exited with code {self.proc.returncode} after {ran:.0f}s")
        self.proc = None
        if self.needs_input:
            self.require_login("The VPN session expired and the login needs an SMS code.")
            return
        if ran >= self.QUICK_EXIT:
            self.failures = 0
            return
        self.failures += 1
        delay = min(60 * 2 ** (self.failures - 1), self.MAX_BACKOFF)
        self.retry_at = now + delay
        log(f"Retrying in {delay}s")
        if self.failures >= 2 and not self.notified:
            self.notified = True
            notify("zju-connect keeps failing to connect; see `nju-connect service logs`.")

    def update_ruleset(self, now):
        if now < self.next_update:
            return
        self.next_update = now + self.update_interval
        log("Updating Clash ruleset")
        try:
            update_ruleset()
        except Exception as e:
            log(f"Ruleset update failed, keeping the old rules: {e}")
            self.next_update = now + 5 * 60

    def healthy(self):
        self.failures, self.notified = 0, False

    def tick(self):
        now = time.monotonic()
        self.reap(now)

        campus = on_campus(self.settings)
        if campus != self.campus:
            log({True: "Network: on campus", False: "Network: off campus",
                 None: "Network: offline"}[campus])
            self.campus = campus
        if campus is None:
            return
        if campus:
            self.stop("on campus")
            self.healthy()
            return

        if not self.proc:
            if port_in_use(self.socks[1], self.socks[0]):
                # another zju-connect (e.g. `nju-connect connect`) owns the port
                if not self.warned_external:
                    self.warned_external = True
                    log(f"Something else is listening on {self.socks[0]}:{self.socks[1]}; "
                        "not starting a second zju-connect")
                self.healthy()
                if vpn_healthy(self.settings, self.socks):
                    self.update_ruleset(now)
                return
            self.warned_external = False
            if self.login_pending():
                return
            if now >= self.retry_at:
                self.start()
            return

        if now - self.started_at < self.STARTUP_GRACE:
            return
        if vpn_healthy(self.settings, self.socks):
            self.health_failures = 0
            self.healthy()
            self.update_ruleset(now)
            return
        self.health_failures += 1
        log(f"VPN health check failed ({self.health_failures}/{self.HEALTH_FAILURES})")
        if self.health_failures >= self.HEALTH_FAILURES:
            self.stop("VPN unhealthy")
            self.retry_at = now


def run_daemon():
    config, settings = load_config(), load_settings()
    try:
        require_fetch_resource()
    except RuntimeError as e:
        log(f"warning: {e} The VPN still works, but the ruleset won't be updated.")
    for problem in instance_problems(config):
        log(f"warning: {problem}")
    supervisor = Supervisor(settings, config)
    interval = int(settings["daemon"]["check_interval"])

    def shutdown(signum, _frame):
        supervisor.stop(f"signal {signum}")
        sys.exit(0)
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    while True:
        try:
            supervisor.tick()
        except Exception as e:  # keep supervising whatever happens
            log(f"Supervisor error: {e!r}")
        time.sleep(interval)
