"""Quality Director item 1: hook enforcement + cold-open reordering.

After planning, the clip's hook moment must land inside the first ~2 s.
When it does not, the hook's whole SENTENCE is moved to the front by
reordering the keep ranges (picture, voice and captions move together;
no words are cut). Context guards beat the 2-second rule: a sentence
opening on a pronoun/back-reference, a punchline, or a payoff with an
earlier setup is never moved — the clip is marked
``kept_original_context`` with the reason recorded.

The pipeline tests render real files and prove the reorder in the
EXPORTED audio: each sentence of the synthetic source carries a
distinct tone, and after a cold open the hook sentence's tone leads.
"""

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pytest

from src.auto_director import hook_enforcement, plan_service
from src.auto_director.clip_plan import (
    ClipPlan,
    HookDecision,
    OpeningTreatment,
    Phase,
    PlannedSfxEvent,
    TransitionChoice,
)
from src.auto_director.hook_enforcement import (
    evaluate_hook,
    remap_plan,
    sentence_block,
)
from src.auto_director.plan_service import ClipPlanDraft
from src.services.video_service import VideoService


def _w(text, start, end):
    return {"text": text, "start": start, "end": end}


# Four sentences on the clip timeline. S3 (5.0-7.4) carries the hook.
WORDS = [
    _w("Welcome", 0.0, 0.5), _w("back", 0.5, 0.9), _w("everyone.", 0.9, 1.6),
    _w("Today", 2.2, 2.6), _w("we", 2.6, 2.8), _w("test", 2.8, 3.2),
    _w("things.", 3.2, 3.9),
    _w("The", 5.0, 5.3), _w("secret", 5.3, 5.9), _w("nobody", 5.9, 6.5),
    _w("expected.", 6.5, 7.4),
    _w("Let", 8.2, 8.5), _w("us", 8.5, 8.7), _w("continue", 8.7, 9.4),
    _w("onwards.", 9.4, 10.2),
]
DURATION = 11.0
RANGES = [(0.0, 11.0)]


def _plan(**overrides):
    plan = ClipPlan(
        niche="podcast_news",
        hook=HookDecision(
            type="strong_statement",
            timestamp=5.6,
            confidence=0.85,
            reason="The secret nobody expected is the clip's turn.",
        ),
        opening_treatment=OpeningTreatment(
            punch_zoom="subtle", at_offset=5.6, reason="Punch on the reveal."
        ),
        transition_choices=[
            TransitionChoice(
                name="smoothleft", at_offset=8.0,
                reason="The topic moves on after the reveal.",
            )
        ],
        phases=[
            Phase(start_offset=0.0, end_offset=5.0, intent="context"),
            Phase(start_offset=5.0, end_offset=11.0, intent="build"),
        ],
        sfx_events=[
            PlannedSfxEvent(at_offset=9.5, name="pop", reason="beat after the reveal"),
            PlannedSfxEvent(at_offset=1.0, name="ding", reason="opening word lands"),
        ],
        source="llm",
    )
    for key, value in overrides.items():
        setattr(plan, key, value)
    return plan


# --------------------------------------------------------------------------
# Sentence block + remap (pure)
# --------------------------------------------------------------------------


def test_sentence_block_finds_the_hook_sentence_with_lead_in():
    block = sentence_block(WORDS, 5.6)
    assert block is not None
    start, end, first, last = block
    # S3 runs 5.0-7.4; the start pulls a 0.1 s lead-in (previous word
    # ended at 3.9, so the full lead-in survives).
    assert start == pytest.approx(4.9)
    assert end == pytest.approx(7.4)
    assert WORDS[first]["text"] == "The"
    assert WORDS[last]["text"] == "expected."


def test_sentence_block_untimed_words_cannot_be_located():
    untimed = [{"text": "hello", "start": 0.0, "end": 0.0}]
    assert sentence_block(untimed, 0.5) is None
    assert sentence_block([], 0.5) is None


