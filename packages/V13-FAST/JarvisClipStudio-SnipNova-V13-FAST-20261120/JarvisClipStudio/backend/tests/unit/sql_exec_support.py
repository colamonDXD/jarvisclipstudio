"""Support for raw-SQL statement-execution tests.

Repository unit tests mock the DB session, so the raw SQL strings in
``backend/src`` were historically never executed anywhere before
shipping -- that blind spot let a duplicated-column INSERT break all
task creation on a real Postgres install on 2026-11-11 (see
``test_task_insert_columns.py`` for that one statement's regression
test). The ``test_sql_exec_*`` modules close the gap for the creation
paths: each test extracts the *actual* statement text out of the
repository source (never a hand-copied paraphrase), normalises only
the Postgres constructs SQLite spells differently, and executes it
against an in-memory SQLite engine with minimal DDL derived from the
migrations.

Normalisation rules (kept deliberately tiny and mechanical):

- ``NOW()`` / ``clock_timestamp()`` -> ``CURRENT_TIMESTAMP``
- ``:param::type`` casts are stripped (SQLite has no ``::`` operator;
  ``CAST(:x AS jsonb)`` is replaced by the bare ``:x`` -- SQLite stores
  JSON as TEXT, and the cast would otherwise apply NUMERIC affinity
  and destroy the value; jsonb *merge semantics* of ``||`` remain a
  Postgres-only behaviour the SQLite run approximates as text
  concatenation, which still exercises the statement's control flow)
- ``CURRENT_TIMESTAMP - (:p * INTERVAL '1 second')`` ->
  ``datetime(CURRENT_TIMESTAMP, '-' || :p || ' seconds')`` (the reclaim
  statements' staleness comparison; same semantics, SQLite spelling)
- ``GREATEST`` / ``LEAST`` are registered as SQLite scalar functions
  on every ``fresh_conn`` connection, with Postgres semantics (NULL
  if any argument is NULL) -- the statement text is untouched, the
  harness merely supplies two Postgres built-ins SQLite lacks.

Anything else Postgres-specific (``make_interval``, ``FILTER``,
``FOR UPDATE SKIP LOCKED``, ``ANY(:array)``, ...) is *not* normalised
away: those statements get an explicit documented xfail instead of a
silent pass.

The UPDATE lane adds ``ExecSession``: an async session double whose
``execute`` runs the *real* statement (transpiled by ``to_sqlite``)
against a sync in-memory SQLite connection. Repository methods whose
SET clause is assembled at runtime (dict-driven updates) cannot be
covered by statement extraction, so their tests drive the real method
through this double and assert on the table afterwards. Row values in
columns named ``*_at`` are decoded from ISO text to ``datetime``,
emulating asyncpg's timestamptz decoding (repository row-serialisers
call ``.isoformat()`` on them).
"""

from __future__ import annotations

import ast
import re
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2] / "src"


def _module_constants(tree: ast.Module) -> dict[str, str]:
    """Module-level ``NAME = "..."`` string constants of a parsed file."""
    constants: dict[str, str] = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = node.value.value
    return constants


