#!/bin/bash
# 构建 EgoBridge.app（菜单栏应用）并做临时签名
# 产物：ego-lite-bridge/dist/EgoBridge.app
set -euo pipefail
cd "$(dirname "$0")/../.."   # ego-lite-bridge 根目录
ROOT=$PWD
VERSION=$(tr -d '[:space:]' < "$ROOT/VERSION")
if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "VERSION 必须是 major.minor.patch" >&2
  exit 1
fi
grep -q "^BRIDGE_VERSION = \"$VERSION\"$" "$ROOT/mac/ego_bridge_common.py" || {
  echo "VERSION 与 BRIDGE_VERSION 不一致" >&2; exit 1;
}
DIST="$ROOT/dist"
APP="$DIST/EgoBridge.app"
mkdir -p "$DIST"
STAGE=$(mktemp -d "$DIST/.build.XXXXXX")
trap 'rm -rf "$STAGE"' EXIT
APP="$STAGE/EgoBridge.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>              <string>ego bridge</string>
    <key>CFBundleDisplayName</key>       <string>ego bridge</string>
    <key>CFBundleIdentifier</key>        <string>app.ego-bridge.EgoBridge</string>
    <key>CFBundleExecutable</key>        <string>EgoBridge</string>
    <key>CFBundlePackageType</key>       <string>APPL</string>
    <key>CFBundleShortVersionString</key><string>$VERSION</string>
    <key>CFBundleVersion</key>           <string>$VERSION</string>
    <key>LSMinimumSystemVersion</key>    <string>13.0</string>
    <key>LSUIElement</key>               <true/>
    <key>NSHighResolutionCapable</key>   <true/>
</dict>
</plist>
PLIST

echo "[build] swiftc 编译…"
swiftc -Onone -parse-as-library \
  -module-cache-path "${TMPDIR:-/tmp}/ego-bridge-swift-cache" \
  -target "$(uname -m)-apple-macosx13.0" \
  -o "$APP/Contents/MacOS/EgoBridge" \
  "$ROOT/mac/EgoBridgeApp/EgoBridgeApp.swift"

cp "$ROOT/mac/bridge_core.py" "$APP/Contents/Resources/bridge_core.py"
cp "$ROOT/mac/ego_bridge_common.py" "$APP/Contents/Resources/ego_bridge_common.py"
cp "$ROOT/mac/ego_activity.py" "$APP/Contents/Resources/ego_activity.py"
cp "$ROOT/mac/ego-bridge" "$APP/Contents/Resources/ego-bridge"
cp "$ROOT/LICENSE" "$APP/Contents/Resources/LICENSE"
chmod +x "$APP/Contents/Resources/ego-bridge"

echo "[build] 临时签名…"
codesign --force --sign - "$APP" >/dev/null 2>&1
rm -rf "$DIST/EgoBridge.app"
mv "$APP" "$DIST/EgoBridge.app"
APP="$DIST/EgoBridge.app"

echo "[build] 完成: $APP"
echo "安装到 /Applications（可选）: cp -R '$APP' /Applications/"
echo "运行: open '$APP'"
