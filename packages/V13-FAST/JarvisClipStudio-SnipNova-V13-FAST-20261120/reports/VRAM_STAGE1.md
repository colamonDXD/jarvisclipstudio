# Wave 4 VRAM — Stage 0 + Stage 1 (build-lite) report

**Branch:** `feature-vram-lite` off next-milestone @ `005e88e` (HEAD verified before branching), worktree `~/workspace/jcs-vram`. **7 commits, tree clean. NOT merged, packaged, or uploaded.**
**Scope note:** the full admission-control manager was NOT built — the scoping recommends against it, and this lane executed Stages 0+1 only. Note: the saved `SCOPING.md` on disk ends mid-sentence inside Option B's cons (the file itself carries a truncation marker) — there is no §5/§6 text in the saved file; the stage plan below follows the scoping's §§1–4 plus the lane brief's restatement of §5, and one interpretation is flagged where the brief and the saved text diverge (the pre-inference unload, below).

## Commits

| Commit | What |
|---|---|
| `372211f` | Stage 0: `media/vram.py` telemetry (`vram_snapshot`, `log_vram`) + stage-boundary wiring |
| `1bac323` | Stage 0: benchmark-pack `vram-scenario.ps1` + lib parsers + off-machine validation script + README |
| `fa9d9c3` | Fix 1: faster-whisper cache eviction + 3 call sites |
| `f649a95` | Fix 2: Ollama `keep_alive: 0` unload handshake + 2 call sites |
| `fb082ca` | Fix 3: Demucs free-VRAM device gate + one CPU retry |
| `fd5c29c` | Fix 4: sequential-whisper + model-load CUDA-OOM catch → CPU int8 |
| `c3f0a4f` | CHANGELOG entries (changelog.test.ts 4/4 immediately after) |

## Stage 0 — telemetry (no behaviour change)

New `backend/src/media/vram.py`: `vram_snapshot()` returns `(free, total)` bytes via `torch.cuda.mem_get_info()`, or `None` = *unknown* on any failure (no torch / no CUDA / probe error) — never raises, and None is never treated as zero by any consumer. `log_vram(stage)` emits one structured INFO line, `vram stage=<name> free_mb=<n> total_mb=<n>` (DEBUG with `free_mb=unavailable` when no snapshot exists, so CPU-only deployments are not spammed).

Wired at the boundaries the collision scenario exercises: `transcription_start`/`transcription_end` (`get_video_transcript`, dispatch wrapped in try/finally — the only structural touch, returns unchanged), `separation_start`/`separation_end`/`separation_cache_hit` (`separate_vocals`), `classification_start`/`classification_end` (`classify_niche` around the candidate walk).

