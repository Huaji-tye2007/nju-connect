"""nju-connect: NJU aTrust VPN helper built on zju-connect.

Connects to the NJU aTrust VPN with zju-connect, keeps it running only while
off campus (systemd user service), and generates a Clash/mihomo ruleset and
global script from the access policy NJU grants your account.
"""

VERSION = "0.5.4"
INSTALL_URL = "https://raw.githubusercontent.com/Huaji-tye2007/nju-connect-cli/main/install.sh"
