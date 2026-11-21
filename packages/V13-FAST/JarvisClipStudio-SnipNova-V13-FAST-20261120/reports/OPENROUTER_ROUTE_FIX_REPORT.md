# OpenRouter Free-Route Repoint — Fix Report (2026-11-11)

**Lane:** config fix, branch `fix-openrouter-free-route` @ commit `5136ce1`, worktree `~/workspace/jcs-orfix`, branched off next-milestone @ `eae0595`. NOT merged — main merges after verification.
**Found by:** the 2026-11-11 free-upgrade sweep (`2026-11-11/UPGRADE_SWEEP.md`, dead-route flag #3).

## The problem

The free-API wiring merged @ `ffd8063` added an `openrouter-free` LiteLLM route pointing at `openrouter/deepseek/deepseek-chat-v3.1:free`. That model ID no longer exists: OpenRouter's live catalog (read directly from `openrouter.ai/api/v1/models` on 2026-11-11 by the sweep lane) carries `deepseek/deepseek-chat-v3.1` only as a **paid** model ($0.25 in / $0.95 out per 1M tokens) with no `:free` variant, and the old DeepSeek/Qwen/Llama `:free` IDs are all gone. If the user ever adds an `OPENROUTER_API_KEY`, that last-resort fallback would fail every time it is reached — silently, at exactly the moment every other route has already failed.

## What changed (3 files, one line each)

1. **`litellm_config.yaml`** — the `openrouter-free` route's model is now `openrouter/nvidia/nemotron-3-ultra-550b-a55b:free`. Nothing else in the config moved: the route keeps its **position** (last model in the list) and stays the **last entry of all three fallback chains** (`clip-fast`, `clip-assured`, `local-qwen`); every other route, order and alias is untouched; `PERMITTED_ALIASES` untouched.
2. **`backend/tests/unit/test_litellm_gateway_config.py`** — the `NEW_MODELS` pin for `openrouter-free` updated to the new model ID; the suite's existing exact-chain and last-resort-position pins are unchanged and still pass.
3. **`CHANGELOG.md`** — a **Fixed** entry at the top of the 2026-11-11 section, in plain language: the free OpenRouter fallback pointed at a retired model variant and now points at a model that exists.

## Catalog evidence for the new ID

From the sweep's direct read of OpenRouter's live models API on 2026-11-11 (`2026-11-11/UPGRADE_SWEEP.md` §3):

- `nvidia/nemotron-3-ultra-550b-a55b:free` is **present in the live catalog at $0 in / $0 out** — a 550B-parameter (55B active, MoE) frontier reasoning model with 1M context; independent uptime testing the same day found it answering reliably.
- The `:free` tier cap is **50 requests/day shared across all `:free` models** (20 RPM) with no card — small, but this route is the last-resort tail for a handful of small calls, which is exactly what the pool is sized for.
- The same model also already backs the pre-existing `or-ultra-free` route in this config (via NVIDIA NIM it is `clip-fast`'s upstream), so the repoint introduces no new provider or key — it reuses the existing `os.environ/OPENROUTER_API_KEY` plumbing.

## Tests (all serial, logs under `~/workspace/jcs-orfix-logs/`)

| Suite | Result |
|---|---|
| `tests/unit/test_litellm_gateway_config.py` (the config-pin suite: exact model list, exact fallback chains, free-routes-last, key plumbing) | **11 passed / 0 failed** |
| Neighbours: `tests/unit/test_openrouter_analysis.py` + `tests/unit/test_provider_manager.py` | **49 passed / 0 failed** (1 unrelated PydanticAI deprecation warning) |
| Neighbour: `tests/unit/test_content_requirements_llm.py` (alias allowlist guard) | **16 passed / 0 failed** |
| `frontend/src/lib/changelog.test.ts` (after the CHANGELOG edit; temporary node_modules symlink from jcs-rc, removed after) | **4 passed / 0 failed** |

Total backend: **76 passed / 0 failed**. No existing test was weakened — the only test change is the pinned model string itself.

## Scope / state

- Config + its tests + changelog only; no product logic changed.
- Diff: 3 files, 3 insertions, 2 deletions (verified with `git diff` before commit).
- Commit: `5136ce1 fix(gateway): repoint openrouter-free fallback at a model that exists`.
- State: IMPLEMENTED / INDIVIDUALLY TESTED on the branch. USER-MACHINE VALIDATION PENDING in the usual sense — the route stays inert until the user adds an `OPENROUTER_API_KEY` locally, and the first live proof is his gateway restart after that, same as the rest of the free-API wiring.
