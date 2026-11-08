# Install and verify the complete bridge without building

Repository: https://github.com/gwen0x4c3/ego-bridge
Release: https://github.com/gwen0x4c3/ego-bridge/releases/tag/v2.3.0
Homebrew: https://github.com/gwen0x4c3/homebrew-tap

Installing this skill only copies instructions. Complete runtime installation and
pairing before declaring setup complete. Normal setup needs no source checkout,
Swift compiler, Xcode, or external Node runtime.

## Mac role

Install the official ego-lite browser separately and finish onboarding. Never replace
the Mac's official `ego-browser` command with the Linux shim.

If Homebrew is already available, prefer its cask: it installs the prebuilt App,
the `ego-bridge` control CLI and Python dependency together.

```bash
brew install --cask gwen0x4c3/tap/ego-bridge
open -a EgoBridge
ego-bridge --json status
```

Otherwise, with working Python 3.9+, install the matching prebuilt Release:

```bash
curl -fsSL https://github.com/gwen0x4c3/ego-bridge/releases/download/v2.3.0/install.sh \
  | bash -s -- --version v2.3.0
export PATH="$HOME/.local/bin:$PATH"
ego-bridge --json status
```

The installer detects the CPU, verifies SHA-256 and App identity/signature/version,
installs into `~/Applications` and `~/.local/bin`, then opens the App. It does not
build or clear credentials. The App is ad-hoc signed, not notarized; the user may
need to approve its first launch through normal macOS controls. Do not disable
Gatekeeper or silently route around an OS security prompt.

For an upgrade, quit the old App first. With `--no-start`, install without opening it.
When migrating from source, check `command -v ego-bridge`: an older `~/.local/bin`
link can shadow Homebrew's command. Do not remove unrelated commands.

## Linux role

With Python 3.7+, OpenSSL, Bash and curl:

```bash
curl -fsSL https://github.com/gwen0x4c3/ego-bridge/releases/download/v2.3.0/install.sh \
  | bash -s -- --version v2.3.0
export PATH="$HOME/.local/bin:$PATH"
ego-browser pair
```

The prebuilt runtime is kept in `~/.local/share/ego-bridge/releases`, with a current
symlink and CLI launchers in `~/.local/bin`. No checkout or local browser is required.
A systemd user session is enabled/restarted when available; otherwise start the
printed `ego-agent-service` command in the user's existing process manager.

If Linux already has Homebrew, its formula installs dependencies too:

```bash
brew install gwen0x4c3/tap/ego-bridge-agent
brew services start gwen0x4c3/tap/ego-bridge-agent
ego-browser pair
```

Use either Homebrew services or the standalone systemd unit, not both. The default
port is TCP 3325, reachable from the Mac. Installer `--prefix /absolute/path` chooses
another CLI/runtime prefix; `--no-start` installs without starting a service.

## Pair and verify

1. Linux: `ego-browser pair` creates a single-use code valid for five minutes.
2. Mac: enter the Linux host, port and code in the App, or run:

   ```bash
   ego-bridge connect --host DEVBOX_HOST --port 3325 --code-stdin
   ```

   Supply the code through stdin; keep it out of logs. If this agent has no authorized
   Mac access, give the user concrete form/command values and wait for that step.
3. Mac: `ego-bridge --json status` must report `state: connected`.
4. Linux: `ego-browser bridge status`, `ego-browser --version`, then
   `ego-browser skill` (read through the end marker).
5. For an authorized smoke test, create one TaskSpace, visit example.com, print its
   title, then `task.finish({keep: []})`. Report actual results.

Never mark setup complete merely because the skill is discoverable. If an endpoint
is unavailable, finish independent installation work and name the missing endpoint
and verification step without claiming an end-to-end connection.

## Development-only source build

Only when the user is developing/customizing the App, clone the repository and run
`bash mac/install.sh` with Xcode Command Line Tools. Linux source installs can use
`bash linux/install.sh --service`; keep that checkout because its service references it.
