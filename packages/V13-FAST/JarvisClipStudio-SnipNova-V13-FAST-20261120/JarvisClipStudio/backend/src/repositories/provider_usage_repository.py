"""Persisted provider-manager state: LLM token usage + provider cooldowns.

Rows are written by the provider manager (best-effort write-through from the
worker/API processes) and read by the per-task token-usage endpoint and by
the manager's warm-up after a restart. Plain SQL in the house style; token
numbers are stored exactly as providers reported them — a call whose response
carried no usage is stored with ``usage_known = FALSE`` and NULL token
columns, never with invented numbers.
"""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import text


async def record_token_usage(
    db,
    *,
    alias: str,
    purpose: str,
    task_id: str | None,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    usage_known: bool,
) -> None:
    total_tokens = None
    if usage_known:
        total_tokens = (prompt_tokens or 0) + (completion_tokens or 0)
    await db.execute(
        text("""
            INSERT INTO llm_token_usage (
                id, task_id, alias, purpose,
                prompt_tokens, completion_tokens, total_tokens, usage_known
            ) VALUES (
                :id, :task_id, :alias, :purpose,
                :prompt_tokens, :completion_tokens, :total_tokens, :usage_known
            )
        """),
        {
            "id": str(uuid4()),
            "task_id": task_id,
            "alias": alias,
            "purpose": purpose,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "usage_known": usage_known,
        },
    )


async def get_task_token_usage(db, task_id: str) -> dict:
    """Per-task token totals, overall and broken down by alias + purpose."""
    result = await db.execute(
        text("""
            SELECT
                alias,
                purpose,
                COUNT(*) AS calls,
                COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                COALESCE(SUM(total_tokens), 0) AS total_tokens,
                COALESCE(SUM(CASE WHEN usage_known THEN 0 ELSE 1 END), 0)
                    AS unknown_usage_calls
            FROM llm_token_usage
            WHERE task_id = :task_id
            GROUP BY alias, purpose
            ORDER BY alias, purpose
        """),
        {"task_id": task_id},
    )
    rows = result.mappings().all()

    by_alias: dict[str, dict] = {}
    totals = {
        "calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "unknown_usage_calls": 0,
    }
    for row in rows:
        entry = {
            "alias": row["alias"],
            "purpose": row["purpose"],
            "calls": int(row["calls"]),
            "prompt_tokens": int(row["prompt_tokens"]),
            "completion_tokens": int(row["completion_tokens"]),
            "total_tokens": int(row["total_tokens"]),
            "unknown_usage_calls": int(row["unknown_usage_calls"]),
        }
        by_alias.setdefault(row["alias"], []).append(entry)
        for key in totals:
            totals[key] += entry[key if key != "calls" else "calls"]

    return {
        "totals": totals,
        "by_alias": [
            {"alias": alias, "purposes": purposes}
            for alias, purposes in sorted(by_alias.items())
        ],
    }


async def upsert_cooldown(
    db,
    *,
    alias: str,
    failure_class: str,
    cooldown_until: datetime,
    consecutive_failures: int,
    last_error: str,
) -> None:
    await db.execute(
        text("""
            INSERT INTO provider_cooldowns (
                alias, failure_class, cooldown_until,
                consecutive_failures, last_error, updated_at
            ) VALUES (
                :alias, :failure_class, :cooldown_until,
                :consecutive_failures, :last_error, CURRENT_TIMESTAMP
            )
            ON CONFLICT (alias) DO UPDATE SET
                failure_class = EXCLUDED.failure_class,
                cooldown_until = EXCLUDED.cooldown_until,
                consecutive_failures = EXCLUDED.consecutive_failures,
                last_error = EXCLUDED.last_error,
                updated_at = CURRENT_TIMESTAMP
        """),
        {
            "alias": alias,
            "failure_class": failure_class,
            "cooldown_until": cooldown_until.isoformat(),
            "consecutive_failures": consecutive_failures,
            "last_error": last_error,
        },
    )


async def load_cooldowns(db) -> dict:
    """All persisted cooldowns as {alias: {failure_class, cooldown_until(epoch), ...}}."""
    result = await db.execute(
        text("""
            SELECT alias, failure_class, cooldown_until,
                   consecutive_failures, last_error
            FROM provider_cooldowns
        """)
    )
    rows = result.mappings().all()
    loaded: dict[str, dict] = {}
    for row in rows:
        raw_until = row["cooldown_until"]
        if isinstance(raw_until, datetime):
            until_dt = raw_until
            if until_dt.tzinfo is None:
                until_dt = until_dt.replace(tzinfo=timezone.utc)
            until_epoch = until_dt.timestamp()
        else:
            parsed = datetime.fromisoformat(str(raw_until))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            until_epoch = parsed.timestamp()
        loaded[row["alias"]] = {
            "failure_class": row["failure_class"],
            "cooldown_until": until_epoch,
            "consecutive_failures": int(row["consecutive_failures"]),
            "last_error": row["last_error"],
        }
    return loaded
