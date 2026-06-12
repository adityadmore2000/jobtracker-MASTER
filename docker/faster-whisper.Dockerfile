FROM nvidia/cuda:12.3.2-cudnn9-runtime-ubuntu22.04

ENV HF_HOME=/root/.cache/huggingface

RUN apt-get update && \
    apt-get install -y \
        python3 \
        python3-pip \
        python3-venv \
        git \
        ca-certificates && \
    rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --no-cache-dir --upgrade pip==24.3.1 && \
    python3 -m pip install --no-cache-dir \
        faster-whisper==1.1.1 \
        ctranslate2==4.5.0

WORKDIR /workspace

CMD ["/bin/bash"]
