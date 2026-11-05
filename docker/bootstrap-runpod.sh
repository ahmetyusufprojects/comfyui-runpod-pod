#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="https://github.com/ahmetyusufprojects/comfyui-runpod-pod/archive/refs/heads/main.tar.gz"
WORK_ROOT="/tmp/runpod-pod-source"
COMFY_ROOT="/comfyui"
MODEL_ROOT="${MODEL_ROOT:-/models}"
WEBUI_ROOT="/opt/webui_flux2"
LLAMA_ROOT="/opt/llama"

log() { printf '[pod] %s\n' "$*"; }
fail() { printf '[pod] ERROR: %s\n' "$*" >&2; exit 1; }

mkdir -p "$WORK_ROOT" "$MODEL_ROOT" "$LLAMA_ROOT" /workspace/user-data
if [ ! -f "$WORK_ROOT/docker/start.sh" ]; then
    log "Downloading deployment source"
    rm -rf "$WORK_ROOT"/*
    wget -qO- "$REPO_ROOT" | tar -xz --strip-components=1 -C "$WORK_ROOT"
fi

rm -rf "$WEBUI_ROOT"
cp -a "$WORK_ROOT/webui_flux2" "$WEBUI_ROOT"
cp "$WORK_ROOT/docker/download_models.py" /opt/download_models.py
cp "$WORK_ROOT/webui_flux2/install_prompt_translation_model.py" \
    /opt/install_prompt_translation_model.py

if [ ! -f "$COMFY_ROOT/main.py" ]; then
    fail "Official Runpod image did not contain ComfyUI at $COMFY_ROOT"
fi
if [ ! -d "$COMFY_ROOT/custom_nodes" ]; then
    mkdir -p "$COMFY_ROOT/custom_nodes"
fi

if [ ! -x "$LLAMA_ROOT/llama-server" ]; then
    log "Downloading CUDA 12.8 llama-server"
    tmp_dir="$(mktemp -d)"
    wget -q --tries=3 \
        "https://github.com/ggml-org/llama.cpp/releases/download/b12128/llama-b12128-bin-ubuntu-cuda-12.8-x64.tar.gz" \
        -O "$tmp_dir/llama.tar.gz"
    tar -xzf "$tmp_dir/llama.tar.gz" -C "$tmp_dir"
    llama_bin="$(find "$tmp_dir" -type f -name llama-server | head -n 1)"
    [ -n "$llama_bin" ] || fail "llama-server was missing from the release archive"
    cp "$llama_bin" "$LLAMA_ROOT/llama-server"
    chmod +x "$LLAMA_ROOT/llama-server"
    rm -rf "$tmp_dir"
fi

export COMFYUI_ROOT="$COMFY_ROOT"
export COMFYUI_URL="${COMFYUI_URL:-127.0.0.1:8188}"
export WEBUI_DATA_ROOT="${WEBUI_DATA_ROOT:-/workspace/user-data}"
export LLM_MODEL_PATH="${LLM_MODEL_PATH:-$MODEL_ROOT/llm/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf}"
export LLM_MMPROJ_PATH="${LLM_MMPROJ_PATH:-$MODEL_ROOT/llm/mmproj-Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-f16.gguf}"
export LLM_MMPROJ_Q8_PATH="${LLM_MMPROJ_Q8_PATH:-$MODEL_ROOT/llm/mmproj-Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q8_0.gguf}"
export LLAMA_SERVER_PATH="$LLAMA_ROOT/llama-server"
export GPG_PATH="${GPG_PATH:-/usr/bin/gpg}"
export GPG_HOME="${GPG_HOME:-$WEBUI_DATA_ROOT/gnupg}"
export PYTHONUNBUFFERED=1

mkdir -p "$MODEL_ROOT"/{checkpoints,clip,clip_vision,controlnet,diffusion_models,embeddings,loras,style_models,text_encoders,unet,upscale_models,vae,vae_approx,llm}
for model_dir in checkpoints clip clip_vision controlnet diffusion_models embeddings loras style_models text_encoders unet upscale_models vae vae_approx; do
    rm -rf "$COMFY_ROOT/models/$model_dir"
    ln -s "$MODEL_ROOT/$model_dir" "$COMFY_ROOT/models/$model_dir"
done

if ! python3 -c 'import huggingface_hub' >/dev/null 2>&1; then
    python3 -m pip install --no-cache-dir huggingface_hub
fi
if [ "${SKIP_MODEL_DOWNLOAD:-0}" != "1" ]; then
    python3 /opt/download_models.py
fi

log "Starting ComfyUI"
python3 "$COMFY_ROOT/main.py" --listen 127.0.0.1 --port 8188 > /tmp/comfyui.log 2>&1 &
comfy_pid=$!
trap 'kill "$comfy_pid" 2>/dev/null || true' EXIT INT TERM

for attempt in $(seq 1 180); do
    kill -0 "$comfy_pid" 2>/dev/null || {
        tail -n 80 /tmp/comfyui.log >&2 || true
        fail "ComfyUI stopped during startup"
    }
    if python3 -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8188/system_stats", timeout=3)' >/dev/null 2>&1; then
        break
    fi
    [ "$attempt" = 180 ] && fail "ComfyUI did not become ready"
    sleep 1
done

log "Starting WebUI on port 8189"
exec python3 "$WEBUI_ROOT/server.py"
