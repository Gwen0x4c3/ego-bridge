#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ego-bridge Mac 侧核心（v2 客户端模式，供 EgoBridge.app 或命令行驱动）

主动拨号连接 Linux 上的 ego_agent_service.py（方向反转），承接 run 请求并在
本机以参数数组 + stdin 执行官方 ego-browser CLI。执行/白名单/幂等/截图回传
逻辑复用 ego_bridge_common.py（与已验收的 v1 完全一致）。

控制协议（stdin 每行一个 JSON 命令 → stdout 每行一个 JSON 状态）：
  in : {"cmd":"start"} {"cmd":"stop"} {"cmd":"pair","host":..,"port":..,"code":..}
       {"cmd":"unpair"} {"cmd":"refresh"} {"cmd":"quit"}
  out: {"ev":"status","state":"off|connecting|connected|error","detail":"...",
        "paired":bool,"host":..,"port":..,"egoRunning":bool|null,"egoInfo":{..},
        "activeRuns":int,"attempt":int}

上行链路协议（TLS + Bearer token + 证书指纹 pin，JSON 行，rid 复用）：
  hello(pair|auth) → pair_ok/auth_ok | pair_fail/auth_fail
  run{rid,...} → ev{rid,...}   file_get{rid} → file_data 分块
  health{rid} → health_ok      result_get{rid} → result_ok
  ping → pong                  unpair_notify

连接信息保存于 macOS 钥匙串（security CLI；失败降级 0600 文件并注明），
用户不可见 token/指纹。

