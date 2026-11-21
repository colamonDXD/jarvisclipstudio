# Gemini docs cleanup — report

Branch `docs-no-gemini-default` (worktree `~/workspace/jcs-gemdocs`, off
next-milestone @ `f363b6f`), 1 commit `2678ca1`. **Not merged.** Docs/comments
only, except one validation-list line in `start.sh` (flagged below).

## What was wrong

The Gemini-fallback fix (`f363b6f`) removed Gemini from the default-LLM
chain, but every setup example in the tree still showed
`google-gla:gemini-3-flash-preview` as the model to use — and `start.sh`
never mentioned the Groq key the app now defaults to. A new user following
the guides would have configured the one model that answers 411.

## Changes (18 files, +49/−41)

| File | Change |
|---|---|
| `start.sh` | Key-presence check now looks for `GROQ_API_KEY` first; `GOOGLE_API_KEY` dropped from the LLM-key list (its remaining use is the YouTube metadata fallback). LLM-format validation accepts `groq:*` — without this, the new default `LLM=groq:openai/gpt-oss-120b` would have tripped the script's own "Unsupported LLM value" warning. Warning/error texts list Groq first and say Gemini is not supported. |
| `.env.example` | New Groq section first ("recommended: openai/gpt-oss-120b — the default model"); OpenAI no longer labelled "(recommended: gpt-5.2)"; Google section reworded to metadata-fallback-only; model examples list now leads with the Groq line. |
| `backend/.env.example` | `GROQ_API_KEY=` added to the provider list; `LLM=` now `groq:openai/gpt-oss-120b`. |
| `AGENTS.md` | Required-keys line: Groq first; "Gemini is not supported (its API answers 411); GOOGLE_API_KEY is only used as a fallback for YouTube metadata requests." |
| `CLAUDE.md` | Example block now shows Groq lines. |
| `README.md` | Feature line "(Gemini, GPT, Claude…)" → "(Groq, GPT, Claude…)"; quick-start key list and env block now Groq. |
| `QUICKSTART.md` | Env table: `GROQ_API_KEY` row replaces the Gemini row; new "### Groq (Recommended)" section; "### Google" section removed; OpenAI no longer "(Recommended)". |
| `docs/configuration.md` | LLM example → Groq; new `GROQ_API_KEY` row; `GOOGLE_API_KEY` row now "Only for the YouTube Data API metadata fallback; Gemini models are not supported"; basic self-host env block now Groq. |
| `docs/setup.md` | Provider list: Groq first (recommended), Google bullet removed; env block now Groq. |
| `docs/troubleshooting.md` | Provider-mismatch examples: `LLM=groq:...` requires `GROQ_API_KEY` replaces the google-gla bullet. |
| `frontend/src/components/articles/self-hosting-guide-article.tsx` | Env block now Groq + a comment line "Gemini models are not supported (their API answers 411)". |
| `frontend/messages/{de,en,es,id,pt,zh}/marketing.json` | "Bring your own models: Gemini, OpenAI, Anthropic, or Ollama" → Groq in all six locales (one occurrence each; single-word swap, JSON re-validated). |
| `CHANGELOG.md` | One **Improved** line in the 2026-11-12 section, next to the fix entry. |

## Sweep — every `gemini`/`google-gla` hit, and its disposition

Full-tree grep after the edits (no truncation). No Gemini default or example
remains anywhere user-facing.

**Left deliberately (not defaults/examples):**
- `backend/src/ai.py:566,584,593` — the provider allowlist + error strings
  ("Use google-gla:*, …", "Selected LLM provider is Google, but
  GOOGLE_API_KEY is not set…"). Code, not docs; an explicitly-selected
  Google model still errors this way. **Follow-up for a code lane:** reword
  the error hint to lead with Groq and drop the Google suggestion.
- `backend/src/api/routes/admin.py:47` — settings-UI metadata:
  "Required for google-gla:* models and fallback YouTube metadata." Code;
  same follow-up (reword to metadata-fallback only).
- `backend/src/auto_director/content_requirements_llm.py` — the Gemini
  denylist guard. Stays by design.
- `backend/src/config.py:597` — the fix lane's own comment ("Gemini is never
  a…"). Fine.
- `CHANGELOG.md` — the 2026-11-12 history entries describing the old
  behaviour and this cleanup. History; stays.
- `docs/JARVIS_AUDIT.md` — audit of a different project (Jarvis), historical
  record. Leave.
- `docs/local-first.md:94` — comparison table describing a *third-party*
  tool (openshorts) as "Gemini cloud only". A factual statement about
  another repo, not our default. Leave.
- Tests (`test_ai_retry.py`, `test_brain_upgrade.py`,
  `test_config_default_llm.py`, `test_content_requirements_llm.py`) —
  fixtures and the never-Gemini invariant itself. Leave.
- The new "Gemini is not supported" notes added by this lane
  (`.env.example`, `AGENTS.md`, `docs/configuration.md`, the article,
  `start.sh` warning). Intended.

## The one non-pure-docs line

`start.sh` LLM-format validation: `groq:*` added to the accepted-prefix
case list (and the hint line). This is a script change, not a comment —
but it is required for the docs fix to be true: without it, `start.sh`
would warn that the documented default is "Unsupported". It only widens
the accepted list; no other value's behaviour changes. `bash -n start.sh`
passes.

## Verification

- `bash -n start.sh` — OK (before and after).
- All six `marketing.json` files re-validated as JSON after the swap.
- `changelog.test.ts` — **4/4 passed** (temp `node_modules` symlink from
  `~/workspace/jcs-rc/frontend/node_modules`, removed after).
- `tsc --noEmit` (single-file, `--jsx react-jsx --skipLibCheck`) on the
  edited article — zero diagnostics, no syntax errors.
- No frontend test covers the article/blog page (searched `*.test.*` for
  `self-hosting` — none), so no frontend suite was run for it; the
  changelog suite is the only affected test.
- `git diff --stat`: 18 files, +49/−41. Tree clean after commit `2678ca1`.

## Follow-ups (not done here)

1. `ai.py` error hint + `admin.py` settings description (above) — a small
   code lane can reword them; they only fire when a user explicitly
   selects a Google model.
2. User-machine validation: none needed beyond the next package install
   (the article renders from the installed frontend; the guides are read
   from the installed tree).
