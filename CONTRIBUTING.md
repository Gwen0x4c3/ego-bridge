# Contributing

Use Python's standard library for the bridge. Keep browser behaviour in the official
SDK/CLI rather than inventing a second browser API. Keep UI changes native to macOS.

Run the checks in [docs/TESTPLAN.md](docs/TESTPLAN.md) before opening a pull request.
Browser-dependent E2E is a local check; CI does not bundle or install the proprietary browser.

## Releases

Versions use `MAJOR.MINOR.PATCH`, with an annotated Git tag `vMAJOR.MINOR.PATCH`.
The initial public release is 2.2.0; earlier prototype numbers are not published tags.

For a new release:

1. Update `VERSION`, `BRIDGE_VERSION` in `mac/ego_bridge_common.py`, `SERVICE_VERSION`
   in `linux/ego_agent_service.py`, `SHIM_VERSION` in `linux/ego-browser`, and skill metadata.
2. Update installation/version examples, `CHANGELOG.md`, and `docs/releases/vX.Y.Z.md`.
3. Run tests, build/signature verification, and any affected native or browser checks.
4. Commit on `main`; let CI pass.
5. Create an annotated tag and push it:

   ```bash
   git tag -a vX.Y.Z -m "ego bridge vX.Y.Z"
   git push origin main
   git push origin vX.Y.Z
   ```

6. The Release workflow tests/builds native Apple Silicon and Intel Apps as ZIP and DMG,
   packages the Linux runtime, checks the tag against VERSION and publishes all archives plus SHA256SUMS.
   Follow the workflow to completion and verify the downloadable assets, including `install.sh`.
7. Trigger Homebrew synchronization (or wait for its six-hour schedule):

   ```bash
   gh workflow run sync.yml -R gwen0x4c3/homebrew-tap
   ```

   The tap tests real Mac/Linux installations. Its own repository token updates the
   tap; the application repository does not store a cross-repository access token.

Local packaging:

```bash
bash scripts/package-release.sh macos  # macOS only, native architecture
bash scripts/package-release.sh linux
```

The Mac build includes this project's Python resources and MIT license, not ego-lite.
DMG packaging uses built-in `hdiutil`, with an Applications shortcut and installation
instructions. Packaging verifies the image, mounts it read-only, checks the bundled
App signature/version and Applications shortcut, then detaches it before publishing.
Public-repository standard GitHub Actions runners and this packaging require no paid service.
Keep Developer ID signing/notarization status explicit; current builds use ad-hoc signing.

## Repository hygiene

Do not commit tokens, certificates/keys, personal browser images, machine-specific
addresses, runtime logs, `dist/`, or `artifacts/`. Documentation images are generated
from sample data. Do not modify another contributor's existing pairing during tests.
