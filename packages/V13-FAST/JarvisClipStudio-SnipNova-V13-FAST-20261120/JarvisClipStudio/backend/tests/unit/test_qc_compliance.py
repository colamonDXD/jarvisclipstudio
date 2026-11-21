"""Per-niche compliance QC (Quality Director item 4).

The original QC judged only technical health (file, duration,
resolution, silence, duplicates). The Quality Director adds the
per-niche workflow contract, checked — not assumed — from what the
render actually did:

- hook_window: the hook enforcement outcome recorded on the clip's
  plan (context-preserved exceptions are flagged honestly, as warns).
- cut_rate: rendered segments/minute vs the profile's band.
- music_expectation: when the effective decision (user pick / profile
  / Auto bed) says music, silent gaps are an expectation FAILURE
  (warn-level, marked fixable) instead of a neutral note.
- caption_presence + caption_hold: measured from the caption timings
  the render actually burned.

A mechanically fixable failure (music missing, caption hold) earns
exactly one corrective re-render through the pipeline seam, then a
re-QC. QC still never fails a task.
"""

import json
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
import pytest

from src.clip_source_map import save_clip_render_facts
from src.media.clip_qc import (
    ClipQcContext,
    check_caption_pacing,
    check_cut_rate,
    check_hook_window,
    check_music,
    run_clip_qc,
)
from src.services import task_service as task_service_module
from src.services.task_service import TaskService


# --------------------------------------------------------------------------
# hook_window
# --------------------------------------------------------------------------


def _ctx(**kwargs):
    return ClipQcContext(**kwargs)


def test_hook_window_passes_for_cold_opened_and_leading():
    for status in ("cold_opened", "already_leading"):
        check = check_hook_window(
            _ctx(hook_outcome={"status": status, "hook_timestamp_after": 0.7, "reason": "x"})
        )
        assert check["status"] == "pass", status


def test_hook_window_warns_for_context_preserved_with_reason():
    check = check_hook_window(
        _ctx(
            hook_outcome={
                "status": "kept_original_context",
                "hook_timestamp_after": 5.6,
                "reason": "the hook sentence opens with 'he'",
            }
        )
    )
    assert check["status"] == "warn"
    assert "opens with 'he'" in check["detail"]


def test_hook_window_warns_when_no_hook_was_found():
    check = check_hook_window(
        _ctx(hook_outcome={"status": "no_hook", "reason": "planner found none"})
    )
    assert check["status"] == "warn"


def test_hook_window_warns_when_record_disagrees_with_timestamp():
    check = check_hook_window(
        _ctx(hook_outcome={"status": "cold_opened", "hook_timestamp_after": 4.2})
    )
    assert check["status"] == "warn"


def test_hook_window_skips_without_a_record():
    assert check_hook_window(_ctx())["status"] == "skip"


# --------------------------------------------------------------------------
# cut_rate
# --------------------------------------------------------------------------


def test_cut_rate_inside_band_passes():
    check = check_cut_rate(
        _ctx(cut_rate_band=(10.0, 15.0), rendered_segment_count=6), duration=30.0
    )
    assert check["status"] == "pass"
    assert check["evidence"]["cuts_per_min"] == 12.0


def test_cut_rate_outside_band_warns():
    check = check_cut_rate(
        _ctx(cut_rate_band=(10.0, 15.0), rendered_segment_count=30), duration=30.0
    )
    assert check["status"] == "warn"
    assert check["evidence"]["cuts_per_min"] == 60.0


def test_cut_rate_skips_without_band_or_count():
    assert check_cut_rate(_ctx(), duration=30.0)["status"] == "skip"
    assert (
        check_cut_rate(_ctx(cut_rate_band=(10.0, 15.0)), duration=30.0)["status"]
        == "skip"
    )


# --------------------------------------------------------------------------
# caption pacing
# --------------------------------------------------------------------------


def test_caption_pacing_passes_within_cap():
    context = _ctx(
        caption_timings=[{"start": 0.0, "end": 2.0}, {"start": 2.0, "end": 4.5}],
        caption_hold_cap_seconds=3.5,
    )
    presence, hold = check_caption_pacing(context, audio_silent=False)
    assert presence["status"] == "pass"
    assert hold["status"] == "pass"


