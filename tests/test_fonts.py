"""Tests for the font-resolution layer added in Phase 2.

Covers:
- `installed_system_fonts()` lazy cache
- `find_system_font()` lookup + negative-result cache
- `fetch_google_font()` host pinning, size cap, magic-byte rejection,
  and the happy path with a stub `urlopen`.
- `MainWindow._resolve_pdf_font` resolution order (base14 → system alias →
  embedded → Google → helv fallback).
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

from conftest import install_doc, make_blank_doc

import pdfedit


# ---------------------------------------------------------------------------
# installed_system_fonts()
# ---------------------------------------------------------------------------
def test_installed_system_fonts_returns_subset_of_common(qapp):
    families = pdfedit.installed_system_fonts()
    assert isinstance(families, list)
    # Every entry must come from the curated COMMON_SYSTEM_FONTS list.
    common = set(pdfedit.COMMON_SYSTEM_FONTS)
    for f in families:
        assert f in common, f"{f!r} returned but not in COMMON_SYSTEM_FONTS"


def test_installed_system_fonts_cached(qapp):
    a = pdfedit.installed_system_fonts()
    b = pdfedit.installed_system_fonts()
    # Lazy cache: the second call must hand back the exact same list object.
    assert a is b, "installed_system_fonts() should cache its result"


# ---------------------------------------------------------------------------
# find_system_font()
# ---------------------------------------------------------------------------
@pytest.mark.skipif(sys.platform != "darwin",
                    reason="Arial only ships in /System/Library/Fonts on macOS")
def test_find_system_font_known_macos_arial():
    p = pdfedit.find_system_font("Arial")
    assert p is not None
    assert p.exists()
    assert p.suffix.lower() in (".ttf", ".ttc")


def test_find_system_font_unknown_returns_none():
    # A family that demonstrably is not on any test host.
    assert pdfedit.find_system_font("ThisDefinitelyDoesNotExist_xyz_999") is None


def test_find_system_font_caches_negative_results(monkeypatch):
    # Use a unique family to keep the test independent of cache state.
    fam = "ZZZ_NoSuchFamily_uniquetoken_42"
    # First call: should populate the cache with None.
    assert pdfedit.find_system_font(fam) is None
    assert fam in pdfedit._system_font_cache
    assert pdfedit._system_font_cache[fam] is None

    # Make rglob blow up if it's called again — the cache must short-circuit.
    sentinel = {"called": False}
    real_rglob = Path.rglob

    def boom(self, *a, **kw):
        sentinel["called"] = True
        return real_rglob(self, *a, **kw)

    monkeypatch.setattr(Path, "rglob", boom)
    assert pdfedit.find_system_font(fam) is None
    assert sentinel["called"] is False, \
        "second call must hit the negative-result cache, not rglob"


# ---------------------------------------------------------------------------
# fetch_google_font() — host pinning, size cap, magic-byte rejection
# ---------------------------------------------------------------------------
class _FakeResponse:
    """Stand-in for the file-like urlopen() result."""

    def __init__(self, body: bytes):
        self._buf = io.BytesIO(body)

    def read(self, *args):
        return self._buf.read(*args)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _isolate_font_cache(monkeypatch, tmp_path):
    """Redirect FONT_CACHE to tmp_path so the test can never touch ~/.pdfedit."""
    monkeypatch.setattr(pdfedit, "FONT_CACHE", tmp_path)


def test_fetch_google_font_builds_googleapis_url(monkeypatch, tmp_path):
    """Defense-in-depth: assert the CSS request goes to fonts.googleapis.com."""
    _isolate_font_cache(monkeypatch, tmp_path)
    captured: list[str] = []

    def fake_urlopen(req, timeout=None):
        # First call is a Request (CSS), second is a plain URL string.
        url = req.full_url if hasattr(req, "full_url") else req
        captured.append(url)
        # Return CSS that points at gstatic but with bad magic — short-circuits.
        css = b'src: url(https://fonts.gstatic.com/x.ttf)'
        if url.startswith("https://fonts.googleapis.com"):
            return _FakeResponse(css)
        return _FakeResponse(b"NOT-A-FONT")

    monkeypatch.setattr(pdfedit, "urlopen", fake_urlopen)
    pdfedit.fetch_google_font("Roboto")
    assert captured, "urlopen never called"
    from urllib.parse import urlparse
    assert urlparse(captured[0]).hostname == "fonts.googleapis.com"


def test_fetch_google_font_rejects_non_gstatic_ttf_host(monkeypatch, tmp_path):
    _isolate_font_cache(monkeypatch, tmp_path)
    calls: list[str] = []

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        calls.append(url)
        if url.startswith("https://fonts.googleapis.com"):
            return _FakeResponse(b"src: url(https://evil.example.com/font.ttf)")
        # If we ever reach here, the test has failed — the host check should
        # have refused before the second urlopen.
        raise AssertionError("evil host was contacted")

    monkeypatch.setattr(pdfedit, "urlopen", fake_urlopen)
    out = pdfedit.fetch_google_font("Roboto")
    assert out is None
    # Exactly one HTTP call (CSS), nothing else.
    assert len(calls) == 1
    assert "evil.example.com" not in "".join(calls)


def test_fetch_google_font_rejects_oversize_ttf(monkeypatch, tmp_path):
    _isolate_font_cache(monkeypatch, tmp_path)
    # Synthesize an oversized payload (cap + 100 bytes) starting with valid
    # magic so the magic-check would otherwise pass.
    body = b"\x00\x01\x00\x00" + b"\x00" * (pdfedit._MAX_FONT_BYTES + 100)

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        if url.startswith("https://fonts.googleapis.com"):
            return _FakeResponse(b"src: url(https://fonts.gstatic.com/x.ttf)")
        return _FakeResponse(body)

    monkeypatch.setattr(pdfedit, "urlopen", fake_urlopen)
    out = pdfedit.fetch_google_font("Roboto")
    assert out is None
    # Cache file should not be written.
    cached = tmp_path / "Roboto.ttf"
    assert not cached.exists()


def test_fetch_google_font_accepts_valid_response(monkeypatch, tmp_path):
    _isolate_font_cache(monkeypatch, tmp_path)
    valid_body = b"\x00\x01\x00\x00" + b"\x00" * 200

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        if url.startswith("https://fonts.googleapis.com"):
            return _FakeResponse(b"src: url(https://fonts.gstatic.com/x.ttf)")
        return _FakeResponse(valid_body)

    monkeypatch.setattr(pdfedit, "urlopen", fake_urlopen)
    out = pdfedit.fetch_google_font("FakeTestFamily_p3")
    assert out is not None
    assert out.exists()
    # Cleanup — we monkey-patched FONT_CACHE so this lives in tmp_path,
    # cleaned automatically.


# ---------------------------------------------------------------------------
# MainWindow._resolve_pdf_font
# ---------------------------------------------------------------------------
def test_resolve_pdf_font_base14_for_helvetica(main_window):
    install_doc(main_window, make_blank_doc())
    page = main_window.view.doc[0]
    assert main_window._resolve_pdf_font("Helvetica", page) == "helv"


def test_resolve_pdf_font_system_alias_for_arial(main_window):
    install_doc(main_window, make_blank_doc())
    page = main_window.view.doc[0]
    # Arial collapses to base14 helv via SYSTEM_FONT_BASE14_ALIAS — no embed.
    assert main_window._resolve_pdf_font("Arial", page) == "helv"


def test_resolve_pdf_font_falls_back_to_helv_when_unknown(main_window, monkeypatch):
    install_doc(main_window, make_blank_doc())
    page = main_window.view.doc[0]
    # Force both the local-system and Google paths to fail.
    monkeypatch.setattr(pdfedit, "find_system_font", lambda f: None)
    monkeypatch.setattr(pdfedit, "fetch_google_font", lambda f: None)
    assert main_window._resolve_pdf_font("UnknownFamily_p3_xyz", page) == "helv"
