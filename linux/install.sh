#!/bin/bash
# ego-bridge shim + agent service 安装脚本（Linux devbox 侧，v2）
#
# 用法:
#   ./install.sh                       # 安装 shim 与 service 启动器
#   ./install.sh --service             # 安装并注册 systemd --user 服务（推荐）
#   ./install.sh --skill-from <dir>    # 同时复制官方 Skill 目录
#   ./install.sh --force               # 覆盖已存在的 ego-browser（需确认是我们的 shim）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="${EGO_BRIDGE_BIN_DIR:-$HOME/.local/bin}"
DEST="${BIN_DIR}/ego-browser"
SVC_LAUNCHER="${BIN_DIR}/ego-agent-service"
SKILL_DEST="${EGO_BRIDGE_SKILL_DIR:-$HOME/.agents/skills/ego-browser}"
FORCE=0; WANT_SERVICE=0; SKILL_FROM=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --force) FORCE=1; shift ;;
    --service) WANT_SERVICE=1; shift ;;
    --skill-from) SKILL_FROM="$2"; shift 2 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

# 1) shim 与 service 启动器
mkdir -p "${BIN_DIR}"
if [[ -e "${DEST}" && "${FORCE}" != "1" ]]; then
  if ! grep -q "ego-bridge shim" "${DEST}" 2>/dev/null; then
    echo "拒绝覆盖：${DEST} 已存在且不是 ego-bridge shim。确认后用 --force。" >&2
    exit 2
  fi
fi
install -m 0755 "${SCRIPT_DIR}/ego-browser" "${DEST}"
cat > "${SVC_LAUNCHER}" <<LAUNCHER
#!/bin/bash
exec python3 "${SCRIPT_DIR}/ego_agent_service.py" "\$@"
LAUNCHER
chmod 755 "${SVC_LAUNCHER}"
echo "已安装: ${DEST}"
echo "已安装: ${SVC_LAUNCHER}"

case ":${PATH}:" in
  *":${BIN_DIR}:"*) ;;
  *) echo "注意: ${BIN_DIR} 不在 PATH 里，请把它加进 shell 配置。" >&2 ;;
esac

# 2) systemd --user 服务（可选）
if [[ ${WANT_SERVICE} == 1 ]]; then
  mkdir -p ~/.config/systemd/user
  sed "s|%h/.local/bin/ego-agent-service|${SVC_LAUNCHER}|" \
    "${SCRIPT_DIR}/ego-bridge.service" > ~/.config/systemd/user/ego-bridge.service
  systemctl --user daemon-reload
  systemctl --user enable --now ego-bridge
  echo "systemd 服务已启用: systemctl --user status ego-bridge"
fi

# 3) 官方 Skill 副本（bootstrap 文档；指南本体始终来自 `ego-browser skill` 实时透传）
if [[ -n "${SKILL_FROM}" ]]; then
  if [[ ! -f "${SKILL_FROM}/SKILL.md" ]]; then
    echo "skill 目录无效（缺 SKILL.md）: ${SKILL_FROM}" >&2
    exit 2
  fi
  mkdir -p "$(dirname "${SKILL_DEST}")"
  rm -rf "${SKILL_DEST}"
  cp -R "${SKILL_FROM}" "${SKILL_DEST}"
  echo "已安装 Skill: ${SKILL_DEST}"
fi

echo
echo "下一步:"
[[ ${WANT_SERVICE} == 1 ]] || echo "  启动服务: ${SVC_LAUNCHER}（或加 --service 用 systemd 托管）"
echo "  生成配对码: ego-browser pair   （在 Mac 的 ego bridge App 中输入）"
