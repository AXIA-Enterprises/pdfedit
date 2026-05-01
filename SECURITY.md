# Security policy

## Supported versions

We accept security reports against the latest release published on the [Releases page](https://github.com/AXIA-Enterprises/pdfedit/releases) and the current `main` branch.

| Version | Supported |
|---|---|
| `main` | Yes |
| Latest release | Yes |
| Older releases | No |

## Reporting a vulnerability

Please **do not file public GitHub issues** for security vulnerabilities.

Use GitHub's private vulnerability reporting flow:

1. Open <https://github.com/AXIA-Enterprises/pdfedit/security/advisories/new> (also reachable via the **Security** tab on the repository).
2. Describe the issue, reproduction steps, affected versions, and any proof-of-concept artifacts.
3. We will receive the report privately. No information becomes public until an advisory is published with your agreement.

## What to expect

- We aim to acknowledge new reports within **7 days**.
- We will keep you informed as we investigate and prepare a fix.
- We will coordinate disclosure timing with you, and credit you in the published advisory unless you prefer otherwise.

## Scope

In scope:

- The PDFEdit application itself (`pdfedit.py`, `make_icon.py`, build scripts, packaging).
- The font-fetching code path that talks to `fonts.googleapis.com` / `fonts.gstatic.com`.
- Any code path that opens, parses, or saves PDF or image content.

Out of scope here (please report upstream):

- Vulnerabilities in [PyMuPDF](https://github.com/pymupdf/PyMuPDF/security)
- Vulnerabilities in [PyQt6](https://www.riverbankcomputing.com/) / Qt
- Vulnerabilities in PyInstaller or any other build dependency

If you find a vulnerability that is upstream but is exposed by PDFEdit's specific use of it (for example, an attacker-controlled input path that PyMuPDF mishandles in a way that PDFEdit's UI exposes), please report it to us — we may need to add a defensive layer even after the upstream fix lands.

## Hardening already in place

- The Google Fonts download path validates hostnames (must be `fonts.googleapis.com` for the CSS request and `fonts.gstatic.com` for the TTF) and caps each font download at 10 MB.
- Saved PDFs are written atomically via a temp file + `os.replace`, so a crash mid-save cannot leave a half-written PDF in place of your original.
- PDFEdit does not execute arbitrary content from PDFs, does not embed a JavaScript runtime, and does not run any code from a downloaded font.
