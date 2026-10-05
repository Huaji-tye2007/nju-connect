# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

nju-connect (repo `Huaji-tye2007/nju-connect-cli`) is a Linux command-line tool for Nanjing University's aTrust VPN. It installs, configures and supervises [zju-connect](https://github.com/Huaji-tye2007/zju-connect), the Go VPN client, and exports the access policy NJU grants the account as rules for proxy clients. User docs are in Chinese: `README.md` and `docs/proxy-clients.md` (one section per proxy client).

**Target audience: users of every Linux distribution and every major proxy client.** Clash/mihomo (Clash Verge Rev, FlClash, Mihomo Party, …), sing-box, Xray/V2Ray (v2rayN, v2rayA) and plain browsers/PAC are all first-class. Never build a feature that only works on one distribution, desktop or proxy client without a fallback for the others.

## Commands

```bash
python3 -m unittest discover -s tests -t .                        # all tests (no network needed)
python3 -m unittest tests.test_config.OptionTest.test_mode_option # one test
python3 -W error::ResourceWarning -m unittest discover -s tests -t .   # what CI-quality runs should pass
python3 -m nju_connect --help                                     # run from source
bash tools/build.sh                                               # build dist/nju-connect (zipapp)
NJU_CONNECT_NONINTERACTIVE=1 bash install.sh                      # install the checkout into ~/.local/bin
```

- shellcheck isn't installed locally. CI runs `shellcheck install.sh tools/build.sh`, and its older shellcheck also flags SC2015 (`A && B || C`), so use `if`. To check locally: `pip install --target <scratch dir> shellcheck-py`.
- After running Python locally, delete `__pycache__/` before committing (it is gitignored, but `git add -A` once picked one up).
- **Releasing:**
  1. Bump `VERSION` in `nju_connect/__init__.py`.
  2. Commit and push `main`, then wait for CI (`gh run watch`).
  3. Push the tag `v<VERSION>`. `release.yml` checks the tag matches `VERSION`, runs the tests, builds the zipapp and attaches `nju-connect`, `install.sh` and `config.toml.example`.
- **Upgrading the dev machine:** `nju-connect upgrade --version vX.Y.Z`. Pin the version: raw.githubusercontent.com and `/releases/latest` are CDN-cached for about 5 minutes after a release.

## Cross-distribution rules

- **Python 3.8+, standard library only.** No third-party packages. Avoid 3.9+ syntax and APIs (`dict | dict`, `str.removeprefix`, `list[str]` at runtime, `match`, …). `tomllib` (3.11+) is optional: `config.read_toml` has a fallback parser for flat `key = value` files.
- **Distributed as one file**, a zipapp of the `nju_connect/` package built by `tools/build.sh` with `#!/usr/bin/env python3`.
- **`install.sh` is POSIX-ish bash.** It may only use `curl`, `tar`, `install`, `mktemp`, and `unzip` (falling back to `python3 -m zipfile`).
  - It installs into `~/.local/bin` without root: no `sudo`, no package managers, no distro detection.
  - It maps `uname -m` to the zju-connect release asset names.
  - If no prebuilt asset exists and Go is installed, it builds from source, using `goproxy.cn` when proxy.golang.org is unreachable (mainland China).
- **Paths follow XDG** (`paths.py`), and every path can be overridden through environment variables.
- **systemd (user units) is optional.** Without `systemctl`, the commands point users to `nju-connect service run` in their desktop autostart. `notify-send` is best-effort.
- **Linux-only interfaces are fine** (`/proc`, `pwd`); other OSes are not targeted.

## Architecture (`nju_connect/`)

