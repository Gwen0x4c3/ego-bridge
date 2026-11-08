#!/usr/bin/env bash
# Mac 本机全链路 E2E：service + core + shim 三方同机（127.0.0.1:3345，避免与真机 3325 冲突）
# 隔离：EGO_AGENT_CONF 指到临时目录；Keychain 服务名 ego-bridge-test；shim 配置临时。
set -uo pipefail
cd "$(dirname "$0")/.."   # ego-lite-bridge 根目录

ROOT="$PWD"
PORT=3345
WORK=$(mktemp -d /tmp/ego-e2e.XXXXXX)
export EGO_AGENT_CONF="$WORK/server"
export EGO_BRIDGE_KC_SERVICE="ego-bridge-test"
export EGO_BRIDGE_CONFIG="$WORK/shim.json"
export EGO_BRIDGE_PORT=$PORT
export EGO_BRIDGE_MIRROR="$WORK/mirror"
export EGO_BRIDGE_CONTROL_SOCKET="$WORK/control/control.sock"
export EGO_BRIDGE_CONN_FILE="$WORK/connection.json"
SHIM="$ROOT/linux/ego-browser"
PASS=0; FAIL=0
KEEP=${EGO_E2E_KEEP:-0}
cleanup() {
  [[ -n "${CORE_PID:-}" ]] && kill -9 "$CORE_PID" 2>/dev/null
  [[ -n "${SVC_PID:-}" ]] && kill -9 "$SVC_PID" 2>/dev/null
  security delete-generic-password -s ego-bridge-test -a connection >/dev/null 2>&1 || true
  if [[ "$KEEP" == 1 && $FAIL -gt 0 ]]; then echo "KEEP WORK=$WORK"; else rm -rf "$WORK"; fi
}
trap cleanup EXIT

check() { if [[ "$2" == "$3" ]]; then PASS=$((PASS+1)); echo "PASS  $1"; else FAIL=$((FAIL+1)); echo "FAIL  $1  (expected [$3] got [$2])"; fi }

# 1) 启动服务
python3 linux/ego_agent_service.py --port $PORT --bind 127.0.0.1 >"$WORK/svc.log" 2>&1 &
SVC_PID=$!
for (( readiness=0; readiness<100; readiness++ )); do
  grep -q '"ev": "listening"' "$WORK/svc.log" && break
  if ! kill -0 "$SVC_PID" 2>/dev/null; then break; fi
  sleep 0.1
done
if ! grep -q '"ev": "listening"' "$WORK/svc.log"; then
  echo "FAIL  service-startup"
  cat "$WORK/svc.log"
  exit 1
fi

# 2) shim 生成配对码
CODE=$("$SHIM" pair --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["code"])')
[[ "$CODE" =~ ^[a-z2-9]{4}-[a-z2-9]{4}$ ]] && { PASS=$((PASS+1)); echo "PASS  pair-code($CODE)"; } || { FAIL=$((FAIL+1)); echo "FAIL  pair-code($CODE)"; }

# 3) 启动 core（stdin 用 FIFO 驱动）
mkfifo "$WORK/core.in"
python3 mac/bridge_core.py --sessions-dir "$WORK/sessions" <"$WORK/core.in" >"$WORK/core.out" 2>"$WORK/core.err" &
CORE_PID=$!
exec 3>"$WORK/core.in"    # 保持 FIFO 打开（FIFO 读端 open 会阻塞到此刻，core 才真正启动）
sleep 0.6
send() { echo "$1" >&3; }
mark_line() { if [[ -f "$WORK/core.out" ]]; then wc -l < "$WORK/core.out" | tr -d ' '; else echo 0; fi; }
wait_status_from() {  # 只在 $1 行之后新增的输出里找 state=$2（超时 $3 秒）
  local from="$1" want="$2" t="${3:-10}" i max=$(( ${3:-10} * 10 / 3 ))
  for (( i=0; i<max; i++ )); do
    if tail -n +$((from+1)) "$WORK/core.out" | grep -q "\"state\": \"$want\""; then return 0; fi
    sleep 0.3
  done
  return 1
}
wait_status() { wait_status_from 0 "$1" "${2:-10}"; }

# 4) 配对 → connected
M=$(mark_line)
send "{\"cmd\":\"pair\",\"host\":\"127.0.0.1\",\"port\":$PORT,\"code\":\"$CODE\"}"
if wait_status_from $M connected 15; then PASS=$((PASS+1)); echo "PASS  pair-connected"; else FAIL=$((FAIL+1)); echo "FAIL  pair-connected"; tail -3 "$WORK/core.out"; fi

