# Full-ladder AI model bake-off — which brain should hold every rung?

_Protocol written 2026-11-14 (Day 2 — AI QUALITY). Offline phase: PROVEN
2026-11-14 (harness self-check PASSED + 25/25 tests, this sandbox).
Live phase: PENDING — no live call has been made from this sandbox and
no provider key exists there, by design._

This document EXTENDS
`~/workspace/research_notes/sprint-7day/NIM_BAKEOFF.md` to the whole
ladder. It does **not** duplicate that document's §2–§5: the NIM-rungs
method, candidate roster, slug verification, provisional ranking and
NIM live-run protocol live there and are referenced, not repeated.
Read NIM_BAKEOFF.md first for the two NIM rungs; this document covers
the ladder as a whole, the non-NIM rungs, and the rules that decide
any swap anywhere on it.

Sprint rule (unchanged): identical inputs/prompts for every candidate;
measured moment / hook / niche / plan quality, latency and schema
reliability; **winner per task, never by model size**; wire measured
winners only.

Evidence labels used throughout — every quality claim carries one:

- **offline-measured** — produced by a run in this sandbox (fake
  server, tests, or a deterministic scorer). Provable now.
- **provisional-benchmark** — a published general-intelligence
  benchmark (Artificial Analysis index, as republished). Says nothing
  about this app's prompts. Used only to order candidates.
- **pending-live** — nothing is known until the live phase runs on
  the user's machine with real keys.

**No winner is named anywhere in this document.** No live evidence
exists yet, so no rung changes. The incumbents below stay until the
live phase justifies a swap under §5.

---

## 1. Scope — the ladder, the task families, the incumbents

### 1.1 The one ladder

From `~/workspace/jcs-next/litellm_config.yaml` (config wins if this
page and the config disagree) and `AI_MODEL_ROUTING.md` §1–§2. There
is deliberately **one** strength ranking; every chain is that ranking
with its own route removed, so a failed route always falls to the next
strongest brain:

**deepseek-chat → clip-assured → clip-fast → nim-deepseek →
openrouter-free → cloudflare-free → groq-20b → local-qwen**

| Rung (alias) | Provider / slug today | Role | Incumbent status |
|---|---|---|---|
| `deepseek-chat` | DeepSeek direct — `openai/deepseek-chat`, `https://api.deepseek.com` | Primary reasoning / editing director; strongest general brain, first hop of every chain | **INCUMBENT, top of ladder** |
| `clip-assured` | Groq — `groq/openai/gpt-oss-120b` | Premium selection pass (reasoning_effort=high); near-equal top | **INCUMBENT** |
| `clip-fast` | NVIDIA NIM — `openai/nvidia/nemotron-3-ultra-550b-a55b`, `https://integrate.api.nvidia.com/v1` | Bulk / fast analysis, long-form chunked analysis | **INCUMBENT — contested by the NIM bake-off** (see NIM_BAKEOFF.md §3–§4; provisional challenger `z-ai/glm-5.3-flash`) |
| `nim-deepseek` | NVIDIA NIM — `openai/deepseek-ai/deepseek-v4.1-flash` | Second NIM rung; specialised / bulk execution | **INCUMBENT — contested by the NIM bake-off** (no verified challenger outranks it offline; `deepseek-ai/deepseek-v4-pro` is the unverified upside) |
| `openrouter-free` | OpenRouter — `openrouter/nvidia/nemotron-3-ultra-550b-a55b:free` | Backup access, same Nemotron Ultra weights via OpenRouter's shared daily-capped free pool | **INCUMBENT** (legacy twin `or-ultra-free` is in no chain) |
| `cloudflare-free` | Cloudflare Workers AI — `cloudflare/@cf/openai/gpt-oss-120b` | Eligible fallback on independent infrastructure; same gpt-oss-120b weights as `clip-assured` | **INCUMBENT — endpoint UNVERIFIED-LIVE** (see §7.3) |
| `groq-20b` | Groq — `groq/openai/gpt-oss-20b` | Light structured passes | **INCUMBENT** |
| `local-qwen` | Ollama on the user's PC — `openai/qwen3:8b`, `http://host.docker.internal:11434/v1` (from inside Docker) | Local emergency floor; last in every chain; cannot run out of anyone's allowance | **INCUMBENT — local-live PENDING** (see §6.3) |

Free-slug pins (test-pinned in
`backend/tests/unit/test_litellm_gateway_config.py`, see
AI_MODEL_ROUTING.md §4): `openrouter-free` must stay a `:free` model;
`cloudflare-free` must stay `@cf/openai/gpt-oss-120b` and may never
contain a paid-plan-only fragment (GLM-5.x, Kimi, DeepSeek V4);
`local-qwen` is the only Ollama route. Gemini is nowhere — no route,
no chain, no default (its API answers 411 here), test-pinned.

### 1.2 Which rungs compete per task family

Task families differ only in where they **enter** the ladder; the
walk from there is the same (AI_MODEL_ROUTING.md §2). The bake-off
therefore runs **one competition per task family**, not one global
ranking — a model can win a family without winning the ladder.

| Task family | Enters at | Then walks | Competing rungs (this bake-off) | What "winning" means here |
|---|---|---|---|---|
| **Premium moment selection** (reasoning_effort=high) | `clip-assured` | deepseek-chat → clip-fast → nim-deepseek → openrouter-free → cloudflare-free → groq-20b → local-qwen | `deepseek-chat`, `clip-assured`, `clip-fast`, `nim-deepseek` (the top four; the lower rungs are fallback-only for this family and are measured for degradation, not for the win) | Best Japan gold agreement + hook-title discipline + schema validity (§3.1). The premium pass is the product's judgement call; it is the only family where the top rung is contested on quality, not just on cost. |
| **Bulk / fast analysis, long-form chunked analysis** | `clip-fast` | deepseek-chat → clip-assured → nim-deepseek → openrouter-free → cloudflare-free → groq-20b → local-qwen | `clip-fast` + all six NIM candidates (NIM_BAKEOFF.md §3), `nim-deepseek`, `clip-assured` | Best selection + plan quality **at bulk speed**; latency is a first-class metric here, not a tie-breaker (§3.2). A slower model that wins on quality does not win this family unless it also clears the latency band. |
| **Light structured passes** | `groq-20b` | deepseek-chat → clip-assured → clip-fast → nim-deepseek → openrouter-free → cloudflare-free → local-qwen | `groq-20b`, `local-qwen`, `cloudflare-free`, `openrouter-free` | Schema validity + latency on small structured outputs (§3.3). Quality here is mostly "does it return clean JSON fast" — the classify workload is the proxy until a dedicated light-pass fixture lands (§2.3). |
| **Local-first passes** (content requirements permits) | `local-qwen` | the cloud ladder strongest-first | `local-qwen` alone, graded against the cloud floor | The question is not "does local beat cloud" (it is not expected to) but "is the local floor good enough to be a usable emergency path" (§3.4). A pass/fail bar, not a ranking. |

