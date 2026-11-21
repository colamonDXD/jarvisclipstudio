# Classification candidate ordering — strongest brain first (2026-11-11)

**State: DONE on branch, NOT merged / packaged / uploaded.**
Branch `fix-classify-order` off next-milestone @ `6362cfe`, worktree `~/workspace/jcs-classify-order`, tree clean.
Commits: `25860db` fix(classify) · `6560d17` docs(changelog).

## The problem (code-verified)

`classify.py::_classify_candidates()` took the provider manager's ordering
head-first. `ProviderManager.select_models` deliberately prefers the local
alias for *routine* work (auto mode) and for *everything* in local-first
mode — and `select_classify_candidates` passes `complexity="routine"`. So in
Luffy's local-first deployment the chain head was `openai:local-qwen`
(Ollama qwen3:8b, the weakest model in the system), and the chain is
first-success-wins: a confidently-wrong local verdict locked the niche and
the strong route (Groq `openai/gpt-oss-120b`, already primary for moment
selection via LiteLLM) was never consulted. Classification is ONE small
call per video — it should be answered by the strongest route available,
with local as the offline backstop.

## Naming evidence (no guessing)

The codebase already owns the cloud/local discriminator:
`provider_manager.is_local_alias()` — provider `ollama`, or the model tail
(after the last `/`) in `LOCAL_ALIAS_SUFFIXES = ("local-qwen",)` or starting
with `local-`. It is the same function `select_models` itself uses to
partition candidates, and the same one classify.py's VRAM helpers already
use. `openai:local-qwen` and `ollama:qwen3:8b` are local; the gateway
aliases (`openai:clip-fast`, `openai:clip-assured`) and direct cloud routes
(`groq:openai/gpt-oss-120b`) are not.

## The change (classify.py only)

`_classify_candidates()` assembles the list exactly as before (manager
ordering → configured LLM → analysis models → analysis fallback → direct
fallback, de-duplicated), then returns `_strongest_first(assembled)`: a
**stable partition** — cloud routes first in their assembled relative
order, local routes at the tail. Properties:

- Within each class, the manager's relative order — including its
  cooldown/health skips, which happen inside `select_models` before the
  list reaches classify.py — is preserved exactly.
- Local candidates remain in the chain, at the tail, ahead of the
  evidence-guardrail fallback path (which runs only when every candidate
  fails, unchanged).
- No cloud candidate in the assembly ⇒ the partition is the identity:
  a local-only deployment's chain is byte-identical to today.
- Untouched, per brief: provider-mode semantics for analysis/selection/
  plan paths, the guardrails, `litellm_config.yaml`, `PERMITTED_ALIASES`,
  `provider_manager.py` (including `select_classify_model`, the legacy
  single-agent accessor — the production classification path walks
  `_classify_candidates`, which is the chain this fix reorders).

## Before / after — Luffy's deployment shape

Manager enabled, PROVIDER_MODE=local-first, gateway cloud routes +
local routes configured (alias names as in code/tests; the exact cloud
alias strings follow his `.env`):

- **Before:** `openai:local-qwen` → cloud LLM → cloud analysis models →
  `groq:openai/gpt-oss-120b` → `ollama:qwen3:8b`
  (local asked FIRST; its answer, right or wrong, ended the chain)
- **After:** cloud LLM → cloud analysis models →
  `groq:openai/gpt-oss-120b` → `openai:local-qwen` → `ollama:qwen3:8b`
  (strongest first; local still answers when every cloud route fails)

## Tests

Targeted serial run (log: `~/workspace/.tmp-classify-order/targeted.log`):
**128 passed / 0 failed** — `test_auto_director_classify_chain.py`,
`test_auto_director_classify_battery.py`, `test_auto_director_classify.py`,
`test_auto_director_classify_guardrails.py`,
`test_auto_director_classify_evidence.py`, `test_vram_stage1.py`,
`test_learning_classifier_seam.py`. CHANGELOG: entry added under
2026-11-11; `changelog.test.ts` **4 passed** (node_modules symlink borrowed
from jcs-rc, removed after).

New/changed pins, per the brief:

- (a) `test_local_first_mode_still_puts_cloud_first` — manager enabled,
  local-first, cloud present → cloud first, local last (plus the updated
  `test_full_ordered_list` for auto mode).
- (b) `test_local_only_chain_is_unchanged` +
  `test_local_only_manager_disabled_is_unchanged` — local-only assemblies
  come back byte-identical; `test_manager_disabled_uses_config_order`
  expectation deliberately unchanged (that config order was already
  cloud-first with a local tail).
- (c) `test_cloud_relative_order_follows_the_manager` — a scrambled
  manager order (groq FB, local, clip-assured, clip-fast) partitions to
  groq FB → clip-assured → clip-fast → locals: cloud relative order
  preserved, locals moved behind as a block.
- (d) The six-case battery lands the same six verdicts
  (travel_vlog / gaming / podcast_news control / finance_business /
  sports / music_artist) and the guardrail-rescue B-roll addendum passes
  unmodified.

**Disclosure — existing tests updated (expectations re-derived, no
assertion weakened):** the chain ordering pins and the fallthrough /
battery / JSON-contract scenarios were written when the local pick headed
the chain; several stubbed the head candidate as the failing one and the
second as the answerer. Those roles rotated to the new head/second
candidates (configured cloud LLM → first analysis model). Every scenario
keeps its failure class (build gate, transport, gateway 400, deep
fallthrough past a transport failure + a build gate, all-fail marked
fallback, JSON-text accept/repair/reject/unparseable) and its verdict and
assertion strength; the rotated identities are named in the test
docstrings/comments.

## States

- Strongest-first classify ordering: **IMPLEMENTED / INDIVIDUALLY TESTED /
  INTEGRATED on branch** — USER-MACHINE VALIDATION PENDING (his fresh TJR
  task's classification leg is the live proof; it also depends on the
  already-delivered INSERT hotfix being in place).
