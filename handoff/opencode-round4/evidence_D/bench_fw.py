"""Benchmark faster-whisper (ctranslate2) on the 20-minute speech file."""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

WAV = "/work/speech_20m.wav"
WARMUP = "/work/warmup_30s.wav"
OUT = Path("/work/results_fw.json")
MODELS = ["small", "large-v3-turbo"]


def gpu_used_mb() -> int:
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=30,
    )
    try:
        return int(out.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return -1


def main() -> None:
    import ctranslate2
    import faster_whisper
    from faster_whisper import BatchedInferencePipeline, WhisperModel

    print(f"ctranslate2 {ctranslate2.__version__}", flush=True)
    print(f"faster_whisper {faster_whisper.__version__}", flush=True)
    print(f"cuda devices {ctranslate2.get_cuda_device_count()}", flush=True)
    print(f"compute types {ctranslate2.get_supported_compute_types('cuda')}", flush=True)
    print(f"gpu used before load {gpu_used_mb()} MiB", flush=True)

    results = []
    for name in MODELS:
        record = {"model": name, "device": "cuda", "compute_type": "float16"}
        started = time.perf_counter()
        try:
            model = WhisperModel(
                name, device="cuda", compute_type="float16",
                download_root="/work/models",
            )
        except Exception as exc:  # noqa: BLE001 - report, do not hide
            record["error"] = f"{type(exc).__name__}: {exc}"[:500]
            print(json.dumps(record), flush=True)
            results.append(record)
            OUT.write_text(json.dumps(results, indent=2), encoding="utf-8")
            continue
        record["load_s"] = round(time.perf_counter() - started, 1)
        record["vram_after_load_mb"] = gpu_used_mb()
        pipe = BatchedInferencePipeline(model=model)

        # Warm up the CUDA kernels and the batching path on 30 s of the same
        # recording, so the measured run is not paying first-call costs.
        warm_started = time.perf_counter()
        list(pipe.transcribe(WARMUP, batch_size=8, language="en")[0])
        record["warmup_s"] = round(time.perf_counter() - warm_started, 1)

        for batch in (16, 8, 4):
            try:
                started = time.perf_counter()
                segments, info = pipe.transcribe(WAV, batch_size=batch, language="en")
                collected = list(segments)
                wall = time.perf_counter() - started
                record.update({
                    "batch_size": batch,
                    "audio_s": round(info.duration, 1),
                    "wall_s": round(wall, 2),
                    "rtf": round(info.duration / wall, 2) if wall else None,
                    "vram_peak_used_mb": gpu_used_mb(),
                    "segments": len(collected),
                    "text_first_200": "".join(s.text for s in collected).strip()[:200],
                    "first_segments": [
                        {"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text}
                        for s in collected[:3]
                    ],
                    "last_segment": (
                        {"start": round(collected[-1].start, 2),
                         "end": round(collected[-1].end, 2),
                         "text": collected[-1].text}
                        if collected else None
                    ),
                })
                break
            except Exception as exc:  # noqa: BLE001 - OOM fallback is the point
                record[f"error_batch_{batch}"] = f"{type(exc).__name__}: {exc}"[:300]
                print(f"{name} batch {batch} failed: {exc}", flush=True)

        results.append(record)
        OUT.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(json.dumps(record, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
