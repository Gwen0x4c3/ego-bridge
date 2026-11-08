#!/usr/bin/env bash
# Build and install the App plus the shared Mac control CLI. Pairing is preserved.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
APP_DIR="${EGO_BRIDGE_APP_DIR:-$HOME/Applications}"
BIN_DIR="${EGO_BRIDGE_BIN_DIR:-$HOME/.local/bin}"
BUILD=1
OPEN=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-build) BUILD=0; shift ;;
    --no-open) OPEN=0; shift ;;
    --help|-h)
      echo "Usage: bash mac/install.sh [--skip-build] [--no-open]"
      echo "Environment: EGO_BRIDGE_APP_DIR, EGO_BRIDGE_BIN_DIR"
      exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ $(uname -s) == Darwin ]] || { echo "This installer requires macOS." >&2; exit 1; }
[[ $BUILD == 0 ]] || bash "$ROOT/mac/EgoBridgeApp/build-app.sh"
SOURCE="$ROOT/dist/EgoBridge.app"
[[ -d "$SOURCE" ]] || { echo "Missing $SOURCE; build the App first." >&2; exit 1; }
mkdir -p "$APP_DIR"
DEST="$APP_DIR/EgoBridge.app"
if [[ -e "$DEST" ]]; then
  IDENTIFIER=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$DEST/Contents/Info.plist" 2>/dev/null || true)
  [[ "$IDENTIFIER" == app.ego-bridge.EgoBridge ]] || {
    echo "Refusing to replace an unrelated App at $DEST" >&2; exit 1;
  }
fi
STAGE=$(mktemp -d "$APP_DIR/.ego-bridge-install.XXXXXX")
cleanup() {
  if [[ ! -e "$DEST" && -d "$STAGE/previous.app" ]]; then
    mv "$STAGE/previous.app" "$DEST"
  fi
  rm -rf "$STAGE"
}
trap cleanup EXIT
ditto "$SOURCE" "$STAGE/EgoBridge.app"
codesign --verify --deep --strict "$STAGE/EgoBridge.app"
[[ ! -e "$DEST" ]] || mv "$DEST" "$STAGE/previous.app"
mv "$STAGE/EgoBridge.app" "$DEST"
EGO_BRIDGE_APP="$DEST" EGO_BRIDGE_BIN_DIR="$BIN_DIR" bash "$ROOT/mac/install-cli.sh"
echo "Installed $DEST"
[[ $OPEN == 0 ]] || open "$DEST"
