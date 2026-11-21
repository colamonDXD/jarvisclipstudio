"""Fallback-visibility tests for the P0 auto-niche fix (2026-11-10).

Before this fix the classification outcome existed only in server
logs: never persisted, never in an API response. These tests pin the
durable surface chosen for it:

- migration 20261110_0001 adds a ``classification`` TEXT column to
  tasks (JSON, the brief_report shape),
- TaskRepository.update_classification persists the outcome with the
  same missing-column tolerance as update_brief_report,
- the task-detail mapping parses it back into a dict,
- get_task_with_clips surfaces it as its own field.

(The warning log line itself is asserted by reading the task_service
source in TestWarningLine below — process_task end-to-end fakes live
in test_task_service.py and do not cover this seam.)
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.repositories.task_repository import TaskRepository

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "migrations"
    / "sql"
    / "20261110_0001_task_classification.sql"
)

CLASSIFICATION = {
    "niche": "podcast_news",
    "confidence": 0.0,
    "reasons": ["fallback"],
    "is_fallback": True,
    "evidence_status": "partial",
    "model_used": None,
    "last_error": "gateway 400",
    "attempts": [
        {"model": "openai:local-qwen", "stage": "run",
         "output_mode": "structured", "error": "gateway 400"}
    ],
}


class _FakeResult:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _RecordingDB:
    """Minimal AsyncSession double: records statements, supports the
    nested-transaction context manager the repository uses."""

    def __init__(self):
        self.executed = []

    def begin_nested(self):
        db = self

        class _Ctx:
            async def __aenter__(self):
                return db

            async def __aexit__(self, *exc):
                return False

        return _Ctx()

    async def execute(self, statement, params=None):
        self.executed.append((str(statement), params or {}))
        return _FakeResult()


class TestMigration:
    def test_migration_adds_classification_column(self):
        sql = MIGRATION.read_text()
        assert "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS classification TEXT" in sql

    def test_migration_has_no_semicolons_in_comments(self):
        for line in MIGRATION.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith("--"):
                assert ";" not in stripped, f"semicolon in comment: {line}"


class TestRepositoryPersistence:
    @pytest.mark.asyncio
    async def test_update_classification_writes_json(self, monkeypatch):
        committed = []

        async def _fake_commit(db):
            committed.append(db)

        monkeypatch.setattr(
            "src.repositories.task_repository.commit_unless_editing",
            _fake_commit,
        )
        db = _RecordingDB()
        await TaskRepository.update_classification(db, "task-1", CLASSIFICATION)
        assert len(db.executed) == 1
        sql, params = db.executed[0]
        assert "classification = :classification" in sql
        assert params["task_id"] == "task-1"
        assert json.loads(params["classification"]) == CLASSIFICATION
        assert committed == [db]

    def test_task_detail_mapping_parses_classification(self):
        row = SimpleNamespace(classification=json.dumps(CLASSIFICATION))
        assert TaskRepository._parsed_json(row.classification) == CLASSIFICATION
        # Both task mappings (detail + list) carry the parsed field.
        text = Path("src/repositories/task_repository.py").read_text()
        assert text.count('"classification": TaskRepository._parsed_json(') == 2


class TestTaskDetailSurfacing:
    @pytest.mark.asyncio
    async def test_get_task_with_clips_surfaces_classification(self, monkeypatch):
        import src.services.task_service as task_service_module

        service = task_service_module.TaskService.__new__(
            task_service_module.TaskService
        )
        task_row = {
            "id": "task-1",
            "status": "completed",
            "classification": dict(CLASSIFICATION),
            "stage_timings_json": None,
            "niche_override": None,
        }

        class _Repo:
            async def get_task_by_id(self, db, task_id):
                return dict(task_row)

        class _ClipRepo:
            async def get_clips_by_task(self, db, task_id):
                return []

        service.db = None
        service.task_repo = _Repo()
        service.clip_repo = _ClipRepo()

        async def _no_stale(task):
            return False

        async def _no_settings(task_id):
            return {}

        service._fail_stale_task = _no_stale
        service._load_task_source_settings = _no_settings

        task = await service.get_task_with_clips("task-1")
        assert task["classification"]["is_fallback"] is True
        assert task["classification"]["last_error"] == "gateway 400"

    @pytest.mark.asyncio
    async def test_get_task_with_clips_classification_defaults_none(
        self, monkeypatch
    ):
        import src.services.task_service as task_service_module

        service = task_service_module.TaskService.__new__(
            task_service_module.TaskService
        )

        class _Repo:
            async def get_task_by_id(self, db, task_id):
                return {"id": "task-2", "status": "completed",
                        "stage_timings_json": None, "niche_override": None}

        class _ClipRepo:
            async def get_clips_by_task(self, db, task_id):
                return []

        service.db = None
        service.task_repo = _Repo()
        service.clip_repo = _ClipRepo()

        async def _no_stale(task):
            return False

        async def _no_settings(task_id):
            return {}

        service._fail_stale_task = _no_stale
        service._load_task_source_settings = _no_settings

        task = await service.get_task_with_clips("task-2")
        assert task["classification"] is None


class TestWarningLine:
    def test_task_service_logs_the_exact_fallback_warning(self):
        text = Path("src/services/task_service.py").read_text()
        assert (
            "Auto niche detection FAILED — default Podcast & news "
            "profile used" in text
        )
        # Persistence sits in the same block as the warning.
        assert "update_classification" in text
