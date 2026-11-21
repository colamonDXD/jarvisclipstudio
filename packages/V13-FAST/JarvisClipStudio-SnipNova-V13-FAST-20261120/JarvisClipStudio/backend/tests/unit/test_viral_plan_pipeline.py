"""Viral Intelligence Engine v1: pipeline seam, persistence, legacy guard.

The seam is exercised for real: EDLs are built by the production builder,
the planner's LLM is the only thing mocked, clip records go through the
real repository SQL (against a fake session that captures parameters),
and the end-to-end test renders a real file through create_single_clip.
"""

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.auto_director import plan_service
from src.auto_director.plan_service import ClipPlanDraft
from src.services.video_service import VideoService


SEGMENT = {
    "id": "seg-1",
    "start_time": "00:00",
    "end_time": "00:20",
    "text": "Nobody believes this part. The answer nobody expected was hiding in the logs.",
    "virality_score": 82,
    "hook_score": 20,
}

WORDS = [
    {"word": "Nobody", "start": 0.0, "end": 0.4},
    {"word": "believes", "start": 0.4, "end": 0.9},
    {"word": "this", "start": 0.9, "end": 1.2},
    {"word": "part.", "start": 1.2, "end": 1.8},
]


def _draft() -> ClipPlanDraft:
    return ClipPlanDraft(
        hook_type="punchline",
        hook_timestamp=4.5,
        hook_confidence=0.8,
        hook_reason="The answer nobody expected is the turn in the clip.",
        punch_zoom="subtle",
        first_caption_emphasis=True,
        sfx_accent=None,
        opening_at_offset=4.5,
        opening_reason="The deadpan answer lands right at the start of the clip.",
        transition_choices=[
            {
                "name": "smoothleft",
                "at_offset": 6.6,
                "reason": "The topic shifts from disbelief to the reveal at this join.",
            }
        ],
        caption_style_name="mrbeast",
        caption_style_reason="Short punchy lines suit the joke-dense delivery.",
        phases=[
            {"start_offset": 0.0, "end_offset": 4.5, "intent": "context"},
            {"start_offset": 4.5, "end_offset": 20.0, "intent": "payoff"},
        ],
        sfx_events=[
            {"at_offset": 5.0, "name": "impact", "reason": "the reveal lands"},
            {"at_offset": 9.0, "name": "pop", "reason": "beat after the reveal"},
        ],
        use_profile_music=True,
        music_reason="Meme comedy carries no profile music.",
        plan_confidence=0.75,
    )


def _mock_agent(output=None, error=None):
    if error is not None:
        return SimpleNamespace(run=AsyncMock(side_effect=error))
    return SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(output=output)))


def _build_edl():
    edl = VideoService.build_pipeline_edl(
        SEGMENT, WORDS, None, None, "meme_comedy", duration=20.0
    )
    assert edl is not None
    return edl


