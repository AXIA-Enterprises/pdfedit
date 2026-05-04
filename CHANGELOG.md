# Changelog

All notable changes to PDFEdit will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Adobe-style fillable form creator.** Nine field types — text, multi-line, checkbox, radio button, dropdown, listbox, signature, date, push button — placed via drag from a new `&Forms` menu. Each field opens a tabbed Properties dialog (General / Appearance / Options / Actions) for name, tooltip, required/read-only/four-state visibility, colors, font, alignment, default value, choices editor, format scripts (Number / Date / Zip / Phone / SSN), Calculate-as (Sum / Product / Average / Min / Max), and per-action JavaScript hooks.
- **Real radio groups.** Radios sharing a name are linked via `/Parent`/`/Kids` xref edits so they're mutually exclusive in Adobe Reader; deletion prunes the parent's kids and removes empty parents from `/AcroForm/Fields`.
- **Tab Order dialog** rewriting per-page `/Annots`. **Reset Form** and **Flatten Form** entries under `&Forms`.
- **Form Builder side panel** (right-side dock) listing every field grouped by page with click-to-focus, drag-to-reorder for tab order, inline rename, delete, right-click Properties, and accent-aware highlight ring. Visibility persists via `QSettings`.
- **Page Thumbnails side panel** (left-side dock) with rendered thumbnails, click-to-jump, drag-to-reorder pages via `doc.select(new_order)` with overlay index remapping, and a right-click context menu (rotate L/R, insert blank above/below, delete, extract). Visibility persists via `QSettings`.
- **Multi-document tabs.** `QTabWidget` central widget where each `DocumentTab` owns its own view, file path, dirty flag, undo/redo, and search state. `Cmd+T` opens a new tab; `Cmd+W` closes; tab labels show filename with a `•` dirty marker; tabs are movable; app close walks every dirty tab in order.
- **Light and dark themes with blue accent.** `LIGHT_PALETTE` / `DARK_PALETTE` drive a single ~300-line QSS template (60/30/10 rule, neutral greys + `#2563EB` accent). System detection via `QGuiApplication.styleHints().colorScheme()` plus a runtime listener so macOS Dark Mode toggles propagate live when the persisted choice is "system".
- **Preferences dialog** (`Cmd+,`). Live-preview controls for theme, UI font size, and accent color, plus toggles for auto-opening field Properties on create, default Form Builder visibility, and a configurable field-name pattern. Reset-appearance and reset-all flows. All preferences persist via `QSettings`.
- **Split PDF dialog** (`File → Split…`). Three modes: explicit page ranges (rejects overlaps), every-N pages, or one file per top-level bookmark. Filename templates support `{stem}`, `{n}`, `{first}`, `{last}`, `{title}` (sanitized for filesystem). Reveals output folder on completion.
- **Compress PDF dialog** (`File → Compress…`). Low/Medium/High image-quality presets driving JPEG re-encode quality (40/65/85) and downsample DPI (72/150/none). Estimate label updates per preset by walking page image lists. Output defaults to `<stem>_compressed.pdf` with a "Replace original" toggle and atomic write.
- **Edit Existing Text** tool. Click on existing text to open an inline popup prefilled with the line; Enter commits with a redact-and-rewrite using a best-match Base14 font, original size, span color, and bold/italic flags. Documents real PDF limits (no vertical text, no justification rebuild).
- **Recognize Text (OCR)** via `pytesseract`. Per-page Tesseract pipeline rendered at ~216 DPI with invisible text glyphs (render_mode=3) aligned to each word's bbox; ten languages plus auto-detect; "skip pages with selectable text" option; graceful per-platform install instructions when Tesseract isn't present.
- **Five drawing tools** (pen, rectangle, ellipse, line, arrow) under `&Insert` with shortcuts P/G/O/L/W. Each produces a movable + resizable overlay that bakes via `to_pdf` on save. Pen stores normalized polyline points so resize scales proportionally; arrow draws a shaft plus a 3-segment head. Right-click → Properties for stroke color, fill color, and width, with session-sticky defaults.
- **Crop Pages** tool (`C`) with a confirmation dialog showing a preview, scope radios (current / all / range), and per-page mediabox clamping. Successive crops compose correctly via cropbox-relative drag translation. `Pages → Reset Crop` restores the original mediabox.
- **Protect PDF** (`Tools → Protect…`). AES-256 encryption with required owner password, optional user password, and seven granular permissions (print, modify, copy, annotate, form fill, assemble, hi-res print) mapped to `fitz.PDF_PERM_*` constants via `getattr` fallback. Output defaults to `<stem>_protected.pdf` with a confirm-guarded "Replace original" toggle.
- **Unlock PDF** (`File → Unlock…`). Saves an unencrypted copy of an authenticated encrypted document. Action is disabled with an explanatory tooltip when the active tab's doc was not opened encrypted.
- **Bates Numbering** (`Tools → Bates Numbering…`). Prefix, suffix, start number, zero-pad width, six corner positions, font size, color picker, and All-pages or page-range scope, with a live "Sample: …" preview.
- **Signature improvements.** Drawing tab (already present) is now polished alongside typed; placed signatures get 2D drag resize via `_SignatureResizeHandle`, dashed selection chrome, and snapshot-on-resize undo. Typed signatures produce path-based `SignatureItem` overlays instead of silently becoming text boxes. Cursive font fetches are pre-warmed on a `QThread` at startup with on-demand `QRunnable` fallback and visible loading/failure indicators.
- **Image overlays.** Inserted images become movable and resizable `ImageOverlayItem` overlays that bake at save time, mirroring the text/signature lifecycle. Drag rect is honored at insert time (was previously ignored).
- **AddTextDialog wired** with a live font/size/color preview when the Add Text tool is invoked (was previously dead code).
- **Navigation polish.** Editable page-jump `QSpinBox` replaces the static page label; prev/next disable at first/last; `Cmd+wheel` zoom anchors to the cursor; cursive fonts only refetch when family changes; `Find Previous` (`Ctrl+Shift+G`) and a Match Case toggle.
- **Watermark preview** in `WatermarkDialog` (live styling preview with color + opacity).
- **PageNumbersDialog** with position, format presets, font size, start number, and skip-first toggle (replaces hardcoded "Page i of N").
- **Edit → Format submenu** exposing Bold/Italic/Underline/Strikeout/Color/Size+/Size− alongside the toolbar icons.
- `Pillow>=10.0` and `pytesseract>=0.3.10` added to `requirements.txt`.

