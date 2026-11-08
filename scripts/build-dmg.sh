#!/usr/bin/env bash
# Use macOS tools only; no paid service or third-party packaging dependency.
set -euo pipefail
[[ $(uname -s) == Darwin ]] || { echo 'DMG packaging requires macOS' >&2; exit 1; }
[[ $# == 2 ]] || { echo 'Usage: bash scripts/build-dmg.sh APP OUTPUT.dmg' >&2; exit 2; }
APP=$1
OUTPUT=$2
[[ -d "$APP/Contents" && "$OUTPUT" == *.dmg ]] || { echo 'Expected an App bundle and .dmg output' >&2; exit 2; }
codesign --verify --deep --strict "$APP"
VERSION=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Contents/Info.plist")
mkdir -p "$(dirname "$OUTPUT")"
WORK=$(mktemp -d "${TMPDIR:-/tmp}/ego-bridge-dmg.XXXXXX")
MOUNTED=0
cleanup() {
  if [[ $MOUNTED == 1 ]]; then
    hdiutil detach "$WORK/mount" >/dev/null || { echo "Could not detach $WORK/mount; staging retained" >&2; return 1; }
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT
mkdir -p "$WORK/payload" "$WORK/mount"
ditto "$APP" "$WORK/payload/EgoBridge.app"
ln -s /Applications "$WORK/payload/Applications"
cat > "$WORK/payload/Install.txt" <<'TEXT'
ego bridge

Drag EgoBridge.app to Applications, then open it from Applications.
将 EgoBridge.app 拖到 Applications，再从 Applications 打开。

Requires macOS 13+, Python 3.9+ and the separately installed official ego-lite browser.
This App is ad-hoc signed, not notarized. macOS may require first-launch approval.

For the CLI and guided Linux setup, run in Terminal:
curl -fsSL https://raw.githubusercontent.com/gwen0x4c3/ego-bridge/main/install.sh | bash
Quit a running copy of ego bridge before running the installer.

https://github.com/gwen0x4c3/ego-bridge
TEXT
hdiutil create -ov -format UDZO -imagekey zlib-level=9 -fs HFS+ \
  -volname "ego bridge $VERSION" -srcfolder "$WORK/payload" "$OUTPUT"
hdiutil verify "$OUTPUT"
hdiutil attach -readonly -nobrowse -noautoopen -mountpoint "$WORK/mount" "$OUTPUT" >/dev/null
MOUNTED=1
codesign --verify --deep --strict "$WORK/mount/EgoBridge.app"
[[ -L "$WORK/mount/Applications" && $(readlink "$WORK/mount/Applications") == /Applications ]]
cmp "$APP/Contents/Resources/VERSION" "$WORK/mount/EgoBridge.app/Contents/Resources/VERSION"
[[ -s "$WORK/mount/Install.txt" ]]
echo "Verified drag-to-install DMG: $OUTPUT"
