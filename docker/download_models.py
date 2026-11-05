#!/usr/bin/env python3
"""Download runtime models to the ephemeral container disk with verification."""

import os
import sys
from pathlib import Path

from huggingface_hub import hf_hub_download

ROOT = Path(os.environ.get("MODEL_ROOT", "/models"))

# Public core files used by the current graph builders. Files are downloaded
# to the container disk, never to the Network Volume.
MODELS = (
    ("Comfy-Org/Flux-2-Klein-4B", "split_files/diffusion_models/flux-2-klein-4b.safetensors", "flux-2-klein-4b.safetensors", "diffusion_models"),
    ("Comfy-Org/Flux-2-Klein-4B", "split_files/text_encoders/qwen_3_4b.safetensors", "qwen_3_4b.safetensors", "text_encoders"),
    ("Comfy-Org/Flux-2-Klein-4B", "split_files/vae/flux2-vae.safetensors", "flux2-vae.safetensors", "vae"),
    ("Comfy-Org/Z-Image-Turbo", "split_files/diffusion_models/z_image_turbo_bf16.safetensors", "z_image_turbo_bf16.safetensors", "diffusion_models"),
    ("Comfy-Org/Z-Image-Turbo", "split_files/vae/ae.safetensors", "ae.safetensors", "vae"),
)

LLM_MODELS = (
    ("hauhau/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-GGUF",
     "Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf",
     "Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf", "llm"),
    ("hauhau/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-GGUF",
     "mmproj-Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q8_0.gguf",
     "mmproj-Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q8_0.gguf", "llm"),
)


def fetch(repo: str, remote_filename: str, filename: str, subdir: str) -> None:
    target = ROOT / subdir / filename
    if target.is_file() and target.stat().st_size:
        print(f"[models] existing {target}", flush=True)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"[models] downloading {repo}/{remote_filename}", flush=True)
    hf_hub_download(
        repo_id=repo,
        filename=remote_filename,
        local_dir=target.parent,
        token=os.environ.get("HF_TOKEN") or None,
        resume_download=True,
    )
    downloaded = target.parent / remote_filename
    if downloaded != target and downloaded.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        downloaded.replace(target)
    if not target.is_file() or not target.stat().st_size:
        raise RuntimeError(f"download completed without a usable file: {target}")


def main() -> int:
    if os.environ.get("SKIP_MODEL_DOWNLOAD") == "1":
        print("[models] SKIP_MODEL_DOWNLOAD=1", flush=True)
        return 0
    try:
        for item in MODELS:
            fetch(*item)
        if os.environ.get("DOWNLOAD_LLM_MODELS") == "1":
            for item in LLM_MODELS:
                fetch(*item)
    except Exception as exc:
        print(f"[models] ERROR: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
