#!/usr/bin/env bash
# 聚焦复现：文件回传（小文件 + 多 chunk 大文件）
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD; PORT=3347; WORK=$(mktemp -d /tmp/ego-fr2.XXXXXX)
export EGO_AGENT_CONF="$WORK/server" EGO_BRIDGE_KC_SERVICE=ego-bridge-fr2 \
       EGO_BRIDGE_CONFIG="$WORK/shim.json" EGO_BRIDGE_PORT=$PORT \
       EGO_BRIDGE_MIRROR="$WORK/mirror" EGO_BRIDGE_HINTS=0
export EGO_BRIDGE_CONTROL_SOCKET="$WORK/control/control.sock"
export EGO_BRIDGE_CONN_FILE="$WORK/connection.json"
SHIM=$ROOT/linux/ego-browser
cleanup(){ kill -9 ${CORE:-} ${SVC:-} 2>/dev/null; security delete-generic-password -s ego-bridge-fr2 -a connection >/dev/null 2>&1; rm -rf "$WORK"; }
trap cleanup EXIT
python3 linux/ego_agent_service.py --port $PORT --bind 127.0.0.1 >"$WORK/svc.log" 2>&1 & SVC=$!
sleep 1
CODE=$("$SHIM" pair | head -1 | awk '{print $2}')
mkfifo "$WORK/in"
python3 mac/bridge_core.py --sessions-dir "$WORK/sessions" <"$WORK/in" >"$WORK/core.out" 2>"$WORK/core.err" & CORE=$!
exec 3>"$WORK/in"
echo "{\"cmd\":\"pair\",\"host\":\"127.0.0.1\",\"port\":$PORT,\"code\":\"$CODE\"}" >&3
for i in $(seq 1 40); do grep -q '"state": "connected"' "$WORK/core.out" && break; sleep 0.25; done
grep -q '"state": "connected"' "$WORK/core.out" || { echo "NOT-CONNECTED"; tail -2 "$WORK/core.out"; exit 1; }
SESS=$(python3 -c "import json;print(json.load(open('$WORK/shim.json'))['session'])")
echo "== 小文件 =="
"$SHIM" nodejs -e "const fs=await import('node:fs'); fs.writeFileSync('$WORK/sessions/$SESS/x.txt','HELLO'); console.log('ok')" 2>&1 | grep -E "回传|^ok"
echo "== 大文件 300KB =="
"$SHIM" nodejs -e "const fs=await import('node:fs'); fs.writeFileSync('$WORK/sessions/$SESS/big.bin', Buffer.alloc(300000,7)); console.log('big')" 2>&1 | grep -E "回传|^big"
echo "== 目录 =="
ls -la "$WORK/sessions/$SESS/" | tail -3
echo "== 原始协议探针 =="
python3 - "$SESS" <<'PYEOF'
import json, socket, ssl, sys, time
sess = sys.argv[1]
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE
c = ctx.wrap_socket(socket.create_connection(("127.0.0.1", 3347), timeout=15))
c.sendall(b'{"op":"hello","mode":"local"}\n')
c.sendall(json.dumps({"op":"file_get","session":sess,"name":"x.txt"}).encode()+b"\n")
f = c.makefile("rb"); c.settimeout(10)
for i in range(8):
    try: line = f.readline()
    except Exception as e: print(f"read{i} EXC {e.__class__.__name__}"); break
    if not line: print(f"read{i} EOF"); break
    print(f"read{i}: {line[:100]!r}")
    ev = json.loads(line)
    if ev.get("t")=="file_data" and ev.get("last"): break
c.close()
PYEOF
echo "== svc tail =="
tail -6 "$WORK/svc.log"
echo "== core err tail =="
tail -4 "$WORK/core.err"
# 保留现场便于排查
trap - EXIT; kill -9 $SVC 2>/dev/null; kill -9 $CORE 2>/dev/null
echo "WORK kept at $WORK"
