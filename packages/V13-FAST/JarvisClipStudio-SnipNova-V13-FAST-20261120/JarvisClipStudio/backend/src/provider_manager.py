"""Local-first LLM provider manager.

JarvisClipStudio must keep producing clips on the user's own machine with no
per-video API charge. Cloud providers (via the LiteLLM gateway aliases in
``ANALYSIS_MODELS`` / ``LLM``) are an accelerant, never a dependency: when a
provider rate-limits, exhausts its quota, or goes down, work moves to the next
configured provider and finally to the local Ollama model — legitimately, in
configured order, never by evading quotas or rotating accounts.

This module is the provider-state layer *above* the existing call sites:

* failure classification (rate limit vs quota vs outage vs auth),
* a per-alias cooldown registry (in-process, write-through persisted when the
  app wires a database session factory at startup),
* model selection by purpose/complexity honouring ``PROVIDER_MODE``
  (``auto`` / ``local-first`` / ``local-only``),
* token accounting captured at the agent-run seam (the run result carries the
  provider's ``usage``; numbers are recorded only when the provider actually
  returned them — unknown usage is counted as a call, never invented).

Kill switch: ``PROVIDER_MANAGER_ENABLED=false`` makes selection return today's
static configuration untouched and leaves agents unwrapped.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Iterator, Optional

logger = logging.getLogger(__name__)

PURPOSES = ("analysis", "classify", "plan", "generic")
COMPLEXITIES = ("routine", "hard")
PROVIDER_MODES = ("auto", "local-first", "local-only")

# Model names that route to the local Ollama runtime in this deployment: the
# LiteLLM gateway alias (``openai-chat:local-qwen``) or a direct ``ollama:*`` model.
LOCAL_ALIAS_SUFFIXES = ("local-qwen",)
DEFAULT_LOCAL_MODEL = "openai-chat:local-qwen"

# Words in an error body/message that mean "the allowance is gone", as opposed
# to a transient per-minute rate limit. OpenAI-style APIs use
# ``insufficient_quota``; Groq spells out "tokens per day (TPD)".
QUOTA_KEYWORDS = (
    "insufficient_quota",
    "insufficient quota",
    "quota exceeded",
    "quota_exceeded",
    "daily quota",
    "tokens per day",
    "per day limit",
    "billing hard limit",
    "exceeded your current quota",
)

# Cooldown policy (seconds). Rate limits cool for minutes, exponential per
# consecutive failure; quota exhaustion cools until the next UTC day boundary;
# outages cool briefly; auth failures cool for hours because only a human
# fixing the key can help — selection skips the alias but the error still
# surfaces if it is the only route.
RATE_LIMIT_BASE_SECONDS = 120.0
RATE_LIMIT_MAX_SECONDS = 1800.0
UNAVAILABLE_BASE_SECONDS = 60.0
UNAVAILABLE_MAX_SECONDS = 900.0
AUTH_COOLDOWN_SECONDS = 6 * 3600.0
UNKNOWN_COOLDOWN_SECONDS = 30.0
QUOTA_MIN_COOLDOWN_SECONDS = 900.0
LOCAL_HEALTH_CACHE_SECONDS = 60.0
LOCAL_PROBE_TIMEOUT_SECONDS = 2.0


class FailureClass(str, enum.Enum):
    OK = "ok"
    RATE_LIMITED = "rate_limited"
    QUOTA_EXHAUSTED = "quota_exhausted"
    UNAVAILABLE = "unavailable"
    AUTH = "auth"
    UNKNOWN = "unknown"


class LocalProviderUnavailable(RuntimeError):
    """Raised in local-only mode when the local model cannot be reached."""

    def __init__(self, local_model: str):
        super().__init__(
            "PROVIDER_MODE is set to local-only, but the local AI model "
            f"({local_model}) is not reachable. Jarvis will not call any cloud "
            "provider in this mode. Start Ollama on this computer (and make "
            "sure the model is downloaded), or set PROVIDER_MODE=auto to allow "
            "the configured cloud providers again."
        )
        self.local_model = local_model


def _split_model_name(model_name: str) -> tuple[str, str]:
    if ":" not in model_name:
        return model_name.strip().lower(), ""
    provider, provider_model = model_name.split(":", 1)
    return provider.strip().lower(), provider_model.strip()


def is_local_alias(model_name: str | None) -> bool:
    """True when a model name routes to the local Ollama runtime."""
    if not model_name:
        return False
    provider, provider_model = _split_model_name(model_name)
    if provider == "ollama":
        return True
    tail = provider_model.rsplit("/", 1)[-1]
    return tail in LOCAL_ALIAS_SUFFIXES or tail.startswith("local-")


def _iter_error_chain(error: BaseException) -> Iterator[BaseException]:
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _error_text(error: BaseException) -> str:
    parts: list[str] = []
    for item in _iter_error_chain(error):
        body = getattr(item, "body", None)
        if body:
            parts.append(str(body))
        parts.append(str(item))
    return " ".join(parts).lower()


def classify_failure(error: BaseException) -> FailureClass:
    """Classify an LLM call failure for cooldown + fallback decisions.

    Signals, in priority order: HTTP status anywhere in the cause chain
    (pydantic-ai ``ModelHTTPError`` or any error carrying ``status_code``),
    quota wording in the provider body/message, then transport error types.
    Auth failures (401/403) are never retryable and classify separately.
    """
    statuses: list[int] = []
    for item in _iter_error_chain(error):
        status = getattr(item, "status_code", None)
        if isinstance(status, int):
            statuses.append(status)

    if any(status in (401, 403) for status in statuses):
        return FailureClass.AUTH
    if any(status in (413, 429) for status in statuses):
        if any(keyword in _error_text(error) for keyword in QUOTA_KEYWORDS):
            return FailureClass.QUOTA_EXHAUSTED
        return FailureClass.RATE_LIMITED
    if any(status == 404 for status in statuses):
        return FailureClass.UNAVAILABLE
    if any(500 <= status <= 599 for status in statuses):
        return FailureClass.UNAVAILABLE

    try:
        import httpx

        transport_errors: tuple[type[BaseException], ...] = (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
        )
    except Exception:  # pragma: no cover - httpx is a hard dependency
        transport_errors = ()
    if any(
        isinstance(item, transport_errors + (TimeoutError, ConnectionError))
        for item in _iter_error_chain(error)
    ):
        return FailureClass.UNAVAILABLE
    return FailureClass.UNKNOWN


def cooldown_seconds_for(
    failure_class: FailureClass, consecutive_failures: int, now: datetime
) -> float:
    """Cooldown length for one recorded failure (see policy constants)."""
    failures = max(1, consecutive_failures)
    if failure_class is FailureClass.RATE_LIMITED:
        return min(
            RATE_LIMIT_BASE_SECONDS * (2 ** (failures - 1)),
            RATE_LIMIT_MAX_SECONDS,
        )
    if failure_class is FailureClass.UNAVAILABLE:
        return min(
            UNAVAILABLE_BASE_SECONDS * (2 ** (failures - 1)),
            UNAVAILABLE_MAX_SECONDS,
        )
    if failure_class is FailureClass.QUOTA_EXHAUSTED:
        next_day = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return max(QUOTA_MIN_COOLDOWN_SECONDS, (next_day - now).total_seconds())
    if failure_class is FailureClass.AUTH:
        return AUTH_COOLDOWN_SECONDS
    return UNKNOWN_COOLDOWN_SECONDS


@dataclass
class CooldownRecord:
    alias: str
    failure_class: FailureClass
    cooldown_until: float  # epoch seconds
    consecutive_failures: int = 1
    last_error: str = ""


@dataclass
class UsageTotals:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    unknown_usage_calls: int = 0


_current_task_id: ContextVar[Optional[str]] = ContextVar(
    "provider_manager_task_id", default=None
)


def current_task_id() -> Optional[str]:
    return _current_task_id.get()


@contextmanager
def task_usage_scope(task_id: Optional[str]) -> Iterator[None]:
    """Attribute LLM usage recorded inside the block to ``task_id``."""
    token = _current_task_id.set(task_id)
    try:
        yield
    finally:
        _current_task_id.reset(token)


def set_task_context(task_id: Optional[str]) -> Any:
    """Set the current task for usage attribution; returns a reset token."""
    return _current_task_id.set(task_id)


def reset_task_context(token: Any) -> None:
    _current_task_id.reset(token)


class ProviderManager:
    """Cooldown registry + selection + token ledger for one process.

    The in-process registry is authoritative at runtime; when a database
    session factory is configured (app/worker startup), cooldowns and usage
    rows are written through so state survives restarts and the API process
    can report per-task totals. Persistence is best-effort: failures are
    counted, logged once, and never break an LLM call.
    """

    def __init__(
        self,
        *,
        now_fn: Callable[[], float] = time.time,
        session_factory: Optional[Callable[[], Any]] = None,
    ):
        self._now_fn = now_fn
        self._session_factory = session_factory
        self._cooldowns: dict[str, CooldownRecord] = {}
        self._usage: dict[tuple[Optional[str], str, str], UsageTotals] = {}
        self._local_health: dict[str, tuple[bool, float]] = {}
        self._loaded = False
        self._persist_failures = 0
        self._persist_disabled = False

    # -- configuration -----------------------------------------------------

    def configure_persistence(self, session_factory: Callable[[], Any]) -> None:
        self._session_factory = session_factory
        self._loaded = False

    # -- cooldown registry ---------------------------------------------------

    def _now(self) -> float:
        return self._now_fn()

    def record_failure(
        self,
        alias: str,
        *,
        error: BaseException | None = None,
        failure_class: FailureClass | None = None,
    ) -> FailureClass:
        """Record an observed failure and cool the alias down."""
        if failure_class is None:
            failure_class = (
                classify_failure(error)
                if error is not None
                else FailureClass.UNKNOWN
            )
        now_dt = datetime.fromtimestamp(self._now(), tz=timezone.utc)
        previous = self._cooldowns.get(alias)
        consecutive = (previous.consecutive_failures + 1) if previous else 1
        seconds = cooldown_seconds_for(failure_class, consecutive, now_dt)
        record = CooldownRecord(
            alias=alias,
            failure_class=failure_class,
            cooldown_until=self._now() + seconds,
            consecutive_failures=consecutive,
            last_error=(
                f"{type(error).__name__}" if error is not None else failure_class.value
            ),
        )
        self._cooldowns[alias] = record
        if is_local_alias(alias) and failure_class in (
            FailureClass.UNAVAILABLE,
            FailureClass.RATE_LIMITED,
            FailureClass.QUOTA_EXHAUSTED,
        ):
            self._local_health[alias] = (False, self._now() + LOCAL_HEALTH_CACHE_SECONDS)
        logger.warning(
            "Provider %s cooled down for %.0fs (%s, failure #%d)",
            alias,
            seconds,
            failure_class.value,
            consecutive,
        )
        self._schedule_persist(self._persist_cooldown(record))
        return failure_class

    def record_success(self, alias: str) -> None:
        if alias in self._cooldowns:
            logger.info("Provider %s recovered; cooldown cleared", alias)
            del self._cooldowns[alias]
        if is_local_alias(alias):
            self._local_health[alias] = (True, self._now() + LOCAL_HEALTH_CACHE_SECONDS)

    def is_available(self, alias: str) -> bool:
        record = self._cooldowns.get(alias)
        return record is None or record.cooldown_until <= self._now()

    def cooldown_record(self, alias: str) -> Optional[CooldownRecord]:
        return self._cooldowns.get(alias)

    # -- local health --------------------------------------------------------

    def local_health(self, alias: str) -> Optional[bool]:
        """Last known local health for an alias: True/False, or None if unknown."""
        cached = self._local_health.get(alias)
        if cached is not None and cached[1] > self._now():
            return cached[0]
        return None

    def note_local_health(self, alias: str, healthy: bool) -> None:
        self._local_health[alias] = (
            healthy,
            self._now() + LOCAL_HEALTH_CACHE_SECONDS,
        )

    async def probe_local_health(
        self, alias: str, base_url: str, *, client: Any = None
    ) -> bool:
        """Probe the local runtime's ``/models`` endpoint (cached 60s).

        ``client`` is injectable for tests: any object with an async
        ``get(url)`` returning a response with ``status_code``.
        """
        known = self.local_health(alias)
        if known is not None:
            return known
        url = base_url.rstrip("/") + "/models"
        healthy = False
        try:
            if client is not None:
                response = await client.get(url)
                healthy = getattr(response, "status_code", 0) == 200
            else:
                import httpx

                # trust_env=False: the local runtime is on this machine or
                # the Docker host — a proxy from the environment must never
                # sit between the app and Ollama.
                async with httpx.AsyncClient(
                    timeout=LOCAL_PROBE_TIMEOUT_SECONDS, trust_env=False
                ) as http_client:
                    response = await http_client.get(url)
                    healthy = response.status_code == 200
        except Exception:
            healthy = False
        self.note_local_health(alias, healthy)
        return healthy

    # -- selection -----------------------------------------------------------

    def select_models(
        self,
        candidates: Iterable[str],
        *,
        purpose: str = "generic",
        complexity: str = "routine",
        mode: str = "auto",
        local_healthy: Optional[bool] = None,
    ) -> list[str]:
        """Order ``candidates`` for one stage, skipping cooled-down aliases.

        * ``auto``: routine work prefers a healthy local alias, hard work
          prefers the configured cloud order; the local alias is always the
          final fallback when it is among (or appended to) the candidates.
        * ``local-first``: local first for everything, cloud after.
        * ``local-only``: local aliases only; raises
          :class:`LocalProviderUnavailable` when no local route is usable.

        In ``auto``/``local-first`` the result is never empty when the input
        is not: if every alias is cooling down, the ordered list is returned
        unchanged so today's retry behaviour still applies (fail-safe).
        """
        ordered_input: list[str] = []
        for candidate in candidates:
            if candidate and candidate not in ordered_input:
                ordered_input.append(candidate)
        if not ordered_input:
            return []

        local = [c for c in ordered_input if is_local_alias(c)]
        cloud = [c for c in ordered_input if not is_local_alias(c)]

        if mode == "local-only":
            usable = [
                c
                for c in local
                if self.is_available(c) and local_healthy is not False
            ]
            if not usable:
                raise LocalProviderUnavailable(local[0] if local else DEFAULT_LOCAL_MODEL)
            return usable

        if mode == "local-first":
            ordered = local + cloud
        elif complexity == "routine":
            ordered = (local + cloud) if local_healthy is not False else (cloud + local)
        else:  # auto + hard: configured cloud order, local as final fallback
            ordered = cloud + local

        available = [
            c
            for c in ordered
            if self.is_available(c)
            and not (is_local_alias(c) and local_healthy is False)
        ]
        if available:
            return available
        logger.warning(
            "All provider aliases are cooling down or unavailable for %s; "
            "falling back to the configured order",
            purpose,
        )
        return ordered

    # -- token accounting ------------------------------------------------------

    def record_usage(
        self,
        *,
        alias: str,
        purpose: str,
        prompt_tokens: Optional[int],
        completion_tokens: Optional[int],
        task_id: Optional[str] = None,
    ) -> None:
        """Record one completed LLM call. Unknown usage counts the call only."""
        key = (task_id, alias, purpose)
        totals = self._usage.setdefault(key, UsageTotals())
        totals.calls += 1
        usage_known = prompt_tokens is not None or completion_tokens is not None
        if usage_known:
            totals.prompt_tokens += prompt_tokens or 0
            totals.completion_tokens += completion_tokens or 0
            totals.total_tokens += (prompt_tokens or 0) + (completion_tokens or 0)
        else:
            totals.unknown_usage_calls += 1
        self._schedule_persist(
            self._persist_usage(
                alias=alias,
                purpose=purpose,
                task_id=task_id,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                usage_known=usage_known,
            )
        )

    def usage_for_task(self, task_id: Optional[str]) -> dict[str, Any]:
        """In-process usage view (the API endpoint reads the persisted rows)."""
        by_alias: dict[str, UsageTotals] = {}
        for (row_task, alias, _purpose), totals in self._usage.items():
            if row_task != task_id:
                continue
            bucket = by_alias.setdefault(alias, UsageTotals())
            bucket.calls += totals.calls
            bucket.prompt_tokens += totals.prompt_tokens
            bucket.completion_tokens += totals.completion_tokens
            bucket.total_tokens += totals.total_tokens
            bucket.unknown_usage_calls += totals.unknown_usage_calls
        overall = UsageTotals()
        for totals in by_alias.values():
            overall.calls += totals.calls
            overall.prompt_tokens += totals.prompt_tokens
            overall.completion_tokens += totals.completion_tokens
            overall.total_tokens += totals.total_tokens
            overall.unknown_usage_calls += totals.unknown_usage_calls
        return {"totals": overall, "by_alias": by_alias}

    # -- persistence (best-effort write-through) ----------------------------

    def _schedule_persist(self, coro: Any) -> None:
        if self._session_factory is None or self._persist_disabled:
            if coro is not None:
                coro.close()
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            coro.close()
            return
        task = loop.create_task(coro)
        task.add_done_callback(self._persist_done)

    def _persist_done(self, task: "asyncio.Task[Any]") -> None:
        if task.cancelled():
            return
        if task.exception() is not None:
            self._persist_failures += 1
            if self._persist_failures == 1:
                logger.warning(
                    "Provider-manager persistence failed (%s); continuing in-memory",
                    task.exception(),
                )
            if self._persist_failures >= 5:
                self._persist_disabled = True
                logger.warning("Provider-manager persistence disabled after repeated failures")

    async def _persist_cooldown(self, record: CooldownRecord) -> None:
        if self._session_factory is None:
            return
        from .repositories import provider_usage_repository

        async with self._session_factory() as db:
            await provider_usage_repository.upsert_cooldown(
                db,
                alias=record.alias,
                failure_class=record.failure_class.value,
                cooldown_until=datetime.fromtimestamp(
                    record.cooldown_until, tz=timezone.utc
                ),
                consecutive_failures=record.consecutive_failures,
                last_error=record.last_error,
            )
            await db.commit()

    async def _persist_usage(
        self,
        *,
        alias: str,
        purpose: str,
        task_id: Optional[str],
        prompt_tokens: Optional[int],
        completion_tokens: Optional[int],
        usage_known: bool,
    ) -> None:
        if self._session_factory is None:
            return
        from .repositories import provider_usage_repository

        async with self._session_factory() as db:
            await provider_usage_repository.record_token_usage(
                db,
                alias=alias,
                purpose=purpose,
                task_id=task_id,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                usage_known=usage_known,
            )
            await db.commit()

    async def warm_up(self) -> None:
        """Load persisted cooldowns once per process (best-effort)."""
        if self._loaded:
            return
        self._loaded = True
        if self._session_factory is None:
            return
        try:
            from .repositories import provider_usage_repository

            async with self._session_factory() as db:
                rows = await provider_usage_repository.load_cooldowns(db)
            now = self._now()
            for alias, row in rows.items():
                until = row["cooldown_until"]
                if until <= now:
                    continue
                existing = self._cooldowns.get(alias)
                if existing is None or existing.cooldown_until < until:
                    self._cooldowns[alias] = CooldownRecord(
                        alias=alias,
                        failure_class=FailureClass(row["failure_class"]),
                        cooldown_until=until,
                        consecutive_failures=row["consecutive_failures"],
                        last_error=row.get("last_error") or "",
                    )
        except Exception as error:
            logger.warning("Provider-manager warm-up failed (%s); continuing", error)


# -- process-wide manager + config-driven helpers ---------------------------

_manager: Optional[ProviderManager] = None


def get_manager() -> ProviderManager:
    global _manager
    if _manager is None:
        _manager = ProviderManager()
    return _manager


def reset_manager_for_tests() -> None:
    global _manager
    _manager = None


def configure_persistence(session_factory: Optional[Callable[[], Any]] = None) -> None:
    """Wire database persistence (called from API/worker startup only)."""
    if session_factory is None:
        from .database import get_session_maker

        session_factory = get_session_maker()
    get_manager().configure_persistence(session_factory)


def manager_enabled(config: Any = None) -> bool:
    if config is not None:
        return bool(getattr(config, "provider_manager_enabled", True))
    import os

    return os.getenv("PROVIDER_MANAGER_ENABLED", "true").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def provider_mode(config: Any = None) -> str:
    raw = ""
    if config is not None:
        raw = getattr(config, "provider_mode", "") or ""
    if not raw:
        import os

        raw = os.getenv("PROVIDER_MODE", "auto")
    mode = str(raw).strip().lower()
    return mode if mode in PROVIDER_MODES else "auto"


def local_model_name(config: Any = None) -> str:
    if config is not None:
        configured = getattr(config, "local_llm", None)
        if configured:
            return configured
    import os

    return os.getenv("LOCAL_LLM", DEFAULT_LOCAL_MODEL) or DEFAULT_LOCAL_MODEL


def _with_local_candidate(candidates: list[str], config: Any) -> list[str]:
    local = local_model_name(config)
    if local and not any(is_local_alias(c) for c in candidates):
        return [*candidates, local]
    return candidates


def select_analysis_models(
    config: Any, *, window_count: int, local_healthy: Optional[bool] = None
) -> Optional[list[str]]:
    """Ordered analysis models for the pipeline, or None for today's default.

    None means "no override": ANALYSIS_MODELS is empty and nothing needs
    rerouting, so the caller keeps the original single-model path (and its
    agent cache) byte-identical. Otherwise the configured order is preserved
    as the cloud preference order, with the local alias appended as the final
    fallback (auto/hard), moved first for routine work when healthy, and made
    the only route in local-only mode.
    """
    manager = get_manager()
    configured = list(getattr(config, "analysis_models", None) or [])
    default_model = getattr(config, "llm", None)
    mode = provider_mode(config)
    complexity = "hard" if window_count > 1 else "routine"

    if not configured and not default_model:
        # No model is configured at all: nothing for the manager to manage.
        return None

    if not configured:
        if mode == "auto":
            candidates = _with_local_candidate([default_model] if default_model else [], config)
            selected = manager.select_models(
                candidates,
                purpose="analysis",
                complexity=complexity,
                mode=mode,
                local_healthy=local_healthy,
            )
            if selected == ([default_model] if default_model else []):
                return None
            return selected
        candidates = _with_local_candidate([default_model] if default_model else [], config)
        return manager.select_models(
            candidates,
            purpose="analysis",
            complexity=complexity,
            mode=mode,
            local_healthy=local_healthy,
        )

    candidates = _with_local_candidate(configured, config)
    return manager.select_models(
        candidates,
        purpose="analysis",
        complexity=complexity,
        mode=mode,
        local_healthy=local_healthy,
    )


def select_classify_model(config: Any, *, local_healthy: Optional[bool] = None) -> str:
    """The model the niche classifier should use right now.

    Classification is a small routine call: a healthy local model is
    preferred in auto/local-first modes; otherwise the configured ``LLM``
    keeps priority, then ANALYSIS_MODELS order. Raises
    :class:`LocalProviderUnavailable` in local-only mode when local is down.
    """
    return select_classify_candidates(config, local_healthy=local_healthy)[0]


def select_classify_candidates(
    config: Any, *, local_healthy: Optional[bool] = None
) -> list[str]:
    """The full ordered candidate list for niche classification.

    Same ordering inputs as :func:`select_classify_model` (configured
    ``LLM``, ANALYSIS_MODELS order, the local alias appended), passed
    through :meth:`ProviderManager.select_models` so cooled-down
    aliases are skipped and routine work still prefers a healthy local
    model. The classify caller walks this list: a failure on one
    candidate (including a build-time missing-key gate, which
    ``select_models`` cannot foresee) advances to the next instead of
    aborting the whole classification.

    Raises :class:`LocalProviderUnavailable` in local-only mode when
    local is down, and ``RuntimeError`` when nothing is configured.
    """
    manager = get_manager()
    mode = provider_mode(config)
    candidates: list[str] = []
    for candidate in [getattr(config, "llm", None), *(getattr(config, "analysis_models", None) or [])]:
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    candidates = _with_local_candidate(candidates, config)
    if local_healthy is None and mode == "local-only":
        # The classifier builds its agent synchronously; in local-only mode an
        # unknown health state must not silently route to the cloud, so fall
        # back to the last observed local outcome (unknown = unavailable).
        local_alias = next((c for c in candidates if is_local_alias(c)), None)
        local_healthy = manager.local_health(local_alias) if local_alias else False
    selected = manager.select_models(
        candidates,
        purpose="classify",
        complexity="routine",
        mode=mode,
        local_healthy=local_healthy,
    )
    if not selected:
        raise RuntimeError("No LLM model is configured for classification.")
    return selected


# -- agent-run seam: usage capture + failure observation ---------------------


def extract_usage(result: Any) -> tuple[Optional[int], Optional[int]]:
    """(prompt_tokens, completion_tokens) from an agent run result, if any."""
    usage_attr = getattr(result, "usage", None)
    if usage_attr is None:
        return None, None
    try:
        # pydantic-ai >= 1.0 exposes usage as a property whose value is the
        # usage object itself (it stays callable for backwards compat, and
        # calling it is what emits the deprecation warning); older versions
        # used a plain method, which must be called to get the usage object.
        # Tell them apart by shape: a usage object carries the token fields,
        # a legacy method does not.
        if callable(usage_attr) and not (
            hasattr(usage_attr, "input_tokens")
            or hasattr(usage_attr, "output_tokens")
        ):
            usage = usage_attr()
        else:
            usage = usage_attr
    except Exception:
        return None, None
    if usage is None:
        return None, None
    prompt = getattr(usage, "input_tokens", None)
    completion = getattr(usage, "output_tokens", None)
    return (
        int(prompt) if isinstance(prompt, int) else None,
        int(completion) if isinstance(completion, int) else None,
    )


async def run_recorded(
    agent: Any,
    *args: Any,
    alias: str,
    purpose: str,
    manager: Optional[ProviderManager] = None,
    **kwargs: Any,
) -> Any:
    """Run an agent, recording its token usage and any provider failure."""
    mgr = manager or get_manager()
    try:
        result = await agent.run(*args, **kwargs)
    except BaseException as error:
        if not isinstance(error, asyncio.CancelledError):
            mgr.record_failure(alias, error=error)
        raise
    prompt_tokens, completion_tokens = extract_usage(result)
    mgr.record_success(alias)
    mgr.record_usage(
        alias=alias,
        purpose=purpose,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        task_id=current_task_id(),
    )
    return result


class ManagedAgentProxy:
    """Transparent agent wrapper that records usage + failures per run.

    Only ``run`` is intercepted; every other attribute delegates to the
    wrapped agent, so call sites see the agent they built.
    """

    def __init__(
        self,
        agent: Any,
        *,
        alias: str,
        purpose: str,
        manager: Optional[ProviderManager] = None,
    ):
        object.__setattr__(self, "_agent", agent)
        object.__setattr__(self, "_alias", alias)
        object.__setattr__(self, "_purpose", purpose)
        object.__setattr__(self, "_manager", manager)

    async def run(self, *args: Any, **kwargs: Any) -> Any:
        return await run_recorded(
            object.__getattribute__(self, "_agent"),
            *args,
            alias=object.__getattribute__(self, "_alias"),
            purpose=object.__getattribute__(self, "_purpose"),
            manager=object.__getattribute__(self, "_manager"),
            **kwargs,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_agent"), name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(object.__getattribute__(self, "_agent"), name, value)

    def __repr__(self) -> str:
        return f"ManagedAgentProxy({object.__getattribute__(self, '_agent')!r})"


__all__ = [
    "COMPLEXITIES",
    "FailureClass",
    "LocalProviderUnavailable",
    "ManagedAgentProxy",
    "PROVIDER_MODES",
    "ProviderManager",
    "UsageTotals",
    "classify_failure",
    "configure_persistence",
    "cooldown_seconds_for",
    "current_task_id",
    "extract_usage",
    "get_manager",
    "is_local_alias",
    "local_model_name",
    "manager_enabled",
    "provider_mode",
    "reset_manager_for_tests",
    "reset_task_context",
    "run_recorded",
    "select_analysis_models",
    "select_classify_candidates",
    "select_classify_model",
    "set_task_context",
    "task_usage_scope",
]
