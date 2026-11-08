#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ego-bridge 公共模块（仅 Mac 侧运行，python3.8+）

包含被 HTTP 桥接（bridge_server.py，调试用）与客户端核心（bridge_core.py，
配合菜单栏 App 使用）共享的全部已验收逻辑：
  - argv 白名单校验（validate_argv）
  - session 目录与受限文件访问（SessionStore）
  - requestId 幂等结果缓存（ResultCache）
  - CLI/app 版本体检（VersionInfo）
  - run 执行器（RunExecutor）：参数数组 + stdin 字节调官方 CLI、双流透传、
    心跳、超时击杀、客户端断开中止、session 目录文件 diff

安全底线（不变）：不拼接 shell；不扫描 session 目录之外的路径；
日志与缓存不含脚本正文、输出内容、页面内容、Cookie、截图。
"""

import base64
import hashlib
import hmac
import json
import os
import plistlib
import re
import secrets
import subprocess
import threading
import time
from pathlib import Path

BRIDGE_VERSION = "2.4.1"

# ----------------------------- 常量 / 策略 -----------------------------

DEFAULT_CLI = "~/.local/bin/ego-browser"
DEFAULT_SESSIONS_DIR = "/tmp/ego-bridge"

MAX_ARGC = 32
MAX_ARG_LEN = 1024 * 1024
MAX_STDIN = 8 * 1024 * 1024
MAX_STREAM = 64 * 1024 * 1024
MAX_FILE = 50 * 1024 * 1024
RESULT_TTL = 1800.0
RESULT_MAX = 256
HEARTBEAT_SEC = 10.0

MIN_TIMEOUT_MS = 1000
MAX_TIMEOUT_MS = 600_000
DEFAULT_TIMEOUT_MS = 180_000

RE_SESSION = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
RE_REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
RE_FILENAME = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
RE_SERVER_NAME = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

BLOCKED_SUBCOMMANDS = ("import", "upgrade")


def log_event(ev, **kw):
    """结构化生命周期日志（stderr，一行 JSON）。不含脚本正文与输出内容。"""
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()), "ev": ev}
    rec.update(kw)
    print(json.dumps(rec, ensure_ascii=False), file=__import__("sys").stderr, flush=True)


_ERROR_STATUS = {
    "blocked_command": 403,
    "idempotency_conflict": 409,
    "in_flight": 409,
    "body_too_large": 413,
    "stdin_too_large": 413,
    "file_too_large": 413,
    "no_body": 411,
}


class BridgeError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = _ERROR_STATUS.get(code, 400)


# ----------------------------- argv 白名单 -----------------------------

def validate_argv(argv):
    """校验转发给官方 CLI 的参数数组。返回 stdin 模式（"none"|"script"|"stdin"）。
    import/upgrade/位置参数脚本明确拒绝；未知子命令原样转发（让官方 CLI 报错）。"""
    if not argv or not all(isinstance(a, str) for a in argv):
        raise BridgeError("bad_argv", "argv 必须是非空字符串数组")
    if len(argv) > MAX_ARGC:
        raise BridgeError("bad_argv", f"argv 元素过多（>{MAX_ARGC}）")
    if any(len(a) > MAX_ARG_LEN for a in argv):
        raise BridgeError("bad_argv", f"单个参数过长（>{MAX_ARG_LEN}）")

    i = 0
    while i < len(argv) and argv[i].startswith("--ego-server-name="):
        name = argv[i].split("=", 1)[1]
        if not RE_SERVER_NAME.match(name):
            raise BridgeError("bad_argv", f"--ego-server-name 非法: {name!r}")
        i += 1
    rest = argv[i:]

    if not rest:
        return "none"

    head = rest[0]

    if head in BLOCKED_SUBCOMMANDS:
        raise BridgeError("blocked_command",
                          f"`{head}` 会修改 Mac 本机状态，桥接明确不支持；请在 Mac 上手动执行")

    if head in ("--version", "-v", "--help", "-h", "help", "skill"):
        if head == "help" and len(rest) == 2:
            if not re.match(r"^[a-z-]{1,32}$", rest[1]):
                raise BridgeError("bad_argv", f"未知 help 主题: {rest[1]!r}")
        if head == "skill":
            ok = (len(rest) == 1
                  or (len(rest) == 3 and rest[1] == "--sdk-path")
                  or (len(rest) == 2 and rest[1] in ("-h", "--help")))
            if not ok:
                raise BridgeError("bad_argv", "skill 仅支持 [--sdk-path <path>]")
        return "none"

    if head == "nodejs":
        j = 1
        has_eval = False
        while j < len(rest):
            a = rest[j]
            if a in ("-e", "--eval"):
                if has_eval:
                    raise BridgeError("bad_argv", "仅允许一个 -e/--eval")
                if j + 1 >= len(rest):
                    raise BridgeError("bad_argv", "-e/--eval 缺少脚本参数")
                has_eval = True
                j += 2
            elif a == "--sdk-path":
                if j + 1 >= len(rest):
                    raise BridgeError("bad_argv", "--sdk-path 缺少参数")
                j += 2
            elif a in ("-h", "--help"):
                j += 1
            else:
                raise BridgeError("bad_argv",
                                  f"nodejs 不支持参数 {a!r}（仅 -e/--eval、--sdk-path；"
                                  f"位置参数脚本文件与交互 REPL 不支持，请用 stdin/heredoc）")
        return "script" if has_eval else "stdin"

    return "none"


# ----------------------------- session 目录 -----------------------------

class SessionStore:
    def __init__(self, base):
        self.base = Path(base)
        self.base.mkdir(parents=True, exist_ok=True)
        os.chmod(self.base, 0o700)
        self._lock = threading.Lock()

    def session_dir(self, session_id):
        if not RE_SESSION.match(session_id or ""):
            raise BridgeError("bad_session", "sessionId 非法（^[-._A-Za-z0-9]{1,64}$）")
        with self._lock:
            d = self.base / session_id
            if not d.exists():
                d.mkdir(parents=True)
            os.chmod(d, 0o700)
            return d

    def snapshot(self, d):
        out = {}
        try:
            for e in os.scandir(d):
                if e.is_file(follow_symlinks=False):
                    st = e.stat(follow_symlinks=False)
                    out[e.name] = (st.st_size, st.st_mtime_ns)
        except FileNotFoundError:
            pass
        return out

    def diff(self, before, d):
        after = self.snapshot(d)
        return [n for n, m in after.items()
                if n not in before or before[n] != m]

    def file_path(self, session_id, name):
        d = self.session_dir(session_id)
        if not RE_FILENAME.match(name or ""):
            raise BridgeError("bad_filename", "文件名非法（仅单段名称，禁止路径）")
        p = d / name
        if p.resolve().parent != d.resolve():
            raise BridgeError("bad_filename", "路径越界")
        if p.is_symlink() or not p.is_file():
            raise BridgeError("no_file", "文件不存在")
        if p.stat().st_size > MAX_FILE:
            raise BridgeError("file_too_large", f"文件超过 {MAX_FILE} 上限")
        return p

    def count(self):
        try:
            return len([e for e in os.scandir(self.base) if e.is_dir()])
        except FileNotFoundError:
            return 0


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


# ----------------------------- 幂等结果缓存 -----------------------------

class ResultCache:
    def __init__(self):
        self._d = {}
        self._lock = threading.Lock()

    def _prune(self):
        now = time.time()
        for k in [k for k, v in self._d.items()
                  if v["status"] == "completed"
                  and now - v["completedAt"] > RESULT_TTL]:
            del self._d[k]
        while len(self._d) > RESULT_MAX:
            self._d.pop(next(iter(self._d)))

    def begin(self, request_id, digest):
        with self._lock:
            self._prune()
            old = self._d.get(request_id)
            if old is None:
                self._d[request_id] = {"status": "running", "digest": digest,
                                       "startedAt": time.time()}
                return None
            return old

    def complete(self, request_id, digest, payload):
        with self._lock:
            self._d[request_id] = {"status": "completed", "digest": digest,
                                   "completedAt": time.time(), "payload": payload}

    def get(self, request_id):
        with self._lock:
            self._prune()
            return self._d.get(request_id)


# ----------------------------- 版本体检 -----------------------------

class VersionInfo:
    """CLI / app 版本探测。只读，不修改任何状态。"""

    def __init__(self, cli):
        self.cli = cli
        self._lock = threading.Lock()
        self._cache = (0, None)

    def _collect_now(self):
        info = {"cliPath": os.path.realpath(self.cli),
                "cliExists": os.path.exists(self.cli)}
        try:
            r = subprocess.run([self.cli, "--version"], capture_output=True, timeout=15)
            raw = r.stdout.decode("utf-8", "replace").strip() \
                or r.stderr.decode("utf-8", "replace").strip()
            info["cliVersionRaw"] = raw
            info["cliVersionOk"] = r.returncode == 0 and bool(raw)
        except Exception as e:  # noqa: BLE001
            info["cliVersionRaw"] = f"探测失败: {e.__class__.__name__}"
            info["cliVersionOk"] = False

        app = Path("/Applications/ego lite.app")
        info["appInstalled"] = app.exists()
        if app.exists():
            try:
                with open(app / "Contents" / "Info.plist", "rb") as f:
                    pl = plistlib.load(f)
                info["appBundleVersion"] = pl.get("CFBundleShortVersionString")
            except Exception:  # noqa: BLE001
                info["appBundleVersion"] = None
            cur = app / "Contents/Frameworks/ego Framework.framework/Versions/Current"
            try:
                info["appFrameworkCurrent"] = os.path.basename(os.readlink(cur))
            except Exception:  # noqa: BLE001
                info["appFrameworkCurrent"] = None

        m = re.search(r"/Versions/([^/]+)/", info["cliPath"])
        info["cliFrameworkVersion"] = m.group(1) if m else None

        try:
            r = subprocess.run(["pgrep", "-f", "MacOS/ego lite"],
                               capture_output=True, timeout=10)
            info["appRunning"] = r.returncode == 0
        except Exception:  # noqa: BLE001
            info["appRunning"] = None

        warnings = []
        cf, cur = info.get("cliFrameworkVersion"), info.get("appFrameworkCurrent")
        if info["appInstalled"] and not info["appRunning"]:
            warnings.append("ego lite 应用未在运行；浏览器操作会失败（或触发 CLI 自动拉起）")
        if cf and cur and cf != cur:
            warnings.append(
                f"版本偏差：PATH 上的 CLI 走框架 {cf}，应用 Current 框架是 {cur}"
                f"（app {info.get('appBundleVersion')}）。"
                f"请让用户在 Mac 自行运行 `ego-browser upgrade` 并重启应用")
        info["warnings"] = warnings
        return info

    def collect(self):
        with self._lock:
            if time.time() - self._cache[0] < 60:
                return self._cache[1]
            info = self._collect_now()
            self._cache = (time.time(), info)
            return info


# ----------------------------- run 执行器 -----------------------------

class RunExecutor:
    """执行一次官方 CLI 调用并流式产出事件。

    execute(...) 返回 payload dict（幂等缓存与 UI 用的元数据，不含输出内容）。
    emit 回调抛出的任何异常都被吞掉并标记 dead（客户端断开）：
    杀死子进程、payload.aborted=True，由调用方决定后续（v1 语义保持）。
    子进程登记在类级 CHILDREN，供整体退出/开关时 kill_all()。
    """

    CHILDREN = set()
    CHILDREN_LOCK = threading.Lock()

    def __init__(self, cli, sessions):
        self.cli = cli
        self.sessions = sessions

    @classmethod
    def kill_all(cls, note="killed_by_shutdown"):
        with cls.CHILDREN_LOCK:
            kids = list(cls.CHILDREN)
        for p in kids:
            try:
                p.kill()
            except Exception:  # noqa: BLE001
                pass
        return len(kids)

    @staticmethod
    def request_digest(argv, stdin_bytes, session_id):
        return hashlib.sha256(json.dumps(
            [argv, stdin_bytes.decode("utf-8", "surrogatepass"), session_id],
            ensure_ascii=True).encode()).hexdigest()

    def execute(self, *, rid, session_id, argv, stdin_bytes, timeout_ms, emit,
                stop_check=None):
        """stop_check() 返回非 None 字符串时中止（如 'mac_disabled'）。"""
        started = time.time()
        dead = False
        abort_reason = None

        def emit_safe(obj):
            nonlocal dead
            if dead:
                return
            try:
                emit(obj)
            except Exception:  # noqa: BLE001
                dead = True

        session_dir = self.sessions.session_dir(session_id)
        before = self.sessions.snapshot(session_dir)

        log_event("run_start", requestId=rid, session=session_id,
                  sub=argv[0] if argv else "?", argc=len(argv),
                  stdin=len(stdin_bytes), timeoutMs=timeout_ms)

        proc = None
        counters = {"o": 0, "e": 0}
        trunc = {}
        try:
            try:
                proc = subprocess.Popen(
                    [self.cli] + argv,
                    stdin=subprocess.PIPE if stdin_bytes else subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    env=os.environ, close_fds=True,
                )
            except FileNotFoundError:
                emit_safe({"t": "error", "code": "cli_missing",
                           "message": f"Mac 上找不到官方 CLI: {self.cli}"})
                return {"exitCode": None, "timedOut": False, "aborted": False,
                        "durationMs": 0, "files": [], "error": "cli_missing"}

            evq = []
            qlock = threading.Lock()
            with RunExecutor.CHILDREN_LOCK:
                RunExecutor.CHILDREN.add(proc)

            def pump(stream, tag):
                total = 0
                while True:
                    chunk = stream.read1(65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total <= MAX_STREAM:
                        with qlock:
                            evq.append({"t": "out", "s": tag,
                                        "d": base64.b64encode(chunk).decode("ascii")})
                    else:
                        trunc[tag] = True
                counters[tag] = total

            t_out = threading.Thread(target=pump, args=(proc.stdout, "o"), daemon=True)
            t_err = threading.Thread(target=pump, args=(proc.stderr, "e"), daemon=True)
            t_out.start()
            t_err.start()

            if stdin_bytes:
                def feed():
                    try:
                        proc.stdin.write(stdin_bytes)
                    except Exception:  # noqa: BLE001
                        pass
                    finally:
                        try:
                            proc.stdin.close()
                        except Exception:  # noqa: BLE001
                            pass
                threading.Thread(target=feed, daemon=True).start()

            def drain_queue():
                with qlock:
                    pending, evq[:] = evq[:], []
                for obj in pending:
                    emit_safe(obj)
                    if dead:
                        break

            timed_out = False
            deadline = time.monotonic() + timeout_ms / 1000.0
            last_write = time.monotonic()
            while True:
                try:
                    proc.wait(timeout=0.25)
                    break
                except subprocess.TimeoutExpired:
                    pass
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
                if stop_check is not None:
                    reason = stop_check()
                    if reason:
                        abort_reason = reason
                        break
                drain_queue()
                now = time.monotonic()
                if now - last_write >= HEARTBEAT_SEC:
                    emit_safe({"t": "ping"})
                    last_write = now

            if timed_out or abort_reason or dead:
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except Exception:  # noqa: BLE001
                    pass
            t_out.join(timeout=5)
            t_err.join(timeout=5)
            drain_queue()
            with RunExecutor.CHILDREN_LOCK:
                RunExecutor.CHILDREN.discard(proc)

            exit_code = None if (timed_out or abort_reason or dead) else proc.returncode

            files = []
            try:
                for name in self.sessions.diff(before, session_dir):
                    fp = session_dir / name
                    files.append({"name": name, "size": fp.stat().st_size,
                                  "sha256": sha256_file(fp)})
            except Exception as e:  # noqa: BLE001
                log_event("file_diff_error", requestId=rid, err=repr(e))

            payload = {
                "exitCode": exit_code,
                "timedOut": timed_out,
                "aborted": bool(dead or abort_reason),
                "durationMs": int((time.time() - started) * 1000),
                "outBytes": counters["o"], "errBytes": counters["e"],
                "outTruncated": "o" in trunc, "errTruncated": "e" in trunc,
                "files": files,
                "error": ("timeout" if timed_out else
                          abort_reason or ("client_disconnected" if dead else None)),
            }
            if "o" in trunc:
                emit_safe({"t": "truncated", "s": "o", "limit": MAX_STREAM})
            if "e" in trunc:
                emit_safe({"t": "truncated", "s": "e", "limit": MAX_STREAM})
            if files:
                emit_safe({"t": "files", "sessionId": session_id,
                           "dir": str(session_dir), "files": files})
            result = dict(payload)
            result["requestId"] = rid
            result["sessionId"] = session_id
            emit_safe({"t": "result", **result})

            log_event("run_complete", requestId=rid, session=session_id,
                      exitCode=exit_code, timedOut=timed_out, aborted=payload["aborted"],
                      durationMs=payload["durationMs"],
                      out=counters["o"], err=counters["e"], files=len(files))
            return payload

        finally:
            if proc is not None and proc.poll() is None:
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass


def constant_time_eq(a, b):
    return hmac.compare_digest(a.encode(), b.encode())


def gen_token(nbytes=32):
    return secrets.token_hex(nbytes)