Fallback-only routes (`deepseek-chat`, `nim-deepseek`,
`openrouter-free`, `cloudflare-free`) are never called by app code
directly — test-pinned. The six NIM bake-off candidates are
pre-registered but **unarmed**: no production chain falls back to
them; if called directly they walk the full ladder. Nothing in
production behaviour changes until a one-line slug flip arms a winner
(§5.3).

### 1.3 What is NOT in scope for this bake-off

- **The deterministic selection floor.** It scores 0.00 on the Japan
  golds in every variant (SELECTION_QUALITY_BASELINE.md, tip
  90f1cb4) and is a safety net, not a brain. Its improvement is a
  separate lane; the bake-off measures only the LLM path, which is
  what production uses when a transcript exists.
- **Transcription, face tracking, the SigLIP B-roll veto.** Local
  non-LLM intelligence; not routed through the gateway at all.
- **Rendering / export quality.** The bake-off grades the *plan and
  the picks*; whether a plan renders well is the Nov 15 release
  gate's acceptance work, not this protocol.
- **Published benchmarks as a decision input.** AA index and friends
  order the candidate list and name provisional swaps only. A
  published-benchmark ranking alone NEVER arms a swap (§5.1, rule 4).

---

## 2. Workloads — identical inputs/prompts for every candidate

**Iron rule, inherited from the NIM harness:** every workload prompt
is built by the app's own builders and every response is parsed and
validated by the app's own schemas, parsers and validators. No
hand-copied prompts, no invented grading. The same request spec goes
to every candidate; the only thing that changes between candidates is
the model slug (and, for the premium family, the `reasoning_effort`
flag — §2.2).

### 2.1 The shared core: the NIM harness fixtures (reused as-is)

The harness `~/workspace/jcs-next/tools/nim-bakeoff/bakeoff.py`
already implements the core workload set, with fixtures in
`tools/nim-bakeoff/fixtures/` regenerated by
`tools/nim-bakeoff/build_fixtures.py` from in-tree sources (gold set,
cached Japan transcript, TJR test constants, niche profiles). Every
fixture is DERIVED, never hand-written. The full-ladder run reuses
this set unchanged for **all** providers — the harness client is a
plain OpenAI-compatible chat-completions call (`BAKEOFF_BASE_URL` +
`BAKEOFF_API_KEY` + model slug), so it works against DeepSeek direct,
Groq, OpenRouter, Cloudflare and Ollama as well as NIM. 8 calls per
model per pass:

| # | Workload | Fixture | App builders that produce the prompt | App parsers/validators that grade the response | Graded on |
|---|---|---|---|---|---|
| 1 | Moment selection — Japan | `japan_selection.json` | `src.ai.build_transcript_system_prompt("auto")` + `build_transcript_analysis_prompt(transcript)` | `TranscriptAnalysis` pydantic schema; `tools/selection-eval/score_selection.py` | Japan gold recall / precision@5 / mean overlap / traps on g1/g3/g4 (the only golds with real transcript evidence in-tree); duration bounds; transcript grounding; hook-title rules |
| 2 | Moment selection — TJR sanity | `tjr_selection.json` | same builders | same schema + scorer (no golds — sanity metrics only) | Schema validity, grounding, title checks; no gold agreement is claimed (unlabelled) |
| 3 | Per-clip plan — Japan g1 | `japan_plan.json` | `auto_director.plan_service.PLAN_SYSTEM_PROMPT` + `_build_plan_prompt(niche, profile, duration, text, words, audio_features, selection_scores)` | `ClipPlanDraft` schema; `validate_plan_dict` (production validator); deterministic plan rubric v1 (`plan_rubric` in `bakeoff.py`) | Schema validity + rubric /100 (hook, phase coverage, restraint vs the validator, SFX placement, reasons/music) |
| 4 | Per-clip plan — TJR | `tjr_plan.json` | same builders | same validators + rubric | Same, under the `finance_business` profile |
| 5–8 | Niche classify — 4 real cases | `classify_cases.json` | `auto_director.classify.NICHE_CLASSIFICATION_SYSTEM_PROMPT` + `_build_niche_classification_prompt(sample, [], title=…)` | `_parse_json_text_classification`; `apply_evidence_guardrails` (production guardrails) | Raw accuracy AND guardrail-corrected accuracy (TJR vlog, $10M trader, Japan restaurants, podcast control) |

