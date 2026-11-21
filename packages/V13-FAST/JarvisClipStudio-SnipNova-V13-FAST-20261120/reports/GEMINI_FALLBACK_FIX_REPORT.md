# Gemini-fallback defect fix — report

Branch `fix-default-llm-groq` (worktree `~/workspace/jcs-geminifix`, off
next-milestone @ `4537b84`), 2 commits: `5d744f1` fix + tests,
`25c1c97` changelog. Tree clean. **Not merged.**

Trigger: Q-A flag (2) from the Nov 17 combo re-run on the packaged v4
tree — `Config._infer_default_llm()` fell back to
`google-gla:gemini-3-flash-preview` with no keys set. Pre-existing
upstream code (also on RC), not a regression.

## Step 0 — consumer map (verified in-tree)

`Config.llm` is consumed by **pydantic-ai direct provider clients** via
`ai._build_transcript_model` — NOT the LiteLLM gateway:

| Consumer | What it is |
|---|---|
| `ai.py` (lines 641, 766, 846, 922, 2476) | transcript analysis agent = moment selection, rerank, B-roll plan — the main clip pipeline |
| `auto_director/classify.py:931` | niche classification |
| `auto_director/plan_service.py:276`, `retention.py:922` | AI plan passes (with `_get_missing_llm_key_error`) |
| `services/clip_ai_edit_service.py:80` | AI edit |
| `auto_director/content_requirements_llm.py:55,185,296` | focus judgements + brief extraction, denylist-guarded |

Model-string format is provider-prefixed (`provider:model`), split by
`ai._split_llm_name` and dispatched in `_build_transcript_model`:
`groq:` → `GroqModel("openai/gpt-oss-120b",
GroqProvider(api_key=os.getenv("GROQ_API_KEY")))`;
`ollama:` → `OllamaModel`; `openrouter:`/`anthropic:` get native-output
handling; anything else is passed through as a raw string. **Gateway
aliases (`clip-assured`, `groq-20b`, `local-qwen`) are NOT valid
`Config.llm` values** — `_get_missing_llm_key_error` rejects unknown
providers. The `alias=` argument to `provider_manager.run_recorded` is
only a recording/cooldown label; the agent model is always the direct
one. So the fix string for Groq is `groq:openai/gpt-oss-120b`, not a
gateway alias.

## Step 0 — reachability on his installed build

- `LLM` is unset: compose passes `LLM=${LLM:-}` (empty) on backend +
  worker, and no `LLM=` is recorded in his `.env` (`.env.example:156`
  shows an OpenRouter default, but that is a template). So
  `_infer_default_llm()` runs whenever the app starts.
- `GROQ_API_KEY` is passed into all four services by
  `compose.override.yml` and is his only working key — but the old
  chain never consulted it.
- Old chain outcomes: Google key present → Gemini default; no keys at
  all → Gemini final fallback → `_get_missing_llm_key_error` returns
  "GOOGLE_API_KEY is not set" and selection fails. He has a Gemini key
  (that is how his 411s were produced), so the first branch was the
  live risk.
- The main pipeline has **no** denylist guard — only
  `content_requirements_llm.py` does (Gemini there means the focus /
  extraction passes are skipped and callers fall back to deterministic
  verdicts). A Gemini default on the main path would have been called
  directly and 411'd on selection/classification.
- Why his app still works today: either `LLM` is admin-set in the
  DB-backed runtime settings (`_get_runtime_setting` prefers an admin
  value over env) or `GOOGLE_API_KEY` is not in his app `.env`. That
  cannot be verified from here (his `.env` is never inspected), so it
  is recorded as unknown — the defect is wrong-by-construction
  regardless: the shipped default resolved to a dead model in every key
  combination except when another provider key happened to be present.

## Fix (`5d744f1`)

`backend/src/config.py` only:

- New attribute `self.groq_api_key =
  self._get_runtime_setting("GROQ_API_KEY")` (an admin-set Groq key is
  honoured the same way as the other keys; `ai.py` already reads the
  same variable from the process env, and
  `apply_settings_to_process_env` syncs admin values there). Not added
  to `as_runtime_settings()` — no change to settings sync behaviour.
- `_infer_default_llm` rewritten: Groq first
  (`groq:openai/gpt-oss-120b`), then OpenAI / Anthropic / OpenRouter in
  their original upstream relative order, final fallback
  `ollama:qwen3:8b` — exactly the codebase's established direct local
  fallback (`analysis_direct_fallback_model` default, config.py:113).
  Gemini appears nowhere in the new chain: a present `GOOGLE_API_KEY`
  can no longer resurrect it.

Deliberately unchanged (recorded as follow-ups, not this lane):
- `start.sh:75` still lists the required keys without Groq
  ("OPENAI_API_KEY, GOOGLE_API_KEY, ANTHROPIC_API_KEY,
  OPENROUTER_API_KEY, or LLM=ollama:<model>").
- `AGENTS.md` "Required runtime keys" line likewise omits Groq.
- `frontend/src/components/articles/self-hosting-guide-article.tsx:90`
  shows `LLM=google-gla:gemini-3-flash-preview` in a self-hosting
  guide article.

## Proof

New suite `backend/tests/unit/test_config_default_llm.py`, 13 tests:

- Parametrized inference table (9 combos): Groq-only → Groq; Groq +
  Google → Groq; Groq + OpenAI → Groq; OpenAI-only → `openai:gpt-5.2`;
  Anthropic-only → `anthropic:claude-4-sonnet`; OpenRouter-only →
  `openrouter:anthropic/claude-sonnet-5.5`; OpenAI + OpenRouter →
  OpenAI (upstream order preserved); Google-only → `ollama:qwen3:8b`;
  no keys → `ollama:qwen3:8b`. Every case also asserts no
  "gemini"/"google" substring in the result.
- `groq_api_key` attribute set/unset; explicit `LLM=` beats inference.
- Consumer acceptance (construction only, no live call): the inferred
  Groq string passes `_get_missing_llm_key_error` and builds a
  `GroqModel` named `openai/gpt-oss-120b`; the Ollama fallback passes
  the key check and builds an `OllamaModel`.

Results: **13 passed / 0 failed** (0.30 s). Bite-proof on the base
code (config.py stashed): **8 failed / 5 passed**, then fix restored.
Neighbours serial: config-ollama + content-requirements-llm +
litellm-gateway-config + new suite = **54 passed / 0 failed**.
No existing test referenced the old default (grep: only the new file
mentions `_infer_default_llm`); the content-requirements denylist
fixtures still list the Gemini strings as banned inputs, unaffected.
`changelog.test.ts` **4/4** (temporary node_modules symlink, removed
after). No backend `.env` exists in the worktree.

## Q-A flag (1) — for the record, NOT a defect, no fix

The combo re-run's other flag: `av_sync` reads +300 ms on all five
ranges because the new `podcast_news` 0.3 s signature fade compacts
audio and video together. The exports are internally in sync; the
reading must be crossfade-aware. Measurement caveat only — no code
touched.

## Remaining validation (user-machine)

- Next install (v4 + this fix, or the following package): with his
  real `.env`, confirm selection/classification behave as before and
  the worker log shows no Gemini model name anywhere. A definitive
  before/after on his machine is impossible to stage from here because
  his current build already works (see reachability unknowns above);
  the fix removes a landmine, it does not change any path that
  demonstrably fires today.
- The fix is deliberately small enough that no re-gate of the whole
  tree is needed beyond the neighbour suites run here; the next full
  gate after merge will cover it.
