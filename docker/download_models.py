#!/usr/bin/env python3
"""Download runtime models to the ephemeral container disk with verification."""

import os
import sys
from pathlib import Path

from huggingface_hub import hf_hub_download

ROOT = Path(os.environ.get("MODEL_ROOT", "/models"))

# Keep this manifest in one place. Files are never copied into the image or volume.
MODELS = (
    ("Comfy-Org/flux2-klein-turbo-fp8", "flux2-klein-turbo-fp8.safetensors", "diffusion_models"),
    ("Comfy-Org/z-image-turbo-fp8", "z-image-turbo-fp8.safetensors", "diffusion_models"),
    ("Comfy-Org/anima-turbo-v1.1", "anima-turbo-v1.1.safetensors", "diffusion_models"),
    ("Comfy-Org/anima-moody-mix-v10", "anima-moody-mix-v10.safetensors", "diffusion_models"),
    ("Comfy-Org/qwen_3_8b_fp8mixed", "qwen_3_8b_fp8mixed.safetensors", "text_encoders"),
    ("Comfy-Org/qwen_3_4b", "qwen_3_4b.safetensors", "text_encoders"),
    ("Comfy-Org/qwen_3_06b_base", "qwen_3_06b_base.safetensors", "text_encoders"),
    ("Comfy-Org/flux2-vae", "flux2-vae.safetensors", "vae"),
    ("Comfy-Org/ae", "ae.safetensors", "vae"),
    ("Comfy-Org/qwen_image_vae", "qwen_image_vae.safetensors", "vae"),
    ("Comfy-Org/minimaxH3INT8INT6INT4_flREF2VAPruned_3258013",
     "minimaxH3INT8INT6INT4_flREF2VAPruned_3258013.safetensors", "diffusion_models"),
    ("Comfy-Org/qwen3vl_32b_heretic_minimax_h3_nvfp4",
     "qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors", "text_encoders"),
    ("Comfy-Org/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16",
     "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors", "loras"),
    ("hauhau/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-GGUF",
     "Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf", "llm"),
    ("hauhau/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-GGUF",
     "mmproj-Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q8_0.gguf", "llm"),
)


def fetch(repo: str, filename: str, subdir: str) -> None:
    target = ROOT / subdir / filename
    if target.is_file() and target.stat().st_size:
        print(f"[models] existing {target}", flush=True)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"[models] downloading {repo}/{filename}", flush=True)
    hf_hub_download(
        repo_id=repo,
        filename=filename,
        local_dir=target.parent,
        token=os.environ.get("HF_TOKEN") or None,
        resume_download=True,
    )
    if not target.is_file() or not target.stat().st_size:
        raise RuntimeError(f"download completed without a usable file: {target}")


def main() -> int:
    if os.environ.get("SKIP_MODEL_DOWNLOAD") == "1":
        print("[models] SKIP_MODEL_DOWNLOAD=1", flush=True)
        return 0
    try:
        for item in MODELS:
            fetch(*item)
    except Exception as exc:
        print(f"[models] ERROR: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
