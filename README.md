# PDFEdit

**A fast, free, cross-platform PDF editor.**

[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)
[![Build status](https://github.com/AXIA-Enterprises/pdfedit/actions/workflows/build.yml/badge.svg)](https://github.com/AXIA-Enterprises/pdfedit/actions/workflows/build.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)

<!-- TODO: drop a real screenshot at docs/screenshots/main.png -->

![PDFEdit](docs/screenshots/main.png)

## What it is

PDFEdit is a desktop PDF editor for people who don't want to fight their tools. Open a PDF (or many — every document gets its own tab), mark it up, edit existing text, build real Acrobat-style forms, reorder pages, watermark, redact, OCR, save. It runs locally on macOS, Linux, and Windows. No cloud, no accounts, no subscription. The whole app is one Python file (`pdfedit.py`, PyQt6 + PyMuPDF) packaged into a self-contained zip per platform.

## Highlights

### Viewing & navigation
- Multi-document tabs — `Cmd+T` new, `Cmd+W` close, drag to reorder
- Drop multiple PDFs onto the window and choose Open-each-tab, Merge, or Cancel
- Password-protected PDFs are detected on open and prompt for the password
- Editable page-jump spinner with prev/next that disable at boundaries
- Cursor-anchored zoom (`Cmd+wheel`) and standard `Cmd+`/`Cmd-`/`Cmd+0`
- Always-visible find box with Match Case, `Ctrl+G` / `Ctrl+Shift+G` for next/previous

### Annotations & overlays
- Add Text with configurable font, size, and color (live preview in the dialog)
- Signatures: type with a cursive Google Font or draw freehand, then drag the corners to resize
- Highlight, Underline, Strikeout — preview color matches the saved color exactly
- Sticky Note and Erase (whiteout) for quick redaction
- Insert Image as a movable, resizable overlay; the image is baked into the PDF only at save time
- Drawing tools: Pen, Rectangle, Ellipse, Line, Arrow, with a right-click Properties panel for stroke, fill, and width
- Edit Existing Text — click a line and rewrite it inline; PDFEdit redacts the original glyphs and re-renders with the closest Base14 font match

### Acrobat-style forms
- Nine fillable field types: Text, Multi-line Text, Checkbox, Radio Button, Dropdown, List Box, Signature, Date, and Push Button
- Full Field Properties dialog (right-click any field) with General / Appearance / Options / Actions tabs
- Real radio groups wired through `/Parent` / `/Kids` xref linking — exactly one button selectable per group
- Calculations: Sum, Product, Average, Min, Max via embedded JavaScript
- Format scripts for Number, Date, Zip, Phone, and SSN (`AFNumber_Format`, `AFDate_Format`, `AFSpecial_Format`)
- Tab Order dialog for setting the keyboard traversal order
- Form Builder side panel with click-to-focus, drag-to-reorder, inline rename, and delete
- Reset Form and Flatten Form

### Page management
- Page Thumbnails side panel with drag-drop reorder, click-to-jump, right-click menu for rotate / insert / delete / extract
- Rotate, Insert Blank (US Letter), Delete page
- Crop Pages — current page, all pages, or a range; mediabox-clamped, with a `Pages → Reset Crop` to undo
- Watermark dialog with live preview, color, opacity, rotation, and per-range targeting
- Page Numbers dialog: position, format presets, font size, starting number, optional skip-first-page
- Bates Numbering dialog: prefix, suffix, padding, start, position, font, color
- Split PDF: by page ranges, every N pages, or by top-level bookmarks, with a templated output filename
- Compress PDF: Low / Medium / High image-quality presets (JPEG re-encode plus downsample) with before/after size estimates
- Extract Pages and Merge PDFs
- Protect PDF: AES-256, separate owner and user passwords, granular permissions
- Unlock PDF: saves an unencrypted copy
- Recognize Text / OCR via Tesseract, ten languages, optional dependency with a graceful "not installed" message

### Look & feel
- Light and dark themes (60/30/10 grey + blue accent) that follow macOS System color scheme live
- Preferences dialog (`Cmd+,`) for theme, UI font size, accent color picker, editor toggles, with Reset and Reset all
- Toolbar overflow chevron (»  button) that pops out hidden tools when the window is narrow
- Two collapsible side docks (Pages thumbnails on the left, Form Fields on the right) with toolbar toggles
- `Edit → Format` submenu for Bold / Italic / Underline / Strikeout / Color / Size+ / Size−

### Robustness
- 355 pytest tests, all green
- Atomic saves — write-temp-then-rename, with bake failures surfaced rather than swallowed
- Undo / redo via byte-snapshot stack
- Recent files (case-insensitive on macOS), persisted with `QSettings`
- Dirty-tab close confirmation; quitting the app walks every dirty tab in order

## Screenshots

<!-- Drop screenshots into docs/screenshots/ to fill these in. -->

![Main window](docs/screenshots/main.png)

![Form Builder panel](docs/screenshots/form-builder.png)

![Field properties dialog](docs/screenshots/field-properties.png)

![Page thumbnails dock](docs/screenshots/page-thumbnails.png)

![Watermark dialog](docs/screenshots/watermark.png)

![Preferences](docs/screenshots/preferences.png)

## Install

### Prebuilt binary

Grab the latest release for your platform from the [Releases page](https://github.com/AXIA-Enterprises/pdfedit/releases):

- **macOS** — download `PDFEdit-macos.zip`, unzip, drag `PDFEdit.app` to `/Applications`. The build is unsigned, so on first launch right-click → Open, or run:
  ```sh
  xattr -d com.apple.quarantine /Applications/PDFEdit.app
  ```
- **Windows** — download `PDFEdit-windows.zip`, unzip, run `PDFEdit\PDFEdit.exe`. No installer.
- **Linux** — download `PDFEdit-linux.zip`, unzip, run `./PDFEdit/PDFEdit`.

### From source

PDFEdit needs Python 3.11 or newer.

```sh
git clone https://github.com/AXIA-Enterprises/pdfedit.git
cd pdfedit
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python pdfedit.py
```

### Optional: OCR (Tesseract)

OCR uses [Tesseract](https://github.com/tesseract-ocr/tesseract). Install it once on your system; PDFEdit will pick it up. If Tesseract isn't installed, the OCR menu item shows a friendly message instead of crashing.

```sh
# macOS
brew install tesseract

# Debian / Ubuntu
sudo apt install tesseract-ocr

# Windows (Chocolatey)
choco install tesseract
```

For non-English OCR, install the matching language pack (`tesseract-ocr-fra`, `tesseract-ocr-deu`, etc.).

## Build from source

The build scripts produce a single zip per platform.

```sh
./build.sh        # macOS or Linux
build.bat         # Windows (Command Prompt or PowerShell)
```

The script drops a runnable `PDFEdit.app` (macOS) or `PDFEdit/` directory (Linux/Windows) next to the source, then zips it as `PDFEdit-macos.zip` / `PDFEdit-linux.zip` / `PDFEdit-windows.zip`.

## Keyboard shortcuts

| Action | Shortcut |
|---|---|
| New tab | `Cmd+T` |
| Close tab | `Cmd+W` |
| Open file | `Cmd+O` |
| Save | `Cmd+S` |
| Save As | `Cmd+Shift+S` |
| Find | `Cmd+F` |
| Find next / previous | `Ctrl+G` / `Ctrl+Shift+G` |
| Undo / redo | `Cmd+Z` / `Cmd+Y` |
| Zoom in / out / reset | `Cmd+`+ / `Cmd+`- / `Cmd+0` |
| Preferences | `Cmd+,` |

(On Windows and Linux, `Cmd` is `Ctrl`.)

## Tests

```sh
pip install pytest pytest-qt
pytest tests/
```

The suite is 355 tests and runs headlessly via the offscreen Qt platform (set automatically by `tests/conftest.py`). On a headless machine you can also set it explicitly:

```sh
QT_QPA_PLATFORM=offscreen pytest tests/
```

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `PDFEDIT_BUNDLE_ID` | `io.github.axia-enterprises.pdfedit` | macOS app bundle identifier. Used by `build.sh` and the GitHub Actions release build. Override at build time. |
| `QT_QPA_PLATFORM` | unset | Set to `offscreen` to run the GUI / tests without a display. |

## Network behavior

PDFEdit fetches Google Fonts on demand the first time you select a non-builtin family in the font picker, with a threaded prefetch in the background and on-demand fallback if the prefetch hasn't finished. It downloads from `fonts.googleapis.com` (CSS) and `fonts.gstatic.com` (the TTF), then caches the result under `~/.pdfedit/fonts/`. Both hostnames are validated and the download is capped at 10 MB. There is no other network use — no telemetry, no analytics, no cloud sync.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for dev setup, test instructions, commit conventions, and the inbound-license clause. The [code of conduct](CODE_OF_CONDUCT.md) applies to participation in this project.

## Security

For security issues, please use the private vulnerability reporting flow described in [SECURITY.md](SECURITY.md). Do not file public issues for security reports.

## License

PDFEdit is released under [AGPL-3.0](LICENSE). PDFEdit links [PyMuPDF](https://pymupdf.readthedocs.io/), which is itself AGPL-3.0; under the AGPL-3.0 copyleft, PDFEdit must be released on the same terms. If you distribute PDFEdit, or a modified version of it, you must make the corresponding source available to recipients on the same terms.

## Acknowledgments

- [PyMuPDF](https://pymupdf.readthedocs.io/) — the PDF rendering and writing engine
- [PyQt6](https://www.riverbankcomputing.com/software/pyqt/) — the GUI toolkit
- [Tesseract](https://github.com/tesseract-ocr/tesseract) — the OCR engine
- [Google Fonts](https://fonts.google.com/) — the on-demand font catalog
