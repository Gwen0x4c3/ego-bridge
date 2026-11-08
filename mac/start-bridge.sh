#!/bin/bash
# ego-lite 桥接前台启动脚本（Mac 侧，原型 v1）
#
# 每次启动：
#   - 在一次性临时目录生成自签名证书（EC P-256，SAN 含 LAN IP/127.0.0.1/localhost）
#   - 生成一次性 Bearer token（32 字节随机）
#   - 前台运行 bridge_server.py；Ctrl-C 即停，退出时临时目录（证书/私钥/token）全部删除
#
# 不安装 launchd/PM2，不写入任何持久凭证。常驻化步骤见 ../docs/SECURITY.md。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${EGO_BRIDGE_PORT:-8747}"

# 解析本机 LAN IP（en0 → en1 → 回退 127.0.0.1 并警告）
LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || true)"
if [[ -z "${LAN_IP}" ]]; then
  LAN_IP="$(ipconfig getifaddr en1 2>/dev/null || true)"
fi
BIND_IP="${EGO_BRIDGE_BIND:-${LAN_IP:-127.0.0.1}}"
if [[ "${BIND_IP}" == "127.0.0.1" ]]; then
  echo "[ego-bridge] 警告：未探测到 LAN IP，将只绑定 127.0.0.1（仅本机回环可用）。" >&2
  echo "[ego-bridge] 可用 EGO_BRIDGE_BIND=<LAN_IP> 指定。" >&2
fi

RUN_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ego-bridge-run.XXXXXX")"
chmod 700 "${RUN_DIR}"
CERT="${RUN_DIR}/cert.pem"
KEY="${RUN_DIR}/key.pem"
TOKEN_FILE="${RUN_DIR}/token"

cleanup() {
  kill "${BRIDGE_PID:-}" 2>/dev/null || true
  rm -rf "${RUN_DIR}"
}
trap cleanup EXIT INT TERM

# openssl 各版本对 EC 参数写法不一，依次尝试（客户端按指纹 pin，无需 SAN）
CERT_OK=0
if openssl req -x509 -newkey ec:prime256v1 -keyout "${KEY}" -out "${CERT}" \
    -nodes -days 30 -subj "/CN=ego-bridge-local" >/dev/null 2>&1; then
  CERT_OK=1
elif openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 \
    -keyout "${KEY}" -out "${CERT}" -nodes -days 30 \
    -subj "/CN=ego-bridge-local" >/dev/null 2>&1; then
  CERT_OK=1
elif openssl req -x509 -newkey rsa:3072 -keyout "${KEY}" -out "${CERT}" \
    -nodes -days 30 -subj "/CN=ego-bridge-local" >/dev/null 2>&1; then
  CERT_OK=1
fi
[[ $CERT_OK == 1 ]] || { echo "[ego-bridge] 证书生成失败" >&2; exit 1; }
chmod 600 "${KEY}" "${CERT}"

openssl rand -hex 32 > "${TOKEN_FILE}"
chmod 600 "${TOKEN_FILE}"
TOKEN="$(cat "${TOKEN_FILE}")"

# 证书 DER sha256 指纹（客户端做 pinning，防 LAN 内中间人）
FP="$(openssl x509 -in "${CERT}" -outform DER | shasum -a 256 | awk '{print $1}')"

python3 "${SCRIPT_DIR}/bridge_server.py" \
  --bind "${BIND_IP}" --port "${PORT}" \
  --cert "${CERT}" --key "${KEY}" \
  --token-file "${TOKEN_FILE}" &
BRIDGE_PID=$!

echo
echo "================ ego-bridge 已启动（前台，Ctrl-C 停止） ================"
echo "  地址        : https://${BIND_IP}:${PORT}"
echo "  Bearer token: ${TOKEN}"
echo "  证书指纹    : sha256:${FP}"
echo
echo "在 Linux devbox 上执行配对（把下面整行粘贴过去）："
echo "  ego-browser bridge pair ${BIND_IP}:${PORT} ${TOKEN} ${FP}"
echo "======================================================================"
echo

wait "${BRIDGE_PID}"
