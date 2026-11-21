"""Migration files must survive the app's own migration runner.

The runner (``src.database``) splits each .sql file on every ``;`` and
executes the fragments one by one, so a semicolon inside a comment is a
live round: the text after it is sent to Postgres as SQL. On 2026-11-08
that crashed the backend during an upgrade (``syntax error at or near
"history"``). This test replays the split for every migration file and
asserts each fragment is either comment-only or begins with a real SQL
statement keyword.
"""

from pathlib import Path

SQL_DIR = Path(__file__).resolve().parents[2] / "src" / "migrations" / "sql"

STATEMENT_STARTERS = {
    "CREATE", "ALTER", "INSERT", "UPDATE", "DELETE", "DROP", "WITH",
    "SELECT", "SET", "BEGIN", "COMMIT", "GRANT", "REVOKE", "COMMENT",
    "DO", "TRUNCATE", "VACUUM", "ANALYZE", "REINDEX", "COPY", "LOCK", "(",
}


def _fragment_body(fragment: str) -> str:
    lines = [
        line for line in fragment.splitlines()
        if not line.strip().startswith("--")
    ]
    return "\n".join(lines).strip()


def test_no_migration_fragment_starts_with_comment_text():
    files = sorted(SQL_DIR.glob("*.sql"))
    assert files, "migration directory must not be empty"
    offenders = []
    for path in files:
        for index, fragment in enumerate(path.read_text().split(";")):
            body = _fragment_body(fragment)
            if not body:
                continue
            first = body.split(None, 1)[0].upper()
            if first not in STATEMENT_STARTERS:
                offenders.append(f"{path.name} fragment {index}: {body[:60]!r}")
    assert not offenders, (
        "migration fragments that are neither comment-only nor SQL "
        "(a semicolon inside a comment?): " + "; ".join(offenders)
    )
