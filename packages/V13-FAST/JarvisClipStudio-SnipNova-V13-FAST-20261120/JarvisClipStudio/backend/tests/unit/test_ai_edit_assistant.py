"""AI edit assistant: op contract, service flow (mocked LLM), and routes.

The contract tests are pure: the schema must accept exactly the editor's
own operations and reject everything else wholesale. The service tests
inject a fake LLM callable — no live model is ever called — and assert
both directions of the fail-safe: a valid reply is dry-run and returned
for queueing, an invalid one leaves the clip completely untouched (the
service raises before any edit is built, and nothing is enqueued).
"""

import json

import pytest

from src.auto_director.ai_edit import (
    build_ai_edit_prompt,
    describe_ai_edit,
    parse_ai_edit_response,
    validate_ai_edit_payload,
)

SEGMENTS = [
    {"start": 0.0, "end": 5.0, "transition_in": "none", "transition_duration": 0.0},
    {"start": 5.0, "end": 10.0, "transition_in": "fade", "transition_duration": 0.4},
    {"start": 10.0, "end": 14.0, "transition_in": "cut", "transition_duration": 0.0},
]


# --- Contract: valid replies -------------------------------------------------


def test_valid_segment_operations_are_accepted():
    edit = parse_ai_edit_response(
        json.dumps(
            {
                "operations": [
                    {"type": "trim", "index": 0, "end": 3.0},
                    {"type": "delete", "index": 2},
                    {"type": "split", "index": 0, "at": 1.5},
                    {"type": "reorder", "order": [1, 0]},
                ]
            }
        )
    )
    assert [op["type"] for op in edit["operations"]] == [
        "trim",
        "delete",
        "split",
        "reorder",
    ]
    assert edit["operations"][0] == {"type": "trim", "index": 0, "end": 3.0}


def test_valid_captions_and_music_are_accepted():
    edit = validate_ai_edit_payload(
        {
            "captions": {"template": "hormozi", "font_size": 44, "position": "top"},
            "music_override": "phonk.mp3",
        }
    )
    assert edit["captions"]["template"] == "hormozi"
    assert edit["music_override"] == "phonk.mp3"


def test_fenced_json_is_unwrapped():
    edit = parse_ai_edit_response(
        '```json\n{"operations": [{"type": "delete", "index": 1}]}\n```'
    )
    assert edit["operations"] == [{"type": "delete", "index": 1}]


# --- Contract: everything else is rejected -----------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "Make the intro shorter please",  # prose, not JSON
        "",  # empty
        "[1, 2, 3]",  # JSON, but not an object
        "{}",  # an edit that changes nothing
        '{"operations": []}',  # still nothing
        '{"operations": [{"type": "zoom", "index": 0, "amount": 2}]}',  # no zoom op
        '{"operations": [{"type": "transition", "index": 1, "style": "fade"}]}',
        '{"operations": [{"type": "delete", "index": 0, "speed": 2}]}',  # extra field
        '{"operations": [{"type": "delete"}]}',  # missing index
        '{"operations": [{"type": "trim", "index": 0}]}',  # trim needs a bound
        '{"operations": [{"type": "trim", "index": "0", "end": 2}]}',  # index type
        '{"operations": [{"type": "split", "index": 0}]}',  # split needs at
        '{"operations": [{"type": "reorder", "order": "1,0"}]}',  # order type
        '{"operations": [{"type": "delete", "index": -1}]}',  # negative index
        '{"captions": {"font_size": 500}}',  # editor range check
        '{"captions": {"animation": "spin"}}',  # unknown caption field
        '{"music_override": 42}',  # music must be a filename string
        '{"niche_override": "gaming"}',  # not part of the AI contract
        '{"summary": "done", "operations": [{"type": "delete", "index": 0}]}',
    ],
)
def test_invalid_replies_are_rejected(raw):
    with pytest.raises(ValueError):
        parse_ai_edit_response(raw)


def test_prompt_describes_the_plan_and_instruction():
    prompt = build_ai_edit_prompt(
        {"segments": SEGMENTS, "captions": {"template": "hormozi"}, "niche": "gaming"},
        "Cut the middle part and make captions pop",
    )
    assert "Cut the middle part" in prompt
    assert "segment 1: 5.0s to 10.0s (5.0s)" in prompt
    assert "Total clip duration: 14.0s" in prompt
    assert "template=hormozi" in prompt
    assert "'gaming'" in prompt


