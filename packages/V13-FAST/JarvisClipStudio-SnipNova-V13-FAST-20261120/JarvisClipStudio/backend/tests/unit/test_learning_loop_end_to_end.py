"""Learning loop, end to end through the REAL repository and service.

The neighbouring suites each stop one layer short of the full loop:

- ``test_learning_engine.py`` proves the aggregation rules and the
  ranking flip from hand-built event dicts (pure functions, no storage).
- ``test_learning_api.py`` proves the service glue and the routes
  against an in-memory FakeLearningRepository (no SQL ever executes).
- ``test_sql_exec_creation_misc.py`` executes the repository's INSERT
  statements in isolation (no round-trip, no service, no ranking).

This module closes the remaining gap: explicit feedback submitted
through ``learning_service.record_explicit_feedback`` (the exact call
the clip feedback route makes) is stored by the REAL
``LearningRepository`` — its actual SQL executed against SQLite via
``sql_exec_support.ExecSession`` — the adjustment document is resolved
back through ``resolve_learning_document`` (the exact entry point
``task_service`` uses per task), and the resolved weights are shown to
flip the pick order of ``ai.dedupe_overlapping_segments`` on a fixed
candidate fixture, before vs after. Rollback through the real service
restores the base order, and a second user's selection is untouched.

One harness adapter is needed: ``ExecResult.mappings()`` returns a
plain list, while ``LearningRepository`` calls ``.mappings().all()`` /
``.mappings().first()``. ``_LearningSession`` wraps the result to
supply that slice; every statement still executes for real.
"""

import json

from src.ai import TranscriptSegment, ViralityAnalysis, dedupe_overlapping_segments
from src.auto_director import learning
from src.services import learning_service
from tests.unit.sql_exec_support import ExecSession


# ---------------------------------------------------------------------------
# Harness: ExecSession + the mappings().all()/first() result slice
# ---------------------------------------------------------------------------


class _Mappings:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _Result:
    def __init__(self, inner):
        self._inner = inner

    def mappings(self):
        return _Mappings(self._inner.mappings())


class _LearningSession:
    """AsyncSession stand-in: real SQL via ExecSession, learning-shaped results."""

    def __init__(self, *ddl):
        self._inner = ExecSession(*ddl)
        self.conn = self._inner.conn

    async def execute(self, statement, params=None):
        return _Result(await self._inner.execute(statement, params))

    async def commit(self):
        await self._inner.commit()

    async def rollback(self):
        await self._inner.rollback()


