#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ego-bridge Linux 侧常驻服务（py3.7+，零依赖）
"""
职责（纯路由，不执行任何脚本）：
  1. TLS 监听 :3325（默认 0.0.0.0，可 --bind 收紧）。两种角色由首行 hello 区分：
     - {"op":"hello","mode":"local"}   本机 shim（仅接受 127.0.0.1 来源），
       每连接一个请求：run / file_get / status / pair_gen / result
     - {"op":"hello","mode":"pair","code":"xxxx-xxxx"} 或 {"op":"hello","mode":"auth","token":"..."}
       Mac 核心（上行链路，同时只允许一条，新链路顶替旧链路）
  2. 配对码：5 分钟有效、一次性；pair 成功签发长期 token（存 0600 文件）
  3. run 请求经上行链路转发到 Mac 执行，事件流回路由 rid 对应回 shim 连接；
     上行断开时：在跑请求向 shim 发「结果未知」错误事件（绝不自动重试）
  4. 证书固定于 ~/.config/ego-bridge/server/（Mac 首次配对时 pin 指纹，
     服务重启不换证书，Mac 无需重新配对）

启动：python3 ego_agent_service.py [--port 3325] [--bind 0.0.0.0]
systemd：见 ego-bridge.service（systemctl --user enable --now ego-bridge）
"""

import argparse
import base64
import hmac
import json
import os
import secrets
import socket
import ssl
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

SERVICE_VERSION = "2.4.1"
CONF_DIR = Path(os.environ.get("EGO_AGENT_CONF",
                               "~/.config/ego-bridge/server")).expanduser()
PAIR_TTL = 300.0
UPLINK_READ_TIMEOUT = 45.0  # Mac 核心 15s 一次 ping；超过 45s 无数据视为断链
OP_TIMEOUT = 45.0           # file_get/health/result 等短操作的上限

log_lock = threading.Lock()


def log(ev, **kw):
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "ev": ev}
    rec.update(kw)
    with log_lock:
        print(json.dumps(rec, ensure_ascii=False), file=sys.stderr, flush=True)


# ----------------------------- 持久化（证书 / token） -----------------------------

def ensure_cert():
    """首次运行生成自签名证书并固定保存（重启不换，Mac 的 pin 才能长期有效）。
    不同 openssl 版本对 EC 参数写法不一，依次尝试直至成功。"""
    CONF_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(CONF_DIR, 0o700)
    cert, key = CONF_DIR / "cert.pem", CONF_DIR / "key.pem"
    if not cert.exists() or not key.exists():
        strategies = [
            ["-newkey", "ec:prime256v1"],                       # 经典写法
            ["-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256"],  # OpenSSL 3.6+
            ["-newkey", "rsa:3072"],                             # 兜底
        ]
        err = ""
        for st in strategies:
            cmd = (["openssl", "req", "-x509"] + st
                   + ["-keyout", str(key), "-out", str(cert), "-nodes", "-days", "825",
                      "-subj", "/CN=ego-bridge-agent"])
            r = subprocess.run(cmd, capture_output=True)
            if r.returncode == 0 and cert.exists() and key.exists():
                os.chmod(key, 0o600)
                os.chmod(cert, 0o644)
                log("cert_generated", path=str(cert), algo=st[1])
                return cert, key
            err = r.stderr.decode()[:200]
        raise SystemExit("证书生成失败: " + err)
    return cert, key


def token_path():
    return CONF_DIR / "token"


def load_token():
    p = token_path()
    if p.exists():
        return p.read_text().strip()
    return None


def save_token(tok):
    token_path().write_text(tok + "\n")
    os.chmod(token_path(), 0o600)


def delete_token():
    p = token_path()
    if p.exists():
        p.unlink()


def cert_fingerprint(cert_path):
    der = subprocess.run(["openssl", "x509", "-in", str(cert_path),
                          "-outform", "DER"], capture_output=True).stdout
    import hashlib
    return hashlib.sha256(der).hexdigest()


# ----------------------------- 配对码 -----------------------------

pair_codes = {}  # code -> expiry_ts（一次性：校验后即删）
pair_lock = threading.Lock()


def gen_pair_code():
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"  # 去易混淆字符
    part = "".join(secrets.choice(alphabet) for _ in range(4))
    part2 = "".join(secrets.choice(alphabet) for _ in range(4))
    code = part + "-" + part2
    with pair_lock:
        now = time.time()
        for k in [k for k, v in pair_codes.items() if v < now]:
            del pair_codes[k]
        pair_codes[code] = now + PAIR_TTL
    return code