def test_remap_plan_moves_hook_and_keeps_tail_offsets():
    plan = remap_plan(_plan(), block_start=4.9, block_end=7.4)
    # Hook was 5.6 inside the block [4.9, 7.4) -> 0.7 on the new timeline.
    assert plan.hook.timestamp == pytest.approx(0.7)
    assert plan.opening_treatment.at_offset == pytest.approx(0.7)
    # SFX at 9.5 sits in the tail block (C): offsets there are unchanged.
    # SFX at 1.0 sits in the head block (A): it shifts later by the
    # block's length (2.5 s).
    offsets = {event.name: event.at_offset for event in plan.sfx_events}
    assert offsets["pop"] == pytest.approx(9.5)
    assert offsets["ding"] == pytest.approx(3.5)
    # Transition choices target joins the reorder rebuilds: dropped.
    assert plan.transition_choices == []


# --------------------------------------------------------------------------
# evaluate_hook decisions
# --------------------------------------------------------------------------


def test_already_leading_hook_changes_nothing():
    plan = _plan()
    plan.hook.timestamp = 1.2
    decision = evaluate_hook(plan, words=WORDS, keep_ranges=RANGES, duration=DURATION)
    assert decision.outcome["status"] == "already_leading"
    assert decision.keep_ranges is None


def test_cold_open_reorders_ranges_and_remaps_plan():
    decision = evaluate_hook(_plan(), words=WORDS, keep_ranges=RANGES, duration=DURATION)
    assert decision.outcome["status"] == "cold_opened"
    assert decision.outcome["hook_timestamp_before"] == pytest.approx(5.6)
    assert decision.outcome["hook_timestamp_after"] == pytest.approx(0.7)
    assert decision.keep_ranges is not None
    # The hook sentence's footage leads; the rest follows in order.
    assert decision.keep_ranges[0][0] == pytest.approx(4.9)
    assert decision.keep_ranges[0][1] == pytest.approx(7.4)
    assert decision.keep_ranges[1] == (0.0, pytest.approx(4.9))
    assert decision.keep_ranges[2] == (pytest.approx(7.4), 11.0)
    total = sum(end - start for start, end in decision.keep_ranges)
    assert total == pytest.approx(DURATION)
    assert decision.plan is not None
    assert decision.plan.hook.timestamp == pytest.approx(0.7)


def test_pronoun_opener_is_kept_for_context():
    words = [
        _w("Welcome", 0.0, 0.5), _w("back", 0.5, 0.9), _w("everyone.", 0.9, 1.6),
        _w("He", 5.0, 5.3), _w("then", 5.3, 5.7), _w("revealed", 5.7, 6.4),
        _w("everything.", 6.4, 7.4),
        _w("Let", 8.2, 8.5), _w("us", 8.5, 8.7), _w("continue", 8.7, 9.4),
        _w("onwards.", 9.4, 10.2),
    ]
    plan = _plan()
    plan.hook.timestamp = 5.8
    decision = evaluate_hook(plan, words=words, keep_ranges=RANGES, duration=DURATION)
    assert decision.outcome["status"] == "kept_original_context"
    assert "'he'" in decision.outcome["reason"]
    assert decision.keep_ranges is None


def test_back_reference_opener_is_kept_for_context():
    words = [
        _w("Welcome", 0.0, 0.5), _w("back", 0.5, 0.9), _w("everyone.", 0.9, 1.6),
        _w("As", 5.0, 5.2), _w("I", 5.2, 5.4), _w("said", 5.4, 5.8),
        _w("before", 5.8, 6.3), _w("clearly.", 6.3, 7.0),
        _w("Let", 8.2, 8.5), _w("us", 8.5, 8.7), _w("continue", 8.7, 9.4),
        _w("onwards.", 9.4, 10.2),
    ]
    plan = _plan()
    plan.hook.timestamp = 5.6
    decision = evaluate_hook(plan, words=words, keep_ranges=RANGES, duration=DURATION)
    assert decision.outcome["status"] == "kept_original_context"
    assert decision.keep_ranges is None


def test_punchline_is_kept_for_context():
    plan = _plan()
    plan.hook.type = "punchline"
    decision = evaluate_hook(plan, words=WORDS, keep_ranges=RANGES, duration=DURATION)
    assert decision.outcome["status"] == "kept_original_context"
    assert "punchline" in decision.outcome["reason"]
    assert decision.keep_ranges is None


