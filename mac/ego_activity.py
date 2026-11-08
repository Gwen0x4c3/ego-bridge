"""Read-only, in-memory Agent-space observation using the public ego-browser SDK."""
import json
import subprocess
import threading
import time

MARKER = "@@ego-bridge-activity@@"
SCRIPT = '''
const spaces = (await listTaskSpaces())
  .filter(s => s.createdBy === "agent" || s.ownership === "agent")
  .map(s => ({id:s.id, name:s.name, ownership:s.ownership,
    recentTitle:(s.recentTabTitles || []).find(t => t.trim()) || ""}));
console.log("@@ego-bridge-activity@@" + JSON.stringify(spaces));
'''


class BrowserActivity:
    def __init__(self, cli):
        self.cli = cli
        self.lock = threading.Lock()
        self.data = {"state": "idle", "spaces": [], "total": 0, "updatedAt": None}

    def snapshot(self):
        with self.lock:
            return dict(self.data, spaces=[dict(s) for s in self.data["spaces"]])

    def clear(self):
        with self.lock:
            self.data = {"state": "idle", "spaces": [], "total": 0, "updatedAt": None}

    @staticmethod
    def parse(output):
        for line in reversed(output.splitlines()):
            if MARKER in line:
                raw = json.loads(line.split(MARKER, 1)[1])
                if not isinstance(raw, list):
                    raise ValueError("invalid activity response")
                spaces = []
                for entry in raw:
                    if not isinstance(entry, dict) or not isinstance(entry.get("id"), int):
                        continue
                    spaces.append({"id": entry["id"],
                                   "name": str(entry.get("name") or "未命名空间")[:120],
                                   "ownership": str(entry.get("ownership") or "unknown"),
                                   "recentTitle": str(entry.get("recentTitle") or "")[:160]})
                return {"state": "ready", "spaces": spaces[:3], "total": len(spaces),
                        "updatedAt": time.time()}
        raise ValueError("missing activity response")

    def collect(self):
        result = {"state": "unavailable", "spaces": [], "total": 0, "updatedAt": time.time()}
        try:
            # Observation must never bootstrap a closed browser through the CLI.
            running = subprocess.run(["pgrep", "-f", "MacOS/ego lite"],
                                     capture_output=True, timeout=2)
            if running.returncode == 0:
                proc = subprocess.run([self.cli, "nodejs"], input=SCRIPT,
                                      capture_output=True, text=True, timeout=4)
                if proc.returncode == 0:
                    result = self.parse(proc.stdout + "\n" + proc.stderr)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        with self.lock:
            self.data = result
