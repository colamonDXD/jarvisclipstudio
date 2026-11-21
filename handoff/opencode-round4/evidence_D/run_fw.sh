#!/bin/bash
set -eu
echo "--- install CUDA 12 cuBLAS / cuDNN 9 into the image venv"
uv pip install --python /app/.venv/bin/python nvidia-cublas-cu12 'nvidia-cudnn-cu12==9.*' 2>&1 | tail -12
SP=/app/.venv/lib/python3.11/site-packages
export LD_LIBRARY_PATH="$SP/nvidia/cublas/lib:$SP/nvidia/cudnn/lib:$SP/nvidia/cuda_runtime/lib:$SP/nvidia/cuda_nvrtc/lib:${LD_LIBRARY_PATH:-}"
echo "--- cuBLAS 12:"; ls "$SP/nvidia/cublas/lib" | grep -E 'libcublas(Lt)?\.so\.12' || true
echo "--- cuDNN 9:"; ls "$SP/nvidia/cudnn/lib" | grep -E 'libcudnn(_ops|_engines|_heuristics)?\.so\.9' | head -6 || true
echo "--- warmup slice"
ffmpeg -v error -y -t 30 -i /work/speech_20m.wav /work/warmup_30s.wav
echo "--- faster-whisper benchmark"
/app/.venv/bin/python -u /work/bench_fw.py