def test_payoff_with_earlier_setup_is_kept_for_context():
    plan = _plan()
    plan.phases = [
        Phase(start_offset=0.0, end_offset=5.0, intent="build"),
        Phase(start_offset=5.0, end_offset=11.0, intent="payoff"),
    ]
    decision = evaluate_hook(plan, words=WORDS, keep_ranges=RANGES, duration=DURATION)
    assert decision.outcome["status"] == "kept_original_context"
    assert "payoff" in decision.outcome["reason"]
    assert decision.keep_ranges is None


def test_hook_inside_opening_sentence_is_kept():
    words = [
        _w("This", 0.0, 0.4), _w("whole", 0.4, 0.8), _w("opening", 0.8, 1.4),
        _w("sentence", 1.4, 2.0), _w("runs", 2.0, 2.5), _w("long", 2.5, 3.0),
        _w("today.", 3.0, 3.8),
        _w("Next", 5.0, 5.4), _w("part", 5.4, 5.9), _w("here.", 5.9, 6.6),
    ]
    plan = _plan()
    plan.hook.timestamp = 2.6  # late, but inside the FIRST sentence
    decision = evaluate_hook(
        plan, words=words, keep_ranges=[(0.0, 7.0)], duration=7.0
    )
    assert decision.outcome["status"] == "kept_original_context"
    assert "opening sentence" in decision.outcome["reason"]
    assert decision.keep_ranges is None


def test_fallback_and_hookless_plans_record_no_hook():
    fallback = _plan()
    fallback.source = "fallback"
    decision = evaluate_hook(fallback, words=WORDS, keep_ranges=RANGES, duration=DURATION)
    assert decision.outcome["status"] == "no_hook"
    assert decision.keep_ranges is None

    hookless = _plan()
    hookless.hook.type = "none_strong"
    decision = evaluate_hook(hookless, words=WORDS, keep_ranges=RANGES, duration=DURATION)
    assert decision.outcome["status"] == "no_hook"


def test_disabled_enforcement_is_a_pure_no_op(monkeypatch):
    monkeypatch.setenv("HOOK_ENFORCEMENT_ENABLED", "false")
    assert hook_enforcement.hook_enforcement_enabled() is False
    decision = evaluate_hook(_plan(), words=WORDS, keep_ranges=RANGES, duration=DURATION)
    assert decision.outcome is None
    assert decision.keep_ranges is None
    assert decision.plan is None


# --------------------------------------------------------------------------
# Pipeline: create_single_clip renders the reorder (real FFmpeg)
# --------------------------------------------------------------------------

ffmpeg_missing = shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None
pytestmark_real = pytest.mark.skipif(ffmpeg_missing, reason="ffmpeg/ffprobe not installed")


def _draft(hook_type="strong_statement", hook_timestamp=5.6):
    return ClipPlanDraft(
        hook_type=hook_type,
        hook_timestamp=hook_timestamp,
        hook_confidence=0.85,
        hook_reason="The secret nobody expected is the clip's turn.",
        punch_zoom="subtle",
        first_caption_emphasis=False,
        sfx_accent=None,
        opening_at_offset=hook_timestamp,
        opening_reason="The reveal deserves the opening punch.",
        transition_choices=[],
        caption_style_name=None,
        caption_style_reason="",
        phases=[
            {"start_offset": 0.0, "end_offset": 5.0, "intent": "context"},
            {"start_offset": 5.0, "end_offset": 11.0, "intent": "build"},
        ],
        sfx_events=[],
        use_profile_music=True,
        music_reason="The profile bed carries the clip.",
        plan_confidence=0.8,
    )


def _mock_agent(output):
    return SimpleNamespace(
        run=AsyncMock(return_value=SimpleNamespace(output=output))
    )


