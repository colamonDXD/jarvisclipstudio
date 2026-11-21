# NIM model bake-off harness

Decides, on **this app's real workloads**, which model should hold the
two NIM rungs of the free-brain ladder (`clip-fast`, currently
Nemotron Ultra, and `nim-deepseek`, currently DeepSeek V4 Flash on
NIM) - instead of swapping on published benchmarks alone.

## What it measures

Every prompt is produced by the app's own builders and every response
is parsed by the app's own code - no hand-copied prompts:

| Workload | App code exercised | Scored on |
|---|---|---|
| Moment selection | `src.ai` prompt builders + `TranscriptAnalysis` schema + `tools/selection-eval/score_selection.py` | Japan gold recall / precision@5 / mean overlap / traps (g1, g3, g4 - the golds with in-tree transcript), TJR sanity segment, duration bounds, transcript grounding, hook-title rules |
| Per-clip plan | `auto_director.plan_service` system prompt + builder + `ClipPlanDraft` schema + `validate_plan_dict` | Schema validity + deterministic plan rubric v1 (hook, phase coverage, restraint vs the production validator, SFX placement, reasons/music) |
| Niche classify | `auto_director.classify` prompt + parser + `apply_evidence_guardrails` | Raw accuracy AND guardrail-corrected accuracy on 4 real cases (TJR vlog, $10M trader, Japan restaurants, podcast control) |

Plus per-call latency and one-shot schema-validity across all 8 calls
per model (production also gets pydantic-ai output retries, so the
validity rate is a fair lower bound, identical for every model).

## Files

- `bakeoff.py` - the harness (HTTP client, graders, aggregation,
  table/report rendering, fake server, CLI).
- `build_fixtures.py` - regenerates `fixtures/` from in-tree sources
  (gold set, cached Japan transcript, TJR test constants, profiles).
  Run it after any of those change.
- `fixtures/` - the pinned bake-off inputs (JSON).
- `offline_evidence.json` - the candidate roster with per-slug
  verification status and the provisional (benchmark-only) ranking.
- `tests/test_bakeoff.py` - offline proof: pure scorers + the full
  pipeline against the fake server.
- Results land outside the repo: `results/` locally, report at
  `~/workspace/research_notes/sprint-7day/NIM_BAKEOFF.md`.

## Offline proof (no key, no network)

```bash
backend/.venv/bin/python tools/nim-bakeoff/bakeoff.py --self-check
backend/.venv/bin/python -m pytest tools/nim-bakeoff/tests -q
backend/.venv/bin/python tools/nim-bakeoff/bakeoff.py --offline   # report from evidence only
```

The fake server ships three responders with designed behaviour:
`fake-good` (gold-exact selections, disciplined plans, correct
niches), `fake-mid` (schema-valid but trap-taking, first-person
titles, podcast-biased, over-effected plans with generic reasons) and
`fake-bad` (never returns JSON). The tests assert the harness scores
them exactly as designed - including the production guardrails
rescuing `fake-mid`'s niche answers from 1/4 raw to 3/3 corrected.

## Live run (NIM)

See the report's §5 (live-run protocol) for the full version,
including the step-0 slug probe against `GET /v1/models` and the
decision rule. Short version:

```bash
cd tools/nim-bakeoff
PY=../../backend/.venv/bin/python   # the backend venv python (the harness imports the app)
export BAKEOFF_BASE_URL=https://integrate.api.nvidia.com/v1
export BAKEOFF_API_KEY=$NVIDIA_API_KEY     # typed locally, never committed
export BAKEOFF_MODELS='z-ai/glm-5.3-flash,deepseek-ai/deepseek-v4.1-flash,moonshotai/kimi-k2.6,nvidia/nemotron-3-ultra-550b-a55b,mistralai/mistral-large-4-1t-a52b,qwen/qwen3-235b-a22b,meta/llama-4-maverick-17b-128e-instruct,deepseek-ai/deepseek-v4-pro'
$PY bakeoff.py --out results/live-YYYYMMDD
$PY bakeoff.py --results results/live-YYYYMMDD/results.json --report ~/workspace/research_notes/sprint-7day/NIM_BAKEOFF.md
```

8 calls per model per pass; repeat 3 passes for a decision-grade
comparison (the gold set's noise floor is ~0.1 on overlap metrics).

## Known limits

- Japan agreement covers 3 of 5 gold windows (g1/g3/g4). g2/g5 have
  no transcript in-tree; they are excluded, never estimated.
- Fixture transcripts are excerpt-scale; production analyses long
  sources in windows. The comparison is relative (same input for
  every model), not a production prediction.
- Unverified candidate slugs are flagged in `offline_evidence.json`;
  the step-0 probe drops any that 404 before the real run starts.
