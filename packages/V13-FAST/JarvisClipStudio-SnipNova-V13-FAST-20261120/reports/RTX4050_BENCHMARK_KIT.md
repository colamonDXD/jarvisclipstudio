# RTX 4050 Benchmark Kit — SnipNova render speed

**For:** Luffy, on the ASUS TUF A16 (Ryzen 7, RTX 4050 Laptop 6 GB, 16 GB RAM), after the exam.
**Time:** about 10 minutes of active steps. Do not start it while a task you care about is rendering — Steps 3–4 run short test encodes that compete for the same CPU/GPU.
**Changes nothing:** every command below only reads, lists, logs, or test-encodes to a null output. Nothing is saved, installed, or reconfigured. Do **not** edit `.env` or restart the stack for this kit.
**Version note:** Steps 1–5 work on the installed V7 build. Step 6's per-clip `encoder` / `nvenc_probe_error` stamp exists only in a build that contains the `perf/rtx4050-render-path` lane; on V7 those two fields are absent by design, and the worker-log lines in Step 2 are the evidence instead.

Open **PowerShell** and start here:

```powershell
cd D:\EditedTool\JarvisClipStudio
docker compose ps
```

The `worker` service must be up. If it is not, start the app the normal way first and wait until http://localhost:3107 opens.

---

## Step 1 — Does the worker's FFmpeg even offer NVENC / CUDA? (~1 minute)

```powershell
docker compose exec worker ffmpeg -hide_banner -encoders | Select-String "h264_nvenc"
docker compose exec worker ffmpeg -hide_banner -hwaccels
docker compose exec worker printenv ENABLE_NVDEC
docker compose exec worker printenv RENDER_ECO_MODE
docker compose exec worker printenv CLIP_RENDER_CONCURRENCY
```

**What each result proves**

- A line containing `h264_nvenc` = the FFmpeg build *can* encode on the GPU. This alone does **not** prove the app's probe passes — that is Step 2.
- `cuda` in the `-hwaccels` list = hardware decode (NVDEC) is at least offered by this FFmpeg build.
- `printenv` printing nothing (or an error) = that setting is unset in the worker, so its code default applies: `ENABLE_NVDEC` defaults **off**, `RENDER_ECO_MODE` defaults **off**, and `CLIP_RENDER_CONCURRENCY` falls back to the app's encoder-based default unless your `.env`/override supplies it to the worker.

## Step 2 — The app's own verdict: probe + encoder log lines (~2 minutes)

```powershell
docker compose logs --since 2h worker | Select-String 'Final encode encoder:|EDL encoder:|Pre-stage stitch encoder:|NVENC probe failed|NVENC disabled|NVDEC probe failed|NVDEC disabled'
```

If nothing comes back, no render has run in the last 2 hours in this worker process: start your shortest normal task (1 clip is enough), let it reach Rendering, then run the command again.

**Screenshot these lines if any appear.** Read them like this:

| Log line | What it settles |
|---|---|
| `Final encode encoder: h264_nvenc` or `EDL encoder: h264_nvenc` or `Pre-stage stitch encoder: h264_nvenc` | The app's 3-stage NVENC probe **passed** in this worker process and the GPU encoder was selected. |
| `Final encode encoder: libx264` (or the EDL/pre-stage equivalents) **together with** an `NVENC probe failed at the … stage: …` line | **CPU-by-silent-fallback, confirmed on your machine:** the probe failed, every clip rendered on the CPU, and (on V7) nothing in the app itself said so. The text after "stage:" is the exact reason — that is the single most valuable line in this kit. |
| `NVENC disabled for this process: …` | The probe had passed, but a real production encode failed with an NVENC error, so the app took NVENC away for the rest of that worker process and retried on CPU. Restarting the worker re-probes. |
| `libx264` lines with **no** probe-failure line anywhere | CPU was selected without a probe failure — check whether `FORCE_CPU_ENCODE` is set, and send the log lines back rather than guessing. |
| `NVDEC probe failed …` | Only possible when NVDEC was opted into; it explains a software-decode fallback for that process. |