def _make_source(path: Path) -> None:
    """11 s source; each sentence band carries a distinct tone.

    [0,2) 300 Hz - [2,4.9) 500 Hz - [4.9,7.4) 900 Hz (hook sentence) -
    [7.4,11) 400 Hz.
    """
    command = ["ffmpeg", "-v", "error", "-y",
               "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=10:duration=11"]
    for freq, dur in ((300, 2.0), (500, 2.9), (900, 2.5), (400, 3.6)):
        command += ["-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate=44100:duration={dur}"]
    command += [
        "-filter_complex", "[1:a][2:a][3:a][4:a]concat=n=4:v=0:a=1[a]",
        "-map", "0:v", "-map", "[a]",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stderr


def _seed_transcript_cache(source: Path) -> None:
    payload = {
        "words": [
            {
                "text": word["text"],
                "start": int(word["start"] * 1000),
                "end": int(word["end"] * 1000),
                "confidence": 1.0,
                "speaker": None,
            }
            for word in WORDS
        ],
        "utterances": [],
    }
    source.with_suffix(".transcript_cache.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _dominant_freq(path: Path, start: float, end: float) -> float:
    raw = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-ss", str(start), "-i", str(path),
            "-t", str(end - start), "-vn", "-ac", "1", "-ar", "16000",
            "-f", "f32le", "-",
        ],
        capture_output=True, timeout=120,
    ).stdout
    samples = np.frombuffer(raw, dtype=np.float32)
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
    freqs = np.fft.rfftfreq(len(samples), d=1 / 16000)
    return float(freqs[int(np.argmax(spectrum))])


async def _render_clip(tmp_path, monkeypatch, draft):
    source = tmp_path / "source.mp4"
    _make_source(source)
    _seed_transcript_cache(source)
    monkeypatch.setattr(
        plan_service, "get_plan_agent", lambda: _mock_agent(output=draft)
    )
    (tmp_path / "out").mkdir(exist_ok=True)
    segment = {
        "start_time": "00:00",
        "end_time": "00:11",
        "text": " ".join(word["text"] for word in WORDS),
        "keep_ranges": [[0.0, 11.0]],
    }
    clip_info = await VideoService.create_single_clip(
        source, segment, 0, tmp_path / "out", niche_override="podcast_news"
    )
    assert clip_info is not None
    return clip_info


@pytestmark_real
@pytest.mark.asyncio
async def test_pipeline_cold_open_moves_hook_sentence_to_front(tmp_path, monkeypatch):
    clip_info = await _render_clip(tmp_path, monkeypatch, _draft())
    plan = clip_info["clip_plan"]
    assert plan is not None
    application = plan["hook_application"]
    assert application["status"] == "cold_opened"
    assert application["hook_timestamp_before"] == pytest.approx(5.6)
    assert application["hook_timestamp_after"] < 2.0
    assert plan["hook"]["timestamp"] < 2.0
    # The EXPORTED file opens on the hook sentence's 900 Hz tone.
    assert _dominant_freq(Path(clip_info["path"]), 0.15, 0.95) == pytest.approx(900, abs=45)


@pytestmark_real
@pytest.mark.asyncio
async def test_pipeline_disabled_enforcement_keeps_original_order(tmp_path, monkeypatch):
    monkeypatch.setenv("HOOK_ENFORCEMENT_ENABLED", "false")
    clip_info = await _render_clip(tmp_path, monkeypatch, _draft())
    plan = clip_info["clip_plan"]
    assert plan is not None
    assert "hook_application" not in plan
    assert plan["hook"]["timestamp"] == pytest.approx(5.6)
    # The exported file opens on the original first sentence (300 Hz).
    assert _dominant_freq(Path(clip_info["path"]), 0.15, 0.95) == pytest.approx(300, abs=45)


@pytestmark_real
@pytest.mark.asyncio
async def test_pipeline_punchline_hook_is_never_reordered(tmp_path, monkeypatch):
    clip_info = await _render_clip(
        tmp_path, monkeypatch, _draft(hook_type="punchline")
    )
    plan = clip_info["clip_plan"]
    assert plan["hook_application"]["status"] == "kept_original_context"
    assert _dominant_freq(Path(clip_info["path"]), 0.15, 0.95) == pytest.approx(300, abs=45)
