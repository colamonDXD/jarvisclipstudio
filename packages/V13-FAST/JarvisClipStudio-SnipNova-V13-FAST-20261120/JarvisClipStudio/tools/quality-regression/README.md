# Quality regression harness — PERMANENT fixture

This harness is the quality milestone's regression set. It is **permanent**:
the 5 ranges in `fixture.json` come from a real user run (task `80fc89e3`,
"CORE $1 VS $10,000 RESTAURANTS IN JAPAN", YouTube `ANoZNWQXYZ4`) whose
exports showed the two headline defects — blurred-pad vertical layout and a
podcast misclassification. Every future quality change re-runs this harness
and compares measured reports. Do not edit the ranges.

## What it does

For each range it reproduces the worker's deterministic render core with the
chosen checkout's own code — `build_edl` (as-run profile from the fixture) →
`attach_reframe_plan` → `create_optimized_clip` — producing a real exported
MP4, then measures it:

- ffprobe specs (codec, size, fps, duration)
- **subject size**: face-bbox area as % of the output frame (YuNet, the
  repo's own model file), union-of-faces and largest-face, 6 sampled frames
- **A/V sync**: envelope cross-correlation of export audio vs source range
  audio → offset in ms with correlation peak (weak peak = weak evidence)
- opening-frame grabs (0.1s / 0.5s / 1.0s / mid) as PNGs
- EDL cut list + reframe shot modes **with their recorded reasons**
- render wall-clock seconds

## Running it (dev sandbox)

The source video is not committed. Download it once:

```bash
mkdir -p ~/workspace/qm-source
~/workspace/render-bench/venv/bin/python3 -m yt_dlp --no-check-certificates \
  -f "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080]" \
  --merge-output-format mp4 -o "~/workspace/qm-source/source.%(ext)s" \
  "https://www.youtube.com/watch?v=ANoZNWQXYZ4"
```

Then, BEFORE (milestone-2 code) and AFTER (any branch) runs:

```bash
RBSP=$(~/workspace/render-bench/venv/bin/python3 -c "import site; print(site.getsitepackages()[0])")

PYTHONPATH=$RBSP ~/workspace/scratch-lane4/venv/bin/python \
  tools/quality-regression/run_regression.py \
  --worktree ~/workspace/jcs-m2 --label before-m2 \
  --source ~/workspace/qm-source/source.mp4

PYTHONPATH=$RBSP ~/workspace/scratch-lane4/venv/bin/python \
  tools/quality-regression/run_regression.py \
  --worktree <after-worktree> --label after-<name> \
  --source ~/workspace/qm-source/source.mp4
```

Outputs land in `~/workspace/research_notes/quality-milestone/runs/<label>/`
(`report.md` + per-range JSON + exported MP4s + frame grabs + manifest with
the exact git commit measured). Use `--profile` to render with a different
profile (recorded in the manifest) and `--only r1,r3` for a subset.

**Low-memory / resumable mode** (the dev sandbox has ~6 GB shared): run in
two phases so the whisper process and the render processes never share a
process. Phase 1 seeds the transcript cache alone:

```bash
... run_regression.py --worktree <wt> --label <label> --source <src> --cache-only
```

Phase 2 renders one range per process (`--only r1`, then `r2`, …). Runs are
resumable: per-range JSONs persist in the output folder, and the final
invocation's `report.md` folds in records from earlier partial invocations
under the same label (the report header states which ranges were rendered
in the last invocation and which were resumed).

## Honesty box

Not reproduced: the LLM pipeline steps upstream of the ranges (moment
selection, clip plan, hook enforcement) — ranges/titles/profile are the
as-run fixture values; the production music-asset library (absent in the dev
sandbox; missing beds degrade as they do in production); word timings come
from a local faster-whisper pass over the 5 ranges only (`QM_WHISPER_MODEL`,
default `small`), seeded into the pipeline's own transcript-cache format.
Caption timing is spot-checked here; the full caption audit is workstream E.
