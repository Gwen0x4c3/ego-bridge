# Changelog

## [2.4.0] - 2026-11-09

- Interactive one-command setup on Mac: arrow-key SSH host selection or manual user@host.
- Transfer verified Linux Release assets over SSH; remote GitHub access is not required.
- Optional automatic pairing via CLI, with one-time codes kept in stdin and both endpoints checked.
- Offline installation, configurable bridge port, and background startup when systemd is unavailable.
- English / 中文 READMEs focused on installation and everyday use.

## [2.3.0] - 2026-11-09

- No-build Release installer for Mac App/CLI and Linux CLI/service, with SHA-256 checks.
- Automatic platform/CPU selection, safe command replacement and preserved pairing.
- Homebrew cask/formula in `gwen0x4c3/homebrew-tap`, including runtime dependencies.
- Mac App discovers Homebrew's versioned Python runtime.
- README and Agent skill prefer prebuilt installation; source builds remain for development.

## [2.2.0] - 2026-11-09

First public release.

- Mac-initiated TLS bridge with one-time pairing, certificate pinning and reconnect.
- Native macOS menu-bar App and shared `ego-bridge` control CLI.
- Original browser/cursor icon, hover feedback, connection editor and Agent-space activity.
- Linux `ego-browser` shim, stdin/output forwarding and verified file return.
- Request-id result queries; disconnected or timed-out actions are not automatically replayed.
- Standard `skills/ego-bridge/` package with complete runtime setup instructions.
- macOS installer, architecture-specific release archives, checksums and GitHub Actions.
- Documented, locally verified per-page/per-invocation Extra headers support.

[2.2.0]: https://github.com/gwen0x4c3/ego-bridge/releases/tag/v2.2.0

[2.3.0]: https://github.com/gwen0x4c3/ego-bridge/releases/tag/v2.3.0

[2.4.0]: https://github.com/gwen0x4c3/ego-bridge/releases/tag/v2.4.0