def test_caption_hold_over_cap_warns_and_is_fixable():
    context = _ctx(
        caption_timings=[{"start": 0.0, "end": 2.0}, {"start": 2.0, "end": 11.5}],
        caption_hold_cap_seconds=3.5,
    )
    _presence, hold = check_caption_pacing(context, audio_silent=False)
    assert hold["status"] == "warn"
    assert hold["evidence"]["max_hold_seconds"] == 9.5
    assert hold["evidence"]["fixable"] is True


def test_caption_presence_warns_for_speech_without_captions():
    context = _ctx(caption_timings=[], caption_hold_cap_seconds=3.5)
    presence, _hold = check_caption_pacing(context, audio_silent=False)
    assert presence["status"] == "warn"
    # A genuinely silent clip has nothing to caption: pass, not warn.
    presence, _hold = check_caption_pacing(context, audio_silent=True)
    assert presence["status"] == "pass"


def test_caption_checks_skip_without_recorded_timings():
    presence, hold = check_caption_pacing(_ctx(), audio_silent=False)
    assert presence["status"] == "skip"
    assert hold["status"] == "skip"


# --------------------------------------------------------------------------
# music expectation failure
# --------------------------------------------------------------------------


def _levels(speech_db=-10.0, gap_db=-90.0):
    return np.array(
        [speech_db] * 40 + [gap_db] * 40 + [speech_db] * 40 + [gap_db] * 40,
        dtype=np.float32,
    )


def test_music_missing_is_an_expectation_failure_and_fixable():
    context = _ctx(music_expected=True, music_label="calm (auto bed)")
    check = check_music(_levels(), context)
    assert check["status"] == "warn"
    assert check["evidence"]["expectation_failed"] is True
    assert check["evidence"]["fixable"] is True


def test_music_present_in_gaps_passes_without_flags():
    context = _ctx(music_expected=True, music_label="calm (auto bed)")
    check = check_music(_levels(gap_db=-25.0), context)
    assert check["status"] == "pass"
    assert "expectation_failed" not in check["evidence"]


# --------------------------------------------------------------------------
# run_clip_qc integration + compliance inputs plumbing
# --------------------------------------------------------------------------


def _ffmpeg(*args: str) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", *args],
        check=True, capture_output=True, timeout=300,
    )


@pytest.fixture(scope="module")
def speech_clip(tmp_path_factory) -> Path:
    directory = tmp_path_factory.mktemp("qd-compliance")
    clip = directory / "speech.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=size=320x568:rate=10:duration=6",
        "-f", "lavfi", "-i",
        "aevalsrc=0.5*sin(2*PI*700*t)*lt(mod(t\\,3)\\,1.5):s=44100:d=6",
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", str(clip),
    )
    return clip


def test_run_clip_qc_report_carries_compliance_and_fixable(speech_clip):
    context = ClipQcContext(
        expected_duration=6.0,
        output_format="original",
        music_expected=True,
        music_label="calm (auto bed)",
        hook_outcome={
            "status": "cold_opened",
            "hook_timestamp_after": 0.7,
            "reason": "moved",
        },
        cut_rate_band=(10.0, 15.0),
        rendered_segment_count=1,  # 10/min over 6 s -> in band
        caption_timings=[{"start": 0.0, "end": 9.5}],
        caption_hold_cap_seconds=3.5,
    )
    report = run_clip_qc(speech_clip, context)
    names = {entry["name"]: entry for entry in report["checks"]}
    assert names["hook_window"]["status"] == "pass"
    assert names["cut_rate"]["status"] == "pass"
    assert names["caption_hold"]["status"] == "warn"
    assert names["music"]["status"] == "warn"
    assert names["music"]["evidence"]["expectation_failed"] is True
    assert report["fixable"] is True
    assert set(report["fixable_reasons"]) == {"music", "caption_hold"}
    assert report["hard_fail"] is False


