"""Fontsdir poison guard: libass must never receive a directory with non-fonts.

The bundled fonts folder carries README.md / SOURCES.md alongside the .ttf
files and reaches the container through a bind mount. libass tries to load
every file in a fontsdir as a font; on some builds that error is fatal to
the subtitles filter and every captioned render fails. ass_fonts_dir must
withhold such a directory (captions then resolve via fontconfig instead).
"""

from pathlib import Path

from src.media import captions


def _make_fonts_dir(tmp_path: Path, poisoned: bool) -> Path:
    fonts_dir = tmp_path / "fonts"
    fonts_dir.mkdir()
    (fonts_dir / "Anton-Regular.ttf").write_bytes(b"fake-font")
    if poisoned:
        (fonts_dir / "README.md").write_text("not a font")
        (fonts_dir / "SOURCES.md").write_text("also not a font")
    return fonts_dir


def test_poisoned_bundled_dir_is_detected(tmp_path):
    assert captions._is_poisoned_fonts_dir(_make_fonts_dir(tmp_path, True)) is True


def test_clean_bundled_dir_is_not_poisoned(tmp_path):
    assert captions._is_poisoned_fonts_dir(_make_fonts_dir(tmp_path, False)) is False


def test_missing_dir_counts_as_unusable(tmp_path):
    assert captions._is_poisoned_fonts_dir(tmp_path / "nope") is True


def test_ass_fonts_dir_withholds_poisoned_fallback(tmp_path, monkeypatch):
    fonts_dir = _make_fonts_dir(tmp_path, poisoned=True)
    monkeypatch.setattr(captions, "FONTS_DIR", fonts_dir)
    monkeypatch.setattr(captions, "find_font_path", lambda *a, **k: None)
    assert captions.ass_fonts_dir("NoSuchFont") is None


def test_ass_fonts_dir_keeps_clean_fallback(tmp_path, monkeypatch):
    fonts_dir = _make_fonts_dir(tmp_path, poisoned=False)
    monkeypatch.setattr(captions, "FONTS_DIR", fonts_dir)
    monkeypatch.setattr(captions, "find_font_path", lambda *a, **k: None)
    assert captions.ass_fonts_dir("NoSuchFont") == fonts_dir


def test_ass_fonts_dir_prefers_resolved_font_parent(tmp_path, monkeypatch):
    fonts_dir = _make_fonts_dir(tmp_path, poisoned=True)
    resolved = fonts_dir / "Anton-Regular.ttf"
    monkeypatch.setattr(captions, "FONTS_DIR", fonts_dir)
    monkeypatch.setattr(captions, "find_font_path", lambda *a, **k: resolved)
    # The resolved font's own folder is returned even when the bundled dir is
    # poisoned: user font folders contain only font files.
    assert captions.ass_fonts_dir("Anton") == fonts_dir
