# SnipNova benchmark pack — RTX 4050 ready

Measures ONE fixed generation end-to-end on the machine the app is
installed on: how long each stage took (the app's own records), how hard
the machine worked (GPU utilisation, VRAM, CPU, RAM sampled live), and
whether the output is what it should be (resolution, codec, encoder,
loudness, duration, frame stills). Every run is written up as one report
and compared against a stored baseline, so "did the update make it
faster or slower?" becomes a number instead of a feeling.

## Run it

Prerequisites: the app stack is up (`docker compose up -d` from the app
root) and nothing else is generating — the benchmark samples the whole
machine, so a second task pollutes the numbers.

```powershell
cd D:\EditedTool\JarvisClipStudio\tools\benchmark-pack
.\run-benchmark-pack.ps1
```

Then follow the on-screen workload instructions (they are also in
FIXTURE.md): start the fixed task in the app UI and leave the window
open. The script detects the task, samples while it processes, and
writes the report when it finishes.

Variations:

```powershell
.\run-benchmark-pack.ps1 -TaskId <id>   # attach to a running/finished task
.\run-benchmark-pack.ps1 -SetBaseline   # make THIS run the new baseline
.\run-benchmark-pack.ps1 -SampleSeconds 2
```

## What lands where

- `benchmark-runs\run-<timestamp>\report.md` — the run's report.
- `...\resources.csv` — the raw resource samples behind the report.
- `...\frames\` — 3 stills from clip 1 (judge framing/captions/lip sync
  by eye; no automated number replaces this).
- `tools\benchmark-pack\baseline.json` — the stored baseline. The first
  run creates it; later runs compare against it and never overwrite it
  unless you pass `-SetBaseline`.

## Reading the numbers honestly

- Stage timings are the app's own `stage_timings_json` (download,
  transcription, analysis, pipeline, render loop) — the pack consumes
  them, it does not run a parallel timing system. The older Phase-1
  script (`run-benchmark.ps1`, per-task log-splitting) still works and
  splits stages further from worker logs when you need that.
- The render-loop figure includes per-clip persistence and QC, which
  run inside that loop in the code. It is not pure encode time.
- `cache_hit = true` invalidates the front stages (transcript/analysis
  were reused). The report warns when it sees it. Benchmark with a
  video the app has never processed.
- The A/V figure in the quality table is a container start-offset from
  ffprobe — a sanity check, NOT a lip-sync measurement.
- An attached, already-finished task reports utilisation as NOT
  SAMPLED. Utilisation is only ever measured live.

## Files

| File | Role |
|---|---|
| `run-benchmark-pack.ps1` | Orchestrator: specs, workload detection, sampling control, harvest, quality checks, report |
| `sampler.ps1` | Resource sampler (nvidia-smi + Windows counters → CSV) |
| `benchmark-lib.ps1` | Pure logic: probe checks, LUFS parse, aggregation, report + baseline diff |
| `make-synthetic-fixture.ps1` | Offline fallback fixture generator (worker ffmpeg) |
| `FIXTURE.md` | The fixed workload definition — do not change mid-series |
| `tests\run-remote-validation.ps1` | Off-machine logic validation (28 assertions; needs only PowerShell) |

| `vram-scenario.ps1` | VRAM collision scenario for the 4050 (see below) |
| `tests\run-vram-scenario-validation.ps1` | Off-machine logic validation for the VRAM telemetry parsing (needs only PowerShell) |

| `separation-scenario.ps1` | Music-heavy separation check: hallucination probe + export attenuation (see below) |
| `tests\run-separation-scenario-validation.ps1` | Off-machine logic validation for the separation scenario helpers (needs only PowerShell) |

## VRAM scenario (RTX 4050, 6 GB)

`vram-scenario.ps1` exercises the Wave-4 collision chain on the real
machine - whisper model resident, then a local-LLM (Ollama) classification
attempt, then Demucs separation - and reports the free VRAM at every
pipeline stage boundary from the app's own Stage-0 telemetry lines
(`vram stage=...` in the worker log), plus the sampler's whole-machine
trace and whether the Stage-1 fix markers (whisper eviction, Demucs
device choice / CPU retry) appear. Setup is in the script's header:
the stack must be started with `PROVIDER_MODE=local-first` and
`SEPARATION_ENABLED=true` from the beginning (restarting the worker
mid-scenario would evict the resident whisper model being measured).
Runs land in `benchmark-runs\vram-<timestamp>\`.

## Separation scenario (music-heavy check)

`separation-scenario.ps1 -MusicWindowStart <s> -MusicWindowEnd <s>` is
the user-machine leg of separation Stage 3. It walks you through two
tasks on the SAME music-heavy content under two different filenames
(fixture-off / fixture-on — the app's caches key on the source
reference, so one file reused for both legs would invalidate the
second): leg A without the separation opt-in, leg B with it, on a
stack started with `SEPARATION_ENABLED=true`. The fixture needs one
labelled music-only window (no speech/lyrics) in source seconds. The
report verifies each leg from the database (`separation_opt_in` as
intended, `cache_hit` false — a failing leg is marked INVALID), counts
each task's transcript words inside the music window from its own
transcript cache (the hallucination probe: the ON leg should read
zero), and measures mean volume in the window on each task's
most-overlapping exported clip via ffmpeg `volumedetect` in the worker
(the attenuation figure). Runs land in
`benchmark-runs\separation-<timestamp>\`.

