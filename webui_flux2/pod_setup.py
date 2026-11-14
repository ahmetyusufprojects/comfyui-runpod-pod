"""Optional RunPod setup. The gallery does not depend on this worker."""

import base64
import hashlib
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import fcntl

ROOT = Path(os.environ["POD_RUNTIME_ROOT"])
COMFY = Path(os.environ["COMFYUI_ROOT"])
MODELS = COMFY / "models"
COMFY_REF = "8713616fcc321624e306bbd36be5906203dcb0dc"
_CATALOG_DATA = json.loads(Path(os.environ["POD_MODEL_CATALOG"]).read_text(encoding="utf-8"))
if _CATALOG_DATA.get("schema") != 2:
    raise RuntimeError("Model listesi desteklenmeyen biçimde.")
CATALOG = copy.deepcopy(_CATALOG_DATA.get("groups") or {})
WORKFLOWS = copy.deepcopy(_CATALOG_DATA.get("workflows") or {})
_CIVITAI_HOSTS = frozenset(("civitai.com", "www.civitai.com",
                            "civitai.red", "www.civitai.red"))
LLAMA_TAG = "b12128"
LLAMA_RELEASE = {
    f"llama-{LLAMA_TAG}-bin-ubuntu-cuda-12.8-x64.tar.gz":
        "dad00f56a9c5eeb0b99fc90350ae462da6985a986e0cbaab5d9cc333d4f41abc",
    f"cudart-llama-{LLAMA_TAG}-bin-ubuntu-cuda-12.8-x64.tar.gz":
        "d4db45f2cb9895ceaeb1f0e4d7885b60d1ec407ea212bce6131e8b714fd49497",
}
LLAMA_RELEASE_ASSETS = {
    f"llama-{LLAMA_TAG}-bin-ubuntu-cuda-12.8-x64.tar.gz": 713618516,
    f"cudart-llama-{LLAMA_TAG}-bin-ubuntu-cuda-12.8-x64.tar.gz": 713613150,
}
_lock = threading.Lock()
_queue = []
_active = None
_thread = None
_state = {}
_active_cancel = None
_pause_requested = set()
_active_process = None
_active_progress = {}
_last_console_progress = {}
_comfy_process = None
_data_lock = None
_flex = None
_ready = False
_system_settings_path = None
_storage_mode = "unknown"
_system_settings_lock = threading.RLock()
_SYSTEM_SETTINGS_DEFAULT = {
    "schema": 1,
    "auto_download": True,
    "default_download_order": ["zimage", "llm_26b"],
    "model_instances": [],
    "link_overrides": {},
    "loras": [{"id": "anime-desire-illustrious",
               "name": "Anime Desire · Illustrious",
               "model": "illustrious", "group": "anime_desire_lora",
               "file": "anime-2d-ill_v3.fp16.safetensors",
               "weight": 1.0, "enabled": True, "default_selected": True,
               "auto_download": False,
               "built_in": True}],
}

_LORA_MODELS = {"z_image", "flux2", "flux3", "krea2", "illustrious"}
_LORA_ID_RE = re.compile(r"^[a-f0-9]{12}$")

# Module implementations; this facade retains the public API and shared state.
from pod_setup_modules.catalog import (
    _bagimlilik_sirasi,
    _engine_ready,
    _group_files_complete,
    _group_files_verified,
    _installation_ready,
    _runtime_available,
    _transitive_dependents,
    _validate_catalog,
    is_ready,
    workflow,
    workflow_enabled,
    workflow_ready,
)
from pod_setup_modules.settings import (
    _apply_saved_links,
    _civitai_resolve_metadata,
    _default_model_instances,
    _file_link,
    _hf_link,
    _hf_resolve_metadata,
    _legacy_lora_source,
    _lora_entries_clean,
    _model_instances_apply,
    _parse_hf_link,
    _parse_lora_link,
    _parse_model_link,
    _register_lora_groups,
    _system_settings_read,
    _system_settings_versions,
    _system_settings_write,
    add_lora,
    configure_lora,
    create_model_instance,
    delete_lora,
    delete_model_instance,
    ensure_loras,
    lora_status,
    model_instance,
    model_options,
    refresh_after_system_import,
    rename_model_instance,
    system_settings,
    update_model_link,
    update_system_settings,
)
from pod_setup_modules.storage import (
    checkpoint,
    clear_rebuildable_storage,
    prepare_data,
    refresh_data_lock,
    status,
    storage_status,
    validate_mount,
)
from pod_setup_modules.installer import (
    _command,
    _download_llama_package,
    _gpu_memory,
    _install_comfy,
    _install_custom_nodes,
    _install_llama,
    _launch_comfy,
    _llama_cuda_devices,
    _process_io_bytes,
    _progress,
    _record_llama_cuda_scan,
    _run,
    _stop_process,
    _tree_size,
    _wait_comfy,
    cancel,
    move,
    pause,
    remove,
    start,
    start_order,
    DownloadCancelled,
)
from pod_setup_modules.downloads import (
    _civitai_model,
    _hf_incomplete_file,
    _model,
    _model_present,
    _model_verified,
    _url_model,
    _valid_model,
    _verification_file,
    _write_verification,
)
_validate_catalog()  # Validate schema-2 model catalog during startup.
