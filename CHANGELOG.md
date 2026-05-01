# Changelog

All notable changes to PDFEdit will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- System fonts (Arial, Times New Roman, Calibri, Verdana, Georgia, Tahoma, Trebuchet MS, Courier New, Comic Sans MS, Impact, Arial Black) added to the font picker. Only the families that are actually installed on the host appear in the dropdown.
- Root `README.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, `CHANGELOG.md`.
- GitHub issue templates (bug report, feature request) and pull request template.
- `.github/dependabot.yml` for weekly pip and GitHub Actions updates.
- Tooling-only `pyproject.toml` with pytest and ruff configuration.
- New CI test job that runs the pytest suite headlessly on Ubuntu for every push and pull request, gating the multi-platform release build.

### Changed

- macOS app bundle identifier is now configurable via the `PDFEDIT_BUNDLE_ID` environment variable. Default: `io.github.axia-enterprises.pdfedit`. Affects `build.sh` and the GitHub Actions release build.
- Organization, organization-domain, and application name are now set on the `QApplication` before any `QSettings` call, and `QSettings()` is invoked with no arguments. Identifiers are centralized as module constants (`APP_ORG`, `APP_ORG_DOMAIN`, `APP_NAME`).
- `tests/README.md` rewritten to use a portable project-local virtual environment instead of the macOS-specific build venv path.

### Security

- `fetch_google_font` now validates that the CSS request goes to `fonts.googleapis.com` and that the TTF URL parsed out of the CSS belongs to `fonts.gstatic.com`. Anything else is refused.
- The TTF download is capped at 10 MB. Anything larger is refused without being written to the cache.

### Removed

- Hardcoded macOS bundle identifier (replaced with the env-driven default above).
- The 56 MB precompiled `PDFEdit-macos.zip` build artifact previously checked into the repository root.

### Fixed

- The "known failing" docstring in `tests/test_smoke.py` was stale; the four methods it referenced (`_add_recent`, `do_underline`, `do_strikeout`, `do_sticky`) now exist on `MainWindow` and the regression tests pass. (Docstring update is tracked separately for Phase 3.)
