# Development and validation

The core uses Python's standard library. The App is built with SwiftUI/AppKit and
requires macOS 13+ with Xcode Command Line Tools. The Linux service supports Python 3.7+.

## Automated tests

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
bash -n mac/install.sh mac/install-cli.sh mac/EgoBridgeApp/build-app.sh linux/install.sh scripts/package-release.sh
```

The 12 core/activity tests cover timeout-safe framing, heartbeat/lost-peer detection,
shared local control, credential-free status, persistence, command completion,
stale dial rejection, socket ownership and read-only activity parsing.
CI runs these on Linux and macOS, then builds and verifies the Mac App.

## Browser-dependent local E2E

Requires macOS, OpenSSL and the separately installed official ego-browser CLI.
Uses an isolated service on loopback port 3345, a test Keychain service and temporary
session/control/configuration files; it does not replace the active production pairing.

```bash
bash tests/e2e_loopback_v2.sh
```

The 22 scenarios cover pairing, version/guide/script forwarding, 65-second idle
keepalive, file return, blocked commands, timeout/result queries, Mac CLI state sync,
pause/resume, reconnection, re-pairing and termination of active tasks.
The service readiness wait avoids a first-start certificate-generation timing race.

## Native render inspection

```bash
swiftc -Onone -parse-as-library -D PREVIEW \
  -module-cache-path /tmp/ego-bridge-swift-cache \
  -target "$(uname -m)-apple-macosx13.0" -o /tmp/ego-bridge-preview \
  mac/EgoBridgeApp/EgoBridgeApp.swift tests/preview_panel.swift
/tmp/ego-bridge-preview "$PWD/docs/images"
```

Fixtures use sample hosts/tasks only. Inspect light/dark, connected/reconnecting,
first pairing, editing, errors, long hostnames and Agent activity. Offscreen renders
do not prove actual mouse hover, keyboard focus or popover edge behaviour; verify
those interactively before claiming a native interaction pass.

## Manual two-machine smoke test

1. Pair once and verify Mac `ego-bridge --json status` and Linux `ego-browser bridge status`.
2. Restart the App; pairing and the persisted switch should survive.
3. Pause; Linux commands should fail without executing. Resume and verify availability.
4. Restart the Linux service; the Mac should reconnect without a new pairing code.
5. Create one Agent TaskSpace, visit example.com, print its title and return a screenshot
   from the session directory. Finish that TaskSpace after checking the result.
6. Hand control to the user; an Agent must stop and must not automatically reclaim it.
7. Check editing, hover/focus, unpair confirmation, activity refresh and App exit.

Do not replay a timed-out or disconnected browser action automatically. Query its
request ID and treat an unknown result as unknown.

## Extra headers probe

`tests/explore_extra_headers.py --space SPACE_ID` runs against a local HTTP echo
server in an Agent-owned validation space. Full procedure and observed per-page,
per-invocation limits are in [EXTRA_HEADERS.md](EXTRA_HEADERS.md).

## Release baseline

On 2026-11-09, CLI 0.6.2.9 passed 22/22 local E2E and 12/12 core/activity tests.
A two-machine run verified pairing, remote CLI health, screenshot/file return and
reconnection. Real activity observation and post-launch App signature checks passed.
The latest icon and quit-hover styling were inspected by native rendering; their
mouse interaction was not separately automated.