# DDL mirrors src/migrations/sql/20261108_0003_learning_engine.sql
# (SQLite spelling: TIMESTAMPTZ -> TEXT, same columns and defaults).
EVENTS_DDL = """
CREATE TABLE learning_feedback_events (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    clip_id TEXT NOT NULL,
    niche TEXT,
    verdict TEXT NOT NULL,
    reason TEXT,
    source TEXT NOT NULL DEFAULT 'explicit',
    weight REAL NOT NULL DEFAULT 1.0,
    features TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

VERSIONS_DDL = """
CREATE TABLE learning_versions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    adjustments TEXT NOT NULL DEFAULT '{}',
    event_count INTEGER NOT NULL DEFAULT 0,
    summary TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, version)
)
"""


def _session():
    return _LearningSession(EVENTS_DDL, VERSIONS_DDL)


# ---------------------------------------------------------------------------
# Fixtures (same shapes as test_learning_api.py / test_learning_engine.py)
# ---------------------------------------------------------------------------

TASK = {
    "id": "task-1",
    "user_id": "user-1",
    "caption_template": "default",
    "niche_override": "meme_comedy",
    "music_override": None,
}


def _clip(clip_id):
    return {
        "id": clip_id,
        "task_id": "task-1",
        "virality_score": 72,
        "hook_score": 12,
        "engagement_score": 20,
        "value_score": 20,
        "shareability_score": 20,
        "hook_type": "statement",
        "relevance_score": 0.9,
        "niche_override": None,
    }


def _segment(start, end, *, hook, engagement, value, shareability, relevance):
    return TranscriptSegment(
        start_time=start,
        end_time=end,
        text=f"segment {start}",
        relevance_score=relevance,
        virality=ViralityAnalysis(
            hook_score=hook,
            engagement_score=engagement,
            value_score=value,
            shareability_score=shareability,
            total_score=hook + engagement + value + shareability,
        ),
    )


def _candidates():
    # Same plain total (72). The balanced segment wins the unlearned
    # ranking on relevance; the hook-heavy one is what a learned hook
    # weight must promote. Non-overlapping windows: order is the signal.
    high_hook = _segment(
        "00:00", "00:30", hook=24, engagement=16, value=16, shareability=16,
        relevance=0.5,
    )
    balanced = _segment(
        "01:00", "01:30", hook=12, engagement=20, value=20, shareability=20,
        relevance=0.9,
    )
    return [balanced, high_hook]


def _order(segments, weights=None):
    ranked = dedupe_overlapping_segments(segments, score_weights=weights)
    return [segment.start_time for segment in ranked]


BASE_ORDER = ["01:00", "00:00"]
LEARNED_ORDER = ["00:00", "01:00"]
LEARNED_WEIGHTS = {
    "hook_score": 1.15,
    "engagement_score": 1.0,
    "value_score": 1.0,
    "shareability_score": 1.0,
}


async def _record_hook_rejects(session, user_id, count):
    """Explicit hook_weak rejects through the real service + repository."""
    results = []
    for index in range(count):
        results.append(
            await learning_service.record_explicit_feedback(
                session,
                user_id=user_id,
                task=TASK,
                clip=_clip(f"clip-{index + 1}"),
                verdict="reject",
                reason="hook_weak",
            )
        )
    return results


async def _resolved_weights(session, user_id, niche="meme_comedy"):
    document = await learning_service.resolve_learning_document(session, user_id)
    return document, learning.score_weights_for(document, niche)


# ---------------------------------------------------------------------------
# The loop: feedback -> real storage -> resolve -> flipped selection
# ---------------------------------------------------------------------------


async def test_feedback_through_real_repository_flips_next_selection():
    session = _session()

    # Before any feedback: no document, no weights, base pick order.
    document, weights = await _resolved_weights(session, "user-1")
    assert document is None
    assert weights is None
    assert _order(_candidates()) == BASE_ORDER

    # Eight explicit rejects citing weak hooks (the engine's >=6 step
    # promises a +0.15 hook weight for the clip's niche).
    results = await _record_hook_rejects(session, "user-1", 8)
    assert all(result["recorded"] for result in results)
    assert results[-1]["learning_version"] == 8

    # The events really landed in storage, with their feature snapshots.
    rows = session.conn.execute(
        "SELECT niche, verdict, reason, source, weight, features "
        "FROM learning_feedback_events WHERE user_id = 'user-1'"
    ).fetchall()
    assert len(rows) == 8
    assert {row[0] for row in rows} == {"meme_comedy"}
    assert {row[1] for row in rows} == {"reject"}
    assert {row[2] for row in rows} == {"hook_weak"}
    assert {row[3] for row in rows} == {"explicit"}
    assert {row[4] for row in rows} == {1.0}
    snapshot = json.loads(rows[0][5])
    assert snapshot["hook_score"] == 12
    assert snapshot["caption_preset_id"] == "large_centre_pop"

    # Resolved through the pipeline's own entry point, the document now
    # carries exactly the promised bounded weight — and the next
    # selection's pick order flips.
    document, weights = await _resolved_weights(session, "user-1")
    assert document["version"] == 8
    assert document["niches"] == {"meme_comedy": {"score_weights": {"hook_score": 1.15}}}
    assert weights == LEARNED_WEIGHTS
    assert _order(_candidates(), weights) == LEARNED_ORDER


async def test_rollback_through_real_repository_restores_base_selection():
    session = _session()
    await _record_hook_rejects(session, "user-1", 8)

    _document, weights = await _resolved_weights(session, "user-1")
    assert _order(_candidates(), weights) == LEARNED_ORDER

    # Version 1 is the document the first verdict produced: one event,
    # below every threshold, so its adjustments are empty. Rolling back
    # to it appends a NEW version (history is never rewritten).
    rolled = await learning_service.rollback_learning(session, "user-1", 1)
    assert rolled["version"] == 9
    assert rolled["niches"] == {}
    version_rows = session.conn.execute(
        "SELECT COUNT(*) FROM learning_versions WHERE user_id = 'user-1'"
    ).fetchone()
    assert version_rows[0] == 9

    document, weights = await _resolved_weights(session, "user-1")
    assert document["version"] == 9
    assert weights is None
    assert _order(_candidates(), weights) == BASE_ORDER


async def test_feedback_is_scoped_to_the_recording_user():
    session = _session()
    await _record_hook_rejects(session, "user-1", 8)

    # The other user has no document and base weights, even though the
    # shared tables now hold eight events.
    document, weights = await _resolved_weights(session, "user-2")
    assert document is None
    assert weights is None
    assert _order(_candidates(), weights) == BASE_ORDER

    status = await learning_service.learning_status(session, "user-2")
    assert status["event_count"] == 0
    assert status["adjustments"] == {}
    assert status["current_version"] is None


async def test_below_threshold_feedback_changes_nothing_end_to_end():
    session = _session()
    results = await _record_hook_rejects(session, "user-1", 2)
    assert all(result["recorded"] for result in results)

    # Two weighted rejects are below the smallest step (3.0): a document
    # exists and advances, but it carries no adjustment and selection
    # behaves exactly as the pre-learning pipeline.
    document, weights = await _resolved_weights(session, "user-1")
    assert document is not None
    assert document["niches"] == {}
    assert weights is None
    assert _order(_candidates(), weights) == BASE_ORDER


async def test_status_reports_real_storage_state():
    session = _session()
    await _record_hook_rejects(session, "user-1", 8)

    status = await learning_service.learning_status(session, "user-1")
    assert status["enabled"] is True
    assert status["current_version"] == 8
    assert status["event_count"] == 8
    assert status["adjustments"] == {
        "meme_comedy": {"score_weights": {"hook_score": 1.15}}
    }
    evidence = status["evidence"]["meme_comedy"]
    assert evidence["events"] == 8
    assert evidence["reject_weight"] == 8.0
    assert evidence["reason_reject_weights"] == {"hook_weak": 8.0}
    assert len(status["versions"]) == 8
    assert status["versions"][-1]["event_count"] == 8
