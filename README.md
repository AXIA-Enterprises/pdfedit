# PDFEdit

A no-friction desktop PDF editor for macOS, Linux, and Windows. Open a PDF, edit text with Google Fonts and common system fonts, add signatures and watermarks, annotate, rearrange pages, and save. Single Python file, single zip per platform, no cloud.

[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)
[![Build status](https://github.com/AXIA-Enterprises/pdfedit/actions/workflows/build.yml/badge.svg)](https://github.com/AXIA-Enterprises/pdfedit/actions/workflows/build.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)

<!-- TODO: drop a real screenshot at docs/screenshots/main.png -->

![PDFEdit](docs/screenshots/main.png)

## Why this exists

Mainstream PDF editors are heavy, paywalled, or cloud-tied. PDFEdit is a single Python file (`pdfedit.py`) with PyQt6 + PyMuPDF doing the work, packaged as a self-contained PyInstaller bundle on each platform. No accounts, no telemetry, no subscriptions.

## Features

- Open and view multi-page PDFs
- Add editable text boxes with Google Fonts and common system fonts (Arial, Times New Roman, Calibri, Verdana, Georgia, Tahoma, Trebuchet MS, Courier New, Comic Sans MS, Impact, Arial Black — when installed)
- Erase text by covering it with a rectangle
- Signature dialog: typed (cursive) or drawn
- Watermarks: text, font, size, opacity, rotation, color, page range
- Highlight, underline, strikeout, sticky-note annotations
- Page tools: rotate, insert blank, delete, extract a range
- Find / replace
- Undo / redo
- Atomic save (write-temp-then-rename, no half-written PDFs on a crash)

## Install — prebuilt binary

Download the latest release for your platform from the [Releases page](https://github.com/AXIA-Enterprises/pdfedit/releases):

- `PDFEdit-macos.zip` — unzip and drag to `/Applications`
- `PDFEdit-linux.zip` — unzip and run `./PDFEdit/PDFEdit`
- `PDFEdit-windows.zip` — unzip and run `PDFEdit\PDFEdit.exe`

**macOS Gatekeeper note.** The macOS build is unsigned. On first launch, macOS will refuse to open it. Either right-click the app and choose Open, or run:

```sh
xattr -d com.apple.quarantine /Applications/PDFEdit.app
```

## Install — from source

PDFEdit needs Python 3.11 or newer.

```sh
git clone https://github.com/AXIA-Enterprises/pdfedit.git
cd pdfedit
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python pdfedit.py
```

## Build a standalone bundle

The build scripts produce a single zip per platform.

```sh
./build.sh        # macOS or Linux
build.bat         # Windows (Command Prompt or PowerShell)
```

Output: `PDFEdit-macos.zip`, `PDFEdit-linux.zip`, `PDFEdit-windows.zip`.

## Run the tests

```sh
pip install pytest pytest-qt
pytest tests/
```

The suite uses pytest-qt and runs headlessly via the offscreen Qt platform (set automatically by `tests/conftest.py`). On a headless machine you can also set it explicitly:

```sh
QT_QPA_PLATFORM=offscreen pytest tests/
```

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `PDFEDIT_BUNDLE_ID` | `io.github.axia-enterprises.pdfedit` | macOS app bundle identifier. Used by `build.sh` and the GitHub Actions release build. Override at build time. |
| `QT_QPA_PLATFORM` | unset | Set to `offscreen` to run the GUI / tests without a display. |

## Network behavior

PDFEdit fetches Google Fonts on demand the first time you select a non-builtin family in the font picker. It downloads from `fonts.googleapis.com` (CSS) and `fonts.gstatic.com` (the TTF), then caches the result under `~/.pdfedit/fonts/`. Both hostnames are validated and the download is capped at 10 MB. There is no other network use — no telemetry, no analytics, no cloud sync.

## Architecture

PDFEdit is a single file by design.

- `pdfedit.py` (~2800 lines) — `_PDFApp` (QApplication subclass, handles macOS FileOpen events), `MainWindow`, `PDFView` (QGraphicsView for the page canvas), `TextBoxItem` (editable QGraphicsTextItem that bakes back into the PDF on save), `SignatureDialog`, `WatermarkDialog`, font helpers, PyMuPDF baking
- `make_icon.py` — regenerates `PDFEdit.icns` from `icon_master.png` (macOS only)
- `tests/test_smoke.py` — pytest-qt smoke tests for the core flows
- `build.sh` / `build.bat` — PyInstaller wrappers per platform
- `.github/workflows/build.yml` — CI: tests on every push and pull request, multi-platform build on `v*` tags

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for dev setup, test instructions, commit conventions, and the inbound-license clause. The [code of conduct](CODE_OF_CONDUCT.md) applies to participation in this project.

## Security

For security issues, please use the private vulnerability reporting flow described in [SECURITY.md](SECURITY.md). Do not file public issues for security reports.

## License

PDFEdit is released under [AGPL-3.0](LICENSE). PDFEdit links [PyMuPDF](https://pymupdf.readthedocs.io/), which is itself AGPL-3.0; under the AGPL-3.0 copyleft, PDFEdit must be released on the same terms. If you distribute PDFEdit, or a modified version of it, you must make the corresponding source available to recipients on the same terms.

## Acknowledgments

- [PyMuPDF](https://pymupdf.readthedocs.io/) — the PDF rendering and writing engine
- [PyQt6](https://www.riverbankcomputing.com/software/pyqt/) — the GUI toolkit
- [Google Fonts](https://fonts.google.com/) — the on-demand font catalog
