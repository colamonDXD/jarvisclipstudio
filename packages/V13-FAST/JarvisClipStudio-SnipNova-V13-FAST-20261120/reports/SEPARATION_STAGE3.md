# Separation Stage 3 — sandbox parts: report

**Branch:** `feature-separation-stage3` off next-milestone @ `1bee376` (HEAD verified first), worktree `~/workspace/jcs-sep3`. NOT merged, NOT packaged, NOT uploaded.
**Scope:** the sandbox-doable parts of SCOPING §6 Stage 3 — stem-cleanup lifecycle audit + fix, the 4050 validation script, the hallucination harness. The GPU validation itself (4050 timings, full hallucination run at the configured whisper model) remains user-machine by design.

---

## 1. Stem-cache audit (premise first)

Code read: `backend/src/media/separation.py` (the whole cache path), `backend/src/media_storage.py` (`delete()`, `SIDECARS`), `backend/src/workers/media_cleanup.py` (deletion drain + orphan sweep), `backend/src/services/task_service.py` (`delete_task`).

### What was already safe — no change, evidence cited

- **Collisions: safe.** The cache key is the source *path*, validated by the sidecar: `_cached_vocals_stem` requires the vocals file to exist non-empty AND the sidecar to parse AND `model == htdemucs` AND `source_sha256` to equal the source's full content SHA-256. Different content at the same path → hash mismatch → re-separation overwrites the stems. The 12-hex-char hash prefix names only the scratch dir. No wrong-stem serving in the serial case.
- **Partial writes: safe by construction.** The sidecar is written *last*, after both stems are placed (`separate_vocals`: "its presence with a matching hash is what makes the cache trustworthy"). Demucs is always invoked with `--flac`, so `_place_stem` takes the `shutil.move` branch — and the work dir is a *sibling* of the target (`source_path.parent / .separation-<hash>`), making the move a same-filesystem rename, i.e. atomic. A crash mid-run leaves at worst an unreferenced stem file with no valid sidecar: never served, overwritten on the next run. The non-FLAC ffmpeg fallback in `_place_stem` writes non-atomically, but the sidecar-last rule still means a partial file can never be trusted. A truncated sidecar fails JSON parsing → cache miss → re-separate.
- **Placement: as scoped.** Stems land beside the managed working source under `TEMP_DIR` (`/app/uploads` in Docker, D:-resident in Luffy's install) — SCOPING §5 risk 6 satisfied; nothing writes stems to C:.

### Known residuals — documented, deliberately not fixed

- **Concurrent separation of the same source.** Two simultaneous `separate_vocals()` calls on one source share the hash-named work dir and `shutil.rmtree` it under each other. The outcome is a soft failure → `None` → original audio (the never-fail contract holds); the cache is never corrupted. A related theoretical window exists for a same-path content swap A→B→A during a re-separation. Both are non-cases in the shipped deployment (the worker runs one job at a time, `WORKER_MAX_JOBS=1`) and fixing them means per-source locking — beyond the smallest-change bar for Stage 3.
- **Stranded scratch dirs.** A hard kill can strand one `.separation-<hash12>` dir (~stem-sized: extracted input + outputs). The `finally` removes it on any handled exit, and the next run for that source removes it on entry; the orphan sweep's staging-dir prefixes do not match it. Bounded by crash frequency, not per-task growth.

### The gap — unbounded post-deletion growth (FIXED)

While a source lives, stems are proportional (~640 MB of FLAC pair per hour of source, SCOPING §5). But **nothing ever deleted them**:

- `MediaStorage.delete()` removed the source plus `.source_map.json` / `.transcript_cache.json` (the `SIDECARS` tuple), `.transcription.mp3` and dotfiles — not `.vocals.flac`, `.accompaniment.flac` or `.separation.json`.
- `delete_task` queues only the source path; the deletion drain, the orphan sweep and upload-session expiry all flow through that same `delete()`.
- The orphan sweep (`orphan_candidates`) only yields files whose suffix is in `VIDEO_SUFFIXES` (uploads/clips/exports) or YouTube-ID-named media at the temp root — `.flac` and `.json` never match, so orphaned stems were invisible to it too.

Net effect: **every deleted separation task permanently leaked ~640 MB per hour of source.** At a plausible 5 opted-in 1-hour videos a day that is ~3.2 GB/day accumulating on the user's D: with no mechanism ever reclaiming it.

**Fix (smallest change, one seam):**
- `separation.py` gains `stem_companion_names()` / `stem_companion_paths()` — the artifact naming rule now has a single source of truth next to the path helpers that create them.
- `MediaStorage.delete()` removes the three companions alongside the existing sidecars, on **both** the local branch (unlink) and the S3 branch (companion object keys). Every deletion path benefits because they all converge on `delete()`.
- Safety: the drain checks `referenced()` before calling `delete()`, so a source still referenced by another task keeps its stems; a *different* source's stems in the same folder are untouched (pinned by test).

**Tests:** 3 new — local delete removes all three companions + idempotent + neighbour stem survives (`test_media_storage.py`); S3 delete removes the companion objects (`test_media_storage.py`); companion naming is exhaustive and matches the individual path helpers, including dotted names (`test_separation.py`).

## 2. 4050 validation script (benchmark pack)

- `tools/benchmark-pack/separation-scenario.ps1` — guided two-leg scenario in the pack's idiom (modelled on `vram-scenario.ps1`): stack started with `SEPARATION_ENABLED=true`; leg A = the fixture uploaded as one filename with the opt-in OFF, leg B = the same content under a second filename with the opt-in ON (two filenames because the app's transcript/analysis caches key on the source reference — one file reused would let leg B inherit leg A's transcript and silently invalidate the comparison).
- The report verifies each leg from the database (`tasks.separation_opt_in` as intended, `cache_hit` false) and marks a failing leg **INVALID** rather than letting its numbers pass as a result.
- Measurements: transcript words inside a user-labelled music-only window, counted from each task's own `.transcript_cache.json` (the hallucination probe — leg B should read zero); mean volume in that window on each task's most-overlapping exported clip via ffmpeg `volumedetect` in the worker container (the attenuation figure, with the scoping's ≥ 20 dB guidance stated); per-leg transcription + wall times from the app's own records; separation device/run markers and `vram stage=separation_*` telemetry from the worker log; whole-machine sampler stats. Anything unmeasured reports NOT OBSERVED.
- Pure helpers added to `benchmark-lib.ps1` (`ConvertFrom-MmSs`, `Get-WindowOverlap`, `Get-VolumeDetectMean`, `Get-TranscriptWindowWords`) + `tests/run-separation-scenario-validation.ps1` (21 Assert-style assertions, synthetic inputs) + README rows and a Separation scenario section.
- **Not executed here:** there is no PowerShell on this VM (`pwsh`/`powershell` absent — verified). Like the pack's other remote-validation scripts before their first user-machine run, the validation suite's first execution is on Luffy's machine.

## 3. Hallucination harness — real sandbox run, honestly labelled

`backend/tests/unit/test_separation_hallucination.py` — opt-in (`SEPARATION_HALLUCINATION_TEST=1` + demucs + faster-whisper importable; skips cleanly otherwise, mirroring `test_separation_quality.py`). Fixture: 24 s of pure instrumental music (chord pads + melody, synthesised with stdlib `wave` — no speech exists in it by construction). Legs use the production composition: `separate_vocals()` then `transcribe_with_faster_whisper(..., vocals_stem=stem)`.

**Sandbox run** (jcs-sep1 venv: demucs 4.1.0 + faster-whisper, htdemucs weights from the pre-existing HF cache, whisper model `base`, CPU):

| Measurement | Result |
|---|---|
| Separation wall time (24 s fixture) | ~52 s |
| Music attenuation in vocals stem | **52.2 dB** (floor asserted: ≥ 15 dB) |
| Raw-mix leg words | **0** |
| Stem-fed leg words | **0** |
| Suite | 1 passed in 63.4 s |

**Honest label:** on this fixture, faster-whisper base's VAD filter discarded all 24 s on *both* legs ("VAD filter removed 00:24.000 of audio") — pure synth tones carry no speech-like energy, so the raw leg never invented a word for the stem leg to beat. The run proves the harness and the production composition end-to-end with real models, proves the stem path stays silent on music-only audio, and the attenuation floor gives the test teeth against a separator regression. It does **not** by itself prove the differential claim (raw hallucinates, stem doesn't). That evidence stands on Stage 1's real-source Japan leg (word counts in music-only windows, raw vs stem-fed) and on the user-machine `separation-scenario.ps1` run at the app's configured model size (medium) — which is exactly why that script exists.

## 4. Test evidence (sandbox discipline: serial, TMPDIR on workspace disk, summaries from log files)

- Fix + neighbours batch 1: `test_media_storage.py` + `test_separation.py` — **97 passed**.
- Neighbour batch 2: separation (unit/transcription/render) + hallucination + quality + media storage + faster-whisper transcription — **142 passed, 2 skipped** (the two opt-in real-model suites skipping in the standard no-demucs venv, as designed), 0 failed.
- Hallucination harness real run (demucs venv, opt-in env set): **1 passed** (numbers above).
- CHANGELOG: **Fixed** (stem leak) + **Under the hood** (benchmark scenario + synthetic check) entries under 2026-11-11; `changelog.test.ts` run immediately after — **4 passed**.

## 5. What remains user-machine

- 4050 separation timings for 10-min and 60-min sources (SCOPING §6 Stage 3) and the first-use model download timing.
- The decisive hallucination run on a real music-heavy source at the configured whisper model, via `separation-scenario.ps1`; first on-machine execution of `run-separation-scenario-validation.ps1`.
- VRAM peak during separation on the 4050 (the VRAM scenario's collision chain covers the neighbouring case).
- Credits/disclosure text: already shipped in Stage 1 (`separation.py` docstring + CHANGELOG) — no Stage-3 addition needed.

## Five states

- **IMPLEMENTED:** yes — deletion fix + companions helper, benchmark separation scenario + lib helpers + validation suite, hallucination harness.
- **INDIVIDUALLY TESTED:** yes — 3 new deletion/naming tests, harness executed for real in-sandbox (1 passed, numbers above), neighbour suites 97 + 142 green.
- **INTEGRATED:** yes on the branch — the fix sits in the single `MediaStorage.delete()` seam every deletion path converges on; the harness drives the production transcription composition.
- **CROSS-FEATURE VERIFIED:** partial — deletion verified against both storage backends (local files + in-memory S3) and the drain's `referenced()` guard by code path; the Postgres-backed drain end-to-end and the PowerShell scenario are not executable in this sandbox (no Postgres/Docker/PowerShell).
- **USER-MACHINE VALIDATION PENDING:** yes — the 4050 script run, the real-source hallucination run at the configured model, and 4050 separation timings.