def consume_pair_code(code):
    with pair_lock:
        exp = pair_codes.pop(code, None)
    if exp is None or exp < time.time():
        return False
    return True


# ----------------------------- 连接封装 -----------------------------

class Peer:
    """线程安全的 JSON 行读写。"""

    def __init__(self, conn, addr):
        self.conn = conn
        self.addr = addr
        self.wlock = threading.Lock()
        self.rfile = conn.makefile("rb")

    def readline_json(self):
        line = self.rfile.readline(64 * 1024 * 1024)
        if not line:
            return None
        line = line.strip()
        if not line:
            return {}
        try:
            return json.loads(line.decode("utf-8"))
        except Exception:  # noqa: BLE001
            return {"op": "__bad_json__"}

    def send_json(self, obj):
        data = json.dumps(obj, ensure_ascii=False).encode() + b"\n"
        with self.wlock:
            self.conn.sendall(data)

    def close(self):
        # 先 shutdown 再 close：避免被阻塞的 readline 持有 io_ref 导致 fd 不真正关闭
        try:
            self.conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.rfile.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.conn.close()
        except Exception:  # noqa: BLE001
            pass


# ----------------------------- 服务核心 -----------------------------

class AgentService:
    def __init__(self, args):
        self.args = args
        self.uplink = None           # Peer（已认证的 Mac 连接）
        self.uplink_lock = threading.Lock()
        self.routes = {}             # rid -> Peer（shim 连接，等待事件回传）
        self.routes_lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.started_at = time.time()
        self.cert, self.key = ensure_cert()
        self.cert_fp = cert_fingerprint(self.cert)

    # ---- 上行链路管理 ----

    def set_uplink(self, peer):
        with self.uplink_lock:
            old = self.uplink
            self.uplink = peer
        if old is not None and old is not peer:
            log("uplink_replaced")
            old.close()

    def clear_uplink(self, peer):
        with self.uplink_lock:
            was_uplink = self.uplink is peer
            if was_uplink:
                self.uplink = None
        if was_uplink:
            self.fail_routes("uplink_lost",
                             "与 Mac 的连接中断，结果未知。请勿自动重试；"
                             "待 Mac 重连后可用 ego-browser bridge result <requestId> 查询。")

    def send_uplink(self, obj, peer=None):
        """返回是否发送成功。"""
        with self.uplink_lock:
            ul = self.uplink
        if ul is None or (peer is not None and ul is not peer):
            return False
        try:
            ul.send_json(obj)
            return True
        except Exception:  # noqa: BLE001
            return False

    # ---- rid 路由 ----

    def add_route(self, rid, peer):
        with self.routes_lock:
            self.routes[rid] = peer

    def pop_route(self, rid):
        with self.routes_lock:
            return self.routes.pop(rid, None)

    def fail_routes(self, code, message):
        with self.routes_lock:
            items = list(self.routes.items())
            self.routes.clear()
        for rid, peer in items:
            try:
                peer.send_json({"t": "error", "code": code, "message": message})
            except Exception:  # noqa: BLE001
                pass
            try:
                peer.close()
            except Exception:  # noqa: BLE001
                pass

    # ---- 上行链路读循环 ----

    def uplink_loop(self, peer):
        try:
            peer.conn.settimeout(UPLINK_READ_TIMEOUT)
            while True:
                try:
                    msg = peer.readline_json()
                except (socket.timeout, ssl.SSLError, OSError):
                    log("uplink_timeout")
                    break
                if msg is None:
                    log("uplink_eof")
                    break
                op = msg.get("op")
                rid = msg.get("rid")
                if op == "pong":
                    continue
                if op == "ping":
                    peer.send_json({"op": "pong"})
                    continue
                if op == "unpair_notify":
                    delete_token()
                    log("unpaired_by_mac")
                    continue
                if op == "ev":
                    ev = msg.get("ev") or {}
                    with self.routes_lock:
                        target = self.routes.get(rid)
                    if target is not None:
                        try:
                            target.send_json(ev)
                        except Exception:  # noqa: BLE001
                            pass
                        if ev.get("t") == "result":
                            # result 之后不再有事件：摘除路由并给 shim 干净的 EOF
                            self.pop_route(rid)
                            target.close()
                    continue
                if op == "file_data":
                    # 分块传输：路由保持到 last 块才摘除；
                    # 翻译为 shim 侧事件模式（t: file_data）
                    with self.routes_lock:
                        target = self.routes.get(rid)
                    if target is not None:
                        try:
                            target.send_json({"t": "file_data",
                                              "seq": msg.get("seq"),
                                              "b64": msg.get("b64"),
                                              "last": bool(msg.get("last"))})
                        except Exception:  # noqa: BLE001
                            pass
                        if msg.get("last"):
                            self.pop_route(rid)
                            target.close()
                    continue
                if op == "file_err":
                    target = self.pop_route(rid) if rid else None
                    if target is not None:
                        try:
                            target.send_json({"t": "error",
                                              "code": msg.get("code"),
                                              "message": msg.get("message")})
                        except Exception:  # noqa: BLE001
                            pass
                    continue
                if op == "result_ok":
                    # bridge result 请求：解包 data 直接作为单回复
                    target = self.pop_route(rid) if rid else None
                    if target is not None:
                        try:
                            target.send_json(msg.get("data") or {"status": "unknown"})
                        except Exception:  # noqa: BLE001
                            pass
                    continue
                if op in ("health_ok", "op_err"):
                    target = self.pop_route(rid) if rid else None
                    if target is not None:
                        try:
                            target.send_json(msg)
                        except Exception:  # noqa: BLE001
                            pass
                    continue
                log("uplink_unknown_op", op=op)
        except Exception as e:  # noqa: BLE001
            log("uplink_error", err=repr(e))
        finally:
            self.clear_uplink(peer)
            peer.close()
            log("uplink_closed")

    # ---- 本机 shim 请求处理 ----
    # 返回 (wait_rid, deadline)：异步请求需要 serve_conn 等路由完成后才能关连接。

    def local_request(self, peer, req):
        op = req.get("op")
        if op == "pair_gen":
            code = gen_pair_code()
            peer.send_json({"ok": True, "code": code,
                            "expiresIn": int(PAIR_TTL),
                            "hint": "在 Mac 的 ego bridge App 中输入该配对码"})
            return None, 0
        if op == "status":
            self.handle_status(peer)
            return None, 0
        if op == "result":
            return self.handle_result(peer, req)
        if op == "file_get":
            return self.handle_file_get(peer, req)
        if op == "run":
            return self.handle_run(peer, req)
        peer.send_json({"t": "error", "code": "bad_op", "message": f"未知操作 {op!r}"})
        return None, 0

    def uplink_roundtrip(self, peer, req, wait_secs=OP_TIMEOUT, rid=None):
        """发送带 rid 的请求到 Mac；回复由 uplink_loop 按路由送回本 peer。
        rid 缺省时生成；run 请求必须透传 shim 的 requestId 作为 rid
        （Mac 以它做幂等缓存键，断链后 `bridge result <requestId>` 才能查到）。
        返回 (rid, deadline) 供 serve_conn 等待；无上行时直接回错误并返回 (None, 0)。"""
        rid = rid or uuid.uuid4().hex
        req = dict(req)
        req["rid"] = rid
        self.add_route(rid, peer)
        if not self.send_uplink(req):
            self.pop_route(rid)
            peer.send_json({"t": "error", "code": "no_uplink",
                            "message": "Mac 侧未连接（未开启或已断开）。"
                                       "请在 Mac 打开 ego bridge 并确认总开关已开启。"})
            return None, 0
        return rid, time.time() + wait_secs

    def handle_run(self, peer, req):
        rid = req.get("requestId", "")
        if not rid:
            peer.send_json({"t": "error", "code": "bad_request_id"})
            return None, 0
        return self.uplink_roundtrip(peer, {
            "op": "run",
            "argv": req.get("argv"),
            "stdinB64": req.get("stdinB64") or "",
            "timeoutMs": int(req.get("timeoutMs", 180000)),
            "session": req.get("sessionId", "default")},
            rid=rid,
            wait_secs=int(req.get("timeoutMs", 180000)) / 1000.0 + 90.0)

    def handle_file_get(self, peer, req):
        return self.uplink_roundtrip(peer, {
            "op": "file_get", "session": req.get("session"),
            "name": req.get("name")}, wait_secs=150.0)

    def handle_result(self, peer, req):
        return self.uplink_roundtrip(peer, {
            "op": "result_get", "requestId": req.get("requestId")}, wait_secs=20.0)

    def handle_status(self, peer):
        info = {
            "ok": True,
            "serviceVersion": SERVICE_VERSION,
            "port": self.args.port,
            "bind": self.args.bind,
            "uptimeSec": int(time.time() - self.started_at),
            "paired": load_token() is not None,
            "uplink": self.uplink is not None,
            "certFingerprint": self.cert_fp[:16] + "…",
        }
        # 有上行时附带 Mac 健康（最多等 5s）
        if self.uplink is not None:
            ev = threading.Event()
            holder = {}
            rid = uuid.uuid4().hex

            class _WaitPeer:
                def send_json(self, msg):
                    if msg.get("op") == "health_ok":
                        holder["data"] = msg.get("data")
                    else:
                        holder["err"] = msg
                    ev.set()
                def close(self):
                    pass
            wp = _WaitPeer()
            self.add_route(rid, wp)
            if self.send_uplink({"op": "health", "rid": rid}):
                ev.wait(5.0)
                self.pop_route(rid)
                if "data" in holder:
                    info["mac"] = holder["data"]
            else:
                self.pop_route(rid)
        peer.send_json(info)

    # ---- 连接入口 ----

    def serve_conn(self, conn, addr):
        peer = None
        try:
            conn.settimeout(30.0)
            tls = self.tls_ctx.wrap_socket(conn, server_side=True)
            peer = Peer(tls, addr)
            hello = peer.readline_json()
            if not hello or hello.get("op") != "hello":
                return
            mode = hello.get("mode")
            if mode == "local":
                if addr[0] not in ("127.0.0.1", "::1"):
                    peer.send_json({"t": "error", "code": "forbidden",
                                    "message": "local 模式仅允许本机连接"})
                    return
                req = peer.readline_json()
                if req:
                    wait_rid, deadline = self.local_request(peer, req)
                    if wait_rid:
                        # 异步请求：等事件流送完（result/错误事件会摘除路由）再关连接
                        while time.time() < deadline:
                            with self.routes_lock:
                                if wait_rid not in self.routes:
                                    break
                            time.sleep(0.2)
                return
            if mode == "pair":
                code = str(hello.get("code") or "")
                if not consume_pair_code(code):
                    peer.send_json({"op": "pair_fail",
                                    "reason": "配对码无效、过期或已使用；"
                                              "请在 Linux 重新运行 ego-browser pair"})
                    log("pair_fail", peer=addr[0])
                    return
                token = secrets.token_hex(32)
                save_token(token)
                self.set_uplink(peer)
                peer.send_json({"op": "pair_ok", "token": token,
                                "certFingerprint": self.cert_fp})
                log("pair_ok", peer=addr[0])
                self.uplink_loop(peer)
                return
            if mode == "auth":
                token = str(hello.get("token") or "")
                cur = load_token()
                if not cur or not hmac.compare_digest(token, cur):
                    peer.send_json({"op": "auth_fail",
                                    "reason": "token 无效（可能已被取消配对或更换）；"
                                              "请重新配对"})
                    log("auth_fail", peer=addr[0])
                    return
                self.set_uplink(peer)
                peer.send_json({"op": "auth_ok",
                                "certFingerprint": self.cert_fp})
                log("auth_ok", peer=addr[0])
                self.uplink_loop(peer)
                return
            peer.send_json({"op": "hello_fail", "reason": f"未知 mode {mode!r}"})
        except (ssl.SSLError, OSError) as e:
            log("conn_error", peer=str(addr), err=repr(e))
        except Exception as e:  # noqa: BLE001
            log("conn_panic", peer=str(addr), err=repr(e))
        finally:
            if peer is not None:
                # 若是当前上行则清掉并通知在跑请求
                self.clear_uplink(peer)
                peer.close()

    def run(self):
        self.tls_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.tls_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        self.tls_ctx.load_cert_chain(str(self.cert), str(self.key))

        srv = socket.socket(socket.AF_INET6 if ":" in self.args.bind else socket.AF_INET,
                            socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.args.bind, self.args.port))
        srv.listen(16)
        log("listening", bind=self.args.bind, port=self.args.port,
            cert=str(self.cert), paired=load_token() is not None)
        print(f"[ego-agent] listening on {self.args.bind}:{self.args.port} "
              f"(TLS, 证书指纹 {self.cert_fp[:16]}…)", flush=True)
        try:
            while True:
                conn, addr = srv.accept()
                threading.Thread(target=self.serve_conn,
                                 args=(conn, addr), daemon=True).start()
        except KeyboardInterrupt:
            pass
        finally:
            srv.close()
            self.fail_routes("service_stopped", "服务已停止，结果未知。")
            log("stopped")


def main():
    ap = argparse.ArgumentParser(description="ego-bridge Linux agent service")
    ap.add_argument("--port", type=int, default=3325)
    ap.add_argument("--bind", default="0.0.0.0",
                    help="默认 0.0.0.0（token+TLS 保护）；可收紧为内网地址")
    args = ap.parse_args()
    AgentService(args).run()


if __name__ == "__main__":
    main()
