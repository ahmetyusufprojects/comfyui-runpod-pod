FROM nvidia/cuda:12.8.1-devel-ubuntu24.04 AS llama-builder

ARG LLAMA_CPP_REF=b6000
ARG COMFYUI_REF=b0b743566f65daafc423b4fea8a2fbda94b3384a
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates git cmake ninja-build build-essential \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch "${LLAMA_CPP_REF}" \
       https://github.com/ggml-org/llama.cpp.git /tmp/llama.cpp \
    && cmake -S /tmp/llama.cpp -B /tmp/llama.cpp/build -G Ninja \
       -DGGML_CUDA=ON -DGGML_NATIVE=OFF -DCMAKE_BUILD_TYPE=Release \
    && cmake --build /tmp/llama.cpp/build --target llama-server

FROM runpod/comfyui:latest

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    COMFYUI_ROOT=/workspace/runpod-slim/ComfyUI \
    COMFYUI_URL=127.0.0.1:8188 \
    WEBUI_DATA_ROOT=/workspace/user-data \
    LLM_MODEL_PATH=/models/llm/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf \
    LLM_MMPROJ_PATH=/models/llm/mmproj-Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-f16.gguf \
    LLM_MMPROJ_Q8_PATH=/models/llm/mmproj-Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q8_0.gguf \
    LLAMA_SERVER_PATH=/usr/local/bin/llama-server \
    GPG_PATH=/usr/bin/gpg \
    GPG_HOME=/workspace/user-data/gnupg

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git gnupg2 libgomp1 rsync \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt
COPY webui_flux2/ /opt/webui_flux2/
COPY docker/download_models.py /opt/download_models.py
COPY webui_flux2/install_prompt_translation_model.py /opt/install_prompt_translation_model.py
COPY docker/start.sh /usr/local/bin/start-comfy-pod
COPY --from=llama-builder /tmp/llama.cpp/build/bin/llama-server /usr/local/bin/llama-server
RUN python3 -m pip install --no-cache-dir huggingface_hub
RUN git init /opt/comfyui-overlay \
    && git -C /opt/comfyui-overlay remote add origin https://github.com/comfyanonymous/ComfyUI.git \
    && git -C /opt/comfyui-overlay fetch --depth 1 origin "${COMFYUI_REF}" \
    && git -C /opt/comfyui-overlay checkout --detach FETCH_HEAD

RUN mkdir -p /models /workspace/user-data \
    /workspace/runpod-slim/ComfyUI \
    && chmod +x /usr/local/bin/start-comfy-pod

WORKDIR /opt/webui_flux2
EXPOSE 8189 8188
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=5 \
    CMD curl -fsS http://127.0.0.1:8188/system_stats >/dev/null \
        && curl -fsS http://127.0.0.1:8189/api/durum >/dev/null

ENTRYPOINT ["/usr/local/bin/start-comfy-pod"]