**Run model.** zju-connect is only ever run by the service loop: `daemon.py`, started by `nju-connect service run` from the systemd user unit. On campus (auto mode) the service stops zju-connect and serves its SOCKS5/HTTP ports itself with a direct proxy (`direct.DirectProxy`, `daemon.campus_proxy`), so rules that send NJU traffic to zju-connect without a fallback keep working; it closes them before starting zju-connect again. `STATE_DIR/direct-proxy` (the service's PID) tells `service status` and `instance_problems(ignore_service=True)` that the service holds the ports. The only command that uses the terminal is `nju-connect login`, which runs zju-connect interactively so the user can type the SMS code, and stops it as soon as `client_data.json` (the saved session) is written (`zju.interactive_login`). `login` pauses and resumes a running service.

**Never let a background process trigger SMS codes:**
- The daemon does not start zju-connect without a saved session.
- It watches zju-connect's output for SMS prompts (`zju.NEEDS_INPUT`). If it sees one, it notifies once and waits until the session file changes, because every retry sends a real SMS.
- `service start/enable/restart` first ask the server whether the saved session is valid (`zju.check_session`, which uses the saved cookies only), and run `login` in the terminal if it isn't.

**Two configuration files** (`config.py`):
- `config.toml` (mode 600) is read directly by zju-connect (`-config`). zju-connect rejects unknown keys, so it may only contain zju-connect options. `phone` is deliberately not written: NJU uses password logins, and an SMS step looks the number up from the server.
- `nju-connect.conf` (INI, via `configparser`) holds nju-connect's own settings.
- **Option registry:** `config.OPTIONS` presents both files as dotted keys for `config show|get|set` and `setup --advanced`. Each option declares its storage, kind and validation, and its effects: `RESTART` (restart the service if active) or `EXPORTS` (regenerate remembered exports).
- **Adding a setting:** add a default in `SETTINGS_DEFAULTS`, an `Option`, and a row in the README settings table.
- **Old setting formats** are converted in `config.migrate_settings`, which runs from `load_settings`. Old command names stay as hidden aliases in `cli.py` (`daemon`, `service install/uninstall`).

**Access policy → exports:**
- `zju-connect --fetch-resource` downloads the policy with the saved session. This option exists only in our zju-connect fork; `zju.require_fetch_resource` checks for it.
- The result is cached as `resource.json`. `policy.update_policy` refuses a policy less than half the size of the cached one.
- `policy.parse_policy` turns it into tool-neutral `Entry(kind, value, network, ports)` tuples:
  - `domain`: an exact host
  - `subdomains`: aTrust's `*.x`, which matches subdomains only
  - `cidr`: an IPv4 range; a domain's resolved IPs also become `cidr` entries
  - `node`: a VPN node address, which must always stay DIRECT
- The parser mirrors how zju-connect reads the policy: L3VPN apps only, IPv4 only, other `*` wildcards skipped.
- **`exporters.FORMATS`** is the registry of formats: `clash`, `clash-config`, `clash-verge` (install only), `mihomo-script`, `sing-box`, `sing-box-config`, `xray`, `v2rayn`, `pac`, `list`. Each `Format` carries its renderer and its own help (summary, description, what `-o` and `--install` mean, examples); `cli.add_export_parser` turns them into one subparser per format, so `export FORMAT -h` is that format's help.
- **One rule for every client:** `--install` puts the rules in files the client rereads (or merges into its config), so policy changes need nothing; without `--install` everything is inline and the client must import it again.
- **When importing again matters:** almost every download differs, but mostly in the IPs the server resolved its domains to (`addr["ip"]`, ~280 of ~710 entries), which the domain rules already cover. `policy.granted` is the policy without them (domains, ranges, ports, nodes); `policy.record_changes` keeps it in `STATE_DIR/policy-changes.json`, and `refresh_exports` notifies once when it changes and hand-imported exports (`MANUAL_IMPORT`) exist; `--list` shows when it last changed. Generated files carry no timestamp, so unchanged content is not rewritten. There is no `--inline` (it now errors with a pointer to `mihomo-script`).
- **Remembered exports:** `[exports] LABEL = [install:]PATH`, LABEL being the format, or `FORMAT@N` for further exports of a format (`exporters.remember` reuses the entry of the same format and path). `--forget` takes a label, a format (all of it) or a path. `--list` shows each installer's `files()`. Older `merge:`, `inline:` and plain `clash-verge` entries are converted in `config.migrate_settings`. The service regenerates every remembered export each time it refreshes the policy (every `daemon.ruleset_interval` seconds), and after `config set` changes an `EXPORTS` setting.
- **`--install`** (`exporters.INSTALLERS`: `clash-verge`, `clash-config`, `sing-box-config`, `xray`) sets a client up so that policy changes and the SOCKS port reach it without the user: each installer has `target(output)` (found with `clients.choose_config` unless `-o`), `apply(target, entries, skipped, interactive)` (used both by the command and by the refresh), `files()` and `describe()` for `--list`, and `keeps()`. nju-connect never uses sudo: a config it cannot write is left alone and `exporters.merge_steps` prints what to merge once (sing-box: rule sets in `paths.SING_BOX_RULE_SETS`; Xray, which has no rule files, is then not remembered). The merge part last shown is kept in `exporters.SNIPPETS`, and the service notifies when it changes. A reload the service can't do (system unit) becomes a notification.
- **Finding clients** (`clients.py`): configs come from the running process's argv and cwd (`-c`, `-confdir`, `-C`, `-D`, `-d`), then the usual paths. Cores a GUI manages are skipped (v2rayN `binConfigs/`, `mihomo-party/`, Clash Verge Rev, `verge-mihomo`, FlClashCore). The systemd unit comes from `/proc/PID/cgroup`, so `clients.reload` knows whether it can restart a user unit, must SIGHUP a hand-started sing-box, or only print the `sudo systemctl` command.

**Rules every export must keep:**
- Rules for the VPN server domain and the node IPs come first and are DIRECT, so zju-connect's own tunnel never loops through the proxy client (TUN mode).
- Many NJU sites appear in the policy only as IP ranges (e.g. `xk.nju.edu.cn` in `219.219.112.0/20`), and zju-connect routes them by IP. So the exports must get those hosts to zju-connect too, for the domains in `export.resolve_domains` (default `nju.edu.cn`) only: either the client resolves them before matching IP ranges, or it hands the whole suffix to zju-connect:
  - Clash: `IP-CIDR,…,no-resolve` plus `AND,((DOMAIN-SUFFIX,nju.edu.cn),(IP-CIDR,…))`. mihomo only resolves once DOMAIN-SUFFIX has matched; this was verified.
  - sing-box: rule-set, then a `resolve` action, then the rule-set again.
  - Xray and v2rayN: no resolving. After the policy rules, `domain:nju.edu.cn` → NJUConnect hands the rest of those domains to zju-connect, which resolves them with the campus DNS through the VPN and routes them by the policy. This works with any domainStrategy, so the merge leaves the user's alone. Only with `resolve_domains = *` is `IPOnDemand` set (`IPIfNonMatch` breaks as soon as a later `geosite:cn` rule matches). With v2rayN's sing-box core, a global IPOnDemand resolves every connection and dials the IP, so never ask users to set it.
  - PAC: calls `dnsResolve` only for those domains.
- mihomo only reads provider files inside its home directory, and watches file providers (fswatch on their folder, so an atomic replace triggers it; `interval` only matters to old versions). Installed mihomo exports (Clash Verge Rev's script, `clash-config --install`) therefore keep everything that changes in `clash.PROVIDER_FILES`: `ruleset/nju-vpn.yaml`, `ruleset/nju-direct.yaml` (server and VPN nodes) and `proxies/nju-connect.yaml` (NJUConnect with its port, plus a `type: direct` member). The group uses only `use: [nju-connect]`: mihomo puts a group's `proxies` before its `use` providers, so a DIRECT listed there would win the fallback. nju-connect never edits a `config.yaml` (no YAML parser in the standard library): `clash-config --install` prints the part to merge once, and the service notifies when names or group settings make it change (`exporters.SNIPPETS`). Without `--install` (`mihomo-script` for FlClash and Clash Party, printed `clash-config` snippets), `clash.clash_parts` gives the same group, names and rules with the providers `type: inline` (proxy provider included), so every mihomo client has one structure.
- Xray (`xray.merge_file`): `routing` is replaced whole by a later `-confdir` file, so the rules are merged into the user's config itself. Our outbound is appended (the first outbound is Xray's default), our rules are prepended with `ruleTag: nju-connect` and replaced on each merge, `xray run -test` runs first when xray is installed, and the first merge backs the file up (comments are lost).
- sing-box (`singbox.merge_file`): same approach, since its config is JSON. It rejects unknown fields, so our rules are recognised by the rule sets they use (`singbox.RULE_SETS`: `nju-direct`, `nju-vpn`, `nju-resolve`), written as local sources in `nju-connect/` next to the config (not top-level `*.json`, which `-C dir` would load). The outbound is appended (the first is the default without `route.final`); direct rules use the user's `direct`-type outbound. `sing-box check` runs first; if the original alone fails too (a config split over `-C`), the merge goes ahead with a warning. sing-box watches local rule sets and reloads on SIGHUP, so a policy change needs no reload.
- v2rayN picks the core by the active node's type; the routing rules apply to its Xray and sing-box cores. Its mihomo core only runs "custom config" Clash profiles, which ignore the routing rules (only a global Mixin.yaml, without a GUI switch, can inject into them), so it is not supported.
- v2rayN regenerates `binConfigs/config.json`, so `merge_file` refuses it. Its rule import (`Import Rules From File/Clipboard`) takes a JSON **list** of `RulesItem` (case-insensitive keys, comments allowed) and either appends at the end or replaces all. The `v2rayn` export therefore reads the active routing's rules from `guiConfigs/guiNDB.db` (sqlite, `mode=ro`, never written) and emits ours (remarks `nju-connect: …`, outbound = the node's remark) followed by those, for a replace-all import. An unknown outbound remark silently becomes `proxy` in v2rayN.
- Clash Verge Rev runs its global script only when it rebuilds its configuration (on start or after a GUI change), not when the file changes. `export clash-verge --install` therefore offers to restart it (`clash.restart_verge`, which relaunches with the old process's argv and environment from `/proc`).

**Campus detection** (`network.on_campus`): a direct UDP DNS query for `www.nju.edu.cn` to NJU's internal DNS servers.
- An answer means on campus; a timeout means off campus; a send error means offline.
- `campus.dns_servers = auto` takes the private UDP-53 servers granted by the policy (`policy.campus_dns_servers`). Public resolvers are excluded because they would answer from anywhere. Before any policy is cached, the built-in `10.12.253.4, 10.28.253.4` are used.
- `daemon.mode = always` skips the probe.
- VPN health is a DNS query through zju-connect's SOCKS5 UDP ASSOCIATE (`network.vpn_healthy`).

**Module map:**

| Module | Contents |
|---|---|
| `cli.py` | argparse, thin `cmd_*` handlers, `service status` |
| `configure.py` | `setup`, `login`, `config show/get/set`, applying effects |
| `service.py` | the systemd unit |
| `daemon.py` | the `Supervisor` |
| `zju.py` | every zju-connect invocation |
| `clash.py` / `xray.py` / `singbox.py` | Clash/mihomo formats and provider files; Xray and v2rayN, merging into an Xray config; sing-box rule sets, merging into a sing-box config |
| `clients.py` | running proxy cores: their configs, units, reloading them |
| `direct.py` | the direct SOCKS5/HTTP proxy that replaces zju-connect on campus |
| `paths.py` | locations, finding zju-connect and itself |

## The zju-connect fork

- `~/Projects/zju-connect` is `Huaji-tye2007/zju-connect` (remote `fork`), and its own `CLAUDE.md` applies there.
- It must stay close to upstream (Mythologyli/zju-connect). It carries only generic Go changes (`--fetch-resource`, atomic client-data saves, trust-device debug output, preferring IP ranges over a domain's resolved IPs for bare-IP TCP tunnels). Anything NJU-specific belongs here, in nju-connect.
- Releases are tagged `v1.3.1-nju.N`. Publishing a GitHub release triggers its CI build, and `install.sh` downloads the newest release.

## Testing safely on a real machine

The developer's own machine runs a real service, a real session and a real Clash Verge Rev. Earlier mistakes restarted that service and Clash Verge during tests, so:

- `tests/__init__.py` points `NJU_CONNECT_CONFIG_DIR`, `NJU_CONNECT_STATE_DIR`, `XDG_DATA_HOME` and `XDG_CONFIG_HOME` at temporary directories before `nju_connect` is imported. Do the same for manual sandbox runs.
- `service.py` refuses to touch the systemd unit while `NJU_CONNECT_CONFIG_DIR` is set. Keep it that way.
- Mock or limit anything that acts on real processes:
  - restarting Clash Verge: tests pass `only_exe=` a stand-in binary
  - `apply_verge_script`: mock it
  - systemctl: mock it
- `DirectProxy` in tests listens on port 0 only (`127.0.0.1:0`); never on the user's real proxy ports, which their service holds.
- `clients.reload` does nothing while `NJU_CONNECT_CONFIG_DIR` is set. Tests inject the process list (`clients.processes`/`clients.running`) instead of scanning the real `/proc`. Never point a test at the user's v2rayN core or Xray service; read v2rayN's database only with `mode=ro`.
- Never call `trust` or `untrust` for real; they change the account's trusted devices on NJU's server.
- Don't `pkill -f <pattern>` when the pattern also appears in your own shell command, because it kills the shell running it. Match with `pgrep -x`, or a `^`-anchored pattern, and `xargs kill`.
- To verify exports end to end, use throwaway proxy cores (mihomo from Clash Verge's `verge-mihomo`, a copy of v2rayN's `bin/xray/xray`, or downloaded `sing-box`/`xray` binaries; `NJU_CONNECT_XRAY` points the merge check at one) together with a logging fake DNS server on a spare port. Never use the user's running instances.

## Conventions

- **Commits:** Conventional Commits with a scope (`feat(export): …`, `fix(install): …`), with a body explaining why.
- **User-facing text:** messages and `--help` are English; README and docs are Chinese.
- **docs/proxy-clients.md:** say whether a step was tested on the real proxy core or is an untested GUI step.
- **Files nju-connect generates** start with `config.MARKER` ("Generated by nju-connect"). A file without the marker is backed up before it's overwritten.
- **Writes go through `util.write_atomic`** (temp file plus rename). The service and the CLI read the same files concurrently.
