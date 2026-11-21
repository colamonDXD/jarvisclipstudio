"""Captions helpers for the video pipeline."""

import os
import re
from typing import Any
from typing import Dict
from typing import Iterable
from ..font_registry import FONTS_DIR
from typing import List
from typing import Optional
from typing import Sequence
from ..emoji_captions import POWER_WORDS
from pathlib import Path
from typing import Tuple
from ..emoji_captions import annotate_caption_words
from ..font_registry import find_font_path
from ..font_registry import get_font_advance_metrics
from ..font_registry import get_font_codepoints
from ..font_registry import get_font_family_name
from ..caption_templates import get_template
from ..emoji_captions import normalize_token
import cv2
import numpy as np
import tempfile
from .common import (
    EMOJI_FONT_NAME,
    HOOK_TITLE_MIN_SECONDS,
    HOOK_TITLE_SIDE_MARGIN_FRAC,
    HOOK_TITLE_TOP_MARGIN_FRAC,
    SENTENCE_END_RE,
    SENTENCE_PAUSE_BOUNDARY_SECONDS,
    _EMOJI_SUPPORT_CACHE,
    logger,
)
from .face_text_guard import block_fits_face_free, guarded_centre_y
from .hinglish import ROMANIZE_PROFILE, romanize_caption_words, romanize_text
from .ffmpeg import (
    rendered_ranges_duration,
    run_ffmpeg_command,
    subtitles_filter_fragment,
)
from .transcription import (
    load_cached_transcript_data,
)
from .timeline import (
    get_words_for_keep_ranges,
    get_words_in_range,
)


def get_scaled_font_size(base_font_size: int, video_width: int) -> int:
    """Scale caption font size by output width while preserving user choices.

    Template defaults remain readable on 1080-wide vertical clips, while the
    full 12-72 UI range produces a meaningful, monotonic size change.
    """
    scaled_size = round(base_font_size * (video_width / 560.0))
    return max(26, min(132, scaled_size))


def get_subtitle_max_width(video_width: int) -> int:
    """Return max subtitle text width with horizontal safe margins."""
    horizontal_padding = max(40, int(video_width * 0.06))
    return max(200, video_width - (horizontal_padding * 2))


def caption_text_measurer(font_family: Optional[str], font_px: int):
    """Return ``measure(text) -> px`` for text rendered by libass at ``font_px``.

    Uses the font's own advance widths when the font file is available, and a
    deliberately wide per-character estimate otherwise.
    """
    font_path = (
        find_font_path(font_family, allow_all_user_fonts=True) if font_family else None
    )
    metrics = get_font_advance_metrics(font_path) if font_path else None
    if metrics is None:
        return lambda text: len(text) * font_px * 0.62
    return lambda text: metrics.text_width(text, font_px)


def split_caption_chunk(
    widths: List[float], space_width: float, max_width: float
) -> List[Tuple[int, int]]:
    """Split one caption chunk into ``(start, end)`` runs that fit ``max_width``.

    A chunk that fits stays whole. Otherwise it is cut where the wider part is
    narrowest, and each part again, so "5,000 MILLIAMP-HOUR RECHARGEABLE
    BATTERY" becomes two balanced lines instead of one that leaves the frame.
    A single word wider than the line stays alone; the caller scales it down.
    """

    def width(start: int, end: int) -> float:
        return sum(widths[start:end]) + space_width * max(0, end - start - 1)

    def split(start: int, end: int) -> List[Tuple[int, int]]:
        if end - start <= 1 or width(start, end) <= max_width:
            return [(start, end)]
        cut = min(
            range(start + 1, end),
            key=lambda index: (max(width(start, index), width(index, end)), -index),
        )
        return split(start, cut) + split(cut, end)

    return split(0, len(widths)) if widths else []


# --- Caption pacing (Quality Director) -------------------------------------
#
# Word-count chunking alone lets a chunk of slow-spoken words sit on screen
# for many seconds (a real user clip held one caption for ~10 s). Every
# display chunk is therefore also capped by DURATION: chunks are split at
# word boundaries so no chunk is displayed longer than the cap. Fast-pace
# profiles (dense cutting) get the tighter cap; everything else the default.
CAPTION_HOLD_DEFAULT_SECONDS = 3.5
CAPTION_HOLD_FAST_SECONDS = 2.5
# A profile whose pacing band starts at or above this many cuts per minute
# counts as fast-pace for caption purposes.
CAPTION_HOLD_FAST_CUTS_PER_MIN = 15.0


def caption_hold_cap_for_profile(profile: Optional[Dict[str, Any]]) -> float:
    """The caption hold cap (seconds) a profile's pacing implies.

    Fast-pace profiles (target_cuts_per_min band starting at >= 15) get
    ``CAPTION_HOLD_FAST_SECONDS``; everything else — including a missing
    or malformed profile — gets ``CAPTION_HOLD_DEFAULT_SECONDS``.
    """
    try:
        band = ((profile or {}).get("pacing") or {}).get("target_cuts_per_min")
        low = float(band[0])
    except (TypeError, ValueError, IndexError, KeyError):
        return CAPTION_HOLD_DEFAULT_SECONDS
    if low >= CAPTION_HOLD_FAST_CUTS_PER_MIN:
        return CAPTION_HOLD_FAST_SECONDS
    return CAPTION_HOLD_DEFAULT_SECONDS


def split_chunk_spans_by_duration(
    words: List[Dict[str, Any]],
    chunk_spans: List[Tuple[int, int, float]],
    cap_seconds: float,
) -> List[Tuple[int, int, float]]:
    """Split ``(start, stop, scale)`` word spans so none displays > cap.

    A span's display duration is ``words[stop-1].end - words[start].start``
    (the line is on screen from its first word until its last word ends).
    Splits land on word boundaries only; a single word longer than the
    cap stays whole — there is no word-internal place to cut. Karaoke
    highlighting is untouched: it is driven by the global word indices,
    which the split preserves.
    """
    if not cap_seconds or cap_seconds <= 0:
        return chunk_spans
    out: List[Tuple[int, int, float]] = []
    for start, stop, scale in chunk_spans:
        cursor = start
        while cursor < stop:
            start_time = float(words[cursor]["start"])
            end = cursor + 1
            while (
                end < stop
                and float(words[end]["end"]) - start_time <= cap_seconds + 1e-9
            ):
                end += 1
            out.append((cursor, end, scale))
            cursor = end
    return out


# Bottom fraction of the frame taken up by platform chrome on
# TikTok / Reels / Shorts (author line, description, progress bar and
# navigation). A caption's bottom edge must stay above this line.
PLATFORM_BOTTOM_SAFE_FRACTION = 0.18


# A pause at least this long between two words is a natural caption
# break (the pipeline's existing sentence-pause boundary).
CAPTION_PAUSE_BREAK_SECONDS = SENTENCE_PAUSE_BOUNDARY_SECONDS

# Karaoke highlighting bridges a gap to the next word only up to this
# long: it hides micro-gap flicker without leaving a word lit (and its
# line on screen as "current") through a real pause in the speech.
KARAOKE_GAP_BRIDGE_SECONDS = 0.35


def split_word_spans_natural(
    words: List[Dict[str, Any]], max_words: int
) -> List[Tuple[int, int]]:
    """Base ``(start, stop)`` word spans with natural break points.

    Word-count chunking alone straddled sentence ends mid-chunk ("Hello
    world. How are | you today?") and held one line across long pauses.
    A span therefore also ends after a word carrying sentence-final
    punctuation and before a word that follows a long pause. Width and
    duration splitting still apply on top of these spans.
    """
    if not words:
        return []
    spans: List[Tuple[int, int]] = []
    start = 0
    for index, word in enumerate(words):
        stop = index + 1
        if stop == len(words):
            spans.append((start, stop))
            break
        following = words[stop]
        gap = float(following["start"]) - float(word["end"])
        if (
            stop - start >= max_words
            or SENTENCE_END_RE.search(str(word.get("text", "")).strip())
            or gap >= CAPTION_PAUSE_BREAK_SECONDS
        ):
            spans.append((start, stop))
            start = stop
    return spans


