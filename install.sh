#!/usr/bin/env bash
# Install checked Release assets; no checkout, compiler, or Node.js required.
set -euo pipefail
VERSION=latest
START=1
PREFIX="${EGO_BRIDGE_PREFIX:-$HOME/.local}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) VERSION="${2:?Missing version}"; shift 2 ;;
    --prefix) PREFIX="${2:?Missing prefix}"; shift 2 ;;
    --no-start) START=0; shift ;;
    --help|-h)
      echo 'Usage: bash install.sh [--version vX.Y.Z] [--prefix DIR] [--no-start]'
      echo 'Installs Release binaries. Mac: App + ego-bridge. Linux: CLI + service.'
      exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done
fail() { echo "ego bridge: $*" >&2; exit 1; }
[[ "$VERSION" == latest || "$VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail 'Invalid version'
[[ "$PREFIX" == /* ]] || fail '--prefix must be an absolute path'
OS=$(uname -s)
case "$OS" in
  Darwin)
    case $(uname -m) in
      arm64) SUFFIX=macos-arm64.zip ;;
      x86_64) SUFFIX=macos-x86_64.zip ;;
      *) fail 'Unsupported Mac architecture' ;;
    esac
    APP_DIR="${EGO_BRIDGE_APP_DIR:-$HOME/Applications}"
    [[ "$APP_DIR" == /* ]] || fail 'EGO_BRIDGE_APP_DIR must be absolute'
    if [[ $START == 1 ]] && pgrep -x EgoBridge >/dev/null 2>&1; then
      fail 'Quit the running ego bridge App before installing, or use --no-start and restart it later.'
    fi
    ;;
  Linux) SUFFIX=linux.tar.gz ;;
  *) fail 'Supported platforms: macOS and Linux' ;;
esac
for required in curl awk grep; do command -v "$required" >/dev/null || fail "Missing $required"; done
PYTHON=''
for candidate in /opt/homebrew/opt/python@3.13/bin/python3.13 /usr/local/opt/python@3.13/bin/python3.13 "$(command -v python3 || true)"; do
  if [[ -x "$candidate" ]] && "$candidate" -c 'import sys; sys.exit(sys.version_info < ((3,9) if sys.platform == "darwin" else (3,7)))' 2>/dev/null; then
    PYTHON="$candidate"; break
  fi
done
[[ -n "$PYTHON" ]] || fail 'Python 3 is required. On Mac: brew install python@3.13; or use the Homebrew cask to install dependencies automatically.'
[[ "$OS" != Linux ]] || command -v openssl >/dev/null || fail 'OpenSSL is required'
BASE="${EGO_BRIDGE_RELEASE_BASE_URL:-https://github.com/gwen0x4c3/ego-bridge/releases}"
if [[ "$VERSION" == latest ]]; then URL="$BASE/latest/download"; else URL="$BASE/download/$VERSION"; fi
WORK=$(mktemp -d "${TMPDIR:-/tmp}/ego-bridge-install.XXXXXX")
trap 'rm -rf "$WORK"' EXIT
fetch() { curl --fail --location --silent --show-error --retry 3 --connect-timeout 15 --max-time 180 "$1" -o "$2"; }
fetch "$URL/SHA256SUMS" "$WORK/SHA256SUMS"
ASSET=$(awk '{print $2}' "$WORK/SHA256SUMS" | grep -E "^ego-bridge-v[0-9]+\\.[0-9]+\\.[0-9]+-$SUFFIX$" || true)
[[ -n "$ASSET" && "$ASSET" != *$'\n'* ]] || fail 'Release manifest has no unique matching asset'
RELEASE="${ASSET#ego-bridge-}"
RELEASE="${RELEASE%-$SUFFIX}"
[[ "$VERSION" == latest || "$RELEASE" == "$VERSION" ]] || fail 'Release version mismatch'
EXPECTED=$(awk -v name="$ASSET" '$2 == name {print $1}' "$WORK/SHA256SUMS")
[[ "$EXPECTED" =~ ^[a-fA-F0-9]{64}$ ]] || fail 'Invalid SHA-256 manifest entry'
echo "Downloading $ASSET"
fetch "$URL/$ASSET" "$WORK/$ASSET"
ACTUAL=$("$PYTHON" -c 'import hashlib,sys; h=hashlib.sha256(); f=open(sys.argv[1],"rb"); [h.update(x) for x in iter(lambda:f.read(1024*1024),b"")]; print(h.hexdigest())' "$WORK/$ASSET")
[[ "$ACTUAL" == "$EXPECTED" ]] || fail 'SHA-256 mismatch; nothing installed'
BIN_DIR="$PREFIX/bin"
DATA_DIR="$PREFIX/share/ego-bridge"
mkdir -p "$BIN_DIR"
check_command() {
  local file="$BIN_DIR/$1" marker="$2"
  if [[ -e "$file" || -L "$file" ]] && ! grep -Eq "$marker" "$file" 2>/dev/null; then
    fail "Refusing to overwrite unrelated command: $file"
  fi
}
if [[ "$OS" == Darwin ]]; then
  check_command ego-bridge 'ego-bridge release launcher|Control the running Mac menu-bar App'
  ditto -x -k "$WORK/$ASSET" "$WORK/payload"
  SOURCE="$WORK/payload/EgoBridge.app"
  codesign --verify --deep --strict "$SOURCE"
  IDENTIFIER=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$SOURCE/Contents/Info.plist")
  [[ "$IDENTIFIER" == app.ego-bridge.EgoBridge ]] || fail 'Unexpected App identity'
  APP_VERSION=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$SOURCE/Contents/Info.plist")
  [[ "v$APP_VERSION" == "$RELEASE" ]] || fail 'App version mismatch'
  DEST="$APP_DIR/EgoBridge.app"
  if [[ -e "$DEST" ]]; then
    EXISTING=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$DEST/Contents/Info.plist" 2>/dev/null || true)
    [[ "$EXISTING" == app.ego-bridge.EgoBridge ]] || fail "Unrelated App at $DEST"
  fi
  mkdir -p "$APP_DIR"
  STAGE=$(mktemp -d "$APP_DIR/.ego-bridge.XXXXXX")
  ditto "$SOURCE" "$STAGE/EgoBridge.app"
  if [[ -e "$DEST" ]]; then mv "$DEST" "$STAGE/previous.app"; fi
  if ! mv "$STAGE/EgoBridge.app" "$DEST"; then
    [[ ! -e "$STAGE/previous.app" ]] || mv "$STAGE/previous.app" "$DEST"
    fail 'App installation failed; previous App restored'
  fi
  printf '#!/bin/bash\n# ego-bridge release launcher\nexec %q -B %q "$@"\n' "$PYTHON" "$DEST/Contents/Resources/ego-bridge" > "$WORK/ego-bridge"
  chmod 755 "$WORK/ego-bridge"
  # Remove only our existing symlink, without following it into the App bundle.
  if [[ -L "$BIN_DIR/ego-bridge" ]]; then unlink "$BIN_DIR/ego-bridge"; fi
  mv "$WORK/ego-bridge" "$BIN_DIR/ego-bridge"
  rm -rf "$STAGE"
  echo "Installed $RELEASE: $DEST and $BIN_DIR/ego-bridge"
  [[ $START == 0 ]] || open "$DEST"
else
  check_command ego-browser 'ego-bridge shim'
  check_command ego-agent-service 'ego_agent_service.py|ego-bridge agent launcher'
  mkdir -p "$WORK/payload"
  tar -xzf "$WORK/$ASSET" -C "$WORK/payload"
  [[ "v$(tr -d '[:space:]' < "$WORK/payload/VERSION")" == "$RELEASE" ]] || fail 'Runtime version mismatch'
  [[ -f "$WORK/payload/linux/ego_agent_service.py" && -f "$WORK/payload/linux/ego-browser" ]] || fail 'Incomplete Linux runtime'
  mkdir -p "$DATA_DIR/releases"
  RUNTIME="$DATA_DIR/releases/$RELEASE"
  if [[ -e "$RUNTIME" ]]; then mv "$RUNTIME" "$DATA_DIR/releases/$RELEASE.backup.$(date +%s).$$"; fi
  mv "$WORK/payload" "$RUNTIME"
  [[ ! -e "$DATA_DIR/current" || -L "$DATA_DIR/current" ]] || fail 'Runtime current path is not a symlink'
  ln -sfn "$RUNTIME" "$DATA_DIR/current"
  ln -sfn "$DATA_DIR/current/linux/ego-browser" "$BIN_DIR/ego-browser"
  printf '#!/bin/bash\n# ego-bridge agent launcher\nexec %q %q "$@"\n' "$PYTHON" "$DATA_DIR/current/linux/ego_agent_service.py" > "$WORK/ego-agent-service"
  chmod 755 "$WORK/ego-agent-service"
  if [[ -L "$BIN_DIR/ego-agent-service" ]]; then unlink "$BIN_DIR/ego-agent-service"; fi
  mv "$WORK/ego-agent-service" "$BIN_DIR/ego-agent-service"
  echo "Installed $RELEASE: $BIN_DIR/ego-browser and $BIN_DIR/ego-agent-service"
  if [[ $START == 1 ]]; then
    if command -v systemctl >/dev/null && systemctl --user show-environment >/dev/null 2>&1; then
      UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
      mkdir -p "$UNIT_DIR"
      "$PYTHON" -c 'import pathlib,sys; p=pathlib.Path(sys.argv[1]); text=p.read_text(); executable=sys.argv[2].replace("\\","\\\\").replace("\"","\\\"").replace("%","%%"); pathlib.Path(sys.argv[3]).write_text(text.replace("%h/.local/bin/ego-agent-service", "\""+executable+"\""))' "$RUNTIME/linux/ego-bridge.service" "$BIN_DIR/ego-agent-service" "$UNIT_DIR/ego-bridge.service"
      systemctl --user daemon-reload
      systemctl --user enable ego-bridge
      systemctl --user restart ego-bridge
      echo "Service started. Next: $BIN_DIR/ego-browser pair"
    else
      echo "No systemd user session. Start manually: $BIN_DIR/ego-agent-service"
    fi
  fi
fi
case ":$PATH:" in *":$BIN_DIR:"*) ;; *) echo "Add to your shell configuration: export PATH=\"$BIN_DIR:\$PATH\"" ;; esac
echo 'Existing pairing and connection settings are preserved.'
