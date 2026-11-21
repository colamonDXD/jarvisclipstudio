from src.caption_templates import CAPTION_TEMPLATES
from src.font_registry import FONTS_DIR, find_font_path, get_font_codepoints
from src.media import captions


def _caps_only_coverage(monkeypatch):
    """Make the bundled font named THEBOLDFONT behave like the dropped file.

    THEBOLDFONT.ttf (licence unverifiable) left the bundle on 2026-11-18 and
    the name now resolves to Montserrat-Black (font_registry.FONT_ALIASES).
    The fallback machinery these tests pin still needs a font that lacks
    accented glyphs, so the coverage lookup for that one file reports what
    the dropped font covered: ASCII only. Every assertion is unchanged.
    """
    real = captions.get_font_codepoints
    target = find_font_path("THEBOLDFONT")
    ascii_only = frozenset(range(0x20, 0x7F))

    def coverage(path):
        if target is not None and str(path) == str(target):
            return ascii_only
        return real(path)

    monkeypatch.setattr(captions, "get_font_codepoints", coverage)


def _pin_default_template_font(monkeypatch, font_family):
    # The fallback tests below exercise the swap machinery with a font
    # that genuinely lacks glyphs (THEBOLDFONT). Since the D13 adoption
    # the shipped default template names Montserrat-Black, which covers
    # Latin-1 — so these tests pin the template font explicitly instead
    # of assuming it.
    monkeypatch.setitem(CAPTION_TEMPLATES["default"], "font_family", font_family)


def _build(tmp_path, monkeypatch, hook_title, words=None):
    monkeypatch.setattr(captions, "emoji_rendering_supported", lambda: False)
    output = tmp_path / "captions.ass"
    built = captions.build_assemblyai_ass_subtitles(
        tmp_path / "missing.mp4",
        0.0,
        5.0,
        1080,
        1920,
        output,
        caption_template="default",
        hook_title=hook_title,
        caption_words=words,
        include_captions=bool(words),
    )
    assert built
    return output.read_text(encoding="utf-8")


def test_get_font_codepoints_reads_the_cmap():
    # THEBOLDFONT.ttf (the bundled font WITHOUT accents) was dropped on
    # 2026-11-18; the cmap reader is now pinned on a real bundled font that
    # lacks a known block (Bangers: no Cyrillic) next to the full fallback.
    display = get_font_codepoints(FONTS_DIR / "Bangers-Regular.ttf")
    fallback = get_font_codepoints(FONTS_DIR / f"{captions.GLYPH_FALLBACK_FONT}.ttf")

    assert ord("A") in display and ord("a") in display
    assert ord("Ж") not in display
    assert {ord(ch) for ch in "áéíóúÁÉÍÓÚñÑüÜ"} <= fallback


def test_dropped_font_name_still_resolves_to_a_licensed_font():
    assert not (FONTS_DIR / "THEBOLDFONT.ttf").exists()
    assert find_font_path("THEBOLDFONT") == FONTS_DIR / "Montserrat-Black.ttf"


def test_accented_hook_title_switches_to_a_font_that_has_the_glyphs(
    tmp_path, monkeypatch
):
    _caps_only_coverage(monkeypatch)
    _pin_default_template_font(monkeypatch, "THEBOLDFONT")
    content = _build(tmp_path, monkeypatch, "Claude ahora actúa sin que le pidas")
    fallback = captions.ass_font_name(captions.GLYPH_FALLBACK_FONT)

    assert f"Style: Hook,{fallback}," in content
    assert f"Style: Default,{fallback}," in content
    # The dropped THEBOLDFONT.ttf only drew capitals, so its fallback was
    # forced to all caps (CAPS_ONLY_FONTS keys on the font file). The name
    # now resolves to Montserrat-Black, which has lowercase: the fallback
    # keeps the text's own case.
    assert "actúa" in content


def test_accented_caption_words_also_trigger_the_fallback(tmp_path, monkeypatch):
    _caps_only_coverage(monkeypatch)
    _pin_default_template_font(monkeypatch, "THEBOLDFONT")
    words = [
        {"text": "Publicá", "start": 0.2, "end": 0.6},
        {"text": "todos", "start": 0.6, "end": 1.0},
        {"text": "días", "start": 1.0, "end": 1.4},
    ]
    content = _build(tmp_path, monkeypatch, None, words)

    assert f"\\fn{captions.ass_font_name(captions.GLYPH_FALLBACK_FONT)}" in content
    assert "días" in content  # own case: see the note in the hook-title test


def test_plain_ascii_text_keeps_the_template_font(tmp_path, monkeypatch):
    _caps_only_coverage(monkeypatch)
    _pin_default_template_font(monkeypatch, "THEBOLDFONT")
    content = _build(tmp_path, monkeypatch, "Claude acts before you ask")
    template_font = captions.ass_font_name("THEBOLDFONT")

    assert f"Style: Hook,{template_font}," in content
    assert "}acts" in content and "ACTS" not in content


def test_shipped_default_font_covers_accents_without_fallback(tmp_path, monkeypatch):
    # The shipped default template (Montserrat-Black since D13) has the
    # Latin-1 glyphs itself, so accented text renders in the template
    # font — no fallback swap, no forced uppercase.
    content = _build(tmp_path, monkeypatch, "Claude ahora actúa sin que le pidas")
    shipped = captions.ass_font_name(CAPTION_TEMPLATES["default"]["font_family"])

    assert f"Style: Hook,{shipped}," in content
    assert "actúa" in content
