#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
APP="${EGO_BRIDGE_APP:-$ROOT/dist/EgoBridge.app}"
BIN_DIR="${EGO_BRIDGE_BIN_DIR:-$HOME/.local/bin}"
CLI="$APP/Contents/Resources/ego-bridge"
if [[ ! -x "$CLI" ]]; then
  echo "请先运行 mac/EgoBridgeApp/build-app.sh" >&2
  exit 1
fi
mkdir -p "$BIN_DIR"
if [[ -e "$BIN_DIR/ego-bridge" && ! -L "$BIN_DIR/ego-bridge" ]]; then
  echo "拒绝覆盖非符号链接文件：$BIN_DIR/ego-bridge" >&2
  exit 1
fi
ln -sfn "$CLI" "$BIN_DIR/ego-bridge"
echo "已安装 $BIN_DIR/ego-bridge"
echo "使用：ego-bridge --json status（确保 $BIN_DIR 在 PATH 中）"