# --- Summary: generated from the applied operations ---------------------------


def test_summary_names_what_actually_changed():
    operations = [
        {"type": "trim", "index": 0, "end": 3.0},
        {"type": "delete", "index": 2},
    ]
    summary = describe_ai_edit(
        SEGMENTS, operations, captions={"template": "hormozi"}, music_override=None
    )
    assert "Trimmed segment 1 from 5.0s to 3.0s" in summary
    assert "Removed segment 3 (4.0s)" in summary
    assert "Captions: template set to hormozi" in summary
    assert "Clip is now 8.0s (was 14.0s)" in summary


def test_summary_for_reorder_and_split():
    summary = describe_ai_edit(
        SEGMENTS,
        [
            {"type": "split", "index": 1, "at": 7.0},
            {"type": "reorder", "order": [3, 2, 1, 0]},
        ],
    )
    assert "Split segment 2 at 7.0s" in summary
    assert "Reordered the segments" in summary
    assert "Clip is now" not in summary  # length unchanged


# --- Service: mocked LLM, fail-safe both ways --------------------------------

from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.services.clip_ai_edit_service import (
    AiEditRejected,
    AiEditUnavailable,
    ClipAiEditService,
)


class StubPlanService:
    """Just enough TaskService for the assistant: the plan read.

    Anything else the assistant might try to call (edit_clip,
    rerender_clip) does not exist here, so a test fails loudly if the
    assistant ever touches the clip outside build_edit's contract.
    """

    def __init__(self, plan):
        self._plan = plan
        self.plan_calls = 0

    async def get_clip_edit_plan(self, task_id, clip_id):
        self.plan_calls += 1
        return {
            "clip_id": clip_id,
            "task_id": task_id,
            "persisted": True,
            "niche": "gaming",
            "plan": self._plan,
        }


def _fake_llm(reply):
    calls = []

    async def call(system_prompt, user_prompt):
        calls.append((system_prompt, user_prompt))
        if isinstance(reply, Exception):
            raise reply
        return reply

    call.calls = calls
    return call


def _plan():
    return {"version": 1, "niche": "gaming", "segments": [dict(s) for s in SEGMENTS]}


@pytest.mark.asyncio
async def test_service_returns_dry_run_edit_and_summary():
    llm = _fake_llm(
        '{"operations": [{"type": "delete", "index": 1}], '
        '"captions": {"template": "hormozi", "position": "top"}}'
    )
    service = ClipAiEditService(StubPlanService(_plan()), llm_call=llm)

    edit = await service.build_edit("task-1", "clip-1", "Drop the middle part")

    assert edit["operations"] == [{"type": "delete", "index": 1}]
    assert edit["captions"] == {"template": "hormozi", "position": "top"}
    assert "Removed segment 2 (5.0s)" in edit["summary"]
    assert "Clip is now 9.0s (was 14.0s)" in edit["summary"]
    system_prompt, user_prompt = llm.calls[0]
    assert "operations" in system_prompt
    assert "Drop the middle part" in user_prompt
    assert "segment 1: 5.0s to 10.0s" in user_prompt


@pytest.mark.asyncio
async def test_service_music_override_success(monkeypatch):
    monkeypatch.setattr(
        "src.services.clip_ai_edit_service.validate_music_override",
        lambda value: value,
    )
    llm = _fake_llm(
        '{"operations": [{"type": "trim", "index": 0, "end": 3.0}], '
        '"music_override": "phonk.mp3"}'
    )
    service = ClipAiEditService(StubPlanService(_plan()), llm_call=llm)

    edit = await service.build_edit("task-1", "clip-1", "Shorter intro, phonk music")

    assert edit["music_override"] == "phonk.mp3"
    assert "Music set to phonk.mp3" in edit["summary"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply",
    [
        "Sure! I made the intro shorter for you.",  # prose
        '{"operations": [{"type": "zoom", "index": 0}]}',  # unknown op
        '{"operations": [{"type": "trim", "index": 0, "end": 99.0}]}',  # cannot fit
        '{"operations": [{"type": "delete", "index": 9}]}',  # out of range
        '{"operations": [{"type": "delete", "index": 0}, {"type": "delete", "index": 0}, {"type": "delete", "index": 0}]}',  # deletes everything
        '{"captions": {"template": "no-such-template"}}',  # registry gate
        '{"music_override": "no-such-track.mp3"}',  # library gate
        "{}",  # nothing the editor can do
    ],
)
async def test_service_rejects_unusable_replies(reply):
    service = ClipAiEditService(
        StubPlanService(_plan()), llm_call=_fake_llm(reply)
    )
    with pytest.raises(AiEditRejected):
        await service.build_edit("task-1", "clip-1", "Do something")


