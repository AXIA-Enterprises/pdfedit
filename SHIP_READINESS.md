# Ship Readiness — PDFEdit

Final checklist for pushing this repo to <https://github.com/AXIA-Enterprises/pdfedit>. All boxes verified by the audit unless explicitly marked otherwise.

## Code & content

- [x] **No personal identifiers in source.** Final grep across the tree (excluding `AUDIT/`) for `cmz3`, `Clement`, `Ziroli`, hardcoded user paths, etc. — zero hits.
- [x] **No secrets anywhere.** No API keys, no `.env*` files, no `.pem`/`.key`, no DB URIs, no high-entropy tokens. Nothing to rotate.
- [x] **License correct and intact.** `LICENSE` is the full AGPL-3.0 text (673 lines) with a 10-line custom preamble explaining the PyMuPDF copyleft chain. License posture matches the AGPL-3.0 dependency requirement.
- [N/A] **No secrets in git history.** There is no git history yet — the repo will be initialized clean. The first `git add .` will only contain the post-audit state.
- [x] **`.gitignore` complete.** Build outputs, generated icons, Python caches, `.env*` (with `!.env.example` negation), `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`, coverage files, `*.log`, OS files, IDE files, and `.claude/`.

## Public-facing documentation

- [x] **`README.md`** at root — title, badges, screenshot reference, why-this-exists, features, prebuilt + from-source install, build instructions, test instructions, env var table, network-behavior note, architecture description, contributing/security pointers, license note, acknowledgments.
- [x] **`CONTRIBUTING.md`** — quick-start, run from source, run tests, build bundle, code style, commit conventions, AGPL inbound clause, security pointer.
- [x] **`CODE_OF_CONDUCT.md`** — pointer to Contributor Covenant 2.1 with private reporting flow.
- [x] **`SECURITY.md`** — supported versions, GitHub private-vulnerability-reporting flow (no email leaked), 7-day acknowledgment SLA, in-scope / out-of-scope split.
- [x] **`CHANGELOG.md`** — Keep-a-changelog format, single `[Unreleased]` block listing every Phase 2 change.

## GitHub-specific scaffolding

- [x] **`.github/ISSUE_TEMPLATE/bug_report.yml`** — modern form-style template.
- [x] **`.github/ISSUE_TEMPLATE/feature_request.yml`** — problem / solution / alternatives.
- [x] **`.github/PULL_REQUEST_TEMPLATE.md`** — summary, linked issue, type, testing, screenshots, AGPL ack.
- [x] **`.github/dependabot.yml`** — pip + GitHub Actions, weekly.
- [x] **`.github/workflows/build.yml`** — `test` job (Ubuntu, headless Qt) on every push + PR; `build` job tri-platform on tag-push / manual dispatch; `release` gated on `[build, test]` green.

## Tooling

- [x] **`pyproject.toml`** — tooling-only (no `[project]` table). pytest config + ruff config.
- [N/A] **`.env.example`** — not needed; the code reads zero runtime env vars. (Build-time `PDFEDIT_BUNDLE_ID` is documented in `README.md` and has a default.)

## Tests

- [x] **All tests pass.** 48 / 48 in 0.92s on a freshly-rsync'd "clean machine" venv.
- [x] **Coverage built out from ~25% to ~88%.** New modules: `test_fonts.py`, `test_save.py`, `test_recent.py`, `test_undo.py`, `test_pages.py`, `test_annotations.py`, `test_find.py`. Existing `test_smoke.py` retained, stale docstrings rewritten.
- [x] **Test runner is portable.** `tests/README.md` rewritten to use a project-local `.venv`.
- [x] **Tests run headlessly.** `QT_QPA_PLATFORM=offscreen` set automatically by `tests/conftest.py`.
- [x] **No real network calls in tests.** `fetch_google_font` tests monkeypatch `urlopen`. CI will not depend on Google's CDN.

## First-time-contributor experience

- [x] **Clean-machine simulation passes.** A fresh `rsync` of the repo (excluding `AUDIT/`, caches, venvs) into `/tmp/pdfedit-clean` followed by the README's documented install steps results in a working `pytest tests/` run (48 passed) and a constructable `MainWindow`. No undocumented step required.
- [x] **`PDFEdit-macos.zip` (56 MB build artifact) is out of the repo.** Moved to `~/Documents/all_code/pdfedit-artifacts/`. `.gitignore` already excludes future builds.
- [x] **Bundle identifier rebrandable.** Default `io.github.axia-enterprises.pdfedit`; override via `PDFEDIT_BUNDLE_ID`. A fork can republish under their own identifier without editing source.

## Security posture

- [x] **`fetch_google_font` hardened.** Validates CSS host (`fonts.googleapis.com`) and TTF host (`fonts.gstatic.com`); caps download at 10 MB; fails closed on any mismatch.
- [x] **Atomic save.** Both `save_pdf` and `save_pdf_as` write through a `.tmp` + `os.replace`; tested for the failure path (no partial output left behind).
- [x] **No code execution from user content.** PDFEdit does not embed a JavaScript runtime, does not execute fonts, does not eval anything from a PDF.

## CI verification — pending first push

- [ ] **CI runs green on first push.** Cannot be verified locally. The workflow YAML parses cleanly; the `pytest tests/` command in the workflow is the same one verified above; a failure here would be a CI-environment issue.

## Pre-push manual steps (you do these)

1. **Decide what to do with `AUDIT/`** — recommended: `mv AUDIT/ ~/Documents/all_code/pdfedit-audit-archive/` to keep the audit history outside the public repo.
2. **Drop a real screenshot** at `docs/screenshots/main.png` — the README references it; the link will 404 on GitHub until it exists.
3. **`git init && git add . && git commit -m "Initial public commit"`**
4. **`git remote add origin https://github.com/AXIA-Enterprises/pdfedit.git && git push -u origin main`**
5. **Enable private vulnerability reporting** in repo Settings → Security.
6. **Tag `v0.1.0` and push the tag** to trigger the first multi-platform release build.

---

**Status:** ready to ship. All audit phases complete. Awaiting your manual `git init` + push.