JSON_STATUS=$("$SHIM" bridge status --json)
if echo "$JSON_STATUS" | python3 -c 'import json,sys; assert json.load(sys.stdin)["uplink"]'; then
  PASS=$((PASS+1)); echo "PASS  machine-readable-pair-and-status"
else FAIL=$((FAIL+1)); echo "FAIL  machine-readable-pair-and-status"; fi

# 5) shim 透传：--version / skill / nodejs
V=$("$SHIM" --version 2>&1 | head -1)
[[ "$V" == ego-browser* ]] && { PASS=$((PASS+1)); echo "PASS  version"; } || { FAIL=$((FAIL+1)); echo "FAIL  version($V)"; }
S=$("$SHIM" skill 2>/dev/null | tail -1)
[[ "$S" == *end\ of\ guide* ]] && { PASS=$((PASS+1)); echo "PASS  skill-sentinel"; } || { FAIL=$((FAIL+1)); echo "FAIL  skill($S)"; }
E=$(EGO_BRIDGE_HINTS=0 "$SHIM" nodejs -e "console.log('e2e-ok')" 2>&1 | grep e2e-ok)
[[ -n "$E" ]] && { PASS=$((PASS+1)); echo "PASS  nodejs-e"; } || { FAIL=$((FAIL+1)); echo "FAIL  nodejs-e"; }

# Long enough to cross the former 20s failure and multiple 15s heartbeats.
BEFORE=$(grep -c '"ev": "uplink_connected"' "$WORK/core.err")
echo "Checking idle keepalive for 65s…"
sleep 65
AFTER=$(grep -c '"ev": "uplink_connected"' "$WORK/core.err")
check "idle-keeps-same-uplink" "$AFTER" "$BEFORE"
"$SHIM" --version >/dev/null 2>&1 && { PASS=$((PASS+1)); echo "PASS  works-after-idle"; } || { FAIL=$((FAIL+1)); echo "FAIL  works-after-idle"; }

# CLI controls the same core; App stdin status stream must observe every change.
M=$(mark_line)
"$ROOT/mac/ego-bridge" --json disconnect >"$WORK/cli.json"
wait_status_from "$M" off 5 && { PASS=$((PASS+1)); echo "PASS  cli-stop-pushes-app-state"; } || { FAIL=$((FAIL+1)); echo "FAIL  cli-stop-pushes-app-state"; }
"$ROOT/mac/ego-bridge" --json connect >"$WORK/cli.json" && { PASS=$((PASS+1)); echo "PASS  cli-start-resumes-same-core"; } || { FAIL=$((FAIL+1)); echo "FAIL  cli-start-resumes-same-core"; }
"$ROOT/mac/ego-bridge" --json configure --host 127.0.0.1 --port "$PORT" >"$WORK/cli.json"
"$ROOT/mac/ego-bridge" --json connect >"$WORK/cli.json" && { PASS=$((PASS+1)); echo "PASS  cli-configure-preserves-pairing"; } || { FAIL=$((FAIL+1)); echo "FAIL  cli-configure-preserves-pairing"; }

# 6) 文件回传（写进 Mac session 目录 → 回传到本机镜像目录）
SESS=$(python3 -c "import json;print(json.load(open('$WORK/shim.json'))['session'])")
OUT=$(EGO_BRIDGE_HINTS=0 "$SHIM" nodejs -e "const fs=await import('node:fs'); fs.writeFileSync('$WORK/sessions/$SESS/e2e.txt','ROUNDTRIP'); console.log('w')" 2>&1)
# core 用 --sessions-dir $WORK/sessions（测试隔离），事件里的 dir 即真实目录
if echo "$OUT" | grep -q "已回传 1 个文件" && [[ -f "$WORK/sessions/$SESS/e2e.txt" ]]; then
  PASS=$((PASS+1)); echo "PASS  file-roundtrip"
else FAIL=$((FAIL+1)); echo "FAIL  file-roundtrip"; echo "$OUT" | tail -3; fi

# 7) import 封禁
"$SHIM" import list >/dev/null 2>&1; RC=$?
check "import-blocked" "$RC" "2"