def get_safe_vertical_position(
    video_height: int, text_height: int, position_y: float
) -> int:
    """Return subtitle y position clamped inside a top/bottom safe area."""
    min_top_padding = max(40, int(video_height * 0.05))
    min_bottom_padding = max(120, int(video_height * 0.10))

    desired_y = int(video_height * position_y - text_height // 2)
    max_y = video_height - min_bottom_padding - text_height
    # The returned value is used as the caption CENTRE (\pos, Alignment
    # 5), so also clamp it far enough up that the text's bottom edge
    # (centre + half the text height) clears the platform chrome. The
    # classic_box template asked for 0.86 and used to render its bottom
    # edge at 86% of the frame height — inside the description area.
    platform_max_y = (
        int(video_height * (1 - PLATFORM_BOTTOM_SAFE_FRACTION)) - text_height // 2
    )
    max_y = min(max_y, platform_max_y)
    return max(min_top_padding, min(desired_y, max_y))


# Spoken captions moved off burned-in source captions stay below this height,
# so they do not land on the speaker's face or under the hook title.
MIN_MOVED_CAPTION_CENTER = 0.45

# Minimum clear space kept between the hook-title block and any caption
# block. The two blocks are placed by independent guards that used to
# know nothing of each other, so a title the face guard pushed to
# mid-frame and a caption chunk pushed to the same band rendered as
# one colliding block (MrBeast exports, 2026-11-15). The title block
# now joins the caption guard's obstacle set, extended from the block
# to the nearest frame edge plus this gap on the far side; the guard's
# own face margin (12px) applies on top, so the rendered separation
# between the two blocks is at least this many pixels.
TITLE_CAPTION_MIN_GAP_PX = 24

# The per-clip caption band is decided from the clip's DOMINANT
# geometry: an obstacle source (face zones, fit strips, the title
# window) only shapes the band when it covers at least this share of
# the caption span. Below it the source stays what it always was — a
# time-scoped local obstacle for the chunks it overlaps — because a
# face that appears for three seconds of a twelve-second clip must
# not relocate the whole clip's captions (the time-scoping contract
# the guard's tests pin).
BAND_DOMINANT_COVERAGE = 0.8


def caption_center_clear_of_band(
    center_y: int, text_height: int, band: Tuple[float, float], video_height: int
) -> int:
    """Move a caption centre so its line does not overlap ``band``.

    Returns ``center_y`` unchanged when there is no overlap. Otherwise the
    caption goes just above the band, or just below it when there is no room
    above, keeping the bottom safe area clear.
    """
    gap = max(12, int(video_height * 0.015))
    half = text_height // 2
    band_top, band_bottom = int(band[0] * video_height), int(band[1] * video_height)
    if center_y + half + gap <= band_top or center_y - half - gap >= band_bottom:
        return center_y

    above = band_top - gap - half
    if above >= video_height * MIN_MOVED_CAPTION_CENTER:
        return above
    below = band_bottom + gap + half
    bottom_limit = video_height - max(120, int(video_height * 0.10)) - half
    if below <= bottom_limit:
        return below
    return max(above, int(video_height * MIN_MOVED_CAPTION_CENTER))


def emoji_rendering_supported() -> bool:
    """Whether this environment's libass renders COLOUR emojis (cached, one-shot).

    Caption emojis are only injected when this returns True, so we never burn
    ugly ".notdef" tofu boxes if the runtime's libass/FreeType can't rasterise
    the bundled colour-emoji font. The captions still get keyword emphasis either
    way. The probe burns a single emoji and checks the frame for saturated colour.
    """
    global _EMOJI_SUPPORT_CACHE
    if _EMOJI_SUPPORT_CACHE is not None:
        return _EMOJI_SUPPORT_CACHE

    result = False
    try:
        with tempfile.TemporaryDirectory(prefix="supoclip_emojiprobe_") as probe_dir:
            root = Path(probe_dir)
            ass = root / "probe.ass"
            frame = root / "probe.png"
            ass.write_text(
                "[Script Info]\n"
                "ScriptType: v4.00+\nPlayResX: 120\nPlayResY: 120\n\n"
                "[V4+ Styles]\n"
                "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, "
                "BackColour, Bold, BorderStyle, Outline, Shadow, Alignment, Encoding\n"
                f"Style: D,{EMOJI_FONT_NAME},90,&H00FFFFFF,&H00000000,&H00000000,0,1,0,0,5,1\n\n"
                "[Events]\n"
                "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
                "Effect, Text\n"
                "Dialogue: 0,0:00:00.00,0:00:01.00,D,,0,0,0,,"
                "{\\pos(60,60)}\U0001F525\n",
                encoding="utf-8",
            )
            fonts = FONTS_DIR if FONTS_DIR.exists() else None
            fragment = subtitles_filter_fragment(ass, fonts)
            command = [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "color=c=black:s=120x120:d=1",
                "-vf", fragment,
                "-frames:v", "1",
                str(frame),
            ]
            if run_ffmpeg_command(command, timeout=60).returncode == 0 and frame.exists():
                image = cv2.imread(str(frame), cv2.IMREAD_COLOR)
                if image is None:
                    raise RuntimeError("probe frame unreadable")
                arr = image.astype(np.int16)
                spread = arr.max(axis=2) - arr.min(axis=2)  # 0 for grey/tofu
                result = int((spread > 40).sum()) > 30
    except Exception as exc:
        logger.info("Emoji support probe failed (%s); disabling caption emojis", exc)
        result = False

    _EMOJI_SUPPORT_CACHE = result
    logger.info("Caption colour-emoji rendering supported: %s", result)
    return result


def ass_timestamp(seconds: float) -> str:
    # Round to whole centiseconds FIRST: formatting the seconds field
    # directly turned 59.999 into the invalid ASS time "0:00:60.00",
    # which libass can drop, taking the caption event with it.
    total_centiseconds = int(round(max(0.0, seconds) * 100))
    hours, remainder = divmod(total_centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    secs, centiseconds = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centiseconds:02d}"


def hex_to_ass_color(
    value: Optional[str], fallback: str = "#FFFFFF", include_alpha: bool = True
) -> str:
    value = (value or fallback).strip()
    if value.startswith("#"):
        value = value[1:]
    alpha = 0
    if len(value) == 8:
        css_alpha = int(value[6:8], 16)
        alpha = 255 - css_alpha
        value = value[:6]
    if len(value) != 6:
        value = fallback.lstrip("#")
        if len(value) == 8:
            css_alpha = int(value[6:8], 16)
            alpha = 255 - css_alpha
            value = value[:6]
    red, green, blue = value[0:2], value[2:4], value[4:6]
    alpha_part = f"{alpha:02X}" if include_alpha else "00"
    return f"&H{alpha_part}{blue}{green}{red}&"


def escape_ass_text(value: str) -> str:
    # Every caption word and hook title is written through here: swear words
    # are masked first ("FUCK YOU!" -> "*** YOU!", media/profanity.py).
    from .profanity import mask_profanity

    return (
        mask_profanity(str(value))
        .replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\n", " ")
        .replace("\r", " ")
        .strip()
    )


def ass_font_name(font_family: Optional[str]) -> str:
    if not font_family:
        return "Arial"
    font_path = find_font_path(font_family, allow_all_user_fonts=True)
    if font_path:
        return get_font_family_name(Path(font_path)) or Path(font_path).stem
    return font_family or "Arial"


def _is_poisoned_fonts_dir(directory: Path) -> bool:
    """Whether libass would choke scanning this directory as a fontsdir.

    libass tries to load EVERY file in a fontsdir as a font. A directory
    that also holds plain files (the bundled fonts folder carries
    README.md / SOURCES.md, and it reaches the container through a bind
    mount that bypasses the image build) makes the subtitles filter fail
    on some libass builds, killing every captioned render. Such a
    directory must never be handed to libass.
    """
    try:
        return any(
            entry.is_file() and entry.suffix.lower() not in (".ttf", ".otf")
            for entry in directory.iterdir()
        )
    except OSError:
        return True


def ass_fonts_dir(font_family: Optional[str]) -> Optional[Path]:
    if not font_family:
        return FONTS_DIR if FONTS_DIR.exists() else None
    font_path = find_font_path(font_family, allow_all_user_fonts=True)
    if font_path:
        return font_path.parent
    if FONTS_DIR.exists() and not _is_poisoned_fonts_dir(FONTS_DIR):
        return FONTS_DIR
    return None


# Bundled font used when the chosen one lacks glyphs for the clip's text. Its
# width is close to THEBOLDFONT's, so hook-title line wrapping still fits.
GLYPH_FALLBACK_FONT = "TikTokSans-Bold"
# These draw lowercase letters as capitals; their fallback is uppercased so the
# clip keeps the same all-caps look.
CAPS_ONLY_FONTS = frozenset({"THEBOLDFONT"})


def ensure_glyph_coverage(
    font_family: str, template: Dict[str, Any], texts: Iterable[str]
) -> Tuple[str, Dict[str, Any]]:
    """Swap to GLYPH_FALLBACK_FONT when ``font_family`` cannot draw ``texts``.

    libass silently renders missing glyphs from another font, so the free
    THEBOLDFONT (no accented letters) printed "ACTúA" with a lowercase ú taken
    from a system font. One covering font for the whole clip looks consistent.
    """
    font_path = find_font_path(font_family, allow_all_user_fonts=True)
    fallback_path = find_font_path(GLYPH_FALLBACK_FONT)
    # ass_fonts_dir() points libass at the original font's folder only.
    if not font_path or not fallback_path or font_path.parent != fallback_path.parent:
        return font_family, template
    covered = get_font_codepoints(font_path)
    fallback_covered = get_font_codepoints(fallback_path)
    if not covered or not fallback_covered:
        return font_family, template

    missing = {
        ch for text in texts for ch in text if ch.isalpha() and ord(ch) not in covered
    }
    if not missing or any(ord(ch) not in fallback_covered for ch in missing):
        return font_family, template

    logger.info(
        "Font %s has no glyphs for %s; rendering captions with %s",
        font_family,
        "".join(sorted(missing)),
        GLYPH_FALLBACK_FONT,
    )
    if font_path.stem in CAPS_ONLY_FONTS:
        template = {**template, "uppercase": True}
    return GLYPH_FALLBACK_FONT, template


def _balance_title_lines(words: List[str], max_chars: int) -> List[str]:
    """Split title words into one line, or two lines balanced around the middle."""
    text = " ".join(words)
    if len(text) <= max_chars or len(words) < 2:
        return [text]
    best_lines = [text]
    best_longest = len(text)
    for i in range(1, len(words)):
        first = " ".join(words[:i])
        second = " ".join(words[i:])
        longest = max(len(first), len(second))
        if longest < best_longest:
            best_longest = longest
            best_lines = [first, second]
    return best_lines


# The caption system's karaoke accent (the templates' default
# highlight): the colour a spoken word turns as it is said. A hook title
# borrows it for its power words when the active template gives the
# title no accent of its own.
HOOK_TITLE_ACCENT_FALLBACK = "#FFE000"


def build_hook_title_ass(
    hook_title: str,
    template: Dict[str, Any],
    video_width: int,
    video_height: int,
    output_duration: float,
    font_name: str,
    caption_font_px: int,
    position_y: Optional[float] = None,
    editor_style: Optional[Dict[str, Any]] = None,
    title_seconds: Optional[float] = None,
    brand_title: Optional[Dict[str, Any]] = None,
    avoid_boxes: Optional[Sequence[Tuple[float, float, float, float]]] = None,
    guard_report: Optional[Dict[str, Any]] = None,
    band_boxes: Optional[Sequence[Tuple[float, float, float, float]]] = None,
) -> Tuple[str, List[str]]:
    """Build the (style_line, dialogue_events) for a burned-in hook title.

    The title sits in the top safe area (Alignment 8), styled off the caption
    template so it reads as part of the same design system: same font, an
    outline/backing for contrast, power words and numbers in the template's
    highlight colour, and a quick fade+pop entrance.

    Duration: the title is PINNED for the whole clip by default (user
    request, 2026-11-09). ``title_seconds`` carries an explicit shorter
    duration chosen upstream (the EDL resolves ``hook.title_seconds`` to
    the full clip duration unless a profile/style explicitly opted out);
    ``None`` means no cap was chosen and the title runs to the end of the
    clip. An ``editor_style`` with explicit start/end (a user's own edit
    in the studio editor) still wins over both.
    """
    uppercase = bool(template.get("uppercase"))
    title_text = hook_title.upper() if uppercase else hook_title

    primary = hex_to_ass_color(template.get("font_color"), "#FFFFFF")
    highlight = hex_to_ass_color(
        template.get("emphasis_color") or template.get("highlight_color"), "#FFE000"
    )
    if highlight == primary:
        # The seam (gaming) templates are all-white by owner choice for
        # the caption words themselves, which left the burned hook title
        # plain white with zero accent — Luffy's complaint on the Kai
        # Cenat clips ("TITLE IS WHITE NO CATCHY"). The title's power
        # words/numbers take the caption system's own accent instead
        # (the karaoke yellow the templates use as their default
        # highlight); the caption style itself is untouched.
        highlight = hex_to_ass_color(HOOK_TITLE_ACCENT_FALLBACK, "#FFE000")
    outline = hex_to_ass_color(template.get("stroke_color") or "#000000", "#000000")
    back_color = hex_to_ass_color(template.get("background_color"), "#00000080")

    # Larger than the captions: the headline is what a scrolling viewer reads
    # before the spoken line. Long titles wrap to two lines, and a line that
    # still does not fit (a long compound word, or two long lines) shrinks
    # below the preferred size so it is not clipped at the frame edge.
    base_px = min(96, max(caption_font_px + 8, int(round(caption_font_px * 1.35))))
    if editor_style is not None:
        base_px = int(editor_style["base_px"])
    usable_width = video_width - 2 * max(48, int(video_width * HOOK_TITLE_SIDE_MARGIN_FRAC))

    def _title_size(base: int) -> Tuple[List[str], int]:
        max_chars = max(10, int(usable_width / (base * 0.52)))
        sized_lines = _balance_title_lines(title_text.split(), max_chars)
        longest = max(len(line) for line in sized_lines)
        px = base
        if longest > max_chars:
            fitted = int(usable_width / (longest * 0.52))
            px = min(base, max(1, fitted))
            if editor_style is not None:
                px = max(30, px)
        return sized_lines, px

    lines, hook_px = _title_size(base_px)
    original_px = hook_px
    if editor_style is not None:
        hook_px *= float(editor_style.get("scale", 1))

    # Brand Kit title treatment: "band" paints the kit colour behind the
    # title (BorderStyle 3 box in that colour), "accent_text" recolours
    # the title text itself. No brand block leaves styling untouched.
    brand_mode = (brand_title or {}).get("mode")
    brand_color = (brand_title or {}).get("color")
    if brand_mode == "accent_text" and brand_color:
        primary = hex_to_ass_color(brand_color, "#FFFFFF")
    base_stroke = int(template.get("stroke_width", 3) or 0)
    has_outline = template.get("stroke_color") is not None and base_stroke > 0
    border_style = 3 if (not has_outline and template.get("background_color")) else 1
    if brand_mode == "band" and brand_color:
        border_style = 3
        back_color = hex_to_ass_color(brand_color, "#000000")
    # Face guard: the pinned title must never sit on a face. When the
    # caller supplies output-space face boxes, the preferred placement
    # (top band, or the reaction-seam anchor) is kept only if the whole
    # title block clears every face; otherwise the block moves to the
    # nearest face-free band, shrinking through smaller sizes when no
    # band fits at full size. Editor-styled titles keep their exact
    # manual placement and are never guarded.
    #
    # Blur-band guard: on a surviving fit the caller also supplies the
    # fit's blurred pad bands as band_boxes. They join the obstacle
    # set, so a placement that clears faces AND bands always wins.
    # Faces outrank bands when no placement clears both: if the
    # combined pick is only the least-bad fallback and it touches a
    # face, the face-only pick (the pre-band behaviour) is used
    # instead — escaping a blur band must never push the title onto
    # a face.
    guard_pos_y: Optional[float] = None
    if (avoid_boxes or band_boxes) and editor_style is None:
        margin_v = max(48, int(video_height * HOOK_TITLE_TOP_MARGIN_FRAC))

        def _outline_for(px: int) -> int:
            value = (
                max(base_stroke, round(px * base_stroke / 26)) if has_outline else 0
            )
            if border_style == 3:
                value = max(4, px // 6)
            elif value == 0:
                value = max(2, px // 16)
            return value

        def _block(cand_lines: List[str], px: int) -> Tuple[float, float]:
            outline = _outline_for(px)
            width = max(len(line) for line in cand_lines) * px * 0.52 + 2 * outline
            height = len(cand_lines) * px * 1.2 + (
                2 * outline if border_style == 3 else 0
            )
            return width, height

        def _guarded_pick(
            obstacles: Sequence[Tuple[float, float, float, float]],
        ) -> Tuple[List[str], int, Optional[float], bool]:
            """(lines, px, pos_y, fitted) for one obstacle set.

            The size ladder over the preferred placement, exactly as
            the face guard has always run it; ``fitted`` is False
            when no size cleared every obstacle and the least-bad
            fallback centre was taken.
            """
            candidates = [(lines, hook_px)] + [
                _title_size(max(28, int(round(base_px * factor))))
                for factor in (0.9, 0.8, 0.7, 0.6)
            ]
            for cand_lines, cand_px in candidates:
                block_w, block_h = _block(cand_lines, cand_px)
                block_x = (video_width - block_w) / 2
                if position_y is None:
                    pref_centre = margin_v + block_h / 2
                else:
                    pref_centre = video_height * position_y - block_h / 2
                centre = guarded_centre_y(
                    pref_centre,
                    block_x,
                    block_w,
                    block_h,
                    obstacles,
                    video_width,
                    video_height,
                    top_limit=block_h / 2 + 8,
                    bottom_limit=video_height * (1 - PLATFORM_BOTTOM_SAFE_FRACTION),
                )
                if block_fits_face_free(
                    centre, block_x, block_w, block_h, obstacles,
                    video_width, video_height,
                ):
                    return (
                        cand_lines,
                        cand_px,
                        None if centre == pref_centre else centre + block_h / 2,
                        True,
                    )
            cand_lines, cand_px = candidates[-1]
            block_w, block_h = _block(cand_lines, cand_px)
            block_x = (video_width - block_w) / 2
            if position_y is None:
                pref_centre = margin_v + block_h / 2
            else:
                pref_centre = video_height * position_y - block_h / 2
            centre = guarded_centre_y(
                pref_centre,
                block_x,
                block_w,
                block_h,
                obstacles,
                video_width,
                video_height,
                top_limit=block_h / 2 + 8,
                bottom_limit=video_height * (1 - PLATFORM_BOTTOM_SAFE_FRACTION),
            )
            return (cand_lines, cand_px, centre + block_h / 2, False)

        face_boxes = list(avoid_boxes or [])
        blur_bands = list(band_boxes or [])
        picked = _guarded_pick(face_boxes + blur_bands)
        if blur_bands and not picked[3]:
            cand_lines, cand_px, cand_pos_y, _fitted = picked
            block_w, block_h = _block(cand_lines, cand_px)
            block_top = (
                margin_v if cand_pos_y is None else cand_pos_y - block_h
            )
            if not block_fits_face_free(
                block_top + block_h / 2,
                (video_width - block_w) / 2,
                block_w,
                block_h,
                face_boxes,
                video_width,
                video_height,
            ):
                picked = _guarded_pick(face_boxes)
        lines, hook_px, guard_pos_y = picked[0], picked[1], picked[2]
        if guard_report is not None:
            guard_report["title_moved"] = guard_pos_y is not None
            guard_report["title_pos_y"] = guard_pos_y
    outline_px = (
        max(base_stroke, round(hook_px * base_stroke / 26)) if has_outline else 0
    )
    if border_style == 3:
        outline_px = max(4, hook_px // 6)  # backing-box padding
    elif outline_px == 0:
        outline_px = max(2, hook_px // 16)  # always keep contrast on video
    shadow_px = max(2, hook_px // 20) if template.get("shadow") else 0
    margin_v = max(48, int(video_height * HOOK_TITLE_TOP_MARGIN_FRAC))

    style_line = (
        f"Style: Hook,{font_name},{hook_px},{primary},&H000000FF,{outline},{back_color},"
        f"1,0,0,0,100,100,0,0,{border_style},{outline_px},{shadow_px},8,60,60,{margin_v},1"
    )

    # Accent power words / numbers in the template highlight colour.
    rendered_lines: List[str] = []
    for line in lines:
        spans: List[str] = []
        for word in line.split():
            token = normalize_token(word)
            accented = bool(token) and (token in POWER_WORDS or any(c.isdigit() for c in token))
            color = highlight if accented else primary
            spans.append(f"{{\\c{color}}}{escape_ass_text(word)}")
        rendered_lines.append(" ".join(spans))
    text = "\\N".join(rendered_lines)

    start = 0.12
    # Persistent by default: no silent cap. Only an explicit title_seconds
    # (an opted-out profile/style, resolved by the EDL) shortens the title.
    natural_end = max(HOOK_TITLE_MIN_SECONDS, output_duration - 0.25)
    end = (
        min(float(title_seconds), natural_end)
        if title_seconds is not None
        else natural_end
    )
    if output_duration <= HOOK_TITLE_MIN_SECONDS:
        start, end = 0.0, max(0.5, output_duration)
    if editor_style is not None and "start" in editor_style:
        start = max(0.0, min(float(editor_style["start"]), max(0.0, output_duration - .04)))
        end = max(start + min(.04, output_duration), min(float(editor_style["end"]), output_duration))
    span = max(0.001, end - start)
    fade_in, fade_out = min(.16, span / 3), min(.24, span / 3)
    entrance = f"\\fad({round(fade_in * 1000)},{round(fade_out * 1000)})"
    if editor_style is not None:
        x = video_width * (0.5 + float(editor_style.get("x", 0)))
        y = margin_v + original_px * 1.2 * len(lines) / 2 + video_height * float(editor_style.get("y", 0))
        entrance += f"\\an5\\pos({round(x)},{round(y)})"
    elif guard_pos_y is not None:
        entrance += f"\\an2\\pos({video_width // 2},{round(guard_pos_y)})"
    elif position_y is not None:
        entrance += f"\\an2\\pos({video_width // 2},{round(video_height * position_y)})"
    if template.get("word_pop", True):
        entrance += f"\\fscx90\\fscy90\\t(0,{round(fade_in * 1000)},\\fscx100\\fscy100)"
    events = [
        f"Dialogue: 1,{ass_timestamp(start)},{ass_timestamp(end)},Hook,,0,0,0,,"
        f"{{{entrance}}}{text}"
    ]
    if guard_report is not None and editor_style is None:
        # The final block geometry, reported for the caption guard:
        # the title is an obstacle the captions must clear (with the
        # separation gap) wherever the face guard finally placed it —
        # top by preference, but the guard may have moved it anywhere.
        # Editor-styled titles are deliberate manual placements; their
        # geometry is the user's own and is not reported.
        block_w = (
            max(len(line) for line in lines) * hook_px * 0.52 + 2 * outline_px
        )
        block_h = len(lines) * hook_px * 1.2 + (
            2 * outline_px if border_style == 3 else 0
        )
        if guard_pos_y is not None:
            block_bottom = float(guard_pos_y)
        elif position_y is not None:
            block_bottom = video_height * position_y
        else:
            block_bottom = margin_v + block_h
        guard_report["title_block"] = [
            (video_width - block_w) / 2,
            block_bottom - block_h,
            block_w,
            block_h,
        ]
        guard_report["title_window"] = [start, end]
    return style_line, events


def shift_words_for_join_plan(
    words: List[Dict[str, Any]], join_plan: Optional[List[Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    """Map caption words from the clip timeline to the rendered timeline.

    An EDL render crossfades its segment joins: every join pulls the
    rendered timeline earlier by the fade duration, and the voice (and
    with it the words) moves by the same amount, or the burned captions
    trail the picture by the cumulative fade. This is the caption-side
    mirror of ``auto_director.edl._to_rendered_time``, fed by the
    renderer's own join plan (``media.edl_render.segment_join_plan``)
    so the fades are exactly the ones the render used. Returns shifted
    copies — the caller's word dicts are never mutated — and a plan
    with no positive fade is the identity.
    """
    joins: List[Tuple[float, float]] = []
    for join in join_plan or []:
        try:
            fade = float(join.get("fade") or 0)
            boundary = float(join.get("source_boundary") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        if fade > 0:
            joins.append((boundary, fade))
    shifted: List[Dict[str, Any]] = []
    for word in words:
        mapped = dict(word)
        if joins:
            for key in ("start", "end"):
                try:
                    t = float(mapped.get(key) or 0)
                except (TypeError, ValueError):
                    continue
                mapped[key] = t - sum(
                    fade for boundary, fade in joins if t >= boundary
                )
        shifted.append(mapped)
    return shifted


# A face zone: (start, end, boxes) with boxes as (x, y, w, h) in output
# pixels — the shape reframe_plan.collect_face_zones produces.
FaceZone = Tuple[float, float, List[Tuple[float, float, float, float]]]


def shift_zones_for_join_plan(
    zones: List[FaceZone], join_plan: Optional[List[Dict[str, Any]]]
) -> List[FaceZone]:
    """Map face zones from the clip timeline to the rendered timeline.

    Mirrors ``shift_words_for_join_plan``: every crossfade join pulls
    the rendered timeline earlier by its fade duration. Zone boundaries
    are clamped so a zone never ends before it starts.
    """
    joins: List[Tuple[float, float]] = []
    for join in join_plan or []:
        try:
            fade = float(join.get("fade") or 0)
            boundary = float(join.get("source_boundary") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        if fade > 0:
            joins.append((boundary, fade))
    if not joins:
        return zones

    def shift(t: float) -> float:
        return t - sum(fade for boundary, fade in joins if t >= boundary)

    shifted: List[FaceZone] = []
    for start, end, boxes in zones:
        new_start = max(0.0, shift(float(start)))
        new_end = max(new_start, shift(float(end)))
        shifted.append((new_start, new_end, boxes))
    return shifted


# A fit strip: (start, end, top, bottom) with top/bottom in output
# pixels — the sharp foreground rect of a fitted shot, the shape
# reframe_plan.collect_fit_strips produces. Outside the strip the fit
# renders only blurred backdrop, so a caption overlapping a strip is
# constrained inside it (the TJR fit defect: captions sat at ~74%
# height, below the strip's ~66% bottom edge, unreadable on blur).
FitStrip = Tuple[float, float, float, float]


def shift_strips_for_join_plan(
    strips: List[FitStrip], join_plan: Optional[List[Dict[str, Any]]]
) -> List[FitStrip]:
    """Map fit strips from the clip timeline to the rendered timeline.

    Mirrors ``shift_zones_for_join_plan``: every crossfade join pulls
    the rendered timeline earlier by its fade duration.
    """
    joins: List[Tuple[float, float]] = []
    for join in join_plan or []:
        try:
            fade = float(join.get("fade") or 0)
            boundary = float(join.get("source_boundary") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        if fade > 0:
            joins.append((boundary, fade))
    if not joins:
        return strips

    def shift(t: float) -> float:
        return t - sum(fade for boundary, fade in joins if t >= boundary)

    shifted: List[FitStrip] = []
    for start, end, top, bottom in strips:
        new_start = max(0.0, shift(float(start)))
        new_end = max(new_start, shift(float(end)))
        shifted.append((new_start, new_end, top, bottom))
    return shifted


def strip_band_boxes(
    strips: Sequence[FitStrip],
    win_start: float,
    win_end: float,
    video_width: int,
    video_height: int,
) -> List[Tuple[float, float, float, float]]:
    """Blur-band obstacle boxes for a caption window overlapping strips.

    The vertical span that stays sharp for the window's whole span is
    the intersection of every overlapping strip; the two bands outside
    it become full-width obstacle boxes for the face guard, whose
    nearest-free-band placement then lands inside the strip. Empty
    when no strip overlaps or the intersection is degenerate.
    """
    overlapping = [
        strip for strip in strips if strip[0] < win_end and win_start < strip[1]
    ]
    if not overlapping:
        return []
    top = max(strip[2] for strip in overlapping)
    bottom = min(strip[3] for strip in overlapping)
    if bottom - top <= 0:
        return []
    boxes: List[Tuple[float, float, float, float]] = []
    if top > 0:
        boxes.append((0.0, 0.0, float(video_width), float(top)))
    if bottom < video_height:
        boxes.append(
            (0.0, float(bottom), float(video_width), float(video_height) - float(bottom))
        )
    return boxes


def natural_hook_title_block(
    hook_title: str,
    template: Dict[str, Any],
    video_width: int,
    caption_font_px: int,
) -> Tuple[float, float]:
    """(width, height) of a hook title's natural (unguarded) block.

    Mirrors the sizing ``build_hook_title_ass`` applies before its
    face guard runs: natural size (no ladder shrink), template-only
    styling (no Brand Kit treatment, no editor style). The task-band
    decision in media.text_bands guards this representative block
    against the union of a task's clips; the render-time guard in
    ``build_hook_title_ass`` remains the authority, so a block that
    renders slightly larger than this estimate (e.g. a Brand Kit
    band adds backing-box padding) is absorbed by that guard and
    stamped as a deviation — never silently placed on a face.
    """
    title_text = hook_title.upper() if template.get("uppercase") else hook_title
    base_px = min(
        96, max(caption_font_px + 8, int(round(caption_font_px * 1.35)))
    )
    usable_width = video_width - 2 * max(
        48, int(video_width * HOOK_TITLE_SIDE_MARGIN_FRAC)
    )
    max_chars = max(10, int(usable_width / (base_px * 0.52)))
    lines = _balance_title_lines(title_text.split(), max_chars)
    longest = max(len(line) for line in lines)
    px = base_px
    if longest > max_chars:
        px = min(base_px, max(1, int(usable_width / (longest * 0.52))))
    base_stroke = int(template.get("stroke_width", 3) or 0)
    has_outline = template.get("stroke_color") is not None and base_stroke > 0
    border_style = (
        3 if (not has_outline and template.get("background_color")) else 1
    )
    if border_style == 3:
        outline = max(4, px // 6)
    elif has_outline:
        outline = max(base_stroke, round(px * base_stroke / 26))
    else:
        outline = max(2, px // 16)
    width = longest * px * 0.52 + 2 * outline
    height = len(lines) * px * 1.2 + (2 * outline if border_style == 3 else 0)
    return float(width), float(height)


def build_assemblyai_ass_subtitles(
    video_path: Path,
    clip_start: float,
    clip_end: float,
    video_width: int,
    video_height: int,
    output_ass_path: Path,
    font_family: Optional[str] = None,
    font_size: Optional[int] = None,
    font_color: Optional[str] = None,
    caption_template: str = "default",
    keep_ranges: Optional[List[Tuple[float, float]]] = None,
    caption_cues: Optional[List[Dict[str, Any]]] = None,
    hook_title: Optional[str] = None,
    include_captions: bool = True,
    caption_words: Optional[List[Dict[str, Any]]] = None,
    position_y_override: Optional[float] = None,
    highlight_words: Optional[List[str]] = None,
    template_override: Optional[Dict[str, Any]] = None,
    font_px_override: Optional[int] = None,
    center_y_override: Optional[int] = None,
    emphasis_indices: Optional[Iterable[int]] = None,
    wrap_lines: bool = False,
    center_x_override: Optional[int] = None,
    hook_editor_style: Optional[Dict[str, Any]] = None,
    caption_placements: Optional[List[Optional[Dict[str, Any]]]] = None,
    avoid_band: Optional[Tuple[float, float]] = None,
    caption_layout_report: Optional[Dict[str, Any]] = None,
    profile_name: Optional[str] = None,
    max_chunk_seconds: Optional[float] = None,
    edl_join_plan: Optional[List[Dict[str, Any]]] = None,
    hook_title_seconds: Optional[float] = None,
    brand_title: Optional[Dict[str, Any]] = None,
    face_zones: Optional[List[FaceZone]] = None,
    fit_strips: Optional[List[FitStrip]] = None,
    task_caption_centre: Optional[float] = None,
    task_title_bottom: Optional[float] = None,
) -> bool:
    """Generate animated word-synced ASS subtitles from cached AssemblyAI words.

    Renders OpusClip-style captions: a per-word active highlight that pops, an
    accent colour on emphasised power/keyword words, contextual emojis, a thick
    scaled outline + drop shadow, and an optional pill behind the active word.
    When ``hook_title`` is set, an AI-written headline is burned into the top
    safe area while the hook plays out (it renders even when word-synced
    captions are unavailable or disabled via ``include_captions``).

    The clip editor holds final style values itself: ``template_override``
    replaces the named template, ``font_px_override`` skips width scaling,
    ``center_y_override`` places the caption centre without the safe-area
    clamp, ``emphasis_indices`` replaces automatic emphasis with exactly those
    words, and ``wrap_lines`` lets libass wrap lines wider than the frame.
    ``avoid_band`` is a (top, bottom) height fraction holding captions burned
    into the source; the spoken captions move clear of it.

    When ``profile_name`` is the hindi_podcast profile, Devanagari caption
    words (and a Devanagari hook title) are first converted to Roman
    Hinglish — see media/hinglish.py. Word timings are never touched.

    ``task_caption_centre`` / ``task_title_bottom`` carry the task-level
    text bands (height fractions) decided once per task by
    media.text_bands: the caption centre is adopted only after it is
    verified clear of THIS clip's hard obstacles, and the title bottom
    is a preference this clip's own title guard still arbitrates — so
    a task band can unify a task's text placement but can never put
    text on a face in a clip whose geometry rejects it. Both default
    to None (per-clip behaviour, unchanged).
    """
    transcript_data = load_cached_transcript_data(video_path)

    template = template_override or get_template(caption_template)
    effective_font_family = font_family or template["font_family"]
    effective_font_size = int(font_size) if font_size else int(template["font_size"])
    effective_font_color = font_color or template["font_color"]
    animation = template.get("animation", "karaoke")

    relevant_words: List[Dict[str, Any]] = list(caption_words or [])
    if (
        include_captions
        and not relevant_words
        and transcript_data
        and transcript_data.get("words")
    ):
        if keep_ranges:
            relevant_words = get_words_for_keep_ranges(transcript_data, keep_ranges)
        else:
            relevant_words = get_words_in_range(transcript_data, clip_start, clip_end)
    if not relevant_words and not hook_title:
        logger.warning("No words or hook title available for ASS subtitles")
        return False

    from .caption_cleanup import drop_repetition_loops, loop_filter_enabled

    if relevant_words and loop_filter_enabled() and caption_placements is None:
        # A speech-to-text repetition loop (the same 3+ word phrase back
        # to back 3+ times over noise) is captioned once, not a dozen
        # times. Editor renders keep their word grid untouched.
        relevant_words, looped = drop_repetition_loops(relevant_words)
        if looped:
            logger.info("Captions: dropped %d words of a transcription repetition loop", looped)

    if relevant_words and caption_placements is None:
        # Caption-word polish (media.caption_polish), after the words are
        # final and before the caption build: the optional LLM tidy of
        # ASR word errors (CAPTION_WORD_FIX, PeakCut) and the
        # deterministic Devanagari -> Hinglish transliteration
        # (TRANSLIT_HINGLISH, NeonClipping), both off by default. Only
        # word text may change; timings are never touched. Editor
        # renders (caption_placements set) keep their word grid
        # untouched, like the loop filter above.
        from .caption_polish import polish_caption_words, polish_needed

        if polish_needed():
            relevant_words = polish_caption_words(relevant_words, title=hook_title or "")

    if profile_name == ROMANIZE_PROFILE:
        # Hindi podcast clips burn captions in Roman Hinglish, not the
        # Devanagari script Whisper returns. The conversion is word by
        # word (timings untouched) and no-ops unless the text really is
        # Devanagari-dominant, so Latin transcripts pass through unchanged.
        relevant_words = romanize_caption_words(relevant_words)
        if hook_title:
            hook_title = romanize_text(hook_title)

    if edl_join_plan:
        # EDL renders crossfade their segment joins: the rendered
        # timeline (and the voice on it) sits earlier than the clip
        # timeline the words were projected onto, by the cumulative
        # fade. Shift the words through the renderer's join plan or
        # the burned captions trail the picture on every crossfade
        # clip. Non-EDL renders pass no plan and are unchanged.
        relevant_words = shift_words_for_join_plan(relevant_words, edl_join_plan)
    if face_zones and edl_join_plan:
        # Face zones live on the same clip timeline as the words; shift
        # them through the same plan so guard decisions are made on the
        # rendered timeline the caption chunks use.
        face_zones = shift_zones_for_join_plan(face_zones, edl_join_plan)
    if fit_strips and edl_join_plan:
        # Fit strips live on the same clip timeline too.
        fit_strips = shift_strips_for_join_plan(fit_strips, edl_join_plan)

    effective_font_family, template = ensure_glyph_coverage(
        effective_font_family,
        template,
        [hook_title or ""] + [str(word.get("text", "")) for word in relevant_words],
    )

    # --- styling knobs (new template fields, all optional) ---
    uppercase = bool(template.get("uppercase"))
    # Only inject emojis when the runtime can actually render them in colour.
    enable_emoji = bool(template.get("emoji", True)) and emoji_rendering_supported()
    word_pop = bool(template.get("word_pop", True))
    word_box = bool(template.get("word_box"))
    glow = bool(template.get("glow"))
    has_outline = template.get("stroke_color") is not None
    # Emphasis colouring only makes sense when something distinguishes words.
    enable_emphasis = (
        emphasis_indices is not None or animation != "none" or bool(highlight_words)
    )

    primary = hex_to_ass_color(effective_font_color)
    highlight = hex_to_ass_color(template.get("highlight_color"), "#FFE000")
    emphasis_color = hex_to_ass_color(
        template.get("emphasis_color") or template.get("highlight_color"), "#FFE000"
    )
    outline = hex_to_ass_color(template.get("stroke_color") or "#000000", "#000000")
    back_color = hex_to_ass_color(template.get("background_color"), "#00000080")
    box_color = hex_to_ass_color(
        template.get("word_box_color") or template.get("highlight_color"), "#00BF49"
    )

    font_px = font_px_override or get_scaled_font_size(effective_font_size, video_width)
    base_stroke = int(template.get("stroke_width", 3) or 0)
    # Scale the outline with the font so big captions keep a chunky, readable edge.
    outline_px = (
        max(base_stroke, round(font_px * base_stroke / 26))
        if (has_outline and base_stroke)
        else 0
    )
    shadow_px = max(2, font_px // 20) if template.get("shadow") else 0
    box_bord = max(outline_px + 2, font_px // 5)
    pos_y = (
        float(position_y_override)
        if position_y_override is not None
        else float(template.get("position_y", 0.80))
    )
    est_text_height = int(font_px * 1.5)
    y_pos = (
        int(center_y_override)
        if center_y_override is not None
        else get_safe_vertical_position(video_height, est_text_height, pos_y)
    )
    if avoid_band is not None and center_y_override is None:
        y_pos = caption_center_clear_of_band(y_pos, est_text_height, avoid_band, video_height)
    if caption_layout_report is not None:
        caption_layout_report["caption_y"] = y_pos / video_height
    font_name = ass_font_name(effective_font_family)
    border_style = (
        3
        if template.get("background") and template.get("background_color")
        else 1
    )
    # libass fills BorderStyle 3 boxes with the outline colour and pads them by
    # the outline width, so an outline-less template would get no box at all.
    style_outline, style_bord = outline, outline_px
    if border_style == 3:
        style_outline, style_bord = back_color, max(outline_px, font_px // 6)

    hook_style_block = ""
    hook_events: List[str] = []
    # The hook title as a caption obstacle: (gap-expanded block,
    # window start, window end), filled from the title build below.
    title_obstacle: Optional[
        Tuple[Tuple[float, float, float, float], float, float]
    ] = None
    if hook_title:
        if keep_ranges:
            output_duration = rendered_ranges_duration(keep_ranges)
        else:
            output_duration = max(0.0, clip_end - clip_start)
        # Face guard for the title: every zone box that overlaps the
        # title's on-screen window (it is pinned from the clip start).
        title_avoid: Optional[List[Tuple[float, float, float, float]]] = None
        if face_zones:
            title_avoid = [
                box
                for zone_start, zone_end, zone_boxes in face_zones
                if zone_start < output_duration and zone_end > 0
                for box in zone_boxes
            ] or None
        # Fit strips guard the title the same way they guard captions
        # below: on a surviving fit the title's preferred top band is
        # blurred backdrop, so the fit's pad bands are computed with
        # the same strip_band_boxes geometry and handed to the title
        # guard as band_boxes (faces keep priority there — see
        # build_hook_title_ass). The title is one pinned block, so
        # the bands are taken over its whole on-screen window — the
        # strip intersection strip_band_boxes computes — exactly
        # like the face boxes above: a band that appears at any point
        # of the title's display constrains the whole display, and a
        # fit that starts only after the title ends does not.
        title_band_boxes: List[Tuple[float, float, float, float]] = []
        if fit_strips:
            title_win_end = output_duration
            if hook_title_seconds is not None:
                natural_end = max(
                    HOOK_TITLE_MIN_SECONDS, output_duration - 0.25
                )
                title_win_end = min(float(hook_title_seconds), natural_end)
            title_band_boxes = strip_band_boxes(
                fit_strips, 0.0, title_win_end, video_width, video_height
            )
        title_guard_report: Dict[str, Any] = {}
        hook_style_line, hook_events = build_hook_title_ass(
            hook_title,
            template,
            video_width,
            video_height,
            output_duration,
            font_name,
            font_px,
            # An upper-third caption seam belongs to a reaction stack. Keep
            # the headline below the source subject, off the reactor's face.
            # Otherwise a task-level title band (decided once per task by
            # media.text_bands) anchors the title's bottom edge; the clip's
            # own guard below still arbitrates it against THIS clip's faces.
            position_y=(
                0.94
                if position_y_override is not None and position_y_override < .4
                else task_title_bottom
            ),
            editor_style=hook_editor_style,
            # Resolved by the EDL: full clip duration unless the profile
            # explicitly opted out. None (no EDL) also means persistent.
            title_seconds=hook_title_seconds,
            brand_title=brand_title,
            avoid_boxes=title_avoid,
            guard_report=title_guard_report,
            band_boxes=title_band_boxes or None,
        )
        hook_style_block = f"{hook_style_line}\n"
        if caption_layout_report is not None and title_guard_report:
            title_stamp: Dict[str, Any] = {
                "moved": bool(title_guard_report.get("title_moved")),
                "pos_y": title_guard_report.get("title_pos_y"),
            }
            if task_title_bottom is not None:
                # The task band was offered as the title's bottom edge.
                # The title deviated exactly when this clip's own guard
                # had to move it (an unmoved title's bottom edge IS the
                # offered fraction; a shrunk-but-unmoved title keeps the
                # band position, so size alone is not a deviation).
                title_stamp["task_bottom"] = task_title_bottom
                title_stamp["task_deviated"] = bool(
                    title_guard_report.get("title_moved")
                )
            caption_layout_report["face_guard_title"] = title_stamp
        if caption_layout_report is not None and title_band_boxes:
            caption_layout_report["fit_strip_title"] = {
                "band_boxes": len(title_band_boxes)
            }
        _title_block = title_guard_report.get("title_block")
        _title_window = title_guard_report.get("title_window")
        if _title_block and _title_window:
            _bx, _by, _bw, _bh = (float(v) for v in _title_block)
            # The obstacle runs from the title block to the NEAREST
            # frame edge (plus the gap on the far side): the title
            # owns its edge of the frame, so captions can only ever
            # sit on the other side of it — never squeezed into the
            # strip between the title and the edge, which the plain
            # nearest-band rule otherwise prefers when the caption
            # preference is up high.
            if _by + _bh / 2 <= video_height / 2:
                _obstacle = (
                    _bx, 0.0, _bw, _by + _bh + TITLE_CAPTION_MIN_GAP_PX
                )
            else:
                _obstacle = (
                    _bx,
                    _by - TITLE_CAPTION_MIN_GAP_PX,
                    _bw,
                    video_height - (_by - TITLE_CAPTION_MIN_GAP_PX),
                )
            title_obstacle = (
                _obstacle,
                float(_title_window[0]),
                float(_title_window[1]),
            )

    # Contextual emoji + emphasis annotations over the whole clip word list.
    emoji_by_idx, emphasis_idx = annotate_caption_words(
        relevant_words,
        caption_cues,
        enable_emoji=enable_emoji,
        enable_emphasis=enable_emphasis and emphasis_indices is None,
    )
    if emphasis_indices is not None:
        emphasis_idx = set(emphasis_indices)
    requested_highlights = {
        normalize_token(word)
        for word in (highlight_words or [])
        if normalize_token(word)
    }
    accent_indices = set()
    if profile_name == "gaming" and os.getenv("HYPE_WORD_ACCENT", "off").lower() in {"1", "true", "on"}:
        game_terms = {"minecraft", "valorant", "fortnite", "gta", "roblox", "elden", "speedrun",
                      "clutch", "ace", "ishowspeed", "kaicenat", "xqc"} | requested_highlights
        for idx, word in enumerate(relevant_words):
            raw = str(word.get("text", "")).strip(".,!?")
            if normalize_token(raw) in game_terms or re.fullmatch(r"[$₹]?\d+(?:[.,]\d+)*(?:[%kKmM])?", raw):
                accent_indices.add(idx)
    secondary_accent = hex_to_ass_color("#62E69B", "#62E69B")
    emphasis_idx.update(
        index
        for index, word in enumerate(relevant_words)
        if normalize_token(str(word.get("text", ""))) in requested_highlights
    )

    max_words = max(1, int(template.get("max_words_per_line", 4) or 4))
    chunk_size = max_words

    # Word-count chunks can be far wider than the frame ("5,000 MILLIAMP-HOUR
    # RECHARGEABLE BATTERY"). Measure each chunk as libass will draw it, split
    # the ones that overflow the side margins, and shrink a single word that
    # still does not fit. The editor lets libass wrap instead (``wrap_lines``).
    measure = caption_text_measurer(effective_font_family, font_px)
    space_width = measure(" ")
    edge_px = max(style_bord, box_bord if word_box else 0) + shadow_px
    peak_scale = {"pop": 1.06, "bounce": 1.12}.get(animation, 1.0)
    available_width = (get_subtitle_max_width(video_width) - 2 * edge_px) / peak_scale
    emoji_width = space_width + font_px * 1.2

    def display_width(index: int, word: Dict[str, Any]) -> float:
        text = str(word.get("text", ""))
        width = measure(text.upper() if uppercase else text)
        return width + (emoji_width if emoji_by_idx.get(index) else 0.0)

    word_widths = [display_width(index, word) for index, word in enumerate(relevant_words)]
    # Base chunking: natural spans (sentence ends / long pauses / word
    # count) for generated renders. Editor renders pass per-chunk
    # placements indexed by the word-count grid, so they keep it.
    if caption_placements is not None:
        base_spans = [
            (start, min(len(relevant_words), start + chunk_size))
            for start in range(0, len(relevant_words), chunk_size)
        ]
    else:
        from .caption_cleanup import phrase_split_enabled, split_word_spans_phrase

        if phrase_split_enabled():
            base_spans = split_word_spans_phrase(
                relevant_words, chunk_size, CAPTION_PAUSE_BREAK_SECONDS
            )
        else:
            base_spans = split_word_spans_natural(relevant_words, chunk_size)
    chunk_spans: List[Tuple[int, int, float]] = []
    for chunk_start, chunk_stop in base_spans:
        if wrap_lines:
            chunk_spans.append((chunk_start, chunk_stop, 1.0))
            continue
        widths = word_widths[chunk_start:chunk_stop]
        for start, end in split_caption_chunk(widths, space_width, available_width):
            line_width = sum(widths[start:end]) + space_width * (end - start - 1)
            scale = min(1.0, available_width / line_width) if line_width > 0 else 1.0
            chunk_spans.append((chunk_start + start, chunk_start + end, scale))

    # Pacing cap: no display chunk may sit on screen longer than the cap,
    # however slow the speech. An explicit ``max_chunk_seconds`` wins, then
    # a template field, then the default. Editor renders pass per-chunk
    # placements indexed by the pre-split chunking, so the split is skipped
    # there (their chunks come from the editor's own layout).
    hold_cap = max_chunk_seconds
    if hold_cap is None:
        hold_cap = template.get("max_chunk_seconds")
    try:
        hold_cap = float(hold_cap) if hold_cap else CAPTION_HOLD_DEFAULT_SECONDS
    except (TypeError, ValueError):
        hold_cap = CAPTION_HOLD_DEFAULT_SECONDS
    if hold_cap > 0 and caption_placements is None:
        chunk_spans = split_chunk_spans_by_duration(
            relevant_words, chunk_spans, hold_cap
        )
    # Display window per chunk. ASR word timings can overlap (a word
    # ending after the next chunk's first word starts); without clamping,
    # two Dialogue events would be on screen at the same position at
    # once, drawing garbled double text. Each chunk's display therefore
    # ends no later than the next chunk's first word start.
    chunk_windows: List[Tuple[float, float]] = []
    for index, (span_start, span_stop, _scale) in enumerate(chunk_spans):
        window_start = float(relevant_words[span_start]["start"])
        window_end = float(relevant_words[span_stop - 1]["end"])
        if index + 1 < len(chunk_spans):
            next_start = float(relevant_words[chunk_spans[index + 1][0]]["start"])
            if next_start > window_start:
                window_end = min(window_end, next_start)
        chunk_windows.append((window_start, window_end))

    if caption_layout_report is not None:
        # The timings that actually render, recorded so QC can verify
        # caption presence and the hold cap on the finished clip.
        caption_layout_report["caption_chunks"] = [
            {
                # Start/end are the clamped display windows that actually
                # render (the quality lane's overlap clamp above); the text
                # is the chunk's actual caption text, which brief compliance
                # (caption rules / must-mention in captions) judges the
                # generated captions from — never from the transcript alone.
                # Timing-only consumers ignore the extra key.
                "start": round(start, 3),
                "end": round(end, 3),
                "text": " ".join(
                    str(word.get("text", ""))
                    for word in relevant_words[span_start:span_stop]
                ).strip(),
            }
            for (span_start, span_stop, _scale), (start, end) in zip(
                chunk_spans, chunk_windows
            )
            if span_stop > span_start
        ]
        caption_layout_report["caption_hold_cap_seconds"] = (
            hold_cap if hold_cap > 0 else None
        )

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {video_width}
PlayResY: {video_height}
WrapStyle: {0 if wrap_lines else 2}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_px},{primary},&H000000FF,{style_outline},{back_color},1,0,0,0,100,100,0,0,{border_style},{style_bord},{shadow_px},5,60,60,60,1
{hook_style_block}
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    x_pos = video_width // 2 if center_x_override is None else center_x_override

    # Face guard for captions: keep every caption block off the faces
    # the reframe planner observed (output coordinates). Applies to the
    # automatic layout only — explicit editor placements and a manual
    # centre override are the user's own choices and win. The centre
    # limits mirror get_safe_vertical_position's own clamp so a moved
    # caption still respects the platform safe area. Fit strips extend
    # the same guard: a chunk overlapping a fitted shot also treats the
    # shot's blurred pad bands as obstacles, so its caption lands
    # inside the sharp strip even when no face is nearby.
    guard_active = (
        (bool(face_zones) or bool(fit_strips))
        and caption_placements is None
        and center_y_override is None
    )
    guard_top = max(40, int(video_height * 0.05))
    guard_bottom = min(
        video_height - max(120, int(video_height * 0.10)) - est_text_height,
        int(video_height * (1 - PLATFORM_BOTTOM_SAFE_FRACTION)) - est_text_height // 2,
    )
    guard_moves = 0
    strip_chunks = 0
    band_deviations = 0

    def chunk_block_dims(
        span_start: int, span_stop: int, scale: float
    ) -> Tuple[float, float]:
        # Mirrors the per-chunk block geometry the render loop computes
        # (original outline/shadow geometry, before any editor
        # placement overrides it inside the loop).
        line_width = (
            sum(word_widths[span_start:span_stop])
            + space_width * max(0, span_stop - span_start - 1)
        )
        block_w = line_width * scale + 2 * (style_bord + shadow_px + 8)
        block_h = est_text_height * scale + 2 * (
            (style_bord if border_style == 3 else outline_px) + shadow_px + 6
        )
        return block_w, block_h

    # ONE caption band per clip, decided from the clip's DOMINANT
    # geometry: the preferred centre guarded once against the union
    # of the obstacle sources that dominate the caption span (see
    # BAND_DOMINANT_COVERAGE). Chunks then start from the band centre
    # and move only when their own shot's geometry forces it — the
    # per-chunk free-for-all put captions at a different height in
    # every clip of one task (and, with the title guard doing the
    # same independently, once directly on top of the title). The
    # band and every deviation from it are stamped in the layout
    # report below.
    #
    # A task band (``task_caption_centre``, decided once per task by
    # media.text_bands against the union of ALL the task's clips)
    # outranks the per-clip decision when offered: it is verified
    # against THIS clip's own hard obstacles with THIS clip's real
    # representative block before it engages, so the face guard's
    # hard guarantee is unchanged — a clip whose faces collide with
    # the task centre falls back to the per-clip computation below
    # and the deviation is stamped.
    clip_centre = y_pos
    band_dominant = False
    band_source = "clip"
    task_band_deviated = False
    if guard_active and chunk_spans and chunk_windows:
        span_start = min(start for start, _end in chunk_windows)
        span_end = max(end for _start, end in chunk_windows)
        span = max(0.0, span_end - span_start)

        def _coverage(intervals: Sequence[Tuple[float, float]]) -> float:
            """Share of the caption span the intervals collectively cover."""
            if span <= 0:
                return 0.0
            clipped = sorted(
                (max(span_start, a), min(span_end, b))
                for a, b in intervals
                if min(span_end, b) > max(span_start, a)
            )
            total = 0.0
            cur_start: Optional[float] = None
            cur_end = 0.0
            for a, b in clipped:
                if cur_start is None:
                    cur_start, cur_end = a, b
                elif a <= cur_end:
                    cur_end = max(cur_end, b)
                else:
                    total += cur_end - cur_start
                    cur_start, cur_end = a, b
            if cur_start is not None:
                total += cur_end - cur_start
            return total / span

        band_obstacles: List[Tuple[float, float, float, float]] = []
        # The hard subset — faces and the title — for the fit check
        # below: the codebase's priority is faces outrank bands, and
        # the title block outranks a blur band too (a caption over a
        # blurred pad is readable; a caption on a face or tangled
        # with the title is not). Strip bands shape the candidate
        # through the full obstacle set, but they alone never veto
        # the band.
        band_hard_obstacles: List[Tuple[float, float, float, float]] = []
        if face_zones and _coverage(
            [(zs, ze) for zs, ze, _boxes in face_zones]
        ) >= BAND_DOMINANT_COVERAGE:
            band_obstacles.extend(
                box for _zs, _ze, boxes in face_zones for box in boxes
            )
            band_hard_obstacles.extend(
                box for _zs, _ze, boxes in face_zones for box in boxes
            )
        if fit_strips and _coverage(
            [(ss, se) for ss, se, _top, _bottom in fit_strips]
        ) >= BAND_DOMINANT_COVERAGE:
            band_obstacles.extend(
                strip_band_boxes(
                    fit_strips, span_start, span_end, video_width, video_height
                )
            )
        if title_obstacle is not None and _coverage(
            [(title_obstacle[1], title_obstacle[2])]
        ) >= BAND_DOMINANT_COVERAGE:
            band_obstacles.append(title_obstacle[0])
            band_hard_obstacles.append(title_obstacle[0])
        task_candidate: Optional[int] = None
        if task_caption_centre is not None:
            task_candidate = int(
                round(float(task_caption_centre) * video_height)
            )
        if band_obstacles or task_candidate is not None:
            rep_w, rep_h = max(
                (
                    chunk_block_dims(cs, ce, cscale)
                    for cs, ce, cscale in chunk_spans
                ),
                key=lambda dims: dims[0],
            )
            if task_candidate is not None:
                if block_fits_face_free(
                    task_candidate,
                    x_pos - rep_w / 2,
                    rep_w,
                    rep_h,
                    band_hard_obstacles,
                    video_width,
                    video_height,
                ):
                    clip_centre = task_candidate
                    band_dominant = True
                    band_source = "task"
                else:
                    task_band_deviated = True
        if band_source != "task" and band_obstacles:
            candidate = int(
                round(
                    guarded_centre_y(
                        y_pos,
                        x_pos - rep_w / 2,
                        rep_w,
                        rep_h,
                        band_obstacles,
                        video_width,
                        video_height,
                        top_limit=guard_top,
                        bottom_limit=guard_bottom,
                    )
                )
            )
            # The band engages only when its centre is genuinely
            # clear of the HARD obstacles (faces + title) for the
            # representative block. When even those leave no fitting
            # band, guarded_centre_y returns its least-bad fallback —
            # a position that still touches a face — and anchoring
            # every chunk there would be worse than the per-chunk
            # arbitration, which fits each chunk's own (smaller)
            # block where it can. In that case the band is the
            # preference and chunks decide locally.
            if not block_fits_face_free(
                candidate,
                x_pos - rep_w / 2,
                rep_w,
                rep_h,
                band_hard_obstacles,
                video_width,
                video_height,
            ):
                # Priority pass: the full obstacle set (strips
                # included) may leave no band at all while the hard
                # set still has one — strips yield, so retry the
                # candidate against faces + title alone.
                candidate = int(
                    round(
                        guarded_centre_y(
                            y_pos,
                            x_pos - rep_w / 2,
                            rep_w,
                            rep_h,
                            band_hard_obstacles,
                            video_width,
                            video_height,
                            top_limit=guard_top,
                            bottom_limit=guard_bottom,
                        )
                    )
                )
            if block_fits_face_free(
                candidate,
                x_pos - rep_w / 2,
                rep_w,
                rep_h,
                band_hard_obstacles,
                video_width,
                video_height,
            ):
                clip_centre = candidate
                band_dominant = True

    def scaled(percent: int, scale: float) -> int:
        return max(1, round(percent * scale))

    def line_prefix(scale: float, y: Optional[int] = None) -> str:
        tags = f"\\pos({x_pos},{y_pos if y is None else y})" + ("\\blur4" if glow else "")
        if scale < 1.0:
            tags += f"\\fscx{scaled(100, scale)}\\fscy{scaled(100, scale)}"
        return f"{{{tags}}}"

    # Every word span re-declares the caption font, so an emoji's \fn override
    # can never leak into the following word.
    font_tag = f"\\fn{font_name}"

    def render_text(global_idx: int, word: Dict[str, Any]) -> str:
        text = str(word.get("text", ""))
        if uppercase:
            text = text.upper()
        disp = escape_ass_text(text)
        emoji = emoji_by_idx.get(global_idx)
        if emoji:
            # Force the colour-emoji font for the glyph; the next word's span
            # re-declares the caption font, so no explicit restore is needed.
            disp = f"{disp} {{\\fn{EMOJI_FONT_NAME}}}{emoji}"
        return disp

    # The active word is distinguished by COLOUR only (and an optional box). We
    # deliberately do NOT scale individual words: scaling a word changes its
    # advance width, which reflows the centre-anchored line and makes the whole
    # caption visibly vibrate as each word pops. The "pop" lives as a one-shot
    # line entrance instead (see below).
    def active_span(disp: str) -> str:
        tags = f"{font_tag}\\c{highlight}"
        if word_box:
            tags += f"\\3c{box_color}\\bord{box_bord}\\shad0"
        return f"{{{tags}}}{disp}"

    def idle_span(global_idx: int, disp: str) -> str:
        color = emphasis_color if (enable_emphasis and global_idx in emphasis_idx) else primary
        if global_idx in accent_indices:
            color = secondary_accent
        tags = f"{font_tag}\\c{color}"
        if word_box:
            tags += f"\\3c{outline}\\bord{outline_px}\\shad{shadow_px}"
        return f"{{{tags}}}{disp}"

    # Subtle one-shot entrance for the whole line (uniform scale, centred), shown
    # only as the first word of a chunk appears — gives a pop without any
    # per-word reflow/vibration.
    def line_entrance(scale: float) -> str:
        if not word_pop:
            return ""
        small, full = scaled(92, scale), scaled(100, scale)
        return f"\\fscx{small}\\fscy{small}\\t(0,140,\\fscx{full}\\fscy{full})"

    events: List[str] = []
    original_geometry = (outline_px, shadow_px, box_bord)
    for chunk_index, (chunk_start, chunk_stop, chunk_scale) in enumerate(chunk_spans):
        chunk = relevant_words[chunk_start:chunk_stop]
        indices = list(range(chunk_start, chunk_stop))
        chunk_end = chunk_windows[chunk_index][1]
        chunk_y = y_pos
        if guard_active:
            win_start, win_end = chunk_windows[chunk_index]
            zone_boxes = [
                box
                for zone_start, zone_end, boxes in face_zones or []
                if zone_start < win_end and win_start < zone_end
                for box in boxes
            ]
            if fit_strips:
                band_boxes = strip_band_boxes(
                    fit_strips, win_start, win_end, video_width, video_height
                )
                if band_boxes:
                    strip_chunks += 1
                    zone_boxes = zone_boxes + band_boxes
            if title_obstacle is not None:
                _t_box, _t_start, _t_end = title_obstacle
                if _t_start < win_end and win_start < _t_end:
                    # Hard mutual rule: while the title is on screen a
                    # caption chunk clears its block by the gap, even
                    # when no face constrains the chunk at all.
                    zone_boxes = zone_boxes + [_t_box]
            if zone_boxes:
                line_width = (
                    sum(word_widths[chunk_start:chunk_stop])
                    + space_width * max(0, chunk_stop - chunk_start - 1)
                )
                block_w = line_width * chunk_scale + 2 * (style_bord + shadow_px + 8)
                block_h = est_text_height * chunk_scale + 2 * (
                    (style_bord if border_style == 3 else outline_px)
                    + shadow_px
                    + 6
                )
                centre = guarded_centre_y(
                    clip_centre,
                    x_pos - block_w / 2,
                    block_w,
                    block_h,
                    zone_boxes,
                    video_width,
                    video_height,
                    top_limit=guard_top,
                    bottom_limit=guard_bottom,
                )
                chunk_y = int(round(centre))
            else:
                # No local obstacle: the chunk sits on the clip band.
                chunk_y = clip_centre
            if chunk_y != y_pos:
                guard_moves += 1
            if chunk_y != clip_centre:
                band_deviations += 1
        prefix = line_prefix(chunk_scale, chunk_y)
        entrance_tags = line_entrance(chunk_scale)
        outline_px, shadow_px, box_bord = original_geometry
        placement = (caption_placements[chunk_start // chunk_size]
                     if caption_placements and chunk_start // chunk_size < len(caption_placements) else None)
        if placement is not None:
            px = placement["font_px"]
            outline_px = max(base_stroke, round(px * base_stroke / 26)) if has_outline else 0
            shadow_px = max(2, px // 20) if template.get("shadow") else 0
            box_bord = max(outline_px + 2, px // 5)
            border = max(outline_px, px // 6) if border_style == 3 else outline_px
            prefix = f"{{\\pos({placement['x']},{placement['y']})\\fs{px}\\bord{border}\\shad{shadow_px}" + ("\\blur4" if glow else "") + "}"

        if animation == "karaoke":
            for local_i, word in enumerate(chunk):
                start = float(word["start"])
                if local_i + 1 < len(chunk):
                    next_start = float(chunk[local_i + 1]["start"])
                    word_end = float(word["end"])
                    # Bridge micro-gaps so the line does not flicker
                    # between words, but never hold the highlight past
                    # a real pause in the speech.
                    end = (
                        min(next_start, word_end + KARAOKE_GAP_BRIDGE_SECONDS)
                        if next_start > word_end
                        else next_start
                    )
                else:
                    end = chunk_end
                if end <= start:
                    end = start + 0.05
                parts = []
                for local_j, other in enumerate(chunk):
                    gj = indices[local_j]
                    disp = render_text(gj, other)
                    # karaoke_fill: words already spoken stay highlighted (owner:
                    # "as he says the word it should be yellow and continue yellow").
                    spoken = bool(template.get("karaoke_fill")) and local_j < local_i
                    parts.append(active_span(disp) if (local_j == local_i or spoken) else idle_span(gj, disp))
                line = " ".join(parts)
                # Entrance only on the first word's event so it plays once, not
                # once per word.
                entrance = f"{{{entrance_tags}}}" if (entrance_tags and local_i == 0) else ""
                events.append(
                    f"Dialogue: 0,{ass_timestamp(start)},{ass_timestamp(end)},Default,,0,0,0,,{prefix}{entrance}{line}"
                )
        else:
            start = float(chunk[0]["start"])
            end = chunk_end
            if end <= start:
                end = start + 0.05
            spans = []
            for local_j, word in enumerate(chunk):
                gj = indices[local_j]
                disp = render_text(gj, word)
                color = emphasis_color if (enable_emphasis and gj in emphasis_idx) else primary
                spans.append(f"{{{font_tag}\\c{color}}}{disp}")
            chunk_text = " ".join(spans)

            effect = ""
            # (from %, peak %, ms to peak, ms to settle) of the scale animation
            scale_steps = {"pop": (88, 106, 130, 250), "bounce": (70, 112, 120, 240)}
            if animation == "fade":
                effect = "{\\fad(120,120)}"
            elif animation in scale_steps:
                low, peak, rise, settle = scale_steps[animation]
                low, peak, full = (scaled(p, chunk_scale) for p in (low, peak, 100))
                effect = (
                    f"{{\\fscx{low}\\fscy{low}\\t(0,{rise},\\fscx{peak}\\fscy{peak})"
                    f"\\t({rise},{settle},\\fscx{full}\\fscy{full})}}"
                )
            events.append(
                f"Dialogue: 0,{ass_timestamp(start)},{ass_timestamp(end)},Default,,0,0,0,,{prefix}{effect}{chunk_text}"
            )

    if caption_layout_report is not None and guard_active:
        caption_layout_report["face_guard_captions"] = {"moves": guard_moves}
        caption_layout_report["caption_band"] = {
            "centre": round(clip_centre / video_height, 4),
            "deviations": band_deviations,
            "title_gap": title_obstacle is not None,
            "dominant": band_dominant,
            "source": band_source,
        }
        if task_caption_centre is not None:
            caption_layout_report["caption_band"]["task_centre"] = (
                task_caption_centre
            )
            caption_layout_report["caption_band"]["task_deviated"] = (
                task_band_deviated
            )
        if fit_strips:
            caption_layout_report["fit_strip_captions"] = {
                "chunks": strip_chunks
            }
    all_events = hook_events + events
    output_ass_path.write_text(header + "\n".join(all_events) + "\n", encoding="utf-8")
    logger.info(
        "Wrote ASS subtitles: %s (%d events%s)",
        output_ass_path,
        len(all_events),
        ", hook title" if hook_events else "",
    )
    return True
