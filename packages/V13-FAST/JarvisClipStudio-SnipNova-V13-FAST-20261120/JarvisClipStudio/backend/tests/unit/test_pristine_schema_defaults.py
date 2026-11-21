"""Regression: every migration-added NOT NULL DEFAULT column the ORM also
defines must carry a matching server_default.

init_db() runs Base.metadata.create_all BEFORE the SQL migrations
(src/database.py). On a pristine DB a migration's
``ADD COLUMN IF NOT EXISTS`` is therefore a no-op for any column the ORM
already defines, and the ORM definition is the only source of that
column's server default. tasks.separation_opt_in shipped with a
Python-side ``default=False`` but no ``server_default`` (models.py), so
on a pristine DB the column was NOT NULL with no default and every raw
INSERT that omitted it failed with NotNullViolationError — the editor
roundtrip suite failed 2/2 on a pristine base (da441ba), while upgraded
DBs were fine because the migration had applied DEFAULT FALSE there.

This test pins the invariant for the whole tree so the class cannot
recur: for every ``ADD COLUMN ... NOT NULL DEFAULT ...`` in the SQL
migrations, if the column exists on an ORM table it must have a
server_default, and when that default is a literal it must match the
migration's default (modulo case/quotes). Columns no ORM table defines
(e.g. media_deletions) are migration-only: create_all never creates
them, so their migration always applies and needs no ORM default.
"""

import re
from pathlib import Path

import src.database as database_module
import src.models  # noqa: F401 - registers the ORM tables on Base.metadata

MIGRATIONS_DIR = Path(database_module.__file__).parent / "migrations" / "sql"

_ADD_COLUMN_RE = re.compile(
    r"ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+"
    r"(?:\"(?P<qname>[^\"]+)\"|(?P<name>[A-Za-z_][A-Za-z0-9_]*))"
    r"\s+(?P<body>[^;]+)",
    re.IGNORECASE,
)


def _migration_not_null_defaults() -> list[tuple[str, str, str]]:
    """(migration file, column, default expression) for every ADD COLUMN
    that declares NOT NULL with a DEFAULT."""
    found = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        for match in _ADD_COLUMN_RE.finditer(path.read_text()):
            body = match.group("body")
            if "NOT NULL" not in body.upper():
                continue
            default = re.search(
                r"DEFAULT\s+(.+?)\s*$", body.strip(), re.IGNORECASE | re.S
            )
            if not default:
                continue
            column = (match.group("qname") or match.group("name")).lower()
            found.append((path.name, column, default.group(1).strip()))
    return found


def _orm_columns() -> dict[str, object]:
    columns = {}
    for table in database_module.Base.metadata.tables.values():
        for column in table.columns:
            columns.setdefault(column.name.lower(), column)
    return columns


def _normalise(expr: str) -> str:
    return re.sub(r"[\s'\"]", "", expr).lower()


def test_migration_not_null_defaults_have_orm_server_default():
    migrations = _migration_not_null_defaults()
    # Guard against the regex silently matching nothing.
    assert len(migrations) >= 5, f"expected several NOT NULL DEFAULT columns, got {migrations}"
    orm_columns = _orm_columns()
    orm_covered = 0
    for filename, column_name, migration_default in migrations:
        column = orm_columns.get(column_name)
        if column is None:
            # Migration-only table (e.g. media_deletions): create_all never
            # creates the column, so the migration always applies.
            continue
        orm_covered += 1
        server_default = column.server_default
        assert server_default is not None, (
            f"{filename}: ORM column {column.name!r} has no server_default, "
            f"but the migration declares NOT NULL DEFAULT {migration_default}; "
            "on a pristine DB create_all runs first, the migration's "
            "ADD COLUMN IF NOT EXISTS is a no-op, and the column ends up "
            "NOT NULL with no default."
        )
        arg = getattr(server_default, "arg", None)
        # TextClause defaults (sql_text("...")) carry comparable text;
        # function defaults (func.now()) are presence-only.
        if hasattr(arg, "text"):
            assert _normalise(arg.text) == _normalise(migration_default), (
                f"{filename}: ORM server_default {arg.text!r} for "
                f"{column.name!r} does not match the migration default "
                f"{migration_default!r}"
            )
    assert orm_covered >= 5, (
        f"expected several ORM-covered NOT NULL DEFAULT columns, got {orm_covered}"
    )


def test_separation_opt_in_has_server_default():
    """The exact column that broke the roundtrip suite on pristine bases."""
    column = database_module.Base.metadata.tables["tasks"].c.separation_opt_in
    assert column.nullable is False
    assert column.server_default is not None
    assert _normalise(column.server_default.arg.text) == "false"
