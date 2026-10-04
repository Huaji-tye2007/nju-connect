#!/usr/bin/env bash
# Install (or upgrade) nju-connect and zju-connect into ~/.local/bin.
#
#   curl -fsSL https://raw.githubusercontent.com/Huaji-tye2007/nju-connect-cli/main/install.sh | bash
#
# Environment:
#   NJU_CONNECT_VERSION         nju-connect release tag (default: latest release)
#   ZJU_CONNECT_VERSION         zju-connect release tag (default: latest release)
#   NJU_CONNECT_PREFIX          install directory (default: ~/.local/bin)
#   NJU_CONNECT_REPO            nju-connect repository (default: Huaji-tye2007/nju-connect-cli)
#   NJU_CONNECT_ZJU_REPO        zju-connect repository (default: Huaji-tye2007/zju-connect)
#   NJU_CONNECT_NONINTERACTIVE  set to 1 to skip the setup wizard
set -euo pipefail

REPO="${NJU_CONNECT_REPO:-Huaji-tye2007/nju-connect-cli}"
ZJU_REPO="${NJU_CONNECT_ZJU_REPO:-Huaji-tye2007/zju-connect}"
PREFIX="${NJU_CONNECT_PREFIX:-$HOME/.local/bin}"

msg() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = Linux ] || die "only Linux is supported"
command -v curl >/dev/null || die "curl is required"
command -v python3 >/dev/null || die "python3 (3.8 or newer) is required"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 8))' || die "python3 3.8 or newer is required"

case "$(uname -m)" in
  x86_64 | amd64) arch=amd64 ;;
  aarch64 | arm64) arch=arm64 ;;
  i?86) arch=386 ;;
  armv7*) arch=arm7 ;;
  armv6*) arch=arm6 ;;
  armv5*) arch=arm5 ;;
  riscv64) arch=riscv64 ;;
  mips64) arch=mips64 ;;
  mips64el) arch=mips64le ;;
  *) die "unsupported architecture: $(uname -m)" ;;
esac

# Newest release tag of a repository: /releases/latest redirects to /releases/tag/<tag>
latest_tag() {
  local url="" attempt
  for attempt in 1 2 3; do
    if url="$(curl -fsSLI -m 30 -o /dev/null -w '%{url_effective}' "https://github.com/$1/releases/latest")"; then
      break
    fi
    url=""
    [ "$attempt" = 3 ] || sleep 2
  done
  [ -n "$url" ] || die "cannot reach GitHub to look up the latest release of $1"
  case "$url" in
    */releases/tag/?*) printf '%s\n' "${url##*/}" ;;
    *) die "no release found in $1" ;;
  esac
}

NJU_VERSION="${NJU_CONNECT_VERSION:-$(latest_tag "$REPO")}"
ZJU_VERSION="${ZJU_CONNECT_VERSION:-$(latest_tag "$ZJU_REPO")}"
msg "Installing nju-connect $NJU_VERSION and zju-connect $ZJU_VERSION ($arch) into $PREFIX"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin"

# zju-connect: prebuilt release binary, or build from source if Go is available
asset="https://github.com/$ZJU_REPO/releases/download/$ZJU_VERSION/zju-connect-linux-$arch.zip"
if curl -fsSL --retry 3 -o "$tmp/zju-connect.zip" "$asset"; then
  if command -v unzip >/dev/null; then
    unzip -q -o "$tmp/zju-connect.zip" -d "$tmp/bin"
  else
    python3 -m zipfile -e "$tmp/zju-connect.zip" "$tmp/bin"
  fi
elif command -v go >/dev/null; then
  warn "no prebuilt zju-connect for linux-$arch in $ZJU_VERSION, building from source"
  # proxy.golang.org is unreachable from mainland China; use a mirror unless GOPROXY was customised
  if [ "$(go env GOPROXY)" = "https://proxy.golang.org,direct" ] &&
    ! curl -fsS -m 5 -o /dev/null https://proxy.golang.org/ 2>/dev/null; then
    msg "proxy.golang.org is unreachable, downloading Go modules from goproxy.cn"
    export GOPROXY=https://goproxy.cn,direct
  fi
  mkdir -p "$tmp/src"
  curl -fsSL --retry 3 "https://github.com/$ZJU_REPO/archive/refs/tags/$ZJU_VERSION.tar.gz" | tar -xz -C "$tmp/src"
  (cd "$tmp"/src/zju-connect-* && CGO_ENABLED=0 go build -trimpath \
    -ldflags "-s -w -X main.zjuConnectVersion=$ZJU_VERSION" -o "$tmp/bin/zju-connect" .)
else
  die "no prebuilt zju-connect for linux-$arch in $ZJU_VERSION and Go is not installed"
fi

# nju-connect: nju_connect.py from this checkout when run from a clone, otherwise the release asset
script_dir=""
if [ -f "${BASH_SOURCE[0]:-}" ]; then
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi
if [ -z "${NJU_CONNECT_VERSION:-}" ] && [ -n "$script_dir" ] &&
  [ -f "$script_dir/nju_connect.py" ] && [ -f "$script_dir/install.sh" ]; then
  cp "$script_dir/nju_connect.py" "$tmp/bin/nju-connect"
elif ! curl -fsSL --retry 3 -o "$tmp/bin/nju-connect" \
  "https://github.com/$REPO/releases/download/$NJU_VERSION/nju-connect"; then
  curl -fsSL --retry 3 -o "$tmp/bin/nju-connect" \
    "https://raw.githubusercontent.com/$REPO/$NJU_VERSION/nju_connect.py" ||
    die "cannot download nju-connect $NJU_VERSION"
fi

mkdir -p "$PREFIX"
for tool in zju-connect nju-connect; do
  install -m 755 "$tmp/bin/$tool" "$PREFIX/$tool.new"
  mv -f "$PREFIX/$tool.new" "$PREFIX/$tool"
done
msg "Installed $("$PREFIX/nju-connect" --version) and $("$PREFIX/zju-connect" -version)"

case ":$PATH:" in
  *":$PREFIX:"*) ;;
  *) warn "$PREFIX is not on your PATH; add this to your shell profile:
    export PATH=\"$PREFIX:\$PATH\"" ;;
esac

# Restart the service so it picks up the new binaries
unit="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/nju-connect.service"
if [ -f "$unit" ] && grep -q "$PREFIX/nju-connect daemon" "$unit" &&
  systemctl --user is-active --quiet nju-connect.service 2>/dev/null; then
  systemctl --user restart nju-connect.service && msg "Restarted nju-connect.service"
fi

if [ "${NJU_CONNECT_NONINTERACTIVE:-0}" = 1 ] || ! [ -r /dev/tty ] || ! (: </dev/tty) 2>/dev/null; then
  msg "Done. Run \`nju-connect setup\` to configure your account."
  exit 0
fi

config="${NJU_CONNECT_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/nju-connect}/config.toml"
if [ -f "$config" ]; then
  msg "Keeping your existing configuration ($config); run \`nju-connect setup\` to change it."
else
  "$PREFIX/nju-connect" setup </dev/tty
fi
if [ -f "$unit" ]; then
  exit 0
fi
printf 'Start NJU Connect automatically whenever you are off campus (systemd user service)? [y/N] '
read -r answer </dev/tty || answer=
case "$answer" in
  y | Y | yes) "$PREFIX/nju-connect" service install </dev/tty || true ;;
  *) msg "You can enable it later with \`nju-connect service install\`." ;;
esac
