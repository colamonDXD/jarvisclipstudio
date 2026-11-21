# Benchmark fixture — the fixed workload

Comparisons are only meaningful if the workload never changes. This is
the workload every benchmark run uses; if it ever has to change, that is
a new baseline (`-SetBaseline`) and old numbers stop being comparable.

## Primary fixture (real video)

- **Source:** https://www.youtube.com/watch?v=z6S9DyxVwLg — Joe Rogan
  Experience clip, ~14 minutes, 3-speaker English podcast. This is the
  project's long-standing bake-off video, chosen because it exercises
  every stage: download, faster-whisper transcription, LLM analysis,
  multi-speaker reframing and rendering.
- **First use:** the app downloads it during the benchmark task itself —
  that download is part of the measurement. Do NOT pre-download it into
  the app and do NOT re-use a video the app has already processed: the
  app caches transcripts/analysis, `cache_hit` becomes true, and the
  front stages stop measuring anything (the report prints this warning
  when it sees it).
- **Cache the file locally (optional, for repeat runs):** after the first
  run, keep your own copy of the downloaded source (e.g. in
  `D:\Downloads`) so the video itself never disappears from YouTube
  mid-comparison-series. Uploading your local copy instead of pasting
  the link changes the download stage (local ingest vs network) — pick
  ONE source method per comparison series and stick to it.

## Fixed task settings (set in the app UI, exactly)

| Setting | Value | Why fixed |
|---|---|---|
| Niche | **Podcast & news** (manual override, NOT auto-detect) | Classifier variance would change segment selection between runs |
| Clip count | **5** | Bounds render time; enough clips to average over |
| Clip length | **Medium (30–60s)** | The default production shape |
| Music | **Off** | Music adds a mixing variable unrelated to machine speed |
| Quality / processing | **Full Quality** | The mode users actually judge; Fast mode numbers would flatter the machine |
| Reference style | **None** | Style application is a separate feature measurement |

## Fallback fixture (synthetic, offline)

If the primary video cannot be downloaded (no network, video removed),
generate the synthetic fixture once:

```powershell
cd D:\EditedTool\JarvisClipStudio\tools\benchmark-pack
.\make-synthetic-fixture.ps1
```

It renders a 180-second 1280×720 animated test clip with a tone track
using the worker container's ffmpeg (nothing to install) and saves it
next to the reports. Upload that file through the app UI with the SAME
fixed settings above.

Honest limits of the synthetic fixture: it exercises download-free
ingest, transcription plumbing and the full render path comparably, but
its "speech" is a tone — transcription content and LLM analysis behave
differently than on real speech, so its transcription/analysis timings
are NOT comparable to the primary fixture's. Never mix the two fixtures
in one comparison series.

## What a run produces

`benchmark-runs\run-<timestamp>\` containing `report.md`,
`resources.csv` (raw samples) and `frames\` (3 stills from clip 1),
plus an updated comparison against `tools\benchmark-pack\baseline.json`.
