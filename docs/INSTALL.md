# Installation

## Requirements

Mac: macOS 13+, Python 3.9+ and the official ego-lite browser with onboarding completed.
The browser is a separate vendor dependency; this repository does not redistribute it.
Linux: Python 3.7+, OpenSSL and Bash. The standalone download path also needs curl.

## Recommended: one command

Run on either platform:

```bash
curl -fsSL https://raw.githubusercontent.com/gwen0x4c3/ego-bridge/main/install.sh | bash
```

On Mac, this installs the prebuilt App/CLI, opens the App and offers an SSH setup wizard.
Its input comes from `/dev/tty`, so arrow keys and password prompts work with `curl | bash`.
On Linux, it installs the CLI/service directly. No Git, Node.js, Swift or Xcode is required.

## Mac setup wizard

```bash
ego-bridge setup
```

1. Choose whether to install Linux remotely.
2. Pick an explicit alias from `~/.ssh/config` and its Include files with ↑/↓ and Enter,
   or enter `user@host` manually. Esc cancels.
3. Confirm the SSH port, Linux address reachable from the Mac, and bridge port.
4. The Mac verifies/downloads Release files, transfers them over SSH, and runs the Linux
   installer offline. Linux does not need outbound GitHub access.
5. Choose whether to connect. A one-time code is generated over SSH and sent to the Mac
   CLI through stdin; both endpoints are checked before success is reported.

SSH uses your existing configuration, keys/agent, known-host checks and normal password
prompts. The wizard does not disable host-key checking or change your SSH configuration.
A jump-host alias may still require a separately reachable bridge address; the bridge
connection itself is direct TLS, not an automatically created SSH tunnel.

For an already installed Linux service:

```bash
ego-bridge setup --host YOUR_SSH_ALIAS --skip-remote-install
```

For authorized noninteractive Agent setup, name the destination explicitly:

```bash
ego-bridge setup --yes --host YOUR_SSH_ALIAS --bridge-host YOUR_LINUX_ADDRESS
```

## DMG: Mac App only

Download the `.dmg` for **arm64** (Apple Silicon) or **x86_64** (Intel) from
[Releases](https://github.com/gwen0x4c3/ego-bridge/releases/latest). Open it, drag
`EgoBridge.app` to `Applications`, then launch it from Applications and eject the image.
The Python and official ego-lite requirements above still apply. For the CLI and
guided Linux setup, use the one-command installer or Homebrew instead.

DMGs are built and verified automatically for each release. They contain the same
ad-hoc signed App as the ZIP; they are not notarized. No Apple Developer subscription
is needed to build them. First launch may require approval in macOS System Settings.

## Homebrew

Mac App + control CLI + Python dependency:

```bash
brew tap gwen0x4c3/tap
# Homebrew 7 requires one-time trust for a custom package:
brew trust --cask gwen0x4c3/tap/ego-bridge
brew install --cask gwen0x4c3/tap/ego-bridge
ego-bridge setup
```

Linux CLI + service + Python/OpenSSL dependencies:

```bash
brew tap gwen0x4c3/tap
brew trust --formula gwen0x4c3/tap/ego-bridge-agent
brew install gwen0x4c3/tap/ego-bridge-agent
brew services start gwen0x4c3/tap/ego-bridge-agent
```

Older Homebrew versions without `brew trust` can omit that command. Choose Homebrew
services or the standalone installer, not both. The [tap](https://github.com/gwen0x4c3/homebrew-tap)
tracks Releases every six hours and supports a manual Sync workflow.

## Manual pairing

Linux: `ego-browser pair`. Enter that five-minute code in the Mac popup, or use
`ego-bridge connect --host HOST --port 3325 --code-stdin`. Codes are single-use.
Verify Mac `ego-bridge --json status` and Linux `ego-browser bridge status`.

## Advanced installer options

```bash
curl -fsSL https://raw.githubusercontent.com/gwen0x4c3/ego-bridge/main/install.sh \
  | bash -s -- --version v2.4.1 --no-start --non-interactive
```

- `--version vX.Y.Z`: pin the Release.
- `--prefix /absolute/path`: choose the CLI/runtime prefix (default `~/.local`).
- `--no-start`: install without opening the App, starting services, or showing the wizard.
- `--non-interactive`: skip the Mac SSH wizard; normal installation/startup still happens.
- `--port N`: Linux bridge port; saves the matching shim configuration.
- `EGO_BRIDGE_APP_DIR`: Mac App directory (default `~/Applications`).
- `--from-dir DIR`: install from a local Release archive and SHA256SUMS; used by SSH setup.

Linux uses systemd user services when available, otherwise a background service with
logs/PID under `~/.local/share/ego-bridge/run`. No root privileges are required.

Quit the old Mac App before upgrading. Existing pairing is preserved. When migrating
from source, remove or adjust an old CLI link that shadows Homebrew's command.
Source builds are for development: see [CONTRIBUTING.md](../CONTRIBUTING.md).