def test_compliance_inputs_reads_plan_and_profile():
    clip = {
        "clip_plan": json.dumps(
            {
                "hook_application": {
                    "status": "cold_opened",
                    "hook_timestamp_after": 0.7,
                    "reason": "moved",
                }
            }
        )
    }
    profile = {
        "pacing": {"target_cuts_per_min": [10, 15]},
        "audio": {"music_mood": "none", "music_volume_0_to_1": 0.0},
    }
    inputs = TaskService._compliance_inputs(clip, profile)
    assert inputs["hook_outcome"]["status"] == "cold_opened"
    assert inputs["cut_rate_band"] == (10.0, 15.0)
    assert inputs["caption_hold_cap_seconds"] == 3.5

    empty = TaskService._compliance_inputs({}, None)
    assert empty["hook_outcome"] is None
    assert empty["cut_rate_band"] is None


# --------------------------------------------------------------------------
# Pipeline seam: a fixable failure earns exactly one corrective re-render
# --------------------------------------------------------------------------


class FakeClipRepo:
    def __init__(self, clip: dict):
        self.clip = clip
        self.qc_updates: list = []

    async def get_clip_by_id(self, _db, clip_id):
        if self.clip is None or self.clip["id"] != clip_id:
            return None
        return dict(self.clip)

    async def get_clips_by_task(self, _db, task_id):
        return [dict(self.clip)] if self.clip else []

    async def update_clip_qc(self, _db, clip_id, qc_status, qc_report):
        self.qc_updates.append((qc_status, qc_report))
        self.clip = {**self.clip, "qc_status": qc_status, "qc_report": qc_report}


@pytest.fixture
def seam(monkeypatch):
    @asynccontextmanager
    async def transaction(_db, _task_id, *, processing=False):
        yield

    monkeypatch.setattr(task_service_module, "task_edit_transaction", transaction)

    class _Storage:
        def local_path(self, reference, **_kwargs):
            return Path(reference)

    monkeypatch.setattr(task_service_module, "MediaStorage", _Storage)


def _clip_dict(path: Path, **extra) -> dict:
    clip = {
        "id": "clip-1",
        "task_id": "task-1",
        "file_path": str(path),
        "filename": path.name,
        "start_time": "00:00",
        "end_time": "00:06",
        "duration": 6.0,
        "text": "speech with gaps",
        "clip_order": 1,
        "niche_override": None,
    }
    clip.update(extra)
    return clip


@pytest.mark.asyncio
async def test_fixable_music_failure_triggers_one_corrective_rerender(
    seam, speech_clip, tmp_path
):
    # The clip as first rendered: speech with silent gaps, no bed, and a
    # caption chunk held for 9.5 s. Both failures are fixable.
    bad = tmp_path / "clip.mp4"
    bad.write_bytes(speech_clip.read_bytes())
    save_clip_render_facts(
        bad,
        {
            "segment_count": 1,
            "caption_chunks": [{"start": 0.0, "end": 9.5}],
            "caption_hold_cap_seconds": 3.5,
        },
    )
    service = TaskService(db=None)
    service.clip_repo = FakeClipRepo(_clip_dict(bad))
    rerender_calls = []

    async def fake_rerender(task_id, clip_id, niche_override, **kwargs):
        rerender_calls.append(clip_id)
        # The corrective re-render: same file, but the facts now show a
        # re-chunked caption track (the music bed is judged again from
        # the audio itself — the fake keeps the same audio, so music
        # stays flagged; the point here is the single attempt + re-QC).
        save_clip_render_facts(
            bad,
            {
                "segment_count": 1,
                "caption_chunks": [
                    {"start": 0.0, "end": 2.0},
                    {"start": 2.0, "end": 4.0},
                ],
                "caption_hold_cap_seconds": 3.5,
            },
        )
        return {}

    service.rerender_clip = fake_rerender
    await service._qc_persisted_clip(
        task_id="task-1",
        clip_id="clip-1",
        segment={"text": "speech with gaps"},
        clip_niche_override="podcast_news",
        project_niche_override=None,
        output_format="original",
        output_short_side=None,
        music_override="bed.mp3",
    )

    assert rerender_calls == ["clip-1"]  # exactly one corrective attempt
    assert len(service.clip_repo.qc_updates) == 2
    _status, second_report = service.clip_repo.qc_updates[-1]
    assert second_report["attempts"] == 2
    names = {entry["name"]: entry for entry in second_report["checks"]}
    # The caption hold was mechanically fixed by the re-render ...
    assert names["caption_hold"]["status"] == "pass"
    # ... while the still-missing music is flagged honestly, not looped.
    assert names["music"]["status"] == "warn"