# 8) 超时语义
EGO_BRIDGE_HINTS=0 EGO_BRIDGE_TIMEOUT_MS=1200 "$SHIM" nodejs -e "await new Promise(r=>setTimeout(r,5000))" >/dev/null 2>"$WORK/timeout.err"; RC=$?
check "timeout-exit" "$RC" "124"

# 9) 幂等（重放由 requestId 触发：手动同 id 复用——经 shim 不易注入，这里验证 result 查询闭环）
RID=$(grep -o 'requestId=[a-f0-9]*' "$WORK/timeout.err" | head -1 | cut -d= -f2)
R=$("$SHIM" bridge result "$RID" 2>/dev/null)
[[ "$R" == *'"timedOut": true'* ]] && { PASS=$((PASS+1)); echo "PASS  result-query"; } || { FAIL=$((FAIL+1)); echo "FAIL  result-query($R)"; }

# 10) 开关：stop → no_uplink；start → 恢复
send '{"cmd":"stop"}'; sleep 1
"$SHIM" --version >/dev/null 2>"$WORK/off.err"; RC=$?
[[ $RC -eq 125 ]] && grep -q "no_uplink\|Mac 侧未连接" "$WORK/off.err" && { PASS=$((PASS+1)); echo "PASS  toggle-off-blocks"; } || { FAIL=$((FAIL+1)); echo "FAIL  toggle-off-blocks(rc=$RC)"; cat "$WORK/off.err"; }
M=$(mark_line); send '{"cmd":"start"}'; wait_status_from $M connected 15
"$SHIM" --version >/dev/null 2>&1 && { PASS=$((PASS+1)); echo "PASS  toggle-on-resumes"; } || { FAIL=$((FAIL+1)); echo "FAIL  toggle-on-resumes"; }

# 11) 断链恢复：杀服务 → core 重连中；重启服务 → 自动回连（证书持久，无需重配）
kill -9 $SVC_PID 2>/dev/null; wait $SVC_PID 2>/dev/null
sleep 1
M=$(mark_line)
python3 linux/ego_agent_service.py --port $PORT --bind 127.0.0.1 >>"$WORK/svc.log" 2>&1 &
SVC_PID=$!
if wait_status_from $M connected 25; then PASS=$((PASS+1)); echo "PASS  auto-reconnect"; else FAIL=$((FAIL+1)); echo "FAIL  auto-reconnect"; tail -3 "$WORK/core.out"; fi
"$SHIM" --version >/dev/null 2>&1 && { PASS=$((PASS+1)); echo "PASS  works-after-reconnect"; } || { FAIL=$((FAIL+1)); echo "FAIL  works-after-reconnect"; }

# 12) 取消配对：凭证清除 + 旧 token 失效 + 回到未配对
send '{"cmd":"unpair"}'; sleep 1
TOK_EXISTS=$( [[ -f "$WORK/server/token" ]] && echo yes || echo no )
check "unpair-linux-token" "$TOK_EXISTS" "no"
# 重新配对一次验证可用，然后用坏 token 测 auth_fail
CODE2=$("$SHIM" pair | head -1 | awk '{print $2}')
M=$(mark_line)
send "{\"cmd\":\"pair\",\"host\":\"127.0.0.1\",\"port\":$PORT,\"code\":\"$CODE2\"}"
wait_status_from $M connected 15 && { PASS=$((PASS+1)); echo "PASS  re-pair"; } || { FAIL=$((FAIL+1)); echo "FAIL  re-pair"; }

# 13) core 退出：子进程清理（跑一个长任务时 quit）
EGO_BRIDGE_HINTS=0 EGO_BRIDGE_TIMEOUT_MS=30000 "$SHIM" nodejs -e "await new Promise(r=>setTimeout(r,20000))" >/dev/null 2>&1 &
LONGPID=$!
sleep 1.5
send '{"cmd":"quit"}'; sleep 1.5
DEAD=$(kill -0 $CORE_PID 2>/dev/null && echo alive || echo dead)
check "core-quit" "$DEAD" "dead"
wait $LONGPID 2>/dev/null; LRC=$?
# 长任务应已收到中止（非 0 退出）而不是继续跑满 20s
[[ $LRC -ne 0 ]] && { PASS=$((PASS+1)); echo "PASS  long-task-aborted(rc=$LRC)"; } || { FAIL=$((FAIL+1)); echo "FAIL  long-task-aborted"; }

echo
echo "== E2E: $PASS passed, $FAIL failed =="
[[ $FAIL -eq 0 ]]