## Step 3 — The 30-second encode race: NVENC vs CPU (~3 minutes)

First find one real video file inside the worker (prefer an `/app/uploads` source; a finished clip from `/app/clips` also works):

```powershell
docker compose exec worker sh -c "find /app/uploads /app/clips -type f -name '*.mp4' -size +1M 2>/dev/null | head -10"
$clip = "PASTE_ONE_PATH_FROM_THE_LIST_HERE"
```

Quick sanity check (3 seconds of the file, errors visible — run this first):

```powershell
docker compose exec worker ffmpeg -hide_banner -y -i $clip -t 3 -an -vf "scale=1080:1920" -c:v h264_nvenc -preset p4 -cq 22 -f null -
```

If that errors, **stop the race** and screenshot the last ~15 lines of the error: NVENC failing in a bare FFmpeg command, outside the app, is itself the answer (driver / Docker GPU passthrough / FFmpeg build — not app logic).

Then the timed race (same 30 seconds, same scaling, EDL-path settings on both sides):

```powershell
$gpu = Measure-Command { docker compose exec worker ffmpeg -hide_banner -y -i $clip -t 30 -an -vf "scale=1080:1920" -c:v h264_nvenc -preset p4 -cq 22 -f null - 2>$null }
"NVENC seconds: $($gpu.TotalSeconds)"
$cpu = Measure-Command { docker compose exec worker ffmpeg -hide_banner -y -i $clip -t 30 -an -vf "scale=1080:1920" -c:v libx264 -preset veryfast -crf 20 -f null - 2>$null }
"CPU seconds: $($cpu.TotalSeconds)"
```

**What it proves**

- NVENC much faster (roughly 2× or more) = the GPU encoder is healthy on your hardware; if Step 2 showed libx264 + a probe failure, the fix is the probe/driver path, not the renderer.
- NVENC barely faster, or slower = even with a working GPU encoder, the CPU filter graph (scale/crop/captions) is the bottleneck — GPU encode alone will not rescue render times, and thread scheduling + filter cost are the real targets.
- Write both numbers down; they go in the results template at the end.

## Step 4 — Two quick A/Bs: hardware decode, and the thread bound (~2 minutes)

Decode only (no encoder involved — this isolates NVDEC):

```powershell
$sw = Measure-Command { docker compose exec worker ffmpeg -hide_banner -y -i $clip -t 30 -an -vf "scale=1080:1920" -f null - 2>$null }
"Software decode seconds: $($sw.TotalSeconds)"
$hw = Measure-Command { docker compose exec worker ffmpeg -hide_banner -y -hwaccel cuda -i $clip -t 30 -an -vf "scale=1080:1920" -f null - 2>$null }
"NVDEC decode seconds: $($hw.TotalSeconds)"
```

- NVDEC clearly faster = enabling `ENABLE_NVDEC` is worth a real app-level A/B later. About the same or slower = decoded frames still land in system memory for the CPU filter graph, so NVDEC stays off — that question is settled, cheaply.