Fixture provenance (from `build_fixtures.py`): Japan selection lines
are formatted by the app's own `format_transcript_for_analysis` from
the faster-whisper utterance cache at
`~/workspace/qm-source/source.transcript_cache.json` (falls back to
each gold window's `transcript_excerpt` if the cache is absent);
TJR selection/plan come from the `TJR_WORDS` faster-whisper word list
in `backend/tests/unit/test_broll_word_sense.py`; plan fixtures carry
real word timings rebased to clip start (audio peaks/pauses are
fixture stand-ins, identical for every model); classify cases are the
real cases from the guardrail suites
(`test_auto_director_classify_guardrails.py`,
`test_auto_director_classify_evidence_window.py`).

**Regenerate before any live run** (fixtures drift when golds,
transcripts or profiles change):

```bash
PY=~/workspace/jcs-modes/backend/.venv/bin/python   # the backend venv python
$PY tools/nim-bakeoff/build_fixtures.py   # writes tools/nim-bakeoff/fixtures/
```

### 2.2 Additional fixture A — premium selection with reasoning_effort=high (REQUIRED for the premium family)

The core harness calls every model with `temperature: 0` and no
`reasoning_effort`. That is correct for the bulk family, but the
production premium pass sends `reasoning_effort=high`, and the
gateway allow-lists that parameter for Groq (`clip-assured`,
`groq-20b`) only. A candidate that is brilliant at temperature 0
and collapses — or refuses the parameter — at high reasoning effort
would be mis-graded by the core set alone.

- **What:** the same two selection fixtures (§2.1, rows 1–2) re-run
  with `reasoning_effort=high` in the request, for every candidate
  whose endpoint accepts the parameter. The harness records, per
  call, whether the parameter was accepted, ignored, or rejected
  (4xx) — a rejection is a **failure of the call**, not a silent
  downgrade to temperature 0.
- **Builders/parsers/validators:** identical to §2.1 row 1–2
  (`build_transcript_system_prompt` / `build_transcript_analysis_prompt`,
  `TranscriptAnalysis`, `score_selection.py`). Nothing new is built
  by hand; the only change is the request parameter.
- **Status:** BUILT 2026-11-14 — `--reasoning-effort high` pass in
  `bakeoff.py` (branch `bakeoff-fixtures-abc` @ `0083b6b`; self-check
  PASSED, 35/35 harness tests). Per call the harness records whether
  the parameter was accepted, ignored, or rejected (4xx); a rejection
  is a failure of the call, not a silent downgrade to temperature 0.
- **Cost:** +2 calls per model per pass (10 calls/model/pass total
  for the premium family).

### 2.3 Additional fixture B — light structured pass (REQUIRED for the light family; BUILT 2026-11-14)

The light family (`groq-20b` entry) serves small structured passes.
The classify workload (§2.1 rows 5–8) is already the lightest
structured workload in the harness and is the interim proxy for this
family — it exercises exactly the skills a light pass needs (short
JSON out, evidence discipline, guardrail interaction). A dedicated
light-pass fixture is still owed, because classify is a *judgement*
task and light passes are mostly *extraction*:

- **What (to build):** one fixture per light-pass shape the app
  actually runs, derived from the same in-tree sources as §2.1
  (test constants and production builders only). The concrete shape
  is the content-requirements pass
  (`backend/src/auto_director/content_requirements_llm.py`, whose
  `PERMITTED_ALIASES` are exactly `clip-assured`, `groq-20b`,
  `local-qwen` — the three rungs this family cares about): feed it a
  real requirements brief from the app's tests, grade the returned
  structure with the app's own parser/validator for that pass.
- **Builders/parsers/validators:** the content-requirements module's
  own prompt builder and response parser — to be named in the fixture
  builder when it is written, following the `build_fixtures.py`
  pattern (AST-extracted test constants, never hand-written inputs).
- **Status:** BUILT 2026-11-14 —
  `tools/nim-bakeoff/fixtures/requirements_focus.json` (branch
  `bakeoff-fixtures-abc` @ `0083b6b`): the content-requirements focus
  pass over the real Expedia brief, graded by the pass's own
  `FocusJudgements` parser plus the app's `_merge_llm_judgement`
  verbatim-evidence gate. The light family's verdict no longer rests
  on the classify proxy alone.
- **Cost when built:** +1–2 calls per model per pass.

### 2.4 Additional fixture C — long-form chunked analysis (REQUIRED for the bulk family; BUILT 2026-11-14)

The bulk family (`clip-fast` entry) analyses long sources in windows
(the chunked analysis walker). Every fixture in §2.1 is
excerpt-scale (a 20 s segment, a 28.6 s clip, gold-range excerpts).
Relative model comparison is unaffected — same input for every model
— but the bulk family's *actual* job (hold coherence across a long
window, don't drift, don't repeat) is not exercised by excerpts.

- **What (to build):** one selection fixture built from a longer
  transcript window — the full cached Japan range transcript
  concatenated (g1+g3+g4 ranges as one windowed input, graded
  against the same three golds), or a JRE excerpt if the Japan cache
  proves too short to be "long-form". Same builders, same schema,
  same scorer as §2.1 row 1. The fixture builder follows
  `build_fixtures.py` (app formatter, derived from the cache, never
  hand-written).
- **Status:** BUILT 2026-11-14 —
  `tools/nim-bakeoff/fixtures/tjr_longform_selection.json` (branch
  `bakeoff-fixtures-abc` @ `0083b6b`): the full REAL_10M_TRANSCRIPT
  (24,033 chars) as one windowed selection input. Note the deviation
  from the first option here: the Japan cache proved too short (36
  utterances, 1,205 chars — `japan_selection` already IS the whole of
  it, so a concatenated Japan fixture would be byte-identical), and
  no JRE transcript exists in-tree, so the fixture uses the TJR long
  transcript with line timestamps estimated at 150 wpm (labelled in
  the fixture). It is unlabelled — sanity metrics only, no gold
  agreement is claimed.
- **Cost when built:** +1 call per model per pass. Note the input is
  longer, so per-call token cost and latency rise for every model
  equally — the comparison stays fair, the absolute latency numbers
  are not comparable to the excerpt set.

### 2.5 Additional fixture D — local-floor expectations (no new fixture; same set, different bar)

`local-qwen` (Ollama `qwen3:8b`) runs the **same 8-call core set**
(§2.1) — no separate fixture is needed, because the question for the
local floor is whether the same production prompts survive an 8B
local model, not whether a different task suits it. Differences are
in execution, not fixtures:

- Endpoint: Ollama OpenAI-compatible API. From the host:
  `http://localhost:11434/v1`; from inside the LiteLLM container
  the gateway uses `http://host.docker.internal:11434/v1`
  (`litellm_config.yaml`, `local-qwen` entry). The bake-off client
  talks to the host endpoint directly.
- Timeout: local inference on the user's RTX 4050 (6 GB VRAM) is
  slower than any cloud hop — set `BAKEOFF_TIMEOUT` higher for this
  rung (600 s) and record it; do not let a timeout masquerade as a
  quality failure.
- Temperature 0, same as every other candidate. No
  `reasoning_effort` (Ollama does not accept it).
- The local floor is graded on the **pass/fail bar** in §3.4, not
  ranked against the cloud models.

### 2.6 What the live run does NOT add

- **No new golds.** g2/g5 stay excluded (no transcript in-tree;
  `gold_set.json` marks them `transcript_grounded=false`). A
  full-source Japan run needs the transcript regenerated from the
  cached source video (faster-whisper) and a fixture rebuild — a
  separate lane, not part of this bake-off.
- **No hand-written prompts anywhere.** If a workload cannot be built
  by an app builder from a derived fixture, it is not run — it is
  listed in §2.3/§2.4 as owed.
- **No production traffic.** The bake-off calls providers directly
  from the harness; it never routes through the production gateway
  chains (a gateway walk would hide which rung answered).