def _render(node: ast.AST, constants: dict[str, str]) -> str | None:
    """Render a string constant or an f-string whose interpolated names
    are all module-level string constants (the ``RETURNING {_COLUMNS}``
    idiom). Returns None when the node is neither."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append(str(value.value))
            elif (
                isinstance(value, ast.FormattedValue)
                and isinstance(value.value, ast.Name)
                and value.value.id in constants
            ):
                parts.append(constants[value.value.id])
            else:
                return None
        return "".join(parts)
    return None


def extract_statement(
    rel_path: str,
    func_name: str,
    needle: str,
    occurrence: int = 0,
    must_contain: tuple[str, ...] = (),
    must_not_contain: tuple[str, ...] = (),
) -> str:
    """Return the exact SQL text of a statement inside ``func_name``.

    ``rel_path`` is relative to ``backend/src``. Candidates are the
    string literals / resolvable f-strings in the function whose text
    contains ``needle`` (and every ``must_contain`` fragment, and none
    of ``must_not_contain``), in walk order; ``occurrence`` picks one.
    """
    path = SRC_ROOT / rel_path
    tree = ast.parse(path.read_text(encoding="utf-8"))
    constants = _module_constants(tree)
    matches: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != func_name:
            continue
        for sub in ast.walk(node):
            text = _render(sub, constants)
            if text is None or needle not in text:
                continue
            if any(fragment not in text for fragment in must_contain):
                continue
            if any(fragment in text for fragment in must_not_contain):
                continue
            if text not in matches:
                matches.append(text)
    assert len(matches) > occurrence, (
        f"statement {needle!r} occurrence {occurrence} not found in "
        f"{rel_path}::{func_name} (found {len(matches)})"
    )
    return matches[occurrence]


_CAST_RE = re.compile(r"::[A-Za-z_][A-Za-z0-9_.]*(\[\])?")
_JSONB_CAST_RE = re.compile(r"CAST\(\s*(:[A-Za-z_][A-Za-z0-9_]*)\s+AS\s+jsonb\s*\)", re.I)
_NOW_RE = re.compile(r"\bNOW\s*\(\s*\)")
_INTERVAL_SECONDS_RE = re.compile(
    r"CURRENT_TIMESTAMP\s*-\s*\(\s*:([A-Za-z_][A-Za-z0-9_]*)\s*\*\s*INTERVAL\s*'1 second'\s*\)"
)


def to_sqlite(sql: str) -> str:
    """Translate only the Postgres spellings SQLite cannot parse."""
    sql = sql.replace("clock_timestamp()", "CURRENT_TIMESTAMP")
    sql = _NOW_RE.sub("CURRENT_TIMESTAMP", sql)
    sql = _INTERVAL_SECONDS_RE.sub(
        r"datetime(CURRENT_TIMESTAMP, '-' || :\1 || ' seconds')", sql
    )
    sql = _JSONB_CAST_RE.sub(r"\1", sql)
    sql = _CAST_RE.sub("", sql)
    return sql


def _greatest(*args):
    return None if any(arg is None for arg in args) else max(args)


def _least(*args):
    return None if any(arg is None for arg in args) else min(args)


def fresh_conn(*ddl: str) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.create_function("GREATEST", -1, _greatest)
    conn.create_function("LEAST", -1, _least)
    for statement in ddl:
        conn.execute(statement)
    return conn


def execute(conn: sqlite3.Connection, sql: str, params: dict) -> sqlite3.Cursor:
    return conn.execute(to_sqlite(sql), params)


def count_rows(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# ---------------------------------------------------------------------------
# Method-level execution: an async session double that really executes.
# ---------------------------------------------------------------------------


def _decode_value(column: str, value):
    """Emulate asyncpg type decoding for timestamp columns: the
    repositories' row serialisers call ``.isoformat()`` on ``*_at``
    values, which on Postgres arrive as ``datetime`` objects."""
    if isinstance(value, str) and (column.endswith("_at") or column == "scheduled_for"):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return value
    return value


class ExecRow:
    """Attribute-access row, mirroring how repository ``_row``
    serialisers read SQLAlchemy rows (``row.id``, ``row.name``, ...)."""

    def __init__(self, values: dict):
        self._values = values

    def __getattr__(self, name: str):
        try:
            return self._values[name]
        except KeyError:
            raise AttributeError(name) from None

    def __getitem__(self, key):
        return self._values[key]

    def keys(self):
        return self._values.keys()


class ExecResult:
    """The slice of SQLAlchemy's ``Result`` the repositories use."""

    def __init__(self, cursor: sqlite3.Cursor):
        self.rowcount = cursor.rowcount
        self._rows: list[ExecRow] = []
        if cursor.description:
            columns = [col[0] for col in cursor.description]
            for raw in cursor.fetchall():
                values = {
                    col: _decode_value(col, val) for col, val in zip(columns, raw)
                }
                self._rows.append(ExecRow(values))
        self._index = 0

    def fetchone(self):
        if self._index >= len(self._rows):
            return None
        row = self._rows[self._index]
        self._index += 1
        return row

    def fetchall(self):
        rows = self._rows[self._index :]
        self._index = len(self._rows)
        return rows

    def all(self):
        return self.fetchall()

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        row = self.first()
        if row is None:
            return None
        return next(iter(row._values.values()))

    def scalar_one_or_none(self):
        return self.scalar()

    def mappings(self):
        return [dict(row._values) for row in self._rows]


class ExecSession:
    """An ``AsyncSession`` stand-in that executes statements for real.

    ``execute`` accepts a SQLAlchemy ``TextClause`` (``str()`` yields
    its SQL) or a plain string, transpiles it with ``to_sqlite`` and
    runs it on the wrapped sync connection. ``commit``/``rollback``
    map to the connection; ``begin_nested`` is a no-op context (the
    one caller uses it only to scope a single UPDATE). Seed and
    assert through ``session.conn`` with the sync helpers above.
    """

    def __init__(self, *ddl: str):
        self.conn = fresh_conn(*ddl)
        self.info: dict = {}

    async def execute(self, statement, params: dict | None = None) -> ExecResult:
        cursor = self.conn.execute(to_sqlite(str(statement)), dict(params or {}))
        return ExecResult(cursor)

    async def commit(self) -> None:
        self.conn.commit()

    async def rollback(self) -> None:
        self.conn.rollback()

    async def flush(self) -> None:
        pass

    @asynccontextmanager
    async def begin_nested(self):
        yield self

    async def close(self) -> None:
        self.conn.close()
