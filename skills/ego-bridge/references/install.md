# Install and pair the complete runtime

Repository: https://github.com/gwen0x4c3/ego-bridge
User instructions: https://github.com/gwen0x4c3/ego-bridge/blob/main/docs/INSTALL.md

Installing the skill only copies instructions. Normal runtime installation needs no
source checkout, compiler, or external Node.js. Mac requires Python 3.9+ and the
separately installed official ego-lite browser; Linux requires Python 3.7+ / OpenSSL.

## Install on the current machine

```bash
curl -fsSL https://raw.githubusercontent.com/gwen0x4c3/ego-bridge/main/install.sh \
  | bash -s -- --version v2.4.0
```

The installer detects macOS/Linux and verifies Release hashes. Mac gets the App and
`ego-bridge` CLI; Linux gets `ego-browser` and `ego-agent-service`. It preserves pairing
and refuses to overwrite unrelated commands. Never install the Linux shim on Mac.

For an unattended tool invocation, use `--non-interactive`; `--no-start` also skips
opening the App / starting services. A Mac terminal user receives an arrow-key SSH
wizard using `/dev/tty`, even when the script itself is piped from curl.

## Configure Linux from Mac

```bash
ego-bridge setup
```

The user can select an explicit alias from SSH config/Include files, enter user@host,
confirm ports/address, install remotely, then choose automatic connection. The Mac
passes checked Release files through SSH and invokes the remote installer offline.
The Linux destination does not need to reach GitHub. SSH keeps normal host-key and
password/key-agent behaviour; no private keys are copied or host checks disabled.

When the task explicitly identifies and authorizes the remote destination, Agents can use:

```bash
ego-bridge setup --yes --host YOUR_SSH_ALIAS --bridge-host YOUR_LINUX_ADDRESS
```

Do not infer SSH authorization from a saved host entry. If the destination is missing,
ask for it or let the user choose in the terminal. For an existing current Linux service,
`--skip-remote-install` skips deployment and offers pairing only.

Pairing uses `ego-browser pair --json`, gives the one-time code to Mac CLI stdin, and
verifies Mac connected state plus Linux uplink. Codes are not logged or retained.
Do not mark setup complete until the actual checks pass. A failed/declined connection
leaves installation intact and can be resumed with `ego-bridge setup`.

## Homebrew alternative

Mac:

```bash
brew tap gwen0x4c3/tap
brew trust --cask gwen0x4c3/tap/ego-bridge
brew install --cask gwen0x4c3/tap/ego-bridge
ego-bridge setup
```

Linux:

```bash
brew tap gwen0x4c3/tap
brew trust --formula gwen0x4c3/tap/ego-bridge-agent
brew install gwen0x4c3/tap/ego-bridge-agent
brew services start gwen0x4c3/tap/ego-bridge-agent
```

Older Homebrew versions without `trust` can omit that command. On Homebrew 7, scope
trust to the requested package; do not turn off global checks. Use one service manager.
Mac builds are ad-hoc signed, not notarized; let the user handle any first-launch OS
approval. Quit the old App before an upgrade and check for an older CLI shadowing PATH.

## Final verification

Mac: `ego-bridge --json status`. Linux: `ego-browser bridge status --json` and
`ego-browser --version`. Read the full `ego-browser skill` guide before any browser
operation. An authorized example.com smoke test uses one TaskSpace and finishes it
with `task.finish({keep: []})`. Do not replay unknown browser actions after a disconnect.
