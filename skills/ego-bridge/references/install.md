# Install and verify the complete bridge

Repository: https://github.com/gwen0x4c3/ego-bridge
Release: https://github.com/gwen0x4c3/ego-bridge/releases/tag/v2.2.0

The `npx skills add` command only copies this skill. Complete the runtime steps
below on the relevant machines; do not end a setup task after installing the skill.

## Obtain source in a stable location

Run separately on each machine that needs the runtime. Reuse an existing verified
checkout rather than overwriting it. For a new checkout:

```bash
mkdir -p "$HOME/.local/share/ego-bridge"
git clone --branch v2.2.0 --depth 1 https://github.com/gwen0x4c3/ego-bridge.git \
  "$HOME/.local/share/ego-bridge/source"
cd "$HOME/.local/share/ego-bridge/source"
```

The source is independent of the installed skill directory. Keep the Linux checkout:
its installed service launcher points to `linux/ego_agent_service.py` there.

## Mac role

Prerequisites: macOS 13+, Python 3.9+, Xcode Command Line Tools for source builds,
and the official ego-lite application with onboarding completed.
The proprietary browser is a separate prerequisite; this repository does not bundle it.
If the user's official ego-browser skill is installed, use its installation workflow;
otherwise have the user install ego-lite from its official distribution and finish onboarding.
Do not use an unofficial browser download or copy a Linux shim onto the Mac.

```bash
export PATH="$HOME/.local/bin:$PATH"
command -v ego-browser
ego-browser --version
cd "$HOME/.local/share/ego-bridge/source"
bash mac/install.sh
command -v ego-bridge
ego-bridge --json status
```

`mac/install.sh` builds the App, installs it to `~/Applications/EgoBridge.app`, installs
the `ego-bridge` CLI in `~/.local/bin`, and opens the App. Before pairing, an unpaired
status is expected. Existing pairing is stored outside the App and survives upgrades.

If building is unavailable, the Release has native macOS archives for each CPU
architecture. Extract the matching App into `~/Applications`, open it, then run its
`Contents/Resources/ego-bridge` CLI directly, or link that file to `~/.local/bin/ego-bridge`.
Release Apps are ad-hoc signed, not Developer ID signed or notarized; source builds
are the recommended alternative when macOS blocks a downloaded App.

## Linux role

Prerequisites: Python 3.7+, OpenSSL, Bash; systemd user services for managed startup.
The default TCP port is 3325 and must be reachable from the Mac.

```bash
cd "$HOME/.local/share/ego-bridge/source"
bash linux/install.sh --service
export PATH="$HOME/.local/bin:$PATH"
systemctl --user status ego-bridge --no-pager
ego-browser pair
```

The installer refuses to overwrite an unrelated `ego-browser`. Do not use `--force`
unless the user deliberately wants to replace that executable. Do not install the
Mac browser on Linux. Without systemd, run `ego-agent-service` in a separately
managed terminal/process, then continue pairing.

## Pair and verify

1. Linux: `ego-browser pair` creates a single-use code valid for five minutes.
2. Mac: fill the host, port and code in the App, or run:

   ```bash
   ego-bridge connect --host DEVBOX_HOST --port 3325 --code-stdin
   ```

   Supply the fresh code through stdin. If this agent has no authorized Mac access,
   give the user the concrete Mac command/form values and wait for that step.
3. Mac: `ego-bridge --json status` must report `state: connected`.
4. Linux: `ego-browser bridge status`, `ego-browser --version`, then
   `ego-browser skill` (read through its end marker).
5. For an authorized browser smoke test, create one TaskSpace, visit example.com,
   print its title, and finish the space with `{keep: []}`. Report actual results.

Never mark setup complete merely because the skill is discoverable. If one endpoint
is unavailable, finish independent installation work and name the remaining endpoint
and verification step without claiming an end-to-end connection.
