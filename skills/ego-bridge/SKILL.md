---
name: ego-bridge
description: Install, pair, diagnose, and operate ego bridge so Linux or remote AI agents can use a Mac's ego-lite browser. Use for remote Mac browser automation, ego-bridge setup, Mac menu-bar/CLI synchronization, pairing, or bridge connectivity issues. Local browser-only tasks should use the browser's own skill.
license: MIT
metadata:
  author: gwen0x4c3
  version: "2.4.1"
---

# Ego Bridge

Connect a remote Linux agent to the user's Mac browser. The browser and login state stay
on the Mac; the Mac initiates the TLS connection to the Linux service.

## Installation includes the runtime

**Installing this skill installs instructions only. It does not install the Mac App,
Linux service, command shims, Python, or ego-lite.** When asked to set up ego bridge,
complete the relevant runtime installation and pairing before reporting success.

Read [references/install.md](references/install.md) for installation. Prefer Homebrew
on a Mac with Homebrew, or the prebuilt Release installer on either platform. Do not
require a user to clone or build the repository for normal setup. Source builds are
only a development fallback. The installed skill directory is not the runtime.

Determine the machine's role before changing it:

- **Mac:** official ego-lite and its `ego-browser` CLI, the EgoBridge menu-bar App,
  and the separate `ego-bridge` control CLI. Never replace the official Mac
  `ego-browser` executable with the Linux shim.
- **Linux:** Python 3, OpenSSL, this project's `ego-browser` shim, and the
  `ego-agent-service` process. No local browser installation is required.
- Do not infer a remote hostname or SSH authorization from an example. Obtain the
  missing connection destination from the task or user.

Verify both ends when available: Mac `ego-bridge --json status`; Linux
`ego-browser bridge status` and `ego-browser --version`. An installed skill or a
successful build alone is not a working bridge.

## Use the remote browser

On Linux, first run `ego-browser skill` and read its complete version-matched guide
through `[ego-browser:skill] end of guide`. Use those documented APIs, not assumed
Playwright methods. If the bridge is unavailable, diagnose it before browser work.

```bash
ego-browser bridge status
ego-browser nodejs <<'JS'
const task = await taskSpace("inspect example page");
const page = task.page("p1");
await page.goto("https://example.com");
console.log({spaceId: task.spaceId, title: await page.title()});
await task.finish({keep: []});
JS
```

Reuse the same TaskSpace for a user goal. Stop when the user takes control; do not
create another space or reclaim control to route around it. Wait for user approval
before the SDK's takeover operation.

For screenshots, obtain the session with `ego-browser bridge status` or the shim's
session hint, and save under `/tmp/ego-bridge/<session>/`. Files there are returned
to Linux automatically. Other Mac paths are not scanned. Uploads, interactive REPL,
`import`, and `upgrade` are not supported through the bridge.

## Operate and diagnose

- Mac pause/resume: `ego-bridge disconnect` / `ego-bridge connect`.
  Both update the same core and menu-bar state. `--json` goes before the subcommand.
- Pair: Linux `ego-browser pair`; Mac
  `ego-bridge connect --host HOST --port 3325 --code-stdin` with the fresh code on stdin.
  Keep the code out of logs and durable files. One-time codes expire after five minutes.
- Linux service: `systemctl --user status ego-bridge`; logs:
  `journalctl --user -u ego-bridge -n 50`.
- Timeout/connection loss: do not replay browser actions automatically. Use
  `ego-browser bridge result REQUEST_ID` to distinguish completed, running, and unknown.
- Certificate/authentication failure: show the actual reason; do not disable TLS
  verification or silently replace saved credentials.

The popup can show Agent spaces, control ownership, recent page titles, and active
bridge commands. These are not a global stream of individual browser actions.
For extra HTTP headers, see the repository's `docs/EXTRA_HEADERS.md`: current
support is per page and per CLI invocation, not a persistent workspace setting.
