# OpenCode Round 4 — Job D report

**Job:** fast GPU speech-to-text inside the app image
**Workspace:** `D:\SnipNova-Dev\opencode-v12`
**Branch:** `lane-b/round4` (base `52f54d0`)
**Commit:** `3dde964 build(docker): ship CUDA 12 cuBLAS/cuDNN 9 for faster-whisper`
**Patch:** `series_D/0001-build-docker-ship-CUDA-12-cuBLAS-cuDNN-9-for-faster-.patch`

## Result

**Goal met: 64.8x real time with `faster-whisper` `large-v3-turbo`, fp16, batch 16, on the RTX 4050 Laptop (6 GB).** The current transformers path is 4x on the same machine, so this is ~16x faster. `small` reaches 70.9x.

The fix is the CUDA 12 cuBLAS / cuDNN 9 wheels that ctranslate2 4.8.5 is linked against, installed alongside torch's CUDA 13 libraries and put on `LD_LIBRARY_PATH`.

## Test setup

| Item | Value |
|---|---|
| Image | `jarvisclipstudio-worker:latest` (throwaway `--rm` container, `--gpus all`) |
| Container name | none (docker-generated; not `supoclip-*`) |
| Mounts | only `%TEMP%\opencode\r4-asr\work` → `/work`; no app volumes |
| GPU | NVIDIA GeForce RTX 4050 Laptop GPU, 6141 MiB, driver 616.92 |
| Python | 3.11.17 |
| torch | 2.13.0+cu130 |
| ctranslate2 | 4.8.5 |
| faster-whisper | 1.2.1 |
| Audio | 20:00 (1200.0 s), 16 kHz mono WAV, 38,400,078 bytes |
| Source | YouTube `tiUdSBqMOtA` (xQc Gaming, GTA RP), section 00:10:00–00:30:00, `yt-dlp -f 251` |
| Baseline GPU use | 1137 MiB (desktop before loading a model) |

The 20-minute WAV was cut from the downloaded audio with `ffmpeg -ss 600 -t 1200 -ac 1 -ar 16000`. Download note: YouTube served this session SABR-only formats and a bot check, so the download ran inside the worker image (deno + the bgutil PO-token plugin) with `--extractor-args "youtube:player_client=web_embedded"` and a **separate throwaway** `r4-bgutil` provider on `127.0.0.1:14416`. Luffy's containers were not touched.

## Benchmark results

Command: `uv pip install --python /app/.venv/bin/python nvidia-cublas-cu12 'nvidia-cudnn-cu12==9.*'`, then `LD_LIBRARY_PATH` set to the venv's `nvidia/{cublas,cudnn,cuda_runtime,cuda_nvrtc}/lib`, then `bench_fw.py` (one 30 s warm-up on the same file, then the full 20 min). Full log: `evidence_D/faster_whisper_bench.txt`; JSON: `evidence_D/results_fw.json`.

| Model | Batch | Audio | Wall | **RTF** | VRAM after load | VRAM peak | Segments | Load* |
|---|---|---|---|---|---|---|---|---|
| `small` | 16 | 1200 s | 16.92 s | **70.93x** | 1667 MiB | 1815 MiB | 19 | 55.6 s |
| `large-v3-turbo` | 16 | 1200 s | 18.51 s | **64.82x** | 3692 MiB | 3316 MiB | 19 | 181.0 s |

\* `load_s` includes the one-time Hugging Face download inside the fresh container (about 0.5 GB for `small`, 1.6 GB for turbo). A warm process loads the already-cached model in a few seconds. VRAM is device-wide (`nvidia-smi`); subtract the 1137 MiB desktop baseline for the model's own footprint (~0.7 GiB small, ~2.2 GiB turbo).

Both models beat the 40x target. **`large-v3-turbo` is the winner** for `vod.transcribe_full`: same speed class, materially better text. `small` is the fallback if VRAM or first-load time matters.

### First 200 characters

`small`:

```
I'm not good with this bike I heard my leg man I don't know what it is with the brain just keeps wanting to I think you may have a problem with your muscles You got um Are you a tweaker? Are you a lit
```

`large-v3-turbo`:

```
Oh, I'm not good with this bike. I hurt my leg man Brain just keeps wanting to I think you may have you got to follow your muscles you got um Are you a tweaker everyone those speakers or what? Yeah, I
```

### Segment timestamps

Both models return 19 monotonic segments spanning the whole file:

- first: `0.00 → 38.22`, `"Oh, I'm not good with this bike. I hurt my leg man ..."`
- last: `1185.58 → 1200.00`, `"Oh, it's Bay City. Surely we have enough money in the bank."`