Thread bound (CPU encode, unbounded vs the lane's 8-thread bound for a 12-thread machine):

```powershell
$free = Measure-Command { docker compose exec worker ffmpeg -hide_banner -y -i $clip -t 30 -an -vf "scale=1080:1920" -c:v libx264 -preset veryfast -crf 20 -f null - 2>$null }
"Unbounded seconds: $($free.TotalSeconds)"
$bound = Measure-Command { docker compose exec worker ffmpeg -hide_banner -y -filter_threads 8 -i $clip -t 30 -an -vf "scale=1080:1920" -c:v libx264 -preset veryfast -crf 20 -threads 8 -f null - 2>$null }
"Bounded (8) seconds: $($bound.TotalSeconds)"
```

- Bounded about as fast = the bound costs nothing in speed while stopping one process from claiming every thread — the scheduling fix is safe on your CPU. Bounded much slower = the 8-thread cap is too tight for this chip and the budget needs re-tuning before it ships to you.

## Step 5 — Watch the GPU during one real render (~1 minute, while a task renders)

Start any normal task, wait until it says **Rendering**, then:

1. Open **Task Manager → Performance → GPU**. Watch the **Video Encode** and **Video Decode** mini-graphs (click a graph heading to switch it if they are not shown).
2. In a second PowerShell window:

```powershell
nvidia-smi --query-gpu=name,utilization.gpu,memory.used --format=csv -l 1
```

Let it run for ~30 seconds of rendering, then stop it with **Ctrl+C**.

**What it proves**

- **Video Encode above 0% during rendering** = the app really is using NVENC on your machine, whatever the logs say.
- **Video Encode pinned at 0% while CPU is ~90%+** = that render is CPU-only (or entirely filter-bound) — combined with Step 2's lines, this separates "probe failed" from "GPU on but filters dominate".
- **Video Decode above 0%** would mean NVDEC is active; on the default config it should sit at 0%.

## Step 6 — Read the last finished task's own timing record (~1 minute)

```powershell
docker compose exec postgres psql -U supoclip -d supoclip -c "SELECT stage_timings_json::jsonb - 'clips' AS stages FROM tasks WHERE stage_timings_json IS NOT NULL ORDER BY created_at DESC LIMIT 1;"
docker compose exec postgres psql -U supoclip -d supoclip -c "SELECT stage_timings_json::jsonb -> 'clips' AS clips FROM tasks WHERE stage_timings_json IS NOT NULL ORDER BY created_at DESC LIMIT 1;"
```

**What it proves**

- `download_seconds` / `transcription_seconds` / `analysis_seconds` / `render_seconds` split the task's wall time into stages — this is how we learn what the 57-minute run actually spent its time on, instead of assuming it was all rendering.
- Inside `clips`, each entry's pass timings (`encode_seconds` vs `total_seconds`, where present) show how much of a clip was the encode itself versus everything around it.
- **Perf-lane builds only:** each clip entry also carries `"encoder": "h264_nvenc"` or `"libx264"`, plus `"nvenc_probe_error": "…"` when the probe failed. That stamp is the permanent, in-app replacement for hunting worker logs.

---

## Send back (copy, fill in, attach the Step 2 screenshot)

```text
Step 1: h264_nvenc listed? yes/no · cuda in hwaccels? yes/no
        ENABLE_NVDEC = ____ · RENDER_ECO_MODE = ____ · CLIP_RENDER_CONCURRENCY = ____
Step 2: encoder lines seen: ____
        NVENC probe failed line (full text): ____
Step 3: NVENC 30s race = ____ s · CPU 30s race = ____ s
Step 4: software decode = ____ s · NVDEC decode = ____ s
        unbounded encode = ____ s · bounded (8 threads) = ____ s
Step 5: during a real render — Video Encode ____% · Video Decode ____% · CPU ____% · RAM ____%
Step 6: stages = ____ · clips encoder stamp (perf-lane builds) = ____
```

## What your results settle

| Open question from the code audit | Settled by |
|---|---|
| Does the app's NVENC probe actually pass on your RTX 4050, inside Docker? | Step 2 (+ Step 3 sanity check) |
| If it fails, at which probe stage, and why? | Step 2's `NVENC probe failed at the … stage: …` line; Step 6's stamp on perf-lane builds |
| Is the render bottleneck the encoder or the CPU filter graph? | Step 3 race + Step 5 Video Encode % |
| Is NVDEC worth enabling? | Step 4 decode A/B |
| Does the thread bound cost speed on your CPU? | Step 4 thread A/B |
| Where did the slow task's time actually go? | Step 6 stage split |

**Still not settled by this kit (needs a longer, separate session):** a full-task Eco-mode on/off comparison on identical real footage, and NVENC VRAM/utilisation across a whole multi-clip task. Those are benchmarking sessions, not 10-minute checks — this kit deliberately does not ask you to change any setting.
