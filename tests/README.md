# pdfedit smoke tests

From a fresh clone of the repo:

```sh
python3 -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt pytest pytest-qt
python -m pytest tests/ -v
```

These tests use `pytest-qt` and run headlessly via `QT_QPA_PLATFORM=offscreen`
(set automatically in `conftest.py`), so no display is required.