Benchmark pack: `tools/benchmark-pack/vram-scenario.ps1` walks the operator through the scoping §3 chain — whisper resident → local-first classification attempt → separation opt-in — with `sampler.ps1` running, then harvests the worker log and writes `benchmark-runs/vram-<timestamp>/report.md` (per-stage first/min free VRAM from the app's own telemetry, sampler peaks, Stage-1 fix-marker counts). Setup is in the script header: `PROVIDER_MODE=local-first` + `SEPARATION_ENABLED=true` from stack start (restarting the worker mid-scenario would evict the resident whisper model being measured). `benchmark-lib.ps1` gained two additive pure functions (`ConvertFrom-VramTelemetryLine`, `Get-VramStageSummary`); the existing lib and its 28-assertion validation are untouched. New `tests/run-vram-scenario-validation.ps1` covers the parsers in the pack's Assert style (16 assertions: prefixed/bare lines, noise, the `free_mb=unavailable` form rejected, min/first/count aggregation, first-seen order, empty input). **The PowerShell was not executed in this sandbox (no PowerShell on the VM) — it is written to the pack's self-test pattern and is off-machine/user-machine validation pending**, exactly like the pack's own remote validation.

## Stage 1 — the four fixes

1. **Whisper cache eviction** (`transcription.release_faster_whisper_models()` — drops the shared `_FASTER_WHISPER_MODEL_CACHE` dict, CUDA and CPU-fallback entries alike, `gc.collect()`, returns the count; never raises). Call sites: (a) `get_video_transcript` immediately before a separation run; (b) `classify_niche` before the candidate walk **only when the chain contains a local route** (`provider_manager.is_local_alias` — `local-qwen` alias or `ollama:*`), via `_release_whisper_for_local_candidates`; cloud-only chains keep the model warm; (c) a `finally:` on the existing try in `workers/tasks.py process_video_task` — every task end (success/cancel/failure/retry-defer), best-effort, cannot mask the job outcome. Reload cost is seconds; transcripts are cached per-source downstream.
2. **Ollama unload handshake** (`vram.request_ollama_unload()` — POST `{root}/api/generate` with `{"model": <name>, "keep_alive": 0}`, 2 s timeout, True only on HTTP 200, **never raises**; refused/absent/stopped all return False). The model name derives from config's direct `ollama:*` route (whose default names the same model the gateway alias serves); the native root is the configured base URL minus its `/v1` suffix. Call sites — the two points the scoping's Option B item 2 names: before local transcription (`get_video_transcript`, local providers only — AssemblyAI/YouTube paths never touch the card), and after a classification the local model actually served (`_unload_local_model_after_classification`, gated on `is_local_alias(chain["model_used"])`, run via `asyncio.to_thread`). **Interpretation flagged:** the brief's §5.2 restatement also lists an unload *immediately before* local inference; I did not implement that half — unloading the model you are about to call only forces a reload, and the VRAM room for the local load is made by fix 1's eviction, the half of the seam that controls memory the app owns. The unload points implemented are the ones with a mechanism (after a local stage completes; before transcription begins).
3. **Demucs device gate + CPU retry** (`separation._separation_device()`): CUDA only when present **and** `mem_get_info` free ≥ floor (default 2250 MB ≈ the scoping's ~2.2 GB headroom over htdemucs' measured ~1.5 GB class; `SEPARATION_CUDA_MIN_FREE_MB` overrides, garbage/non-positive → default). Probe-unknown (`None`) preserves the old presence-only behaviour. On a CUDA subprocess failure, exactly one CPU retry before returning None; CPU-first failures are not retried. `_run_demucs`' call shape is unchanged, so existing stubs bind as before; the never-fail contract is intact (any failure → None, no sidecar, task continues on original audio).
4. **Sequential/load OOM catch** (`transcribe_with_faster_whisper`): a CUDA-OOM **model load** retries once on the CPU int8 model; a CUDA-OOM **sequential transcribe** retries once on the CPU int8 model — except when the model in hand already *is* that CPU model (the batched ladder's re-point after its own fallback), where it re-raises instead of looping, and except when the fallback can't load, where the original error propagates. Non-OOM errors raise untouched with no CPU attempt — historic behaviour preserved. No call shapes changed: the loader still gets the historical single argument on the default path; the device/compute kwargs are the ones the batched ladder already used (the conditional-kwarg parity pattern).

## Proof (all VRAM figures stubbed — real 4050 numbers are user-machine validation)

- **New tests:** `backend/tests/unit/test_vram_stage1.py` — **34 passed**. Per fix: telemetry format/silence/never-raises; eviction count + cache emptied, release-before-separation ordering, no release without separation, classify evicts for `local-qwen` and `ollama:*` candidates and not for cloud-only; unload POST URL/payload/timeout exact, transport failure + HTTP 500 silent, no derivable model name = no request, transcription asks only for local providers, classify asks only when the local model answered, a raising handshake still returns the classification; Demucs device matrix (roomy/starved/unknown/no-CUDA), threshold env override, end-to-end gated-to-CPU, CUDA-fail→CPU-retry success with device sequence `[cuda, cpu]`, double failure → None with no sidecar, CPU failure attempted exactly once; load-OOM → CPU completes (`[cuda, cpu]` constructions), sequential-OOM → CPU retry completes, OOM on an already-CPU model fails the stage, non-OOM load/sequential errors propagate with no CPU attempt, OOM after the batched ladder's CPU re-point does not retry.
- **Neighbour suites (serial, TMPDIR on workspace disk, summaries from log files):** transcription + separation families **125 passed / 1 skipped**; classify family **120 passed**; workers-adjacent + remaining importers (incl. `test_task_recovery_cancel`, `test_oom_guard`) **168 passed**. Total **413 passed / 1 skipped / 0 failed**.
- **No existing test was modified or weakened** — the lane adds one test file and touches no existing test.
- **CHANGELOG:** one **Fixed** + one **Under the hood** entry under 2026-11-11; `changelog.test.ts` run immediately after via the standard node_modules symlink: **4 passed**.
- Sandbox environment note: torch 2.13.0+cu130 is installed in the test venv but CUDA is unavailable (no GPU), so every probe here returns None and the presence-only fallbacks were exercised live; the gated paths are pinned by the stubbed tests above.

## Not covered / honest limits

- The `process_video_task` finally block is code-reviewed, not unit-tested (the arq job harness has no unit coverage elsewhere either; the block is six best-effort lines that cannot mask an outcome).
- Real 4050 behaviour — actual free-VRAM values at the boundaries, eviction/unload effect on a live local classification, Demucs gate decisions under real residency — is **user-machine validation pending** via `vram-scenario.ps1` after this lands in a package.
- The PowerShell validation script has not been executed anywhere yet (no PowerShell in the sandbox); its Python-side contract (the telemetry line format) is pinned by `test_log_vram_emits_structured_line`.

## Five states

- **IMPLEMENTED:** yes — Stage 0 + all four Stage-1 fixes, 7 commits on `feature-vram-lite`.
- **INDIVIDUALLY TESTED:** yes — 34 new unit tests, all stubbed-figure behaviour pins.
- **INTEGRATED:** yes on the branch — call sites land in the production seams (transcription dispatch, classify candidate walk, separation run, worker job finally); neighbour families green (413/0).
- **CROSS-FEATURE VERIFIED:** partially — the classify→eviction→unload and transcription→unload→eviction→separation orderings are pinned by unit tests with recording fakes at the real seams; no live GPU pipeline run exists in this sandbox.
- **USER-MACHINE VALIDATION PENDING:** yes — all real VRAM numbers, via the benchmark pack's new VRAM scenario on the 4050.