They look right: ordered, non-overlapping, inside `[0, 1200]`, and the last segment ends exactly at the audio duration. The first segment is long (38 s) because this GTA RP section is continuous speech and VAD is off; the timestamps are segment-level, not word-level. That is the same granularity the current `transcribe_full` consumes for keyword search. If finer boundaries are wanted, add `vad_filter=True` (not measured here).

## Exact image change (committed)

`backend/pyproject.toml` — `cuda` extra:

```toml
cuda = [
    "torch==2.13.0",
    "nvidia-cublas-cu12>=12.9,<13; sys_platform == 'linux'",
    "nvidia-cudnn-cu12>=9.28,<10; sys_platform == 'linux'",
]
```

`backend/uv.lock` — `uv lock` added exactly three packages (no unrelated churn, 39 lines):

```
nvidia-cublas-cu12==12.9.3.13
nvidia-cuda-nvrtc-cu12==12.9.86   (dependency of cublas/cudnn)
nvidia-cudnn-cu12==9.28.1.4
```

`backend/Dockerfile` — after the existing `uv sync --frozen --extra cuda` block:

```dockerfile
ENV LD_LIBRARY_PATH="/app/.venv/lib/python3.11/site-packages/nvidia/cublas/lib:/app/.venv/lib/python3.11/site-packages/nvidia/cudnn/lib:/app/.venv/lib/python3.11/site-packages/nvidia/cuda_nvrtc/lib"
```

Verified with `uv export --frozen --extra cuda`: the lock selects `nvidia-cublas-cu12==12.9.3.13`, `nvidia-cudnn-cu12==9.28.1.4`, and `nvidia-cuda-nvrtc-cu12==12.9.86` on Linux. Nothing else in the lock changed.

## Winner snippet

```python
from faster_whisper import BatchedInferencePipeline, WhisperModel

_PIPE = None


def _pipe() -> BatchedInferencePipeline:
    global _PIPE
    if _PIPE is None:
        model = WhisperModel(
            "large-v3-turbo",
            device="cuda",
            compute_type="float16",
            # download_root="/app/models/whisper",  # optional; else HF cache
        )
        _PIPE = BatchedInferencePipeline(model=model)
    return _PIPE


def transcribe_segments(wav_path: str) -> list[dict]:
    """Whole-file GPU transcription with segment timestamps (~65x real time)."""
    segments, _info = _pipe().transcribe(
        wav_path,
        batch_size=16,   # 16 fits 6 GB; drop to 8 if the desktop is using VRAM
        language=None,   # "en" for known-English streams skips detection
        vad_filter=False,
    )
    return [
        {"start": float(s.start), "end": float(s.end), "text": s.text.strip()}
        for s in segments
    ]
```

Notes for wiring it into `vod.transcribe_full`:

- Keep the function lazy: the first call downloads ~1.6 GB and loads the model; later calls reuse `_PIPE`.
- The existing function chunks the audio every 600 s to report progress. `BatchedInferencePipeline` can take the whole WAV at once and is faster that way; if progress messages are required, chunk the input but keep one pipeline instance.
- The image patch installs the libraries. It does **not** bake the model weights; the first transcription downloads them unless the installer pre-fetches `large-v3-turbo` into the image or a mounted model cache.

## What is NOT done / NOT VERIFIED

- **NOT RUN** — step (b), a ctranslate2 build for CUDA 13. `ctranslate2` 4.8.5 (2026-11-02) is the newest on PyPI and its manylinux wheel is CUDA 12-linked; (a) worked, so there was nothing to fall back to.
- **NOT RUN** — step (c), transformers `distil-whisper/distil-large-v3.5`. It is only a fallback in the brief, and (a) cleared 40x. The current transformers path (`openai/whisper-large-v3-turbo`, fp16, batch 16) remains the 4x baseline quoted in the brief.
- **NOT REBUILT** — the `jarvisclipstudio-worker` image was not rebuilt and no `supoclip-*` container was restarted. The patch is for Sanji's installer. The benchmark used the same image plus the cu12 wheels installed at runtime, which is exactly what the patched `uv sync` will produce.
- **NOT MEASURED** — `vad_filter=True` (finer segments, unknown cost), word-level timestamps, and non-English streams.
- **NOT VERIFIED** — the Dockerfile build itself (no `docker build`). The lock export and the runtime benchmark prove the dependency set and the library path, not the image layer.

## Evidence index

| Path | Contents |
|---|---|
| `evidence_D/faster_whisper_bench.txt` | full container log: install, library check, both models |
| `evidence_D/results_fw.json` | the same results as JSON |
| `evidence_D/run_fw.sh` | exact install + `LD_LIBRARY_PATH` + benchmark commands |
| `evidence_D/bench_fw.py` | benchmark harness |
| `series_D/0001-build-docker-ship-CUDA-12-cuBLAS-cuDNN-9-for-faster-.patch` | Job D commit (`pyproject.toml`, `uv.lock`, `Dockerfile`) |