@pytest.mark.asyncio
async def test_service_llm_failure_is_unavailable_not_a_rejection():
    service = ClipAiEditService(
        StubPlanService(_plan()),
        llm_call=_fake_llm(RuntimeError("provider down")),
    )
    with pytest.raises(AiEditUnavailable):
        await service.build_edit("task-1", "clip-1", "Trim it")


@pytest.mark.asyncio
async def test_service_rejects_empty_and_overlong_instructions():
    llm = _fake_llm("{}")
    service = ClipAiEditService(StubPlanService(_plan()), llm_call=llm)
    with pytest.raises(AiEditRejected):
        await service.build_edit("task-1", "clip-1", "   ")
    with pytest.raises(AiEditRejected):
        await service.build_edit("task-1", "clip-1", "x" * 601)
    assert llm.calls == []  # the model is never bothered


# --- Routes -------------------------------------------------------------------

from fastapi import HTTPException

from src.api.routes import tasks as task_routes

from test_clip_rerender import FakeRedis


def _request(body, queue_adapter):
    return SimpleNamespace(
        json=AsyncMock(return_value=body),
        headers={},
        app=SimpleNamespace(state=SimpleNamespace(queue_adapter=queue_adapter)),
    )


@pytest.fixture
def ai_route(monkeypatch):
    clip = {"id": "clip-1", "task_id": "task-1"}

    class StubClipRepo:
        async def get_clip_by_id(self, _db, _clip_id):
            return clip

    class StubTaskService:
        def __init__(self, _db):
            self.clip_repo = StubClipRepo()

    monkeypatch.setattr(task_routes, "TaskService", StubTaskService)
    monkeypatch.setattr(
        task_routes, "_require_task_owner", AsyncMock(return_value={})
    )
    redis = FakeRedis()
    queue = SimpleNamespace(
        enqueue_job=AsyncMock(return_value="job"),
        get_pool=AsyncMock(return_value=redis),
        get_job_status=AsyncMock(return_value="queued"),
    )
    monkeypatch.setattr(task_routes, "JobQueue", queue)
    return queue, redis


def _stub_ai_service(monkeypatch, *, result=None, error=None):
    class FakeAiService:
        def __init__(self, _task_service):
            pass

        async def build_edit(self, task_id, clip_id, instruction):
            if error is not None:
                raise error
            return result

    monkeypatch.setattr(task_routes, "ClipAiEditService", FakeAiService)


@pytest.mark.asyncio
async def test_ai_edit_route_queues_the_editor_job(monkeypatch, ai_route):
    queue, redis = ai_route
    _stub_ai_service(
        monkeypatch,
        result={
            "operations": [{"type": "delete", "index": 1}],
            "captions": {"template": "hormozi"},
            "music_override": None,
            "summary": "Removed segment 2 (5.0s).",
        },
    )

    result = await task_routes.ai_edit_clip(
        "task-1",
        "clip-1",
        _request({"instruction": "Drop the middle part"}, queue),
        db=None,
    )

    assert result["status"] == "queued"
    assert result["job_id"].startswith("clip-edit:clip-1:")
    assert result["summary"] == "Removed segment 2 (5.0s)."
    args, kwargs = queue.enqueue_job.call_args
    assert args[0] == "edit_clip_task"
    assert args[1:4] == ("task-1", "clip-1", [{"type": "delete", "index": 1}])
    assert args[4] == {"template": "hormozi"}
    assert args[5] is None  # niche_override: AI edits keep the clip's style
    assert kwargs["_job_id"] == result["job_id"]
    stored = json.loads(redis.store[f"clip_rerender:{result['job_id']}"])
    assert stored["status"] == "queued"
    assert stored["source"] == "ai-edit"
    assert stored["summary"] == "Removed segment 2 (5.0s)."


