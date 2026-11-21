"""Regression: the tasks INSERT must never name a column twice.

On 2026-11-11 a user-machine install failed every task creation with
asyncpg DuplicateColumnError: two feature merges had each appended the
same field group (music_override, music_mode, target_clip_count,
source_duration_seconds, content_brief, campaign_id) to the raw INSERT
in TaskRepository.create_task, and the merge kept both copies. Unit
tests mock the DB session, so the statement was never executed anywhere
with a real engine before shipping. These tests pin the statement text
itself, and execute it against a real (SQLite) engine.
"""

import re
import sqlite3

from src.repositories.task_repository import TaskRepository


def _insert_sql() -> str:
    import inspect

    src = inspect.getsource(TaskRepository.create_task)
    match = re.search(r'(INSERT INTO tasks.*?RETURNING id)', src, re.S)
    assert match, "create_task INSERT statement not found"
    return match.group(1)


def _columns(sql: str) -> list[str]:
    match = re.search(r'INSERT INTO tasks \((.*?)\)\s*VALUES', sql, re.S)
    return [c.strip().lower() for c in match.group(1).replace("\n", " ").split(",") if c.strip()]


def test_tasks_insert_has_no_duplicate_columns():
    cols = _columns(_insert_sql())
    dups = sorted({c for c in cols if cols.count(c) > 1})
    assert not dups, f"duplicate columns in tasks INSERT: {dups}"


def test_tasks_insert_executes_against_a_real_engine():
    sql = _insert_sql()
    cols = _columns(sql)
    conn = sqlite3.connect(":memory:")
    col_defs = ", ".join(
        f"{c} TEXT" for c in cols if c not in ("created_at", "updated_at")
    )
    conn.execute(
        f"CREATE TABLE tasks ({col_defs}, created_at TEXT, updated_at TEXT)"
    )
    sqlite_sql = sql.replace("NOW()", "CURRENT_TIMESTAMP")
    sqlite_sql = re.sub(r"RETURNING id", "", sqlite_sql)
    params = {
        "task_id": "t-1",
        "user_id": "u-1",
        "source_id": "s-1",
        "status": "queued",
        "font_family": None,
        "font_size": None,
        "font_color": None,
        "caption_template": "default",
        "include_broll": 0,
        "processing_mode": "fast",
        "niche_override": None,
        "music_override": None,
        "music_mode": "auto",
        "template_mode": None,
        "framing_mode": None,
        "layout_template": None,
        "layout_credit": None,
        "hook_title_enabled": None,
        "broll_enabled": 1,
        "separation_opt_in": 0,
        "target_clip_count": 10,
        "source_duration_seconds": 2365,
        "content_brief": None,
        "campaign_id": None,
        "brand_kit_id": None,
        "intro_asset": None,
        "outro_asset": None,
        "creation_mode": "full_auto",
        "manual_ranges_json": None,
    }
    conn.execute(sqlite_sql, params)
    row = conn.execute("SELECT id, target_clip_count FROM tasks").fetchone()
    assert row == ("t-1", "10") or row[0] == "t-1"
