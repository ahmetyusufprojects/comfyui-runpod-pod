#!/usr/bin/env bash
set -Eeuo pipefail

log() { printf '[pod] %s\n' "$*"; }
fail() { printf '[pod] ERROR: %s\n' "$*" >&2; exit 1; }

COMFY_ROOT="${COMFYUI_ROOT:?}"
mkdir -p "${WEBUI_DATA_ROOT:?}" /models "$COMFY_ROOT"
if [ ! -f "$COMFY_ROOT/main.py" ]; then
    rsync -a /opt/comfyui-baked/ "$COMFY_ROOT/"
fi
rsync -a /opt/comfyui-overlay/ "$COMFY_ROOT/"
mkdir -p /models/{checkpoints,clip,clip_vision,controlnet,diffusion_models,embeddings,loras,style_models,text_encoders,unet,upscale_models,vae,vae_approx,llm}
for model_dir in checkpoints clip clip_vision controlnet diffusion_models embeddings loras style_models text_encoders unet upscale_models vae vae_approx; do
    rm -rf "$COMFY_ROOT/models/$model_dir"
    ln -s "/models/$model_dir" "$COMFY_ROOT/models/$model_dir"
done
if [ ! -f /opt/webui_flux2/models/opus_mt_tr_en/pytorch_model.bin ]; then
    python3 /opt/install_prompt_translation_model.py
fi
log "Starting ComfyUI"
python3 "$COMFY_ROOT/main.py" --listen 127.0.0.1 --port 8188 > /workspace/comfyui.log 2>&1 &
comfy_pid=$!

cleanup() {
    status=$?
    if kill -0 "$comfy_pid" 2>/dev/null; then
        kill "$comfy_pid" 2>/dev/null || true
        wait "$comfy_pid" 2>/dev/null || true
    fi
    exit "$status"
}
trap cleanup EXIT INT TERM

for attempt in $(seq 1 120); do
    if ! kill -0 "$comfy_pid" 2>/dev/null; then
        tail -n 80 /workspace/comfyui.log >&2 || true
        fail "ComfyUI stopped during startup"
    fi
    if curl -fsS http://127.0.0.1:8188/system_stats >/dev/null; then
        break
    fi
    sleep 1
    if [ "$attempt" = 120 ]; then
        tail -n 80 /workspace/comfyui.log >&2 || true
        fail "ComfyUI did not become ready"
    fi
done

log "Starting WebUI on port 8189"
python3 /opt/webui_flux2/server.py