---

## 3. Metrics — per workload, per call, per provider

Every metric below is computed by the harness (`bakeoff.py`:
`aggregate()`, `render_table()`) from the same raw `results.json`.
Nothing is hand-scored.

### 3.1 Per-workload metrics (the quality verdict)

| Workload | Metrics | Noise floor / decision band |
|---|---|---|
| Moment selection (Japan) | recall, precision@5, mean overlap, traps taken, duplicate picks (`score_selection.py`, golds g1/g3/g4) | Gold-set README: **~0.1 on overlap metrics**. A difference ≤ 0.1 is noise. A win needs > 0.1 over the incumbent (§5.1). |
| Moment selection (TJR sanity) | schema validity, grounded count, out-of-bounds count, title violations | No golds — sanity only. A candidate that hallucinates timestamps or ungrounded windows here fails the family regardless of its Japan score. |
| Per-clip plan (×2) | plan rubric v1 /100 (`plan_rubric`: hook 25, phases 20, restraint 25, SFX placement 15, reasons/music 15) + validator drop count | Rubric regression **> 10 points vetoes** a swap (§5.1). The rubric is deterministic and computed on the raw draft vs the validator-surviving plan, so it measures the model's discipline, not the validator's. |
| Niche classify (×4) | raw accuracy (x/4) AND guardrail-corrected accuracy (x/3 — Japan is raw-only, see §2.1) | Any regression in guardrail-corrected accuracy vetoes a swap. The fake-server proof shows the guardrails can rescue a bad model (fake-mid: 1/4 raw → 3/3 corrected) — so raw accuracy alone never decides. |
| Hook titles (all selection segments) | violations / checked (3–9 words, third person, no hashtags/emoji/quotes — `title_violations` in `bakeoff.py`, rules taken from the selection prompt itself) | Any increase in violations vs the incumbent vetoes a swap for the premium family. |

### 3.2 Per-call latency

- Recorded per call (`latency_sec`, wall time of the HTTP round
  trip, one retry on transient 429/5xx with a 20 s delay —
  `chat_completion` in `bakeoff.py`). Reported as **median** (and
  mean) per model across the pass.
- **Role by family:** for the bulk family, latency is a first-class
  metric with a band (§5.1 rule 3b); for the premium family it is a
  tie-breaker only; for the light family it is a first-class metric
  (a light pass that takes 30 s has failed its job); for the local
  floor it is informational (local inference is expected to be
  slower).
- **Honesty note:** NIM free endpoints are shared preview capacity
  and OpenRouter `:free` is a shared pool — latency numbers measure
  the day, not the model. Never rank on a single day's latency.
  Compare medians across the 3 repeat passes (§4) and treat a
  > 2× median gap as the only latency signal worth acting on.

### 3.3 One-shot schema validity

- A call is schema-valid when the response text yields a JSON object
  that validates against the production pydantic schema in **one
  shot** (`TranscriptAnalysis`, `ClipPlanDraft`,
  `_parse_json_text_classification`). Production additionally gets
  pydantic-ai output retries, so this rate is a **lower bound** —
  identical conditions for every model, which is what makes it a
  fair comparison.
- **Gate first:** a challenger must be reliable before it can win
  (§5.1 rule 1): 8/8 on a single pass, or ≥ 23/24 across 3 repeats
  (≥ 29/30 when the premium fixture adds 2 calls). A rung that
  returns unparseable JSON costs a fallback hop on real traffic.
- Validity is reported per workload as well as in aggregate — a
  model that is 8/8 on classify but 0/2 on plans has a plan problem,
  and the aggregate must not hide it.

### 3.4 Free-tier ceiling cost (RPM/day) — what the run itself costs

The run is small by design; the table exists so the live-phase owner
can prove *before* running that no ceiling is approached, and so a
winner's day-to-day cost is part of the verdict (a rung that wins on
quality but cannot serve a day's clips inside its free tier has not
won).