@pytest.mark.asyncio
async def test_ai_edit_route_rejection_queues_nothing(monkeypatch, ai_route):
    queue, _redis = ai_route
    _stub_ai_service(
        monkeypatch, error=AiEditRejected("not a usable edit")
    )
    with pytest.raises(HTTPException) as exc:
        await task_routes.ai_edit_clip(
            "task-1",
            "clip-1",
            _request({"instruction": "Add a zoom"}, queue),
            db=None,
        )
    assert exc.value.status_code == 422
    queue.enqueue_job.assert_not_called()


@pytest.mark.asyncio
async def test_ai_edit_route_llm_outage_is_502(monkeypatch, ai_route):
    queue, _redis = ai_route
    _stub_ai_service(
        monkeypatch, error=AiEditUnavailable("could not be reached")
    )
    with pytest.raises(HTTPException) as exc:
        await task_routes.ai_edit_clip(
            "task-1",
            "clip-1",
            _request({"instruction": "Trim it"}, queue),
            db=None,
        )
    assert exc.value.status_code == 502
    queue.enqueue_job.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{}, {"instruction": ""}, {"instruction": "  "}, {"instruction": 42}])
async def test_ai_edit_route_requires_an_instruction(monkeypatch, ai_route, body):
    queue, _redis = ai_route
    _stub_ai_service(monkeypatch, result={})
    with pytest.raises(HTTPException) as exc:
        await task_routes.ai_edit_clip("task-1", "clip-1", _request(body, queue), db=None)
    assert exc.value.status_code == 422
    queue.enqueue_job.assert_not_called()


# --- Real-ffmpeg proof: AI ops drive a real render ----------------------------

import shutil
import subprocess
from pathlib import Path

from src.auto_director.clip_edits import (
    apply_segment_operations,
    attach_source_shares,
    kept_source_ranges,
)
from src.media.ffmpeg import render_source_ranges_ffmpeg

from test_clip_edit_render import _colours_over_time, _make_marked_source

ffmpeg_missing = (
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None
)


def _duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "json", str(path),
        ],
        capture_output=True,
        timeout=120,
    )
    assert result.returncode == 0
    return float(json.loads(result.stdout.decode())["format"]["duration"])


@pytest.mark.skipif(ffmpeg_missing, reason="ffmpeg/ffprobe not installed")
@pytest.mark.asyncio
async def test_ai_edit_ops_render_the_edited_footage(tmp_path):
    """Mocked LLM -> validated ops -> editor applier -> real ffmpeg.

    The render step is the same range renderer the worker's clip path
    uses (render_source_ranges_ffmpeg), fed with the keep-ranges the
    editor's kept_source_ranges computes for the edited plan — the exact
    seam ClipService.edit_clip hands to the render.
    """
    _make_marked_source(tmp_path / "marked.mp4")
    ranges = [(0.0, 4.0), (4.0, 8.0), (8.0, 12.0)]
    plan_segments = attach_source_shares(
        [
            {"start": 0.0, "end": 4.0},
            {"start": 4.0, "end": 8.0},
            {"start": 8.0, "end": 12.0},
        ],
        ranges,
    )
    plan = {"version": 1, "niche": "podcast_news", "segments": plan_segments}

    llm = _fake_llm('{"operations": [{"type": "delete", "index": 1}]}')
    service = ClipAiEditService(StubPlanService(plan), llm_call=llm)
    edit = await service.build_edit(
        "task-1", "clip-1", "Remove the middle segment"
    )
    assert "Removed segment 2 (4.0s)" in edit["summary"]

    new_segments = apply_segment_operations(plan_segments, edit["operations"])
    kept = kept_source_ranges(new_segments, ranges)
    assert kept == [(0.0, 4.0), (8.0, 12.0)]

    output = tmp_path / "ai-edited.mp4"
    ok = render_source_ranges_ffmpeg(
        tmp_path / "marked.mp4", [tuple(r) for r in kept], output
    )
    assert ok is True and output.is_file()
    assert _duration(output) == pytest.approx(8.0, abs=0.6)
    # Green (the deleted middle) never appears; red then blue remain.
    assert _colours_over_time(output, 8) == ["red"] * 4 + ["blue"] * 4
