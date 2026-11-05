#!/usr/bin/env python3
"""Download runtime models to the ephemeral container disk with verification."""

import os
import sys
import json
import threading
import time
from pathlib import Path

try:
    from huggingface_hub import hf_hub_download
except ImportError:
    hf_hub_download = None

ROOT = Path(os.environ.get("MODEL_ROOT", "/models"))

# Public core files used by the current graph builders. Files are downloaded
# to the container disk, never to the Network Volume.
MODEL_GROUPS = {
    "llm": (
        ("hauhau/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-GGUF", "Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf", "Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf", "llm"),
        ("hauhau/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-GGUF", "mmproj-Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q8_0.gguf", "mmproj-Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q8_0.gguf", "llm"),
    ),
    "flux2": (
        ("Comfy-Org/Flux-2-Klein-4B", "split_files/diffusion_models/flux-2-klein-4b.safetensors", "flux-2-klein-4b.safetensors", "diffusion_models"),
        ("Comfy-Org/Flux-2-Klein-4B", "split_files/text_encoders/qwen_3_4b.safetensors", "qwen_3_4b.safetensors", "text_encoders"),
        ("Comfy-Org/Flux-2-Klein-4B", "split_files/vae/flux2-vae.safetensors", "flux2-vae.safetensors", "vae"),
    ),
    "zimage": (
        ("Comfy-Org/Z-Image-Turbo", "split_files/diffusion_models/z_image_turbo_bf16.safetensors", "z_image_turbo_bf16.safetensors", "diffusion_models"),
        ("Comfy-Org/Z-Image-Turbo", "split_files/text_encoders/qwen_3_4b.safetensors", "qwen_3_4b.safetensors", "text_encoders"),
        ("Comfy-Org/Z-Image-Turbo", "split_files/vae/ae.safetensors", "ae.safetensors", "vae"),
    ),
}
GROUP_LABELS = {"llm": "Gemma 4 E4B", "flux2": "Flux 2 Klein", "zimage": "Z-Image Turbo"}
DEFAULT_ORDER = ("llm", "flux2", "zimage")
ORDER_TIMEOUT_SECONDS = 60


def fetch(repo: str, remote_filename: str, filename: str, subdir: str) -> None:
    if hf_hub_download is None:
        raise RuntimeError("huggingface_hub kurulu değil")
    target = ROOT / subdir / filename
    if target.is_file() and target.stat().st_size:
        print(f"[models] existing {target}", flush=True)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"[models] downloading {repo}/{remote_filename}", flush=True)
    downloaded = Path(hf_hub_download(
        repo_id=repo,
        filename=remote_filename,
        local_dir=target.parent,
        token=os.environ.get("HF_TOKEN") or None,
        resume_download=True,
    ))
    if downloaded.parent != target.parent:
        downloaded = target.parent / remote_filename
    if downloaded != target and downloaded.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        downloaded.replace(target)
    if not target.is_file() or not target.stat().st_size:
        raise RuntimeError(f"download completed without a usable file: {target}")


class ModelDownloadManager:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.state_path = self.root / ".model-download-state.json"
        self._lock = threading.RLock()
        self._selection_event = threading.Event()
        self._order = list(DEFAULT_ORDER)
        self._started_at = time.time()
        self._selected = False
        self._running = False
        self._thread = None
        self._current = None
        self._error = None
        self._completed = []
        self._load_state()

    def _load_state(self):
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            if data.get("order"):
                self._order = self._valid_order(data["order"])
        except (OSError, ValueError, TypeError):
            pass

    @staticmethod
    def _valid_order(order):
        result = [x for x in order if x in MODEL_GROUPS]
        return result + [x for x in DEFAULT_ORDER if x not in result]

    def _save_state(self):
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"order": self._order}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.state_path)

    def start(self):
        if os.environ.get("SKIP_MODEL_DOWNLOAD") == "1" or self._thread:
            return
        self._thread = threading.Thread(target=self._run, name="model-downloads", daemon=True)
        self._thread.start()

    def set_order(self, order):
        with self._lock:
            if self._running:
                raise RuntimeError("İndirme başladıktan sonra sıra değiştirilemez")
            self._order = self._valid_order(order)
            self._selected = True
            self._save_state()
            self._selection_event.set()

    def status(self):
        with self._lock:
            remaining = max(0, int(ORDER_TIMEOUT_SECONDS - (time.time() - self._started_at)))
            return {
                "order": [{"id": x, "label": GROUP_LABELS[x]} for x in self._order],
                "default_order": list(DEFAULT_ORDER),
                "selection_open": not self._running and not self._selected and remaining > 0,
                "selection_remaining_seconds": remaining,
                "running": self._running,
                "current": self._current,
                "completed": list(self._completed),
                "completed_groups": [
                    group for group, items in MODEL_GROUPS.items()
                    if all(item[2] in self._completed for item in items)
                ],
                "error": self._error,
                "skipped": os.environ.get("SKIP_MODEL_DOWNLOAD") == "1",
                "llm_enabled": os.environ.get("DOWNLOAD_LLM_MODELS") == "1",
            }

    def _run(self):
        if not self._selected:
            self._selection_event.wait(max(0, ORDER_TIMEOUT_SECONDS - (time.time() - self._started_at)))
        with self._lock:
            self._running = True
        for group in self._order:
            if group == "llm" and os.environ.get("DOWNLOAD_LLM_MODELS") != "1":
                continue
            for item in MODEL_GROUPS[group]:
                with self._lock:
                    self._current = {"group": group, "label": GROUP_LABELS[group], "file": item[2]}
                    self._error = None
                try:
                    fetch(*item)
                    with self._lock:
                        self._completed.append(item[2])
                except Exception as exc:
                    with self._lock:
                        self._error = str(exc)[:500]
                    if group == "llm":
                        continue
        with self._lock:
            self._current = None
            self._running = False


MODEL_MANAGER = ModelDownloadManager()


def main() -> int:
    if os.environ.get("SKIP_MODEL_DOWNLOAD") == "1":
        print("[models] SKIP_MODEL_DOWNLOAD=1", flush=True)
        return 0
    try:
        manager = ModelDownloadManager()
        manager._selected = True
        manager._run()
    except Exception as exc:
        print(f"[models] ERROR: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