| Provider / rung | Free-tier ceiling (as known 2026-11-14) | Bake-off cost (3-repeat decision run) | Day-to-day fit |
|---|---|---|---|
| NVIDIA NIM (`clip-fast`, `nim-deepseek`, + 6 candidates) | **40 RPM, 10,000 req/day** (verified in-repo; MEMORY.md, NIM_BAKEOFF.md §5) | 8 candidates × 24 calls = **192 calls** (plus the step-0 probe). ~2% of the daily ceiling. | A day's production traffic is ~576 calls at the laptop's ~72-video/day max — under 6% of the ceiling (MEMORY.md token math). NIM is not the binding limit. |
| DeepSeek direct (`deepseek-chat`) | **Pay-per-token** (paise-level; a one-time new-account grant was reported, unconfirmed). Not a free tier — a deliberate, documented choice by the owner (AI_MODEL_ROUTING.md §4). | 24 calls (core set) + 6 premium calls (fixture A) = **30 calls**, excerpt-scale prompts. Cost is paise-level; record the exact token usage from the response `usage` field and report it. | Metered, but the owner ordered strength-first routing knowing this. If the measured cost per selection pass is material, say so in the results — do not hide a metered rung behind "free". |
| Groq (`clip-assured`, `groq-20b`) | **Confirm from the Groq console at run time** — no RPM/day figure is pinned in-repo, and this document does not invent one. The run's cost (24 calls/model) is far below any published Groq free tier; record the limits the console shows when the run starts. | 24 calls per Groq model (+6 premium for `clip-assured`). | Groq-only binds at ~8–13 videos/day/model (MEMORY.md) — Groq is the tightest cloud ceiling on the ladder, which is exactly why the ladder walks past it instead of stopping there. |
| OpenRouter `:free` (`openrouter-free`) | **50 req/day** at ₹0 (reported; MEMORY.md free-API radar). Shared pool, daily-capped. | 24 calls — **~half the daily free allowance**. Run this rung's pass alone, on its own day if needed; do not burn the allowance on repeats and then have none left for the verdict re-run. | A fallback rung, not a workhorse: 50/day cannot carry production. Its value is independence from NIM/Groq, not volume. |
| Cloudflare Workers AI (`cloudflare-free`) | **10,000 Neurons/day** on the Workers Free plan; gpt-oss-120b costs 31,818 neurons/M input tokens — **~10 selection-size passes a day** (AI_MODEL_ROUTING.md §4, Cloudflare's own pricing table). | 24 calls would **exceed the daily Neuron budget** if every call were selection-size. Run `cloudflare-free` on the **classify + plan subset only** (6 calls/pass, 18 across 3 repeats) and take its selection score from the single-pass core set (2 calls) — or spread the 3 repeats across days. Record the Neuron spend per call if the API reports it. | Matches the hop's depth: it is only reached when five routes above it are down or drained. Its ceiling is a feature of its position, not a defect. |
| Ollama local (`local-qwen`) | **No ceiling** — runs on the user's own PC. Cost is wall time + VRAM, not requests. | 24 calls. | The emergency floor. Its limit is speed (RTX 4050, 6 GB VRAM), not quota. |

**No account rotation, no quota evasion, ever.** One account, one
key per provider; keys live only in the user's `.env`; no route is
duplicated under a second key to multiply a quota. If a ceiling
blocks a repeat, the run waits a day — it does not borrow a second
account.

### 3.5 Failure behaviour (does the candidate walk / fail honestly)

Measured, not assumed. For every candidate, the harness records:

- **HTTP failures:** 429 (rate limit), 5xx/outage, timeout,
  connection failure — recorded as an errored call with the exact
  status/body fragment (`chat_completion` returns `ok: False` with
  `error`). One retry on retryable statuses, then the call is
  recorded failed; the harness does **not** silently substitute
  another model (that is the gateway's job, tested separately by
  the router drill — AI_MODEL_ROUTING.md §6).
- **Unparseable responses:** `extract_json` failure modes (empty
  response, no JSON object, unterminated object) — counted against
  schema validity (§3.3), never papered over.
- **Schema-invalid JSON:** a well-formed JSON object that fails
  `TranscriptAnalysis` / `ClipPlanDraft` validation — recorded with
  the first 300 chars of the validation error.
- **Refusals / empty content:** `finish_reason` and empty text are
  recorded; a model that refuses the prompt fails the workload.
- **Reasoning-effort rejection (fixture A only):** a 4xx on
  `reasoning_effort=high` is a failed call for the premium family,
  recorded as such (§2.2).
- **Walk behaviour is out of scope here:** whether the *gateway*
  falls through correctly on each failure shape is already
  drill-proven (Cloudflare drill + ladder v2 drill,
  AI_MODEL_ROUTING.md §6). The bake-off measures the *candidate*;
  the drill measured the *walk*.

---

## 4. Noise floor and repetition (what makes a run decision-grade)

- **Noise floor: ~0.1 on overlap metrics** (gold-set README; the
  harness's own caveat, repeated in NIM_BAKEOFF.md §5). Differences
  at or under 0.1 on Japan mean overlap / recall are noise, not a
  win. The floor is the reason the decision rule (§5.1) demands a
  margin *above* 0.1.
- **The deterministic floor is 0.00** (SELECTION_QUALITY_BASELINE.md)
  — every variant, every window. That is the number the LLM path
  must beat, and it is also a sanity check on the harness: any
  candidate scoring at or below the floor on Japan has failed the
  workload outright, not merely lost to the incumbent.
- **Repetition: 3 full passes per model** (NIM_BAKEOFF.md §5). The
  full set is 10 calls per model per pass (8 core + fixture B +
  fixture C, built 2026-11-14); a decision-grade run is 30 calls
  per model (36 for the premium family with fixture A, which
  re-runs the 2 selection fixtures at `reasoning_effort=high`).
  Compare **medians** across the 3 passes,
  not single-pass bests. A challenger that wins once and regresses
  twice has not won.
- **Sample-size honesty:** 8 calls per pass is a smoke comparison,
  not a statistic. The 3-repeat rule is the minimum that turns it
  into a decision. Do not upgrade a single-pass result into a swap.
- **Determinism:** temperature 0 everywhere. A candidate whose
  temperature-0 outputs vary wildly across repeats is itself a
  finding — record the spread (min/max per metric across passes),
  don't average it away.
- **Fixture freeze:** regenerate fixtures (§2.1) once, before pass
  1, and freeze them for all 3 passes. If a fixture changes
  mid-run, the run restarts from pass 1.

---

## 5. Decision rule — winner per task, and what a swap costs

### 5.1 The rule (applies per task family, per rung)

Adapted from NIM_BAKEOFF.md §5, extended to the whole ladder. A
challenger must clear **all four** gates, in order. Failing any one
ends the challenge for that rung — the incumbent stays.

1. **Reliable first.** Schema validity 8/8 on a single pass, or
   ≥ 23/24 across 3 repeats (≥ 29/30 premium, ≥ 25/26 light,
   ≥ 26/27 bulk — same 96% bar, scaled to the family's call
   count). A rung that returns unparseable JSON costs a fallback
   hop on real traffic.
2. **Win the workload, not the benchmark.** Japan mean overlap or
   recall better than the incumbent rung's model by **more than
   the 0.1 noise floor**, with **no regression** in traps taken,
   niche (guardrail-corrected) accuracy, title violations, or plan
   rubric (rubric regression **> 10 points vetoes**). For the bulk
   family, the win must also hold on fixture C when it exists.
3. **Latency in its place.**
   - (a) Premium family: tie-breaker only. A median per-call
     latency worse than 2× the incumbent needs a quality win at
     the top of the margin band to compensate.
   - (b) Bulk family: first-class, with a band. A challenger must
     not be slower than 1.5× the incumbent's median latency on the
     same fixtures; slower than that, and it must win on quality
     by > 0.2 (double the noise floor) to compensate.
   - (c) Light family: first-class. A light-pass candidate slower
     than 2× the `groq-20b` median has failed the family's job,
     whatever its quality.
   - (d) Local floor: informational only.
4. **No ceiling veto.** The winner must be servable inside its
   free-tier ceiling at the laptop's production volume (§3.4). A
   model that wins on quality but cannot serve a day's clips
   inside its free tier has not won the rung. (DeepSeek direct is
   the documented exception: metered, paise-level, owner-ordered —
   its cost per pass is reported, not hidden.)
5. **If no challenger clears 1–4, the incumbent stays.** The
   published-benchmark ranking alone NEVER arms a swap. A
   provisional-benchmark leader that loses on the workload stays
   provisional.

### 5.2 What stays provisional until live evidence lands

- **Every cross-provider comparison in this document.** No live
  call has been made from this sandbox. The only measured numbers
  anywhere in the bake-off programme are the fake-server responders
  (offline-measured, §6.1) and the deterministic floor 0.00
  (offline-measured, SELECTION_QUALITY_BASELINE.md).
- **The NIM provisional ranking** (NIM_BAKEOFF.md §4):
  `z-ai/glm-5.3-flash` (AA 41.8) > `deepseek-ai/deepseek-v4.1-flash`
  (AA 39.5) > `moonshotai/kimi-k2.6` (AA 27.0) >
  `nvidia/nemotron-3-ultra-550b-a55b` (AA 22.9). Provisional-benchmark
  only. The provisional swap it names (`clip-fast` →
  `z-ai/glm-5.3-flash`) is **prepared, not armed**.
- **The Cloudflare endpoint and slug on the user's account**
  (UNVERIFIED-LIVE, AI_MODEL_ROUTING.md §5). The route is wired,
  pinned and drill-proven, but has never served a real request.
- **The local floor's quality.** `qwen3:8b` is pulled on the user's
  machine (MEMORY.md) but has never been graded on these fixtures.
  Its pass/fail bar (§3.4 / §5.1 applied as a bar, not a ranking)
  is: schema validity ≥ 7/8 per pass (the 8B model gets a slightly
  lower bar than the 96% cloud bar, because it is an emergency path
  — and the lower bar is stated here, not smuggled in later), no
  refusal on any workload, and a Japan recall above the 0.00 floor.
  If it fails that bar, the finding is "local floor is a
  last-resort text path, not a selection path" — and the ladder
  documentation is updated to say so, rather than the bar being
  lowered.
- **Fixture B and C verdicts.** Until those fixtures are built, the
  light and bulk families' verdicts rest on proxies (§2.3, §2.4)
  and are labelled as such.

### 5.3 The exact arming step (once a winner is measured)

Arming a winner is a **one-line slug flip + pinned-test update +
gateway suite**. Nothing else. The full step, per rung:

1. In `~/workspace/jcs-next/litellm_config.yaml`, change the
   `model:` of the rung's entry to the winner's slug (same provider
   prefix and `api_base` as the rung's other entries; for a NIM
   candidate the alias entry already exists pre-registered, e.g.
   `nim-glm-flash` — the flip is on the `clip-fast` entry, not on
   the candidate alias).
2. In `backend/tests/unit/test_litellm_gateway_config.py`, update
   the pinned expectation for that rung to the same slug.
3. Run the gateway suite (the suite that pins the model list,
   chains, free-slug pins and key plumbing). It must pass with no
   test weakened and no pin re-written to fit a different slug.
4. Record the swap in this document's §8 (winner, margin, the 3
   repeat medians, the results path) and in
   `AI_MODEL_ROUTING.md` if the ladder order itself changed.
5. **Never** arm a winner on a single pass, never arm on a
   published benchmark, never arm two rungs in one commit. One
   rung, one commit, one gateway-suite run.
6. The Nov 15 release gate (or the Nov 17 integration job) owns
   the ship decision for any armed swap — a swap lands on a branch,
   is parent-verified, and ships only through the gate.

---

## 6. Offline phase — provable NOW, no keys

Everything in this section was verified in this sandbox on
2026-11-14, with no provider key and no network call to any model.
**Offline-measured.**

### 6.1 Harness self-check (fake server)

```bash
PY=~/workspace/jcs-modes/backend/.venv/bin/python   # the backend venv python
cd ~/workspace/jcs-next
$PY tools/nim-bakeoff/bakeoff.py --self-check
$PY -m pytest tools/nim-bakeoff/tests -q
```

Result on 2026-11-14 (this sandbox, tip `235cf5c`):

- **SELF-CHECK PASSED** (exit 0). The fake server's three
  responders score exactly as designed: `fake-good` — Japan recall
  1.0, mean overlap 1.0, P@5 1.0, 0 traps, schema 8/8, niche 4/4
  raw and 3/3 corrected, 0/4 title violations, plan rubric 95.0;
  `fake-mid` — recall 0.3333, overlap 0.32, P@5 0.5, 3 traps,
  schema 8/8, niche 1/4 raw → 3/3 corrected (the production
  guardrails rescuing a bad model, as designed), 2/3 title
  violations, plan rubric 40.0; `fake-bad` — schema 0/8, niche 0/4,
  no gold score. The harness discriminates good / mediocre /
  broken exactly as specified.
- **Harness tests: 25 passed / 0 failed** (13.14 s).

What this proves: the graders, the plan rubric, the JSON extractor,
the title rules and the fake server all behave as designed. What it
does **not** prove: anything about any real model. The fake
responders are canned strings, not models.

### 6.2 Fixture regeneration

```bash
$PY tools/nim-bakeoff/build_fixtures.py   # writes tools/nim-bakeoff/fixtures/
```

Regenerates all five fixture files from their in-tree sources (§2.1).
Run it after any change to the gold set, the cached Japan transcript,
the TJR test constants, or a niche profile — and always once before
pass 1 of a live run. **Not run from this lane** (it writes into
`~/workspace/jcs-next`, which this doc-only lane must not touch);
the live-phase owner runs it and records the fixture hashes with the
results (§7.4).

### 6.3 Ollama local floor — checked, NOT runnable in this sandbox

Checked 2026-11-14 in this sandbox:

- `which ollama` → **not found** (no Ollama binary installed).
- `curl http://localhost:11434/api/tags` → **no response** (no
  Ollama server listening).
- No `qwen3:8b` model exists here, and none was downloaded.

**Local-live is therefore PENDING, not faked.** This document does
not install Ollama and does not download any model (that would be a
live-environment change outside this lane's remit, and the sandbox
is not the user's PC in any case). The local-floor live run happens
on the user's machine, where Ollama + `qwen3:8b` are already pulled
(MEMORY.md), via the host endpoint `http://localhost:11434/v1`
(§2.5). Until that run, every statement about `local-qwen` quality
is **pending-live**, and the rung's status is "incumbent by
position, unmeasured".

---

## 7. Live phase — for the parent, with real keys

**No live call has been made from this sandbox and no key exists
there by design.** Everything below runs on the user's machine (or a
machine holding his `.env`), with his keys, under his supervision.
Nothing here is to be run from the build sandbox.

### 7.1 Environment variables (one per provider)

Keys are typed locally, never committed, never pasted in chat. The
harness reads exactly two variables plus the model list:

| Variable | Value (live phase) | Notes |
|---|---|---|
| `BAKEOFF_BASE_URL` | per-provider endpoint (see §7.2) | One provider per invocation — the harness is single-provider per run. Run it once per provider, same fixtures, same flags. |
| `BAKEOFF_API_KEY` | the provider's key from the user's `.env` | `NVIDIA_API_KEY`, `DEEPSEEK_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `CLOUDFLARE_API_KEY` (+ `CLOUDFLARE_ACCOUNT_ID` for the endpoint — see §7.3). Ollama needs no key (`api_key: ollama` in the gateway config; pass any placeholder). |
| `BAKEOFF_MODELS` | comma-separated slugs for that provider | See §7.2. |
| `BAKEOFF_TIMEOUT` | 300 (default); 600 for `local-qwen` | Per-call seconds. |
| `BAKEOFF_TEMPERATURE` | 0 (default) | Never change between candidates. |

The backend venv python is required (the harness imports the app):
on the user's machine that is the python in his installed tree's
backend venv; in the build sandbox it is
`~/workspace/jcs-modes/backend/.venv/bin/python` — but the sandbox
holds no keys, so the live phase does not run there.

### 7.2 Slug probe step, per provider (step 0 — minutes, saves the run)

Before any provider's pass, probe that its slugs answer. Any slug
that 404s (or answers a 1-token probe with an error) is dropped from
that run's `BAKEOFF_MODELS` — the harness records failed calls as
errors either way, but dropping keeps the run inside the ceilings
(§3.4). Do not hand-edit a slug mid-run.

| Provider | Base URL | Probe | Candidates for this run |
|---|---|---|---|
| NVIDIA NIM | `https://integrate.api.nvidia.com/v1` | `curl -s https://integrate.api.nvidia.com/v1/models -H "Authorization: Bearer $NVIDIA_API_KEY"` — check the 8 slugs in NIM_BAKEOFF.md §3 against the listing | `z-ai/glm-5.3-flash,deepseek-ai/deepseek-v4.1-flash,moonshotai/kimi-k2.6,nvidia/nemotron-3-ultra-550b-a55b,mistralai/mistral-large-4-1t-a52b,qwen/qwen3-235b-a22b,meta/llama-4-maverick-17b-128e-instruct,deepseek-ai/deepseek-v4-pro` |
| DeepSeek direct | `https://api.deepseek.com/v1` | `curl -s https://api.deepseek.com/v1/models -H "Authorization: Bearer $DEEPSEEK_API_KEY"` | `deepseek-chat` (single candidate; it competes as the ladder top, not as a rung challenger) |
| Groq | `https://api.groq.com/openai/v1` | `curl -s https://api.groq.com/openai/v1/models -H "Authorization: Bearer $GROQ_API_KEY"` | `openai/gpt-oss-120b` (`clip-assured`), `openai/gpt-oss-20b` (`groq-20b`). Note the harness passes the model string verbatim — use the Groq-side slug, not the gateway alias. |
| OpenRouter | `https://openrouter.ai/api/v1` | `curl -s https://openrouter.ai/api/v1/models -H "Authorization: Bearer $OPENROUTER_API_KEY"` and confirm the `:free` suffix is present on the slug | `nvidia/nemotron-3-ultra-550b-a55b:free` |
| Cloudflare Workers AI | `https://api.cloudflare.com/client/v4/accounts/<ACCOUNT_ID>/ai/v1` | `GET https://api.cloudflare.com/client/v4/accounts/<ACCOUNT_ID>/ai/v1/models` with the token must list `@cf/openai/gpt-oss-120b` (AI_MODEL_ROUTING.md §5) | `@cf/openai/gpt-oss-120b` |
| Ollama (user's PC) | `http://localhost:11434/v1` | `curl http://localhost:11434/api/tags` must list `qwen3:8b` | `qwen3:8b` |

### 7.3 Cloudflare — the extra pre-flight

`cloudflare-free` is the only rung whose endpoint is
UNVERIFIED-LIVE on the user's account. Before its bake-off pass,
run the AI_MODEL_ROUTING.md §5 check on the user's machine:

1. In `D:\EditedTool\JarvisClipStudio\.env`, confirm
   `CLOUDFLARE_API_KEY` and `CLOUDFLARE_ACCOUNT_ID` are both set.
2. In `compose.override.yml`, the `litellm` service's environment
   list must carry both passthrough lines. (The user's live
   override may not have them yet — his keys predate the route;
   the shipped template carries them.)
3. Restart the stack, then one forced call through the gateway with
   model `cloudflare-free` must return a real completion. Until
   that runs, the Cloudflare bake-off pass is blocked — do not
   substitute a guessed endpoint.

### 7.4 The run (step 1) and where results land

```bash
PY=<the backend venv python on the live machine>   # the harness imports the app
cd ~/workspace/jcs-next/tools/nim-bakeoff          # or the equivalent path on the live machine
$PY build_fixtures.py                              # once, before pass 1; record fixture hashes

# One invocation per provider (example: NIM). Repeat the whole set
# 3 times per provider for a decision-grade run (§4).
export BAKEOFF_BASE_URL=https://integrate.api.nvidia.com/v1
export BAKEOFF_API_KEY=$NVIDIA_API_KEY
export BAKEOFF_MODELS='z-ai/glm-5.3-flash,deepseek-ai/deepseek-v4.1-flash,moonshotai/kimi-k2.6,nvidia/nemotron-3-ultra-550b-a55b,mistralai/mistral-large-4-1t-a52b,qwen/qwen3-235b-a22b,meta/llama-4-maverick-17b-128e-instruct,deepseek-ai/deepseek-v4-pro'
$PY bakeoff.py --out results/live-YYYYMMDD-nim-pass1   # writes results.json + table.md

# DeepSeek direct / Groq / OpenRouter / Cloudflare / Ollama: same
# command, different BAKEOFF_BASE_URL / BAKEOFF_API_KEY / BAKEOFF_MODELS
# from §7.2. Cloudflare: plan + classify subset only (§3.4).
# Ollama: BAKEOFF_TIMEOUT=600, BAKEOFF_BASE_URL=http://localhost:11434/v1.
```

**Results location:** `results/` next to the harness (outside the
repo's tracked tree — results are never committed). One directory
per provider per pass: `results/live-YYYYMMDD-<provider>-pass<N>/`
containing `results.json` + `table.md`. When a provider's 3 passes
are complete, render the report into this document:

```bash
$PY bakeoff.py --results results/live-YYYYMMDD-<provider>-pass3/results.json \
  --report ~/workspace/research_notes/sprint-7day/AI_MODEL_BAKEOFF.md
```

Note: the harness's `--report` renders the NIM report shape
(`render_report` in `bakeoff.py`); the full-ladder §8 table below
is filled in by hand from the per-provider `table.md` files, with
medians across the 3 passes — do not paste a single pass as the
verdict.

**Rate-limit ceilings during the run** (§3.4): NIM 40 RPM /
10,000 req/day; OpenRouter `:free` 50 req/day (run alone, watch
the count); Cloudflare ~10 selection passes/day (subset only);
Groq — console limits recorded at run start; DeepSeek — token
usage recorded per call from the `usage` field; Ollama — none.
If any provider 429s mid-run, the harness retries once after 20 s
and then records the failure — do not re-run the pass to "get a
cleaner result"; a 429 on the free tier is data (§3.5).

### 7.5 Keys needed (live-phase owner)

- `NVIDIA_API_KEY` — NIM pass (8 candidates). **The Day-2 live
  bake-off is blocked on this key** (SPRINT_LOG, 2026-11-13):
  Luffy's key, in his `.env`.
- `DEEPSEEK_API_KEY` — DeepSeek direct pass (ladder top).
- `GROQ_API_KEY` — Groq pass (`clip-assured` + `groq-20b`).
- `OPENROUTER_API_KEY` — OpenRouter `:free` pass.
- `CLOUDFLARE_API_KEY` + `CLOUDFLARE_ACCOUNT_ID` — Cloudflare
  pass (after the §7.3 pre-flight).
- Ollama — no key; `qwen3:8b` already pulled on the user's machine.

All keys already exist in the user's `.env` (MEMORY.md: keys
complete Nov 12) except the Cloudflare passthrough lines, which may
be missing from his live `compose.override.yml` (§7.3). Nothing is
asked of Luffy during the overnight autonomous runs — the live phase
is his validation-window work, not a sandbox job.

---

## 8. Results

_Live run pending._ When it completes, this section carries one
comparison table per task family (medians across the 3 passes),
built from the per-provider `results.json` files (§7.4):

| Family / rung | Candidate | Japan recall | Japan mean overlap | P@5 | Traps | Schema valid (x/24) | Niche raw | Niche +guardrails | Title violations | Plan rubric /100 | Median latency | Ceiling hit? |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Premium selection | _pending-live_ | | | | | | | | | | | |
| Bulk / fast | _pending-live_ | | | | | | | | | | | |
| Light structured | _pending-live_ | | | | | | | | | | | |
| Local floor | _pending-live_ | | | | | | | | | | | |

For each family, the winner (if any) is named here with the margin
over the incumbent, the 3-pass medians, and the arming commit that
followed (§5.3). If no challenger cleared §5.1, the entry reads
"incumbent stays — [reason]".

---

## 9. Limitations (read before quoting any number)

- **One labelled source, five golds; three scored.** Japan
  agreement covers g1/g3/g4 only — the golds with real transcript
  evidence in-tree. g2/g5 are excluded, not estimated. Differences
  under ~0.1 on overlap metrics are noise (gold-set README).
- **Fixture transcripts are excerpt-scale** (except fixture C when
  built); production analyses long sources in windows. Relative
  model comparison is unaffected (same input for every model);
  absolute agreement numbers are not production predictions.
- **Schema validity is one-shot** against the production schemas;
  production also gets pydantic-ai output retries, so live validity
  understates production validity for every model equally.
- **Classify fixtures run without named entities** and (except the
  $10M case's guardrail sample) without visual/audio evidence — the
  same evidence shape for every model.
- **NIM free endpoints are shared preview capacity; OpenRouter
  `:free` is a shared pool.** Latency numbers measure the day, not
  the model. Treat latency as §5.1 directs (tie-breaker for
  premium, banded for bulk/light, informational for local).
- **Fixtures A/B/C are BUILT** (2026-11-14, branch
  `bakeoff-fixtures-abc`, merged `eafbbe5`) — the premium, light
  and bulk verdicts are now measurable (§2.2–§2.4). Until the live
  phase runs they remain pending-live, not proxy-based. Fixture C's
  timestamps are estimates (150 wpm), not production times.
- **The deterministic floor is 0.00** — any candidate at or below
  it on Japan has failed the workload, not lost a close race
  (SELECTION_QUALITY_BASELINE.md).
- **DeepSeek direct is metered, not free.** Its per-call token cost
  is part of its verdict (§3.4); do not present a DeepSeek win as
  a free win.
- **No live evidence exists yet.** Every model-quality statement in
  this document other than §6 is provisional-benchmark or
  pending-live. Nothing here justifies a swap.

---

## Sources

- `~/workspace/jcs-next/AI_MODEL_ROUTING.md` (§1–§2 provider roles
  and the one ladder; §4 never-paid/never-rotate guarantees; §5–§6
  verification status and drills) — ladder order, rung roles,
  ceilings for Cloudflare, DeepSeek metering.
- `~/workspace/jcs-next/litellm_config.yaml` — the authoritative
  rung slugs, endpoints, fallback chains, retry/cooldown settings.
- `~/workspace/research_notes/sprint-7day/NIM_BAKEOFF.md` (§2–§5)
  — NIM-rungs method, candidate roster, slug verification,
  provisional ranking, NIM live-run protocol, decision rule this
  document extends. Not duplicated here.
- `~/workspace/jcs-next/tools/nim-bakeoff/README.md`,
  `bakeoff.py` (1,184 lines), `build_fixtures.py`,
  `offline_evidence.json`, `tests/test_bakeoff.py` — the harness,
  its fake server, its fixtures and its offline proof.
- `~/workspace/research_notes/sprint-7day/SELECTION_QUALITY_BASELINE.md`
  — deterministic floor 0.00 on the Japan golds; the LLM path is
  what the bake-off must measure.
- `~/workspace/jcs-next/tools/selection-eval/` (gold set,
  `score_selection.py`, README) — the ~0.1 noise floor; g1/g3/g4
  transcript-grounded, g2/g5 excluded.
- `~/workspace/research_notes/sprint-7day/SPRINT_LOG.md` (tail,
  2026-11-13 ~23:24 – 2026-11-14 01:24 IST) — programme state:
  Day 1 complete, settled tip `235cf5c`, gate green on 9ba6602,
  Day-2 live bake-off blocked on Luffy's NIM key.
