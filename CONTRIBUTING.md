# Contributing to PDFEdit

Thanks for your interest in contributing! PDFEdit is a small, focused, single-file Python desktop PDF editor, and we welcome bug reports, feature requests, and pull requests.

## Quick start

PDFEdit targets Python 3.11 or newer. From a fresh clone:

```sh
git clone https://github.com/AXIA-Enterprises/pdfedit.git
cd pdfedit
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt pytest pytest-qt
```

## Running the app from source

```sh
python pdfedit.py
```

## Running the tests

The test suite uses `pytest-qt` and runs headlessly (offscreen Qt platform). After installing the dev dependencies above:

```sh
pytest tests/
```

On a headless machine (CI, remote SSH, container), set the offscreen Qt platform first:

```sh
QT_QPA_PLATFORM=offscreen pytest tests/
```

## Building a standalone bundle

The project ships as a self-contained PyInstaller bundle on each platform.

- macOS / Linux: `./build.sh`
- Windows: `build.bat`

The build outputs a single zip per platform (`PDFEdit-macos.zip`, `PDFEdit-linux.zip`, `PDFEdit-windows.zip`).

## Code style

- The application is one file (`pdfedit.py`) by design. Please keep it that way unless you have a strong reason to split it. Reviewers will push back on speculative refactors.
- Keep changes minimal and targeted. A pull request that fixes one bug or adds one feature is much easier to land than one that also rewrites surrounding code.
- We use `ruff` for lint and formatting (config lives in `pyproject.toml`). If you have `ruff` installed, `ruff check .` and `ruff format .` will keep things tidy.

## Commit and PR conventions

- Use a clear, present-tense subject line, e.g. `Fix find/replace cycling past last match`.
- Keep the body short and focused on *why*, not *what*. The diff already shows the *what*.
- Link any related issue in the PR description (`Closes #123`).
- Fill in the PR template, including the testing checklist.

## License of contributions

By submitting a pull request, you agree that your contribution will be licensed under the **AGPL-3.0** license, the same terms as the rest of the project. PDFEdit links [PyMuPDF](https://pymupdf.readthedocs.io/), which is AGPL-3.0; under the AGPL-3.0 copyleft, PDFEdit cannot be relicensed. There is no contributor license agreement to sign — submitting a PR is the agreement.

## Reporting security issues

Please do not file public issues for security vulnerabilities. See [SECURITY.md](SECURITY.md) for the private disclosure process.

## Code of conduct

Participation in this project is governed by the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md).
