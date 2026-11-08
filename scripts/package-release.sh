#!/usr/bin/env bash
# Produce redistributable first-party files only; never bundle ego-lite itself.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
VERSION=$(tr -d '[:space:]' < "$ROOT/VERSION")
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Invalid VERSION" >&2; exit 1; }
TAG="v$VERSION"
if [[ -n ${RELEASE_TAG:-} && "$RELEASE_TAG" != "$TAG" ]]; then
  echo "Release tag $RELEASE_TAG does not match VERSION $TAG" >&2; exit 1
fi
OUT="$ROOT/dist/release"
mkdir -p "$OUT"
case "${1:-}" in
  macos)
    [[ $(uname -s) == Darwin ]] || { echo "macos packaging requires macOS" >&2; exit 1; }
    bash "$ROOT/mac/EgoBridgeApp/build-app.sh"
    codesign --verify --deep --strict "$ROOT/dist/EgoBridge.app"
    NAME="ego-bridge-$TAG-macos-$(uname -m).zip"
    ditto -c -k --sequesterRsrc --keepParent "$ROOT/dist/EgoBridge.app" "$OUT/$NAME"
    ;;
  linux)
    NAME="ego-bridge-$TAG-linux.tar.gz"
    tar --exclude='__pycache__' --exclude='*.pyc' -czf "$OUT/$NAME" -C "$ROOT" \
      linux skills docs README.md LICENSE VERSION CHANGELOG.md
    ;;
  *) echo "Usage: bash scripts/package-release.sh macos|linux" >&2; exit 2 ;;
esac
cd "$OUT"
shasum -a 256 "$NAME" > "$NAME.sha256"
echo "$OUT/$NAME"