单独调试：python3 bridge_core.py  （stdin 喂命令，看 stdout 状态）
"""

import argparse
import base64
import fcntl
import hashlib
import json
import os
import random
import socket
import ssl
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ego_bridge_common import (  # noqa: E402
    BRIDGE_VERSION, DEFAULT_CLI, DEFAULT_SESSIONS_DIR,
    validate_argv, SessionStore, ResultCache, VersionInfo, RunExecutor,
    BridgeError, log_event,
)
from ego_activity import BrowserActivity

KC_SERVICE = os.environ.get("EGO_BRIDGE_KC_SERVICE", "ego-bridge")  # 测试可覆盖
KC_ACCOUNT = "connection"
KC_FALLBACK = os.environ.get("EGO_BRIDGE_CONN_FILE", os.path.expanduser(
    "~/Library/Application Support/ego-bridge/connection.json"))
CONTROL_SOCKET = os.environ.get("EGO_BRIDGE_CONTROL_SOCKET", os.path.expanduser(
    "~/Library/Application Support/ego-bridge/control.sock"))
SETTINGS_PATH = os.path.join(os.path.dirname(CONTROL_SOCKET), "settings.json")

RETRY_BACKOFF = [1, 2, 5, 10, 10, 10, 15, 15]  # 秒
PING_INTERVAL = 15.0
UPLINK_READ_TIMEOUT = 60.0


# ----------------------------- 连接信息存储（Keychain 优先） -----------------------------

def _kc_run(*args, stdin=None):
    return subprocess.run(["security", *args], capture_output=True, input=stdin)


def conn_save(info):
    blob = json.dumps(info, ensure_ascii=False)
    r = _kc_run("add-generic-password", "-U", "-s", KC_SERVICE,
                "-a", KC_ACCOUNT, "-w", blob)
    if r.returncode == 0:
        return "keychain"
    os.makedirs(os.path.dirname(KC_FALLBACK), exist_ok=True)
    with open(KC_FALLBACK, "w") as f:
        f.write(blob + "\n")
    os.chmod(KC_FALLBACK, 0o600)
    return "file"


def conn_load():
    r = _kc_run("find-generic-password", "-s", KC_SERVICE,
                "-a", KC_ACCOUNT, "-w")
    if r.returncode == 0 and r.stdout.strip():
        try:
            return json.loads(r.stdout.decode().strip()), "keychain"
        except Exception:  # noqa: BLE001
            return None, "keychain"
    if os.path.exists(KC_FALLBACK):
        try:
            return json.loads(open(KC_FALLBACK).read().strip()), "file"
        except Exception:  # noqa: BLE001
            return None, "file"
    return None, None


def conn_delete():
    _kc_run("delete-generic-password", "-s", KC_SERVICE, "-a", KC_ACCOUNT)
    if os.path.exists(KC_FALLBACK):
        os.unlink(KC_FALLBACK)


# ----------------------------- TLS 拨号 -----------------------------

class DialResult:
    def __init__(self, ok, sock=None, fp="", fail="", reason="", token=None):
        self.ok = ok
        self.sock = sock
        self.fp = fp          # 对端（Linux 服务）证书 sha256
        self.fail = fail      # pair_fail/auth_fail reason
        self.reason = reason  # unreachable / cert_changed / auth_failed / pair_fail / bad_hello
        self.token = token    # pair_ok 签发的长期 token


def dial(host, port, mode, *, token=None, code=None, expect_fp=None, timeout=12.0):
    """连 Linux 服务并完成 hello。expect_fp 为 None（配对）时记录指纹；否则校验。"""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # 用指纹 pin 代替 CA
    try:
        raw = socket.create_connection((host, port), timeout=timeout)
    except (ConnectionRefusedError, socket.timeout, OSError) as e:
        return DialResult(False, reason="unreachable",
                          fail=f"{e.__class__.__name__}: 无法连接 {host}:{port}")
    try:
        sock = ctx.wrap_socket(raw, server_hostname=host)
        der = sock.getpeercert(binary_form=True)
        fp = hashlib.sha256(der).hexdigest()
        if expect_fp is not None and fp != expect_fp:
            sock.close()
            return DialResult(False, reason="cert_changed",
                              fail="对端证书与配对时不一致（Linux 服务可能重装）；请重新配对")
        hello = {"op": "hello", "mode": mode}
        if mode == "auth":
            hello["token"] = token
        elif mode == "pair":
            hello["code"] = code
        sock.sendall(json.dumps(hello).encode() + b"\n")
        sock.settimeout(timeout)
        f = sock.makefile("rb")
        try:
            line = f.readline()
        finally:
            f.close()
        if not line:
            sock.close()
            return DialResult(False, reason="bad_hello", fail="服务未回应 hello")
        resp = json.loads(line.decode())
        op = resp.get("op")
        if op == "pair_ok":
            return DialResult(True, sock=sock, fp=fp, reason="paired",
                              token=resp.get("token"))
        if op == "auth_ok":
            return DialResult(True, sock=sock, fp=fp, reason="authed")
        if op in ("pair_fail", "auth_fail"):
            sock.close()
            return DialResult(False,
                              reason="pair_fail" if op == "pair_fail" else "auth_failed",
                              fail=resp.get("reason") or op)
        sock.close()
        return DialResult(False, reason="bad_hello", fail=f"未知回应 {op!r}")
    except (ssl.SSLError, OSError, json.JSONDecodeError) as e:
        return DialResult(False, reason="unreachable", fail=f"{e.__class__.__name__}")


# ----------------------------- 核心 -----------------------------

class Core:
    def __init__(self, args):
        self.cli = os.path.expanduser(args.cli)
        self.sessions = SessionStore(args.sessions_dir)
        self.results = ResultCache()
        self.versions = VersionInfo(self.cli)
        self.executor = RunExecutor(self.cli, self.sessions)

        info, store = conn_load()
        self.conn_info = info            # {host,port,token,certFp} 或 None
        self.store_kind = store
        self.enabled = False
        self.state = "off"
        self.detail = ""
        self.active_runs = 0
        self.attempt = 0

        self.sock = None
        self.sock_lock = threading.Lock()
        self.disabled_flag = threading.Event()   # stop 置位：在跑任务以 mac_disabled 中止
        self.kick = threading.Event()            # 唤醒 supervisor
        self.want_exit = threading.Event()
        self.stopped_cond = threading.Condition()
        self.command_lock = threading.Lock()
        self.output_lock = threading.Lock()
        self.send_lock = threading.Lock()
        self.generation = 0
        self.connected_since = None
        self.control_listener = None
        self.operation_error = None
        self.activity = BrowserActivity(self.cli)
        self.watch_activity = threading.Event()
        try:
            with open(SETTINGS_PATH) as f:
                self.enabled = bool(json.load(f).get("enabled")) and bool(info)
        except (OSError, ValueError):
            pass

    # ---- 状态 ----

    def status_data(self):
        ego = None
        if self.state == "connected":
            ego = bool(self.versions.collect().get("appRunning"))
        return {"ev": "status", "state": self.state, "detail": self.detail,
                "enabled": self.enabled, "paired": self.conn_info is not None,
                "host": (self.conn_info or {}).get("host"),
                "port": (self.conn_info or {}).get("port"),
                "egoRunning": ego, "activeRuns": self.active_runs,
                "attempt": self.attempt, "connectedSince": self.connected_since,
                "operationError": self.operation_error,
                "activity": self.activity.snapshot(),
                "bridgeVersion": BRIDGE_VERSION}

    def persist_enabled(self):
        os.makedirs(os.path.dirname(SETTINGS_PATH), mode=0o700, exist_ok=True)
        temp = SETTINGS_PATH + ".tmp"
        with open(temp, "w") as f:
            json.dump({"enabled": self.enabled}, f)
        os.chmod(temp, 0o600)
        os.replace(temp, SETTINGS_PATH)

    def push_status(self, state=None, detail=None, command_done=False):
        if state is not None:
            self.state = state
        if detail is not None:
            self.detail = detail
        with self.output_lock:
            rec = self.status_data()
            rec["commandDone"] = command_done
            try:
                sys.stdout.write(json.dumps(rec, ensure_ascii=False) + "\n")
                sys.stdout.flush()
            except BrokenPipeError:
                # The App died. Close the executable channel even if this is a worker.
                self.want_exit.set()
                self.close_uplink()
                RunExecutor.kill_all()

    # ---- 上行发送 ----

    def send_uplink(self, obj, expected=None):
        # SSL sockets cannot safely accept simultaneous writes from run/file/ping threads.
        with self.send_lock:
            with self.sock_lock:
                s = self.sock
            if s is None or (expected is not None and s is not expected):
                return False
            try:
                s.sendall(json.dumps(obj, ensure_ascii=False).encode() + b"\n")
                return True
            except OSError:
                return False

    def close_uplink(self):
        with self.sock_lock:
            s = self.sock
            self.sock = None
        if s is not None:
            # 必须先 shutdown：被阻塞的 readline 持有 io_ref，仅 close() 不会
            # 真正关闭 fd、也不会发 FIN，读线程将永远卡住
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                s.close()
            except Exception:  # noqa: BLE001
                pass

    # ---- supervisor：拨号 + 读循环 + 自动重连 ----

    def supervisor(self):
        while not self.want_exit.is_set():
            with self.command_lock:
                ready = self.enabled and self.conn_info and not self.want_exit.is_set()
                if ready:
                    info = self.conn_info
                    generation = self.generation
                    self.attempt += 1
                    self.push_status("connecting",
                                     f"正在连接 {info['host']}:{info['port']}"
                                     + (f"（第 {self.attempt} 次）" if self.attempt > 1 else ""))
            if not ready:
                self.kick.wait(timeout=1.0)
                self.kick.clear()
                continue
            r = dial(info["host"], int(info["port"]), "auth",
                     token=info["token"], expect_fp=info.get("certFp"))
            with self.command_lock:
                if generation != self.generation or not self.enabled or self.want_exit.is_set():
                    if r.sock:
                        r.sock.close()
                    continue
                if not r.ok:
                    log_event("dial_fail", reason=r.reason, attempt=self.attempt)
                    if r.reason in ("auth_failed", "cert_changed", "pair_fail"):
                        self.enabled = False
                        self.persist_enabled()
                        self.push_status("error", r.fail)
                        delay = 1.0
                    else:
                        delay = RETRY_BACKOFF[min(self.attempt - 1, len(RETRY_BACKOFF) - 1)]
                        delay *= 0.8 + random.random() * 0.4
                        self.push_status("connecting",
                                         f"{r.fail}；{delay:.0f}s 后自动重试（不会重放浏览器操作）")
                else:
                    with self.sock_lock:
                        self.sock = r.sock
                    self.attempt = 0
                    self.connected_since = time.time()
                    self.push_status("connected", "已连接")
                    log_event("uplink_connected", host=info["host"], port=info["port"])
            if not r.ok:
                self.kick.wait(timeout=delay)
                self.kick.clear()
                continue
            try:
                self.uplink_read_loop(r.sock)
            except Exception as e:  # noqa: BLE001
                log_event("uplink_error", err=repr(e))
            with self.command_lock:
                self.close_uplink()
                self.connected_since = None
                log_event("uplink_closed")
                if self.want_exit.is_set() or not self.enabled or generation != self.generation:
                    continue
                self.push_status("connecting", "连接断开，正在重连…")
            self.kick.wait(timeout=1.0)
            self.kick.clear()
            # 重新回到循环顶部：若仍 enabled 且已配对则自动重拨

    def uplink_read_loop(self, sock):
        # makefile().readline() is permanently poisoned after its first timeout.
        # recv keeps partial frames across timeouts; heartbeat has its own monotonic clock.
        sock.settimeout(min(1.0, PING_INTERVAL))
        buffer = bytearray()
        last_rx = last_ping = time.monotonic()
        while not self.want_exit.is_set():
            now = time.monotonic()
            if now - last_rx > UPLINK_READ_TIMEOUT:
                raise ConnectionError("keepalive timeout")
            if now - last_ping >= PING_INTERVAL:
                if not self.send_uplink({"op": "ping"}, expected=sock):
                    raise ConnectionError("ping fail")
                last_ping = now
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                raise ConnectionError("eof")
            last_rx = time.monotonic()
            buffer.extend(chunk)
            if len(buffer) > 64 * 1024 * 1024:
                raise ConnectionError("uplink frame too large")
            while b"\n" in buffer:
                line, _, rest = buffer.partition(b"\n")
                buffer = bytearray(rest)
                try:
                    msg = json.loads(line.decode("utf-8"))
                except (ValueError, UnicodeError):
                    continue
                op = msg.get("op")
                if op == "ping":
                    self.send_uplink({"op": "pong"}, expected=sock)
                elif op == "run":
                    self.handle_run(msg)
                elif op == "file_get":
                    self.handle_file_get(msg)
                elif op == "health":
                    self.send_uplink({"op": "health_ok", "rid": msg.get("rid"),
                                      "data": self.health_data()}, expected=sock)
                elif op == "result_get":
                    self.handle_result_get(msg)

    def health_data(self):
        v = self.versions.collect()
        return {"bridgeVersion": BRIDGE_VERSION,
                "cli": v,
                "sessions": self.sessions.count(),
                "sessionsDir": str(self.sessions.base)}

    # ---- run ----

    def handle_run(self, msg):
        rid = msg.get("rid") or ""
        argv = msg.get("argv")
        session = msg.get("session") or "default"
        run_socket = self.sock

        def reply(obj):
            self.send_uplink({"op": "ev", "rid": rid, "ev": obj}, expected=run_socket)

        try:
            stdin_bytes = base64.b64decode(msg.get("stdinB64") or "")
            timeout_ms = int(msg.get("timeoutMs", 180000))
            stdin_mode = validate_argv(argv)
            if stdin_bytes and stdin_mode != "stdin":
                reply({"t": "error", "code": "bad_stdin",
                       "message": "仅 nodejs（无 -e）支持 stdin 脚本"})
                return
        except BridgeError as e:
            reply({"t": "error", "code": e.code, "message": e.message})
            return
        except Exception as e:  # noqa: BLE001
            reply({"t": "error", "code": "bad_request", "message": repr(e)})
            return

        digest = RunExecutor.request_digest(argv, stdin_bytes, session)
        existing = self.results.begin(rid, digest)
        if existing is not None:
            if existing["digest"] != digest:
                reply({"t": "error", "code": "idempotency_conflict",
                       "message": "该 requestId 已用于不同请求体，拒绝执行"})
                return
            if existing["status"] == "running":
                reply({"t": "error", "code": "in_flight", "message": "仍在执行中"})
                return
            payload = dict(existing["payload"])
            payload.update({"replayed": True, "requestId": rid})
            reply({"t": "result", **payload})
            log_event("idempotent_replay", requestId=rid)
            return

        def work():
            self.active_runs += 1
            self.push_status()
            try:
                payload = self.executor.execute(
                    rid=rid, session_id=session, argv=argv,
                    stdin_bytes=stdin_bytes, timeout_ms=timeout_ms,
                    emit=reply,
                    stop_check=lambda: ("mac_disabled" if self.disabled_flag.is_set()
                                        else "uplink_lost" if self.sock is not run_socket
                                        else None))
                self.results.complete(rid, digest, payload)
            finally:
                self.active_runs -= 1
                self.push_status()

        threading.Thread(target=work, daemon=True).start()

    # ---- file_get（分块回传） ----

    def handle_file_get(self, msg):
        rid = msg.get("rid")
        file_socket = self.sock

        def reply(obj):
            return self.send_uplink(obj, expected=file_socket)

        def work():
            try:
                p = self.sessions.file_path(msg.get("session"), msg.get("name"))
                seq = 0
                with open(p, "rb") as f:
                    while True:
                        chunk = f.read(1 << 16)
                        if not chunk:
                            break
                        seq += 1
                        if not reply({
                                "op": "file_data", "rid": rid, "seq": seq,
                                "b64": base64.b64encode(chunk).decode("ascii"),
                                "last": False}):
                            return
                reply({"op": "file_data", "rid": rid, "seq": seq + 1,
                                  "b64": "", "last": True})
            except BridgeError as e:
                reply({"op": "file_err", "rid": rid,
                                  "code": e.code, "message": e.message})
            except OSError as e:
                reply({"op": "file_err", "rid": rid,
                                  "code": "io_error", "message": repr(e)})

        threading.Thread(target=work, daemon=True).start()

    def handle_result_get(self, msg):
        rid = msg.get("rid")
        req_id = msg.get("requestId")
        entry = self.results.get(req_id) if req_id else None
        if entry is None:
            data = {"requestId": req_id, "status": "unknown",
                    "message": "结果未知：Mac 未记录该请求（可能未到达、已过期或核心重启）。"}
        elif entry["status"] == "running":
            data = {"requestId": req_id, "status": "running", "message": "仍在执行中"}
        else:
            data = {"requestId": req_id, "status": "completed", **entry["payload"]}
        self.send_uplink({"op": "result_ok", "rid": rid, "data": data})

    # ---- 控制命令 ----

    def cmd_start(self):
        if not self.conn_info:
            raise ValueError("未配对：请填写 Linux 主机与配对码")
        if self.enabled and self.state == "connected":
            return
        self.disabled_flag.clear()
        self.enabled = True
        self.persist_enabled()
        self.attempt = 0
        self.kick.set()
        self.push_status("connecting", "正在连接…")

    def cmd_stop(self):
        """总开关关闭：在跑任务以 mac_disabled 显式中止（不重跑），随后断链。"""
        self.enabled = False
        self.generation += 1
        self.persist_enabled()
        self.disabled_flag.set()
        deadline = time.time() + 5.0
        while self.active_runs > 0 and time.time() < deadline:
            time.sleep(0.1)
        self.close_uplink()
        self.kick.set()
        self.disabled_flag.clear()
        self.push_status("off", "已关闭（保留配对，开启后自动连接）")

    def cmd_pair(self, host, port, code):
        if not str(code).strip():
            raise ValueError("请填写配对码")
        # Keep an existing working link while validating a replacement pairing.
        r = dial(host, int(port), "pair", code=str(code).strip())
        if not r.ok:
            raise ValueError(f"配对失败：{r.fail}")
        # 配对连接本身可用，但为避免与 supervisor 双读竞争：保存凭证后关闭它，
        # 由 supervisor 用 token 重新 auth（服务端两段式本来就支持）。
        try:
            r.sock.close()
        except Exception:  # noqa: BLE001
            pass
        info = {"host": host, "port": int(port),
                "token": r.token, "certFp": r.fp}
        where = conn_save(info)
        self.cmd_stop()
        self.conn_info = info
        self.store_kind = where
        log_event("paired", host=host, port=port, store=where)
        self.cmd_start()

    def cmd_configure(self, host, port):
        if not self.conn_info:
            raise ValueError("首次连接需要配对码")
        info = dict(self.conn_info, host=host, port=port)
        conn_save(info)
        enabled = self.enabled
        self.cmd_stop()
        self.conn_info = info
        if enabled:
            self.cmd_start()

    def cmd_unpair(self):
        notified = self.send_uplink({"op": "unpair_notify"})
        if notified:
            time.sleep(0.3)
        self.cmd_stop()
        conn_delete()
        self.conn_info = None
        self.kick.set()
        self.push_status("off", "已取消配对" if notified else
                         "本地配对已删除；远端离线，请在远端撤销凭证")
        log_event("unpaired")

    def cmd_quit(self):
        self.want_exit.set()
        self.enabled = False
        self.disabled_flag.set()
        deadline = time.time() + 3.0
        while self.active_runs > 0 and time.time() < deadline:
            time.sleep(0.1)
        RunExecutor.kill_all()
        self.close_uplink()
        if self.control_listener:
            self.control_listener.close()
            try:
                os.unlink(CONTROL_SOCKET)
            except OSError:
                pass
        self.push_status("off", "已退出")
        log_event("core_quit")
        os._exit(0)

    # ---- Shared local control plane: App stdin + Mac CLI, one owner ----

    def dispatch(self, cmd):
        with self.command_lock:
            if cmd.get("cmd") not in ("status", "refresh", "watch_activity"):
                self.operation_error = None
            try:
                c = cmd.get("cmd")
                if c in ("pair", "configure"):
                    host = str(cmd.get("host") or "").strip()
                    port = int(cmd.get("port", 3325))
                    if not host or any(ch.isspace() for ch in host) or not 1 <= port <= 65535:
                        raise ValueError("请输入有效的主机名和端口（1–65535）")
                    if c == "pair":
                        self.cmd_pair(host, port, cmd.get("code", ""))
                    else:
                        self.cmd_configure(host, port)
                elif c == "start":
                    self.cmd_start()
                elif c == "stop":
                    self.cmd_stop()
                elif c == "unpair":
                    self.cmd_unpair()
                elif c in ("refresh", "status"):
                    pass
                elif c == "watch_activity":
                    if cmd.get("enabled"):
                        self.watch_activity.set()
                    else:
                        self.watch_activity.clear()
                        self.activity.clear()
                elif c == "quit":
                    self.cmd_quit()
                else:
                    raise ValueError("未知控制命令")
            except (ValueError, OSError) as e:
                self.operation_error = str(e)
            self.push_status(command_done=cmd.get("source") == "app" and bool(cmd.get("operation")))
            error = self.operation_error if cmd.get("cmd") not in ("status", "refresh", "watch_activity") else None
            return {"ok": error is None, "error": error, "status": self.status_data()}

    def start_control(self):
        directory = os.path.dirname(CONTROL_SOCKET)
        os.makedirs(directory, mode=0o700, exist_ok=True)
        os.chmod(directory, 0o700)
        # Single owner protects the active App from a second core replacing its socket.
        self.control_lock = open(CONTROL_SOCKET + ".lock", "a")
        try:
            fcntl.flock(self.control_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("ego bridge 核心已在运行")
        try:
            os.unlink(CONTROL_SOCKET)
        except FileNotFoundError:
            pass
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(CONTROL_SOCKET)
        os.chmod(CONTROL_SOCKET, 0o600)
        listener.listen(8)
        self.control_listener = listener

        def client(conn):
            with conn:
                conn.settimeout(30)
                try:
                    with conn.makefile("rb") as f:
                        line = f.readline(65537)
                    if len(line) > 65536:
                        return
                    reply = self.dispatch(json.loads(line))
                    conn.sendall(json.dumps(reply, ensure_ascii=False).encode() + b"\n")
                except (OSError, ValueError, TypeError, AttributeError):
                    return

        def accept():
            while not self.want_exit.is_set():
                try:
                    conn, _ = listener.accept()
                except OSError:
                    return
                threading.Thread(target=client, args=(conn,), daemon=True).start()

        threading.Thread(target=accept, daemon=True).start()

    # ---- 主循环 ----

    def serve(self):
        self.start_control()
        status = self.conn_info is not None
        self.push_status("off",
                         "已就绪（已配对，等待开启）" if status else "未配对：请填写 Linux 主机与配对码")
        threading.Thread(target=self.supervisor, daemon=True).start()
        if self.store_kind == "file":
            log_event("keychain_fallback", path=KC_FALLBACK)
        def refresh():
            while not self.want_exit.wait(5):
                self.push_status()
        threading.Thread(target=refresh, daemon=True).start()
        def observe_activity():
            while not self.want_exit.is_set():
                if not self.watch_activity.wait(timeout=1):
                    continue
                self.activity.collect()
                if self.watch_activity.is_set():
                    self.push_status()
                else:
                    self.activity.clear()
                self.want_exit.wait(3)
        threading.Thread(target=observe_activity, daemon=True).start()
        while not self.want_exit.is_set():
            line = sys.stdin.readline()
            if not line:
                # stdin 关闭（App 崩溃）：整体退出，避免留下可执行通道
                self.cmd_quit()
                return
            try:
                cmd = json.loads(line.strip())
            except Exception:  # noqa: BLE001
                continue
            if isinstance(cmd, dict):
                self.dispatch(cmd)


def main():
    ap = argparse.ArgumentParser(description="ego-bridge core (Mac, client mode)")
    ap.add_argument("--cli", default=DEFAULT_CLI)
    ap.add_argument("--sessions-dir", default=DEFAULT_SESSIONS_DIR)
    args = ap.parse_args()
    core = Core(args)
    import signal

    def on_term(signum, frame):
        core.cmd_quit()

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)
    core.serve()


if __name__ == "__main__":
    main()