### Changed

- Underline and strikeout annotations now save with the same blue used by the rubber-band preview, eliminating the previous mismatch between drag preview and saved color.
- `Insert Blank Page` now defaults to US Letter (612 × 792) instead of inheriting the active page's dimensions.
- `_save_clone_atomic` returns `False` and shows a critical alert when every overlay failed to bake, preventing the document from being marked clean.
- Recent-files dedup is now case-insensitive on macOS (handles `A.pdf` vs `a.pdf` collisions).
- Drag-dropping multiple PDFs offers `Open each in its own tab` / `Merge all` / `Cancel` instead of silently dropping all but the first file.
- Sticky note with an empty body shows a status message and skips creation instead of silently aborting.
- Font-size slider only rebuilds the global QSS on release (`sliderReleased` / `editingFinished`) instead of every tick.
- The Select tool no longer appears in the `&Insert` menu (it's not an insert command).
- `parse_page_range` now returns `(pages, warnings)` so callers can surface `1-` / `-3` / out-of-range / zero entries to the user.
- `extract_pages_dialog` now bakes overlays into a clone before extracting, so unbaked edits aren't silently lost in the extracted output.

### Fixed

- **Signature dialog font preview** no longer fails to update when the font is changed. Synchronous Google-font fetches were blocking the GUI thread and silently falling back to the system default on network failure; fetches now run on a `QThread` at startup with an on-demand `QRunnable` fallback and a visible loading/failure indicator.
- **Inline rename in the Form Builder panel** no longer corrupts radio groups or strips a leading icon-prefix character from names like "Total" or "Title". Group renames write `/T` on the parent xref; the icon strip is now scoped to the widget's own icon character.
- **Cross-page drag-reorder** in the Form Builder panel no longer leaves a duplicated widget xref on the source page's `/Annots`.
- **Deleting a grouped radio** now prunes the deleted xref from the parent's `/Kids` array and removes the parent from `/AcroForm/Fields` if `/Kids` becomes empty.
- **Field Properties dialog round-trip:** alignment now persists via `/Q`; choice defaults preselect on reopen; the Hidden checkbox became a four-state combo (Visible / Hidden / Visible-but-no-print / Hidden-but-printable); a multi-line toggle was added; user edits to the Actions tab are no longer overwritten by Options-tab format selection.
- **Theme/accent fidelity:** the accent swatch and color picker now read from the active palette instead of always the light palette; the highlight ring re-reads `current_accent_color()` on every show; `Reset to defaults` snaps the theme combo to System.
- **Tool shortcuts** (V/T/S/H/U/K/N/E/I/R/D/B) and format-toolbar shortcuts (Ctrl+B/I/U) no longer fire while focus is in `QPlainTextEdit`, `QTextEdit`, or the find box, so typing in the JS editor or find box no longer switches tool modes.
- **Password-protected PDFs** now prompt for and authenticate the password on open instead of rendering blank pages. `merge_pdfs` handles per-source prompts and skips files with the wrong password.
- `rotate_current_page`, `insert_blank_page`, and `delete_current_page` now refresh the Form Builder panel and the Page Thumbnails panel and clear stale search results.
- The pre-existing latent `code=4: annotation not bound` failure mode is now centralized behind the `_bound_widget` context manager covering edit, delete, rename, and reset paths.
- The `Cmd+=` zoom shortcut now also accepts `Cmd+Shift+=` for keyboards where `Cmd+Plus` requires Shift.
- The bake-failure summary identifies overlays by their actual `DISPLAY_NAME` instead of always labelling them "Signature".

### Security

- `fetch_google_font` now validates that the CSS request goes to `fonts.googleapis.com` and that the TTF URL parsed out of the CSS belongs to `fonts.gstatic.com`. Anything else is refused.
- The TTF download is capped at 10 MB. Anything larger is refused without being written to the cache.

### Removed

- Hardcoded macOS bundle identifier (replaced with the env-driven default above).
- The 56 MB precompiled `PDFEdit-macos.zip` build artifact previously checked into the repository root.
- Single-`PDFView` `MainWindow` (replaced with the per-tab `DocumentTab` model behind delegating properties so all pre-existing call sites continue to work).

### Earlier housekeeping (still in this Unreleased)

- System fonts (Arial, Times New Roman, Calibri, Verdana, Georgia, Tahoma, Trebuchet MS, Courier New, Comic Sans MS, Impact, Arial Black) added to the font picker. Only the families that are actually installed on the host appear in the dropdown.
- Root `README.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, `CHANGELOG.md`.
- GitHub issue templates (bug report, feature request) and pull request template.
- `.github/dependabot.yml` for weekly pip and GitHub Actions updates.
- Tooling-only `pyproject.toml` with pytest and ruff configuration.
- New CI test job that runs the pytest suite headlessly on Ubuntu for every push and pull request, gating the multi-platform release build.
- macOS app bundle identifier is now configurable via the `PDFEDIT_BUNDLE_ID` environment variable. Default: `io.github.axia-enterprises.pdfedit`. Affects `build.sh` and the GitHub Actions release build.
- Organization, organization-domain, and application name are now set on the `QApplication` before any `QSettings` call, and `QSettings()` is invoked with no arguments. Identifiers are centralized as module constants (`APP_ORG`, `APP_ORG_DOMAIN`, `APP_NAME`).
- `tests/README.md` rewritten to use a portable project-local virtual environment instead of the macOS-specific build venv path.
- The "known failing" docstring in `tests/test_smoke.py` was stale; the four methods it referenced (`_add_recent`, `do_underline`, `do_strikeout`, `do_sticky`) now exist on `MainWindow` and the regression tests pass.