class TestSeam:
    @pytest.mark.asyncio
    async def test_planned_sfx_merge_into_edl(self, monkeypatch, tmp_path):
        monkeypatch.setattr(plan_service, "get_plan_agent", lambda: _mock_agent(output=_draft()))
        edl = _build_edl()
        # meme_comedy's only native event is its signature transition's
        # mapped SFX (the zoomin at the 3.33s join plays a whoosh at
        # its rendered boundary); its peak events need energy peaks.
        assert edl["sfx_events"] == [
            {"name": "whoosh", "at_sec": 3.183, "gain_db": -6.0}
        ]
        plan = await VideoService._apply_viral_plan(
            edl,
            video_path=tmp_path / "missing.mp4",
            segment=SEGMENT,
            text=SEGMENT["text"],
            words=WORDS,
            profile_name="meme_comedy",
            source_start=0.0,
            source_end=20.0,
        )
        assert plan is not None
        assert plan["source"] == "llm"
        assert plan["niche"] == "meme_comedy"
        assert plan["hook"]["type"] == "punchline"
        merged = [(e["name"], e["at_sec"], e["gain_db"]) for e in edl["sfx_events"]]
        # The plan's events are re-timed onto the rendered timeline:
        # the signature crossfade (0.3s at the 3.33s join) and the
        # draft's smoothleft crossfade (0.4s at the 6.67s join) pull
        # rendered content earlier, so the impact planned at 5.0s sits
        # at 4.7s and the pop planned at 9.0s at 8.3s — the same
        # content moments, on the rendered timeline the EDL speaks.
        # The signature's native whoosh rides along in front of them.
        assert merged == [
            ("whoosh", 3.183, -6.0),
            ("impact", 4.7, 0.0),
            ("pop", 8.3, -3.0),
        ]

    @pytest.mark.asyncio
    async def test_disabled_leaves_edl_byte_identical(self, monkeypatch, tmp_path):
        monkeypatch.setenv("VIRAL_PLAN_ENABLED", "false")
        reference = json.dumps(_build_edl(), sort_keys=True)
        edl = _build_edl()
        plan = await VideoService._apply_viral_plan(
            edl,
            video_path=tmp_path / "missing.mp4",
            segment=SEGMENT,
            text=SEGMENT["text"],
            words=WORDS,
            profile_name="meme_comedy",
            source_start=0.0,
            source_end=20.0,
        )
        assert plan is None
        assert json.dumps(edl, sort_keys=True) == reference

    @pytest.mark.asyncio
    async def test_plan_transitions_punch_and_caption_style_are_consumed(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(plan_service, "get_plan_agent", lambda: _mock_agent(output=_draft()))
        edl = _build_edl()
        # meme_comedy: 6 pacing segments over 20s, boundaries every ~3.33s;
        # the draft's choice at 6.6s targets the join at 6.67s (segment 2).
        assert edl["segments"][2]["transition_in"] == "cut"
        # No plan punch yet — the only zoom keyframe on a fresh EDL is
        # the retention lane's opening push-in (2026-11-15), which every
        # Auto clip now carries: a ken-burns ramp over the first 2s.
        assert [k["type"] for k in edl["zoom_keyframes"]] == ["ken_burns"]
        ramp = edl["zoom_keyframes"][0]
        assert ramp["trigger"] == "opening_punch"
        assert ramp["start"] == 0.0
        assert ramp["end"] == pytest.approx(2.0)
        assert ramp["scale_start"] == 1.0
        assert ramp["scale_end"] == pytest.approx(1.12)
        plan = await VideoService._apply_viral_plan(
            edl,
            video_path=tmp_path / "missing.mp4",
            segment=SEGMENT,
            text=SEGMENT["text"],
            words=WORDS,
            profile_name="meme_comedy",
            source_start=0.0,
            source_end=20.0,
        )
        assert plan is not None
        assert plan["source"] == "llm"
        # (a) the transition choice landed on the targeted join, with a
        # visible crossfade duration replacing the profile's cut length.
        assert edl["segments"][2]["transition_in"] == "smoothleft"
        assert edl["segments"][2]["transition_duration"] == pytest.approx(0.4)
        # (b) the opening punch zoom joined the EDL's zoom keyframes at
        # the hook offset, scaled by the plan's subtle level.
        punches = [k for k in edl["zoom_keyframes"] if k["type"] == "punch_in"]
        assert len(punches) == 1
        assert punches[0]["time"] == pytest.approx(4.5)
        assert punches[0]["scale"] == pytest.approx(1.25)
        # (c) the caption style decision is recorded on the EDL and plan.
        assert edl["captions"]["preset_id"] == "mrbeast"
        assert plan["caption_style"] == {
            "name": "mrbeast",
            "reason": "Short punchy lines suit the joke-dense delivery.",
        }
        assert plan["transition_choices"][0]["name"] == "smoothleft"

    @pytest.mark.asyncio
    async def test_fallback_plan_never_consumes_into_edl(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            plan_service, "get_plan_agent", lambda: _mock_agent(error=RuntimeError("down"))
        )
        reference = _build_edl()
        edl = _build_edl()
        plan = await VideoService._apply_viral_plan(
            edl,
            video_path=tmp_path / "missing.mp4",
            segment=SEGMENT,
            text=SEGMENT["text"],
            words=WORDS,
            profile_name="meme_comedy",
            source_start=0.0,
            source_end=20.0,
        )
        assert plan is not None
        assert plan["source"] == "fallback"
        # The fallback plan describes today's behaviour; consuming it
        # would change renders exactly when the planner is down.
        assert edl["segments"] == reference["segments"]
        assert edl["zoom_keyframes"] == reference["zoom_keyframes"]
        assert edl["captions"] == reference["captions"]

    @pytest.mark.asyncio
    async def test_llm_failure_keeps_native_edl_and_records_fallback(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            plan_service, "get_plan_agent", lambda: _mock_agent(error=RuntimeError("down"))
        )
        edl = _build_edl()
        native = list(edl["sfx_events"])
        plan = await VideoService._apply_viral_plan(
            edl,
            video_path=tmp_path / "missing.mp4",
            segment=SEGMENT,
            text=SEGMENT["text"],
            words=WORDS,
            profile_name="meme_comedy",
            source_start=0.0,
            source_end=20.0,
        )
        assert plan is not None
        assert plan["source"] == "fallback"
        assert edl["sfx_events"] == native


class _FakeResult:
    def __init__(self, *, scalar=None, rows=None, first=None):
        self._scalar = scalar
        self._rows = rows or []
        self._first = first

    def scalar(self):
        return self._scalar

    def fetchall(self):
        return self._rows

    def mappings(self):
        return SimpleNamespace(first=lambda: self._first)


class _FakeSession:
    """Minimal AsyncSession stand-in: queued results + captured params."""

    def __init__(self, results):
        self._results = list(results)
        self.calls: list[dict] = []

    @asynccontextmanager
    async def begin_nested(self):
        yield

    async def execute(self, _statement, params=None):
        self.calls.append(params or {})
        return self._results.pop(0)


class TestRepositoryPlumbing:
    @pytest.mark.asyncio
    async def test_create_clip_persists_plan_and_readback_serves_hook_report(self, monkeypatch):
        from src.repositories import clip_repository
        from src.repositories.clip_repository import ClipRepository

        monkeypatch.setattr(
            clip_repository, "persist_clip", AsyncMock(return_value="/tmp/clip.mp4")
        )
        plan = {
            "niche": "meme_comedy",
            "hook": {
                "type": "punchline",
                "timestamp": 4.5,
                "confidence": 0.8,
                "reason": "The answer nobody expected is the turn in the clip.",
            },
            "opening_treatment": {
                "punch_zoom": "subtle",
                "first_caption_emphasis": True,
                "sfx_accent": None,
            },
            "phases": [],
            "sfx_events": [],
            "music_decision": {"use_profile_music": True, "reason": "default"},
            "plan_confidence": 0.75,
            "source": "llm",
        }
        session = _FakeSession([_FakeResult(first=None), _FakeResult(scalar="clip-123")])
        clip_id = await ClipRepository.create_clip(
            session,
            task_id="task-1",
            filename="clip.mp4",
            file_path="/tmp/clip.mp4",
            start_time="00:00",
            end_time="00:20",
            duration=20.0,
            text="text",
            relevance_score=0.5,
            reasoning="",
            clip_order=1,
            clip_plan=plan,
        )
        assert clip_id == "clip-123"
        insert_params = session.calls[-1]
        assert json.loads(insert_params["clip_plan"]) == plan

        row = SimpleNamespace(
            id="clip-123",
            filename="clip.mp4",
            file_path="/tmp/clip.mp4",
            start_time="00:00",
            end_time="00:20",
            duration=20.0,
            text="text",
            relevance_score=0.5,
            reasoning="",
            clip_order=1,
            created_at=datetime.now(timezone.utc),
            virality_score=82,
            hook_score=20,
            engagement_score=0,
            value_score=0,
            shareability_score=0,
            hook_type=None,
            hook_title=None,
            niche_override=None,
            qc_status=None,
            qc_report=None,
            clip_plan=json.dumps(plan),
        )
        read_session = _FakeSession([_FakeResult(rows=[row])])
        clips = await ClipRepository.get_clips_by_task(read_session, "task-1")
        assert len(clips) == 1
        assert json.loads(clips[0]["clip_plan"]) == plan
        report = clips[0]["hook_report"]
        assert report["type"] == "punchline"
        assert report["timestamp"] == 4.5
        assert report["why"] == "The answer nobody expected is the turn in the clip."
        assert report["confidence"] == 0.8
        assert "subtle punch zoom" in report["opening_strategy"]


class TestCreateSingleClipEndToEnd:
    @pytest.mark.asyncio
    async def test_render_carries_plan(self, monkeypatch, tmp_path):
        import subprocess

        source = tmp_path / "source.mp4"
        subprocess.run(
            [
                "ffmpeg", "-v", "error", "-y",
                "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=10",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
                "-filter_complex", "[1:a]volume=0.4[a]",
                "-map", "0:v", "-map", "[a]",
                "-t", "12",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                str(source),
            ],
            check=True,
            capture_output=True,
            timeout=300,
        )
        monkeypatch.setattr(plan_service, "get_plan_agent", lambda: _mock_agent(output=_draft()))
        (tmp_path / "out").mkdir()
        segment = {
            "start_time": "00:00",
            "end_time": "00:12",
            "text": SEGMENT["text"],
            "hook_title": "The answer nobody expected",
        }
        clip_info = await VideoService.create_single_clip(
            source,
            segment,
            0,
            tmp_path / "out",
            niche_override="meme_comedy",
        )
        assert clip_info is not None
        assert Path(clip_info["path"]).exists()
        plan = clip_info["clip_plan"]
        assert plan is not None
        assert plan["source"] == "llm"
        assert plan["hook"]["type"] == "punchline"
