FROM jarvisclipstudio-backend
RUN uv pip install --target /opt/whisper-cu12 --no-deps nvidia-cublas-cu12==12.9.3.13 nvidia-cudnn-cu12==9.10.2.21 nvidia-cuda-runtime-cu12==12.9.79
ENV LD_LIBRARY_PATH="/opt/whisper-cu12/nvidia/cublas/lib:/opt/whisper-cu12/nvidia/cudnn/lib:/opt/whisper-cu12/nvidia/cuda_runtime/lib:/usr/local/nvidia/lib:/usr/local/nvidia/lib64"
ENTRYPOINT ["/app/.venv/bin/python"]
