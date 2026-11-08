#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ego-bridge HTTP 桥接服务（Mac 侧，v1 调试模式）

⚠️ 日常使用请改用 v2 方向反转模式：Linux 常驻 ego_agent_service.py +
Mac 菜单栏 App（EgoBridge.app）+ bridge_core.py。本文件保留用于本机回环
调试与回归测试，接口与 v1 完全一致。

公共逻辑见 ego_bridge_common.py。
"""

import argparse
import base64
import json
import os
import signal
import ssl
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ego_bridge_common import (  # noqa: E402
    BRIDGE_VERSION, DEFAULT_CLI, DEFAULT_SESSIONS_DIR,
    MAX_STDIN, MAX_FILE, MIN_TIMEOUT_MS, MAX_TIMEOUT_MS,
    DEFAULT_TIMEOUT_MS, RE_SESSION, RE_REQUEST_ID,
    BridgeError, SessionStore, ResultCache, VersionInfo, RunExecutor,
    constant_time_eq, gen_token, log_event,
)

DEFAULT_PORT = 8747  # HTTP 旧模式端口
MAX_BODY = 16 * 1024 * 1024  # HTTP 请求体上限（含 base64 膨胀）

STATE = None


class BridgeState:
    def __init__(self, args):
        self.token = self._load_token(args)
        self.cli = os.path.expanduser(args.cli)
        self.sessions = SessionStore(os.path.expanduser(args.sessions_dir))
        self.results = ResultCache()
        self.versions = VersionInfo(self.cli)
        self.executor = RunExecutor(self.cli, self.sessions)
        self.startedAt = time.time()

    @staticmethod
    def _load_token(args):
        if args.token_file:
            with open(os.path.expanduser(args.token_file)) as f:
                t = f.read().strip()
        elif args.token:
            t = args.token
        else:
            t = gen_token()
            print(f"[ego-bridge] 未提供 token，本次随机生成: {t}", file=sys.stderr)
        if len(t) < 16:
            raise SystemExit("token 至少 16 个字符")
        return t

    def check_auth(self, handler):
        auth = handler.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return False
        return constant_time_eq(auth[7:], self.token)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server_version = f"ego-bridge/{BRIDGE_VERSION}"

    def log_message(self, fmt, *a):
        pass

    def _reply_json(self, status, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _check_auth_or_401(self):
        if not STATE.check_auth(self):
            self._reply_json(401, {"error": "unauthorized",
                                   "message": "缺少或错误的 Bearer token"})
            log_event("auth_fail", peer=self.client_address[0])
            return False
        return True

    def _read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            raise BridgeError("no_body", "缺少请求体")
        if n > MAX_BODY:
            raise BridgeError("body_too_large", f"请求体超过 {MAX_BODY} 上限")
        raw = self.rfile.read(n)
        if len(raw) != n:
            raise BridgeError("short_body", "请求体不完整")
        return raw

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if not self._check_auth_or_401():
                return
            if path == "/v1/health":
                self._handle_health()
            elif path.startswith("/v1/result/"):
                self._handle_result(path.split("/", 3)[3])
            elif path.startswith("/v1/file/"):
                self._handle_file(path)
            else:
                self._reply_json(404, {"error": "not_found", "path": path})
        except BridgeError as e:
            self._reply_json(e.status, {"error": e.code, "message": e.message})
        except Exception as e:  # noqa: BLE001
            log_event("error", where="GET", err=repr(e))
            self._reply_json(500, {"error": "internal", "message": repr(e)})

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if not self._check_auth_or_401():
                return
            if path == "/v1/run":
                self._handle_run()
            else:
                self._reply_json(404, {"error": "not_found", "path": path})
        except BridgeError as e:
            self._reply_json(e.status, {"error": e.code, "message": e.message})
        except Exception as e:  # noqa: BLE001
            log_event("error", where="POST", err=repr(e))
            try:
                self._reply_json(500, {"error": "internal", "message": repr(e)})
            except Exception:  # noqa: BLE001
                pass

    def _handle_health(self):
        v = STATE.versions.collect()
        self._reply_json(200, {
            "ok": True,
            "bridgeVersion": BRIDGE_VERSION,
            "mode": "http-legacy",
            "uptimeSec": int(time.time() - STATE.startedAt),
            "cli": v,
            "sessions": STATE.sessions.count(),
            "sessionsDir": str(STATE.sessions.base),
            "limits": {"maxTimeoutMs": MAX_TIMEOUT_MS, "maxFile": MAX_FILE,
                       "maxStdin": MAX_STDIN},
        })

    def _handle_result(self, request_id):
        if not RE_REQUEST_ID.match(request_id or ""):
            self._reply_json(400, {"error": "bad_request_id"})
            return
        entry = STATE.results.get(request_id)
        if entry is None:
            self._reply_json(404, {
                "requestId": request_id, "status": "unknown",
                "message": "结果未知：请求可能未到达、已过期或桥接已重启。"
                           "如需确认，请用只读脚本重新查询页面状态，"
                           "勿重复可能有副作用的操作。",
            })
            return
        if entry["status"] == "running":
            self._reply_json(200, {"requestId": request_id, "status": "running",
                                   "message": "该请求仍在执行中"})
            return
        self._reply_json(200, {"requestId": request_id, "status": "completed",
                               **entry["payload"]})

    def _handle_file(self, path):
        parts = path.split("/")
        if len(parts) != 5:
            self._reply_json(404, {"error": "not_found", "path": path})
            return
        _, _, _, session_id, name = parts
        p = STATE.sessions.file_path(session_id, name)
        size = p.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(size))
        self.end_headers()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 16), b""):
                self.wfile.write(chunk)

    def _handle_run(self):
        from ego_bridge_common import validate_argv
        raw = self._read_body()
        try:
            req = json.loads(raw.decode("utf-8"))
        except Exception:
            raise BridgeError("bad_json", "请求体不是合法 JSON")

        request_id = req.get("requestId", "")
        session_id = req.get("sessionId", "default")
        argv = req.get("argv")
        stdin_b64 = req.get("stdinB64") or ""
        timeout_ms = int(req.get("timeoutMs", DEFAULT_TIMEOUT_MS))

        if not RE_REQUEST_ID.match(request_id or ""):
            raise BridgeError("bad_request_id", "requestId 非法")
        if not RE_SESSION.match(session_id or ""):
            raise BridgeError("bad_session", "sessionId 非法")
        stdin_bytes = b""
        if stdin_b64:
            try:
                stdin_bytes = base64.b64decode(stdin_b64, validate=True)
            except Exception:
                raise BridgeError("bad_stdin", "stdinB64 不是合法 base64")
            if len(stdin_bytes) > MAX_STDIN:
                raise BridgeError("stdin_too_large", f"stdin 超过 {MAX_STDIN} 上限")
        timeout_ms = max(MIN_TIMEOUT_MS, min(MAX_TIMEOUT_MS, timeout_ms))

        stdin_mode = validate_argv(argv)
        if stdin_bytes and stdin_mode != "stdin":
            raise BridgeError("bad_stdin",
                              "仅 `nodejs`（无 -e）支持 stdin 脚本；其他子命令不读 stdin")

        digest = RunExecutor.request_digest(argv, stdin_bytes, session_id)

        existing = STATE.results.begin(request_id, digest)
        if existing is not None:
            if existing["digest"] != digest:
                raise BridgeError("idempotency_conflict",
                                  "该 requestId 已用于不同请求体，拒绝执行")
            if existing["status"] == "running":
                self._stream_begin()
                self._emit({"t": "error", "code": "in_flight",
                            "message": "该 requestId 仍在执行中，请勿重复提交"})
                return
            payload = dict(existing["payload"])
            payload.update({"replayed": True, "requestId": request_id})
            self._stream_begin()
            self._emit({"t": "result", **payload})
            log_event("idempotent_replay", requestId=request_id)
            return

        self._stream_begin()
        # HTTP 连接断开时 RunExecutor 内部 emit 失败即标记 dead → 杀子进程
        STATE.results.complete(request_id, digest,
                               STATE.executor.execute(
                                   rid=request_id, session_id=session_id,
                                   argv=argv, stdin_bytes=stdin_bytes,
                                   timeout_ms=timeout_ms, emit=self._emit))

    def _stream_begin(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _emit(self, obj):
        line = json.dumps(obj, ensure_ascii=False).encode() + b"\n"
        self.wfile.write(line)
        self.wfile.flush()


class BridgeServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# BridgeError 在 HTTP 层需要携带 status；给旧语义加个包装
def _http_error(status):
    class HttpBridgeError(BridgeError):
        def __init__(self, code, message):
            super().__init__(code, message)
            self.status = status
    return HttpBridgeError


def main():
    global STATE
    ap = argparse.ArgumentParser(description="ego-lite HTTP bridge (legacy/debug)")
    ap.add_argument("--bind", required=True)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--cli", default=DEFAULT_CLI)
    ap.add_argument("--cert", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--token")
    ap.add_argument("--token-file")
    ap.add_argument("--sessions-dir", default=DEFAULT_SESSIONS_DIR)
    ap.add_argument("--allow-all-interfaces", action="store_true")
    args = ap.parse_args()

    if args.bind == "0.0.0.0" and not args.allow_all_interfaces:
        raise SystemExit("拒绝默认绑定 0.0.0.0：请用 --bind <LAN_IP>，"
                         "或显式传 --allow-all-interfaces")

    STATE = BridgeState(args)

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(os.path.expanduser(args.cert), os.path.expanduser(args.key))

    srv = BridgeServer((args.bind, args.port), Handler)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)

    def shutdown(signum, frame):
        log_event("shutdown", sig=signum)
        threading.Thread(target=srv.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    v = STATE.versions.collect()
    log_event("listening", bind=args.bind, port=args.port, cli=STATE.cli,
              sessionsDir=str(STATE.sessions.base),
              cliVersion=v.get("cliVersionRaw"), appRunning=v.get("appRunning"))
    print(f"[ego-bridge] listening on https://{args.bind}:{args.port}", flush=True)
    try:
        srv.serve_forever()
    finally:
        srv.server_close()
        log_event("stopped")


if __name__ == "__main__":
    main()
