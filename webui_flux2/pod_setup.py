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


class DownloadCancelled(RuntimeError):
    pass


def _validate_catalog():
    """Fail early when a model link, target path, or workflow mapping is invalid."""
    if not isinstance(CATALOG, dict) or not CATALOG:
        raise RuntimeError("Model listesi boş veya bozuk.")
    targets = {}
    for group, item in CATALOG.items():
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,47}", group):
            raise RuntimeError("Model grubu adı geçersiz.")
        if item.get("engine") not in ("comfy", "llama", "translation"):
            raise RuntimeError(f"{group}: desteklenmeyen çalışma ortamı.")
        if not isinstance(item.get("label"), str) or not item.get("label"):
            raise RuntimeError(f"{group}: görünen ad eksik.")
        custom_nodes = item.get("custom_nodes") or []
        if not isinstance(custom_nodes, list):
            raise RuntimeError(f"{group}: eklenti listesi geçersiz.")
        for node in custom_nodes:
            if (not isinstance(node, dict)
                    or not re.fullmatch(r"[a-z0-9_-]{1,64}", str(node.get("name") or ""))
                    or not re.fullmatch(r"[0-9a-f]{40}", str(node.get("revision") or ""))
                    or not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?",
                                        str(node.get("repo") or ""))):
                raise RuntimeError(f"{group}: eklenti kaynağı geçersiz.")
        files = item.get("files")
        if not isinstance(files, list) or not files:
            raise RuntimeError(f"{group}: dosya listesi boş.")
        for file in files:
            directory, name = file.get("dir"), file.get("name")
            if (not isinstance(directory, str) or not isinstance(name, str)
                    or not directory or not name
                    or Path(directory).is_absolute() or Path(name).name != name
                    or ".." in Path(directory).parts):
                raise RuntimeError(f"{group}: hedef dosya yolu geçersiz.")
            target = (directory, name)
            source_type = file.get("source") or "hf"
            source = (source_type, file.get("repo"), file.get("url"),
                      file.get("revision"), file.get("path"), file.get("version"),
                      file.get("host"), file.get("sha256"))
            if target in targets and targets[target] != source:
                raise RuntimeError(f"{group}: ortak dosyanın kaynağı gruplar arasında farklı.")
            targets[target] = source
            if source_type not in ("hf", "civitai", "url"):
                raise RuntimeError(f"{group}: desteklenmeyen indirme kaynağı.")
            if source_type == "civitai":
                if not isinstance(file.get("version"), int):
                    raise RuntimeError(f"{group}: Civitai sürüm numarası eksik.")
                if file.get("host", "civitai.com") not in _CIVITAI_HOSTS:
                    raise RuntimeError(f"{group}: Civitai alan adı geçersiz.")
            elif source_type == "url":
                parsed = urllib.parse.urlsplit(file.get("url") or "")
                if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                        or parsed.password or parsed.fragment):
                    raise RuntimeError(f"{group}: doğrudan indirme bağlantısı güvenli HTTPS olmalı.")
            elif not all(isinstance(file.get(key), str) and file.get(key)
                         for key in ("repo", "revision", "path")):
                raise RuntimeError(f"{group}: Hugging Face deposu, sürümü veya yolu eksik.")
            digest = file.get("sha256")
            if digest is not None and not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
                raise RuntimeError(f"{group}: SHA-256 biçimi geçersiz.")
        dependencies = item.get("depends_on", [])
        if not isinstance(dependencies, list) or any(dep not in CATALOG for dep in dependencies):
            raise RuntimeError(f"{group}: bağımlılık listesi geçersiz.")

    def visit(group, visited, active):
        if group in active:
            raise RuntimeError("Model listesinde döngüsel bağımlılık var.")
        if group in visited:
            return
        active.add(group)
        for dependency in CATALOG[group].get("depends_on", []):
            visit(dependency, visited, active)
        active.remove(group)
        visited.add(group)

    visited = set()
    for group in CATALOG:
        visit(group, visited, set())
    for name, workflow in WORKFLOWS.items():
        if workflow.get("group") not in CATALOG:
            raise RuntimeError(f"{name}: iş akışının model grubu bulunamadı.")
        for profile in (workflow.get("profiles") or {}).values():
            if profile.get("group") not in CATALOG:
                raise RuntimeError(f"{name}: profil grubu bulunamadı.")


_validate_catalog()


_LORA_MODELS = {"z_image", "flux2", "flux3", "krea2", "illustrious"}
_LORA_ID_RE = re.compile(r"^[a-f0-9]{12}$")

def _legacy_lora_source(item, filename):
    """Recover installer metadata from older backups that stored only a link."""
    source_file = item.get("source_file")
    if isinstance(source_file, dict):
        source = source_file.get("source") or "hf"
        if source == "hf" and all(source_file.get(key) for key in
                                  ("repo", "revision", "path")):
            return {**source_file, "source": "hf", "dir": "loras", "name": filename}
        if source == "civitai" and str(source_file.get("version", "")).isdigit():
            host = source_file.get("host", "civitai.com")
            if host in _CIVITAI_HOSTS:
                return {**source_file, "source": "civitai", "host": host,
                        "dir": "loras", "name": filename}
        if source == "url" and str(source_file.get("url", "")).startswith("https://"):
            return {**source_file, "source": "url", "dir": "loras", "name": filename}
    link = str(item.get("link") or "").strip()
    parsed = urllib.parse.urlsplit(link)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        return None
    if parsed.hostname == "huggingface.co":
        try:
            source = _parse_hf_link(link)
        except ValueError:
            return None
        if Path(source["path"]).suffix.lower() not in (".safetensors", ".pt"):
            return None
        return dict(source, source="hf", dir="loras", name=filename)
    if parsed.hostname not in _CIVITAI_HOSTS:
        return None
    query = urllib.parse.parse_qs(parsed.query)
    version = None
    match = re.fullmatch(r"/api/download/models/(\d+)/?", parsed.path)
    if match:
        version = int(match.group(1))
    else:
        for key in ("modelVersionId", "versionId"):
            try:
                version = int((query.get(key) or [""])[0])
                break
            except (TypeError, ValueError):
                continue
    if not version or version < 1:
        return None
    host = "civitai.red" if parsed.hostname.endswith(".red") else "civitai.com"
    return {"source": "civitai", "host": host, "version": version,
            "dir": "loras", "name": filename}


def _lora_entries_clean(values):
    result = []
    if not isinstance(values, list):
        return result
    used = set()
    for item in values[:100]:
        if not isinstance(item, dict):
            continue
        identifier = item.get("id")
        if not isinstance(identifier, str) or not (
                identifier == "anime-desire-illustrious"
                or _LORA_ID_RE.fullmatch(identifier)) or identifier in used:
            continue
        model = item.get("model")
        group = item.get("group")
        name = str(item.get("name") or "").strip()[:80]
        filename = item.get("file")
        if (model not in _LORA_MODELS or not isinstance(group, str)
                or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,47}", group)
                or not isinstance(filename, str) or Path(filename).name != filename
                or Path(filename).suffix.lower() not in (".safetensors", ".pt")
                or not name):
            continue
        try:
            weight = max(0.0, min(2.0, float(item.get("weight", 1.0))))
        except (TypeError, ValueError, OverflowError):
            weight = 1.0
        result.append({"id": identifier, "name": name, "model": model,
                       "group": group, "file": filename,
                       "weight": round(weight, 2),
                       "enabled": item.get("enabled") is True,
                       "auto_download": item.get("auto_download") is True,
                       "default_selected": item.get("default_selected") is True,
                       "built_in": item.get("built_in") is True,
                       "source_file": _legacy_lora_source(item, filename),
                       "link": str(item.get("link") or "")[:1000]})
        used.add(identifier)
    return result


def _register_lora_groups(settings):
    """Expose saved LoRAs as ordinary cancellable installer download groups."""
    entries = _lora_entries_clean(settings.get("loras"))
    groups = {entry["group"] for entry in entries
              if workflow_enabled(entry["model"])
              and isinstance(entry.get("source_file"), dict)}
    for group in list(CATALOG):
        if group.startswith("lora_") and group not in groups:
            CATALOG.pop(group, None)
            _state.pop(group, None)
    for entry in entries:
        if not workflow_enabled(entry["model"]):
            continue
        group = entry["group"]
        if group in CATALOG:
            continue
        file = entry.get("source_file")
        if not isinstance(file, dict):
            continue
        CATALOG[group] = {"label": "LoRA · " + entry["name"],
                          "engine": "comfy", "files": [dict(file)]}
    _validate_catalog()


def refresh_after_system_import():
    """Merge legacy backup settings with this image's current models and LoRAs."""
    with _system_settings_lock:
        settings = _system_settings_read()
        _model_instances_apply(settings)
        settings["loras"] = _lora_entries_clean(settings.get("loras"))
        _register_lora_groups(settings)
        settings["default_download_order"] = [
            group for group in settings.get("default_download_order", [])
            if group in CATALOG and CATALOG[group].get("enabled", True)]
        _apply_saved_links(settings)
        _system_settings_write(settings)
        return {"model_count": len(settings.get("model_instances", [])),
                "lora_count": len(settings.get("loras", []))}


def _system_settings_read():
    settings = json.loads(json.dumps(_SYSTEM_SETTINGS_DEFAULT))
    if _system_settings_path is None:
        return settings
    saved = None
    # Global Volume is object-backed storage, not a POSIX filesystem. Read
    # immutable, checksummed versions first; keep the old single-file format as
    # a migration fallback for settings written by earlier images.
    for timestamp, digest, candidate in _system_settings_versions():
        try:
            payload = candidate.read_bytes()
            if hashlib.sha256(payload).hexdigest() != digest:
                continue
            value = json.loads(payload.decode("utf-8"))
        except (OSError, ValueError, TypeError, UnicodeDecodeError):
            continue
        if isinstance(value, dict) and value.get("schema") == 1:
            saved = value
            break
    if saved is None:
        try:
            saved = json.loads(_system_settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return settings
    if not isinstance(saved, dict) or saved.get("schema") != 1:
        return settings
    if isinstance(saved.get("auto_download"), bool):
        settings["auto_download"] = saved["auto_download"]
    order = saved.get("default_download_order")
    if isinstance(order, list):
        settings["default_download_order"] = [
            group for group in order if isinstance(group, str)]
    instances = saved.get("model_instances")
    if isinstance(instances, list):
        settings["model_instances"] = instances
    overrides = saved.get("link_overrides")
    if isinstance(overrides, dict):
        settings["link_overrides"] = overrides
    if isinstance(saved.get("loras"), list):
        saved_loras = _lora_entries_clean(saved["loras"])
        builtin = [entry for entry in settings.get("loras", [])
                   if entry.get("built_in")]
        saved_ids = {entry["id"] for entry in saved_loras}
        settings["loras"] = saved_loras + [entry for entry in builtin
                                             if entry["id"] not in saved_ids]
    return settings


def _system_settings_versions():
    """Return verified-version filenames in newest-first order."""
    if _system_settings_path is None:
        return []
    pattern = re.compile(
        re.escape(_system_settings_path.stem)
        + r"-(\d{19})-([a-f0-9]{16})-([a-f0-9]{64})\.json$")
    try:
        entries = list(_system_settings_path.parent.iterdir())
    except OSError:
        return []
    versions = []
    for candidate in entries:
        match = pattern.fullmatch(candidate.name)
        if match and candidate.is_file():
            versions.append((match.group(1), match.group(3), candidate))
    return sorted(versions, key=lambda item: (item[0], item[2].name), reverse=True)


def _system_settings_write(settings):
    if _system_settings_path is None:
        raise RuntimeError("Sistem ayarlarının kaydedileceği depolama hazır değil.")
    payload = json.dumps(settings, ensure_ascii=False, indent=2).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    timestamp = time.time_ns()
    unique = os.urandom(8).hex()
    destination = _system_settings_path.with_name(
        "%s-%019d-%s-%s.json" %
        (_system_settings_path.stem, timestamp, unique, digest))
    destination.parent.mkdir(parents=True, exist_ok=True)
    # A new object is written directly and checked after close. This avoids
    # relying on rename/replace semantics that Global Volume does not promise.
    with destination.open("wb") as stream:
        stream.write(payload)
    # Global Volume objects can become visible to the mounted filesystem a
    # little after the write completes. Retry the read-back before reporting a
    # failed save; the immutable object remains safe if visibility is delayed.
    verified = False
    last_error = None
    for attempt in range(6):
        try:
            if destination.read_bytes() == payload:
                verified = True
                break
        except OSError as error:
            last_error = error
        if attempt < 5:
            time.sleep(min(0.2 * (2 ** attempt), 2.0))
    if not verified:
        message = "Kalıcı sistem ayarları doğrulanamadı."
        if last_error is not None:
            message += " (%s)" % last_error
        raise RuntimeError(message)
    versions = _system_settings_versions()
    valid = []
    invalid = []
    for _, expected_digest, old in versions:
        try:
            old_payload = old.read_bytes()
            old_settings = json.loads(old_payload.decode("utf-8"))
            is_valid = (hashlib.sha256(old_payload).hexdigest() == expected_digest
                        and isinstance(old_settings, dict)
                        and old_settings.get("schema") == 1)
        except (OSError, ValueError, TypeError, UnicodeDecodeError):
            is_valid = False
        (valid if is_valid else invalid).append(old)
    for old in invalid + valid[8:]:
        try:
            old.unlink()
        except OSError:
            pass


def _hf_link(file):
    if (file.get("source") or "hf") != "hf":
        return ""
    repo = urllib.parse.quote(file.get("repo", ""), safe="/")
    revision = urllib.parse.quote(file.get("revision", ""), safe="")
    path = urllib.parse.quote(file.get("path", ""), safe="/")
    return f"https://huggingface.co/{repo}/resolve/{revision}/{path}"


def _file_link(file):
    source = file.get("source") or "hf"
    if source == "hf":
        return _hf_link(file)
    if source == "civitai":
        host = file.get("host", "civitai.com")
        if host not in _CIVITAI_HOSTS:
            host = "civitai.com"
        return "https://%s/api/download/models/%s" % (host, int(file["version"]))
    return str(file.get("url") or "")


def _apply_saved_links(settings):
    overrides = settings.get("link_overrides") or {}
    for group, item in CATALOG.items():
        for file in item.get("files", []):
            key = group + "/" + file["name"]
            override = overrides.get(key)
            if not isinstance(override, dict):
                continue
            for field in ("source", "repo", "revision", "path", "url", "version",
                          "host", "size", "sha256"):
                file.pop(field, None)
                if field in override:
                    file[field] = override[field]
    _validate_catalog()


def _default_model_instances():
    instances = []
    for family, workflow in (_CATALOG_DATA.get("workflows") or {}).items():
        group_id = workflow.get("group")
        group = CATALOG.get(group_id)
        if not group or not group.get("enabled", True):
            continue
        if group.get("engine") not in ("comfy", "llama"):
            continue
        instances.append({
            "id": family, "family": family, "name": group["label"],
            "group": group_id, "workflow": copy.deepcopy(workflow),
            "files": copy.deepcopy(group["files"]), "builtin": True,
        })
    return instances


def _model_instances_apply(settings):
    """Rebuild editable model variants from the checksummed universal settings."""
    saved = settings.get("model_instances") or []
    saved_variant_groups = {item.get("group") for item in saved
                            if isinstance(item, dict)
                            and re.fullmatch(r"variant_[a-f0-9]{10}",
                                             str(item.get("group") or ""))}
    for group, item in list(CATALOG.items()):
        if item.get("variant") and group not in saved_variant_groups:
            CATALOG.pop(group, None)
    builtin_ids = set((_CATALOG_DATA.get("workflows") or {}).keys())
    saved_variant_ids = {item.get("id") for item in saved
                         if isinstance(item, dict)
                         and re.fullmatch(r"variant_[a-f0-9]{10}",
                                          str(item.get("group") or ""))}
    for identifier in list(WORKFLOWS):
        if identifier not in builtin_ids and identifier not in saved_variant_ids:
            WORKFLOWS.pop(identifier, None)
    saved_by_id = {item.get("id"): item for item in saved
                   if isinstance(item, dict) and isinstance(item.get("id"), str)}
    records = _default_model_instances()
    for record in records:
        stored = saved_by_id.get(record["id"])
        if stored and isinstance(stored.get("name"), str):
            record["name"] = stored["name"][:80].strip() or record["name"]
        group = CATALOG[record["group"]]
        group["label"] = record["name"]
        group["instance_id"] = record["id"]
        group["family"] = record["family"]
        WORKFLOWS[record["id"]] = copy.deepcopy(record["workflow"])
    known = {record["id"] for record in records}
    for stored in saved:
        if not isinstance(stored, dict):
            continue
        identifier = stored.get("id")
        group_id = stored.get("group")
        family = stored.get("family")
        if (identifier in known or not isinstance(identifier, str)
                or not re.fullmatch(r"[a-z][a-z0-9_]{2,47}", identifier)
                or family not in WORKFLOWS or not isinstance(group_id, str)
                or not re.fullmatch(r"variant_[a-f0-9]{10}", group_id)):
            continue
        workflow = stored.get("workflow")
        files = stored.get("files")
        name = str(stored.get("name") or "")[:80].strip()
        if not name or not isinstance(workflow, dict) or not isinstance(files, list):
            continue
        base_group = CATALOG.get((WORKFLOWS.get(family) or {}).get("group"))
        if not base_group or not isinstance(workflow.get("group"), str):
            continue
        CATALOG[group_id] = {"label": name, "engine": base_group["engine"],
                             "files": copy.deepcopy(files), "instance_id": identifier,
                             "family": family, "variant": True}
        workflow = copy.deepcopy(workflow)
        workflow["group"] = group_id
        WORKFLOWS[identifier] = workflow
        records.append({"id": identifier, "family": family, "name": name,
                        "group": group_id, "workflow": workflow,
                        "files": copy.deepcopy(files), "builtin": False})
        known.add(identifier)
    settings["model_instances"] = records
    _validate_catalog()


def model_options():
    settings = _system_settings_read()
    records = settings.get("model_instances") or _default_model_instances()
    result = []
    for record in records:
        group = CATALOG.get(record.get("group")) or {}
        workflow = WORKFLOWS.get(record["id"]) or {}
        primary_name = next((workflow.get(key) for key in
                             ("diffusion_file", "checkpoint_file", "unet_file", "model_file")
                             if workflow.get(key)), None)
        result.append({"id": record["id"], "family": record["family"],
                       "name": record["name"], "group": record["group"],
                       "engine": group.get("engine"),
                       "ready": workflow_ready(record["id"]),
                       "builtin": bool(record.get("builtin")),
                       "primary_file": primary_name,
                       "files": [{"name": file["name"], "dir": file["dir"],
                                  "optional": bool(file.get("optional")),
                                  "workflow_key": next((key for key, value in workflow.items()
                                                        if value == file["name"]), None),
                                  "link": _file_link(file),
                                  "default_link": _file_link(
                                      file.get("_default_source") or file)}
                                 for file in group.get("files", [])]})
    return result


def model_instance(identifier):
    if not isinstance(identifier, str):
        return None
    for record in _system_settings_read().get("model_instances", []):
        if record.get("id") == identifier:
            return copy.deepcopy(record)
    return None


def create_model_instance(payload):
    if not isinstance(payload, dict):
        raise ValueError("Model bilgileri geçersiz.")
    family = payload.get("family")
    base_workflow = copy.deepcopy(_CATALOG_DATA.get("workflows", {}).get(family) or {})
    base_group_id = base_workflow.get("group")
    base_group = copy.deepcopy(_CATALOG_DATA.get("groups", {}).get(base_group_id) or {})
    if (not base_workflow or not base_group or base_group.get("engine") not in ("comfy", "llama")
            or not base_group.get("enabled", True)
            or not CATALOG.get(base_group_id, {}).get("enabled", True)):
        raise ValueError("Bu model türü için yeni kayıt oluşturulamıyor.")
    name = str(payload.get("name") or "").strip()
    if not name or len(name) > 80:
        raise ValueError("Model adı 1-80 karakter olmalı.")
    links = payload.get("links") or {}
    if not isinstance(links, dict) or len(links) > 12:
        raise ValueError("Model dosyası bağlantıları geçersiz.")
    excluded = payload.get("exclude_files") or []
    if (not isinstance(excluded, list) or len(excluded) > 12
            or any(not isinstance(item, str) for item in excluded)):
        raise ValueError("İsteğe bağlı dosya seçimi geçersiz.")
    optional_names = {file["name"] for file in base_group.get("files", [])
                      if file.get("optional")}
    if not set(excluded).issubset(optional_names):
        raise ValueError("Zorunlu veya bilinmeyen bir dosya devre dışı bırakılamaz.")
    primary = next((key for key in ("diffusion_file", "checkpoint_file",
                                    "unet_file", "model_file")
                    if base_workflow.get(key)), None)
    primary_file = base_workflow.get(primary) if primary else None
    primary_link = next((_file_link(file) for file in base_group.get("files", [])
                         if file.get("name") == primary_file), None)
    if not isinstance(links.get(primary_file), str) or not links[primary_file].strip():
        raise ValueError("Yeni model için ana dosyanın bağlantısını gir.")
    if links[primary_file].strip() == primary_link:
        raise ValueError("Yeni model kaydında ana dosya bağlantısı farklı olmalı.")
    suffix = uuid.uuid4().hex[:10]
    identifier = family + "_" + suffix
    group_id = "variant_" + suffix
    group = copy.deepcopy(base_group)
    workflow = copy.deepcopy(base_workflow)
    group["label"] = name
    group["instance_id"] = identifier
    group["family"] = family
    group["variant"] = True
    rewritten = []
    for file in group.get("files", []):
        old_name = file["name"]
        if old_name in excluded:
            for key, value in list(workflow.items()):
                if value == old_name:
                    workflow.pop(key, None)
            continue
        file["_default_source"] = {
            key: value for key, value in file.items()
            if key in ("source", "repo", "revision", "path", "url",
                       "version", "host", "size", "sha256")
        }
        requested_link = links.get(old_name)
        is_primary = bool(primary and workflow.get(primary) == old_name)
        if requested_link or is_primary:
            path = Path(old_name)
            new_name = "%s__%s%s" % (path.stem, suffix, path.suffix)
            file["name"] = new_name
            for key, value in list(workflow.items()):
                if value == old_name:
                    workflow[key] = new_name
            if requested_link:
                source, metadata = _parse_model_link(requested_link)
                for field in ("source", "repo", "revision", "path", "url",
                              "version", "host", "size", "sha256"):
                    file.pop(field, None)
                file.update(source, **metadata)
        rewritten.append(file)
    group["files"] = rewritten
    workflow["group"] = group_id
    record = {"id": identifier, "family": family, "name": name,
              "group": group_id, "workflow": workflow,
              "files": copy.deepcopy(rewritten), "builtin": False}
    with _system_settings_lock:
        settings = _system_settings_read()
        settings["model_instances"] = settings.get("model_instances") or _default_model_instances()
        CATALOG[group_id] = group
        WORKFLOWS[identifier] = workflow
        settings["model_instances"].append(record)
        _validate_catalog()
        _system_settings_write(settings)
    return system_settings()


def rename_model_instance(identifier, name):
    name = str(name or "").strip()
    if not name or len(name) > 80:
        raise ValueError("Model adı 1-80 karakter olmalı.")
    with _system_settings_lock:
        settings = _system_settings_read()
        records = settings.get("model_instances") or _default_model_instances()
        record = next((item for item in records if item.get("id") == identifier), None)
        if record is None:
            raise ValueError("Model bulunamadı.")
        record["name"] = name
        record["files"] = copy.deepcopy(CATALOG[record["group"]]["files"])
        record["workflow"] = copy.deepcopy(WORKFLOWS[identifier])
        CATALOG[record["group"]]["label"] = name
        _system_settings_write(settings)
    return system_settings()


def delete_model_instance(identifier):
    with _system_settings_lock:
        settings = _system_settings_read()
        records = settings.get("model_instances") or _default_model_instances()
        record = next((item for item in records if item.get("id") == identifier), None)
        if record is None or record.get("builtin"):
            raise ValueError("Varsayılan model kaydı silinemez.")
        group = record["group"]
        if _active == group or group in _queue:
            raise RuntimeError("Model indirilirken kaydı silinemez.")
        has_group_files = any((MODELS / file["dir"] / file["name"]).exists()
                              for file in CATALOG.get(group, {}).get("files", []))
        if has_group_files or _state.get(group, ("idle",))[0] != "idle":
            remove(group)
        records = [item for item in records if item.get("id") != identifier]
        settings["model_instances"] = records
        CATALOG.pop(group, None)
        WORKFLOWS.pop(identifier, None)
        _system_settings_write(settings)
    return system_settings()


def system_settings():
    """Return universal configuration without account-specific settings."""
    with _system_settings_lock:
        settings = _system_settings_read()
        groups = []
        for group, item in CATALOG.items():
            if not item.get("enabled", True):
                continue
            files = []
            originals = {source.get("name"): source
                         for source in (_CATALOG_DATA.get("groups", {}).get(group, {})
                                        .get("files", []))}
            for file in item.get("files", []):
                original = (originals.get(file["name"])
                            or file.get("_default_source") or file)
                files.append({
                    "name": file["name"], "dir": file["dir"],
                    "size": file.get("size"), "optional": bool(file.get("optional")),
                    "gated": bool(file.get("gated")),
                    "link": _file_link(file),
                    "default_link": _file_link(original),
                })
            groups.append({"id": group, "label": item["label"], "files": files})
        return {"ayarlar": settings, "gruplar": groups,
                "model_instances": model_options(),
                "model_families": [
                    {"id": family, "name": (CATALOG.get(workflow.get("group")) or {}).get("label", family),
                     "engine": (CATALOG.get(workflow.get("group")) or {}).get("engine")}
                    for family, workflow in _CATALOG_DATA.get("workflows", {}).items()
                    if workflow.get("group") in CATALOG
                    and CATALOG[workflow["group"]].get("enabled", True)
                    and CATALOG[workflow["group"]].get("engine") in ("comfy", "llama")
                ], "lora_durum": lora_status()}


def _parse_hf_link(link):
    parsed = urllib.parse.urlsplit(str(link or "").strip())
    if (parsed.scheme != "https" or parsed.hostname != "huggingface.co"
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Yalnızca Hugging Face resolve bağlantısı kullanılabilir.")
    parts = [urllib.parse.unquote(part) for part in parsed.path.strip("/").split("/")]
    if len(parts) < 5 or parts[2] != "resolve" or not parts[3]:
        raise ValueError("Bağlantı biçimi: huggingface.co/{hesap}/{depo}/resolve/{sürüm}/{dosya}")
    repo = "/".join(parts[:2])
    path = "/".join(parts[4:])
    if (not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo)
            or any(part in ("", ".", "..") for part in path.split("/"))):
        raise ValueError("Hugging Face depo veya dosya yolu geçersiz.")
    return {"repo": repo, "revision": parts[3], "path": path}


def update_system_settings(payload):
    if not isinstance(payload, dict):
        raise ValueError("Sistem ayarları sözlük olmalı.")
    with _system_settings_lock:
        settings = _system_settings_read()
        if "auto_download" in payload:
            if not isinstance(payload["auto_download"], bool):
                raise ValueError("Otomatik indirme seçeneği açık veya kapalı olmalı.")
            settings["auto_download"] = payload["auto_download"]
        if "default_download_order" in payload:
            order = payload["default_download_order"]
            if (not isinstance(order, list) or len(order) > len(CATALOG)
                    or any(group not in CATALOG or not CATALOG[group].get("enabled", True)
                           for group in order) or len(set(order)) != len(order)):
                raise ValueError("Varsayılan indirme sırası geçersiz.")
            settings["default_download_order"] = order
        _system_settings_write(settings)
        return system_settings()


def update_model_link(group, name, link, restore=False):
    if group not in CATALOG or not CATALOG[group].get("enabled", True):
        raise ValueError("Bu model grubu düzenlenemez.")
    file = next((item for item in CATALOG[group]["files"] if item["name"] == name), None)
    if file is None:
        raise ValueError("Dosya bulunamadı.")
    with _system_settings_lock:
        affected = [(candidate, item) for candidate, catalog in CATALOG.items()
                    for item in catalog.get("files", [])
                    if item["dir"] == file["dir"] and item["name"] == name]
        affected_groups = {candidate for candidate, _ in affected}
        if _active in affected_groups or affected_groups.intersection(_queue):
            raise RuntimeError("İndirme kuyruğu çalışırken bağlantı değiştirilemez.")
        settings = _system_settings_read()
        overrides = settings.setdefault("link_overrides", {})
        target = MODELS / file["dir"] / file["name"]
        installed_groups = ([candidate for candidate in affected_groups
                             if _group_files_complete(candidate)]
                            if _model_verified(target, file) else [])
        if restore:
            for candidate, shared in affected:
                overrides.pop(candidate + "/" + name, None)
                original_files = (_CATALOG_DATA.get("groups", {}).get(candidate, {})
                                  .get("files", []))
                if not original_files:
                    saved_lora = next((entry for entry in settings.get("loras", [])
                                       if entry.get("group") == candidate), None)
                    source_file = (saved_lora or {}).get("source_file") or {}
                    original_files = [source_file] if source_file else []
                original = (next((item for item in original_files
                                  if item.get("name") == name), None)
                            or shared.get("_default_source"))
                if not original:
                    raise ValueError("Varsayılan LoRA bağlantısı bulunamadı.")
                for field in ("source", "repo", "revision", "path", "url",
                              "version", "size", "sha256"):
                    if field in original:
                        shared[field] = original[field]
                    else:
                        shared.pop(field, None)
        else:
            source, metadata = _parse_model_link(link)
            for candidate, shared in affected:
                overrides[candidate + "/" + name] = dict(source, **metadata)
                for field in ("source", "repo", "revision", "path", "url", "version"):
                    shared.pop(field, None)
                shared.update(source)
                for field in ("size", "sha256"):
                    if field in metadata:
                        shared[field] = metadata[field]
                    else:
                        shared.pop(field, None)
        if installed_groups:
            target.unlink(missing_ok=True)
            _verification_file(target).unlink(missing_ok=True)
        # A partial transfer belongs to the previous URL and must not be resumed.
        target.with_suffix(".part").unlink(missing_ok=True)
        _validate_catalog()
        _system_settings_write(settings)
    for candidate in installed_groups:
        try:
            start(candidate)
        except RuntimeError:
            # A shared installed dependency can already be queued by another
            # affected group; the first queued job will restore that dependency.
            pass
    result = system_settings()
    result["restarted_groups"] = installed_groups
    return result


def _hf_resolve_metadata(source):
    """Read file size and Git-LFS digest without downloading its model weights."""
    path = "/%s/resolve/%s/%s" % (source["repo"],
                                  urllib.parse.quote(source["revision"], safe=""),
                                  urllib.parse.quote(source["path"], safe="/"))
    request = urllib.request.Request("https://huggingface.co" + path,
                                     method="HEAD", headers={"User-Agent": "RunPod-WebUI/1"})
    token = os.environ.get("HF_TOKEN")
    if token:
        request.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            headers = response.headers
    except (OSError, urllib.error.URLError, urllib.error.HTTPError):
        # Public sources may not expose HEAD metadata through every mirror/proxy.
        # The downloader will still verify the result if a digest is supplied.
        return {}
    size = headers.get("x-linked-size") or headers.get("content-length")
    digest = (headers.get("x-linked-etag") or "").strip().strip('"').lower()
    result = {}
    try:
        if size and int(size) > 0:
            result["size"] = int(size)
    except (TypeError, ValueError):
        pass
    if re.fullmatch(r"[0-9a-f]{64}", digest):
        result["sha256"] = digest
    return result


def _parse_model_link(link):
    """Resolve HF, Civitai, or direct HTTPS sources without changing the target."""
    value = str(link or "").strip()
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.fragment):
        raise ValueError("Model bağlantısı kimlik bilgisi içermeyen HTTPS olmalı.")
    if parsed.hostname == "huggingface.co":
        source = _parse_hf_link(value)
        source["source"] = "hf"
        return source, _hf_resolve_metadata(source)
    if parsed.hostname in _CIVITAI_HOSTS:
        source, _ = _parse_lora_link(value)
        return source, {key: source[key] for key in ("size", "sha256")
                        if key in source}
    if any(key.casefold() in ("token", "api_key", "apikey", "key")
           for key in urllib.parse.parse_qs(parsed.query)):
        raise ValueError("Gizli anahtar bağlantıya eklenmemeli; Pod Secret kullanılır.")
    request = urllib.request.Request(value, method="HEAD",
                                     headers={"User-Agent": "RunPod-WebUI/1"})
    metadata = {}
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            size = response.headers.get("Content-Length")
            if size and int(size) > 0:
                metadata["size"] = int(size)
    except (OSError, ValueError, urllib.error.URLError, urllib.error.HTTPError):
        pass
    return {"source": "url", "url": value}, metadata


def _civitai_resolve_metadata(version, host="civitai.com"):
    token = os.environ.get("CIVITAI_TOKEN")
    if not token or token.startswith("{{"):
        return {}
    if host not in _CIVITAI_HOSTS:
        host = "civitai.com"
    if host.startswith("www."):
        host = host[4:]
    request = urllib.request.Request(
        "https://%s/api/v1/model-versions/%d" % (host, int(version)),
        headers={"Authorization": "Bearer " + token,
                 "User-Agent": "RunPod-WebUI/1"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
        files = data.get("files") or []
        chosen = next((item for item in files if item.get("primary")),
                      files[0] if files else {})
        result = {}
        size = chosen.get("sizeKB")
        if size and float(size) > 0:
            result["size"] = int(float(size) * 1024)
        digest = ((chosen.get("hashes") or {}).get("SHA256") or "").lower()
        if re.fullmatch(r"[0-9a-f]{64}", digest):
            result["sha256"] = digest
        return result
    except (OSError, ValueError, TypeError, urllib.error.URLError,
            urllib.error.HTTPError):
        return {}


def _parse_lora_link(link):
    value = str(link or "").strip()
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("LoRA bağlantısı HTTPS olmalı.")
    if parsed.hostname == "huggingface.co":
        source = _parse_hf_link(value)
        suffix = Path(source["path"]).suffix.lower()
        if suffix not in (".safetensors", ".pt"):
            raise ValueError("Hugging Face LoRA dosyası .safetensors veya .pt olmalı.")
        return dict(source, source="hf", **_hf_resolve_metadata(source)), suffix
    if parsed.hostname not in _CIVITAI_HOSTS:
        raise ValueError("Yalnızca Hugging Face veya Civitai LoRA bağlantısı desteklenir.")
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    if any(key.casefold() in ("token", "api_key", "apikey") for key in query):
        raise ValueError("Civitai anahtarı bağlantıya eklenmemeli; Pod Secret kullanılır.")
    match = re.fullmatch(r"/api/download/models/(\d+)/?", parsed.path)
    version = int(match.group(1)) if match else None
    if version is None:
        for key in ("modelVersionId", "versionId"):
            try:
                version = int((query.get(key) or [""])[0])
                break
            except (TypeError, ValueError):
                continue
    if not version or version < 1:
        raise ValueError("Civitai bağlantısında model sürümü numarası bulunamadı.")
    host = "civitai.red" if parsed.hostname.endswith(".red") else "civitai.com"
    source = {"source": "civitai", "version": version, "host": host}
    return dict(source, **_civitai_resolve_metadata(version, host)), ".safetensors"


def add_lora(payload):
    if not isinstance(payload, dict):
        raise ValueError("LoRA bilgileri geçersiz.")
    model = payload.get("model")
    name = str(payload.get("name") or "").strip()[:80]
    link = str(payload.get("link") or "").strip()
    if (model not in _LORA_MODELS or not workflow_enabled(model)
            or not name or len(link) > 2000):
        raise ValueError("LoRA adı, modeli veya bağlantısı geçersiz.")
    source, suffix = _parse_lora_link(link)
    digest = hashlib.sha256((model + "\0" + name + "\0" + link + "\0"
                             + str(time.time_ns())).encode("utf-8")).hexdigest()[:12]
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")[:36] or "lora"
    filename = f"{slug}-{digest}{suffix}"
    group = "lora_" + digest
    source_file = dict(source, dir="loras", name=filename)
    entry = {"id": digest, "name": name, "model": model, "group": group,
             "file": filename, "weight": 1.0, "enabled": True,
             "default_selected": False, "auto_download": False,
             "source_file": source_file, "link": link}
    with _system_settings_lock:
        settings = _system_settings_read()
        if any(item.get("id") == digest for item in settings.get("loras", [])):
            raise RuntimeError("Bu LoRA zaten kayıtlı.")
        settings["loras"] = _lora_entries_clean(settings.get("loras", []) + [entry])
        CATALOG[group] = {"label": "LoRA · " + name,
                          "engine": "comfy", "files": [source_file]}
        _validate_catalog()
        _system_settings_write(settings)
        return system_settings()


def configure_lora(identifier, payload):
    if not isinstance(payload, dict):
        raise ValueError("LoRA ayarları geçersiz.")
    with _system_settings_lock:
        settings = _system_settings_read()
        entry = next((item for item in settings.get("loras", [])
                      if item.get("id") == identifier), None)
        if entry is None:
            raise ValueError("LoRA bulunamadı.")
        for key in ("enabled", "default_selected", "auto_download"):
            if key in payload:
                if not isinstance(payload[key], bool):
                    raise ValueError(key + " true/false olmalı.")
                entry[key] = payload[key]
        if "weight" in payload:
            try:
                weight = float(payload["weight"])
            except (TypeError, ValueError, OverflowError):
                raise ValueError("LoRA ağırlığı sayı olmalı.")
            if not 0 <= weight <= 2:
                raise ValueError("LoRA ağırlığı 0 ile 2 arasında olmalı.")
            entry["weight"] = round(weight, 2)
        order = settings.get("default_download_order", [])
        if entry["auto_download"] and entry["group"] not in order:
            order.append(entry["group"])
        elif not entry["auto_download"] and entry["group"] in order:
            order.remove(entry["group"])
        settings["default_download_order"] = order
        _system_settings_write(settings)
        return system_settings()


def delete_lora(identifier):
    with _system_settings_lock:
        settings = _system_settings_read()
        entry = next((item for item in settings.get("loras", [])
                      if item.get("id") == identifier), None)
        if entry is None:
            raise ValueError("LoRA bulunamadı.")
        if entry.get("built_in"):
            raise ValueError("Uygulamayla gelen temel LoRA silinemez; devre dışı bırakılabilir.")
        group = entry["group"]
        if group in _queue or group == _active:
            raise RuntimeError("LoRA indirilirken kaydı silinemez.")
        settings["loras"] = [item for item in settings["loras"]
                              if item.get("id") != identifier]
        settings["default_download_order"] = [item for item in
                                                settings.get("default_download_order", [])
                                                if item != group]
        group_config = CATALOG.get(group)
        if group_config:
            for file in group_config["files"]:
                target = MODELS / file["dir"] / file["name"]
                target.unlink(missing_ok=True)
                _verification_file(target).unlink(missing_ok=True)
                target.with_suffix(".part").unlink(missing_ok=True)
            CATALOG.pop(group, None)
            _state.pop(group, None)
        _validate_catalog()
        _system_settings_write(settings)
        return system_settings()


def lora_status(model=None):
    settings = _system_settings_read()
    result = []
    for entry in settings.get("loras", []):
        if not workflow_enabled(entry.get("model")):
            continue
        if model and entry.get("model") != model:
            continue
        group = entry.get("group")
        file = next((item for item in CATALOG.get(group, {}).get("files", [])
                     if item.get("name") == entry.get("file")), None)
        target = MODELS / file["dir"] / file["name"] if file else None
        ready = bool(file and target and _model_verified(target, file))
        state, detail = _state.get(group, ("idle", ""))
        if ready:
            state, detail = "ready", "LoRA dosyası hazır."
        result.append({key: entry.get(key) for key in
                       ("id", "name", "model", "group", "file", "weight",
                        "enabled", "default_selected", "auto_download", "built_in", "link")}
                      | {"hazir": ready, "durum": state, "ayrinti": detail,
                         "boyut": file.get("size") if file else None})
    return result


def ensure_loras(model, selected=None, job=None):
    """Download selected compatible LoRAs, then return ComfyUI model names."""
    settings = _system_settings_read()
    entries = [item for item in settings.get("loras", [])
               if item.get("model") == model and item.get("enabled")]
    if selected is None:
        chosen = [{"id": item["id"], "weight": item["weight"]}
                  for item in entries if item.get("default_selected")]
    else:
        if not isinstance(selected, list) or len(selected) > 8:
            raise ValueError("En fazla 8 uyumlu LoRA seçilebilir.")
        chosen = selected
    resolved = []
    lookup = {item["id"]: item for item in entries}
    for request_item in chosen:
        if isinstance(request_item, str):
            identifier, weight = request_item, None
        elif isinstance(request_item, dict):
            identifier, weight = request_item.get("id"), request_item.get("weight")
        else:
            continue
        entry = lookup.get(identifier)
        if entry is None:
            raise ValueError("Seçili LoRA bu görsel modeliyle uyumlu değil.")
        try:
            weight = max(0.0, min(2.0, float(weight if weight is not None
                                             else entry["weight"])))
        except (TypeError, ValueError, OverflowError):
            raise ValueError("LoRA ağırlığı sayı olmalı.")
        file = next((item for item in CATALOG[entry["group"]]["files"]
                     if item["name"] == entry["file"]), None)
        if file is None:
            raise RuntimeError("LoRA dosyasının indirme kaydı bulunamadı.")
        target = MODELS / file["dir"] / file["name"]
        if not _model_verified(target, file):
            start(entry["group"])
            deadline = time.monotonic() + 3600
            while time.monotonic() < deadline:
                if job is not None and job.get("iptal"):
                    raise RuntimeError("İş durduruldu; LoRA indirmesi kurulum kuyruğunda kalabilir.")
                if _model_verified(target, file):
                    break
                state, detail = _state.get(entry["group"], ("idle", ""))
                if state == "error":
                    raise RuntimeError("LoRA indirilemedi: " + str(detail)[:300])
                if job is not None:
                    job["ilerleme"] = {"asama": "lora",
                                       "calisan": entry["name"] + " indiriliyor",
                                       "oran": 0.08}
                time.sleep(0.5)
            else:
                raise TimeoutError("LoRA indirmesi 60 dakika içinde tamamlanmadı.")
        resolved.append({"name": entry["file"], "weight": weight})
    return resolved


def prepare_data(data_root, accounts):
    """Use a mounted volume, or keep the app on container disk if absent."""
    global _data_lock, _flex, _ready, _system_settings_path, _storage_mode
    requested_mount = Path(os.environ.get("POD_VOLUME_MOUNT", "/workspace")).resolve()
    mount = validate_mount()
    target = Path(data_root).resolve()
    volume_attached = mount == requested_mount and mount != Path("/")
    _flex = None
    flex_prefix = os.environ.get("POD_FLEX_PREFIX")
    if volume_attached and flex_prefix:
        from flex_store import FlexStore

        if not flex_prefix.isascii() or not flex_prefix.replace("-", "").isalnum():
            raise RuntimeError("Flex dizini tek bir güvenli addan oluşmalı.")
        if not target.is_relative_to(ROOT) or target == ROOT:
            raise RuntimeError("Flex çalışma dizini geçici diskte olmalı.")
        snapshots = (mount / flex_prefix).resolve()
        if snapshots.parent != mount:
            raise RuntimeError("Flex dizini seçilen bağlama noktasında olmalı.")
        snapshots.mkdir(mode=0o700, parents=True, exist_ok=True)
        _system_settings_path = snapshots / "system-settings.json"
        _flex = FlexStore(target, snapshots)
    elif volume_attached:
        if not target.is_relative_to(mount) or target == mount:
            raise RuntimeError("Kullanıcı dizini kalıcı diskin altında olmalı.")
        _system_settings_path = target / "system-settings.json"
    else:
        if not target.is_relative_to(ROOT) or target == ROOT:
            raise RuntimeError("Volume yokken kullanıcı verileri container diskinde olmalı.")
        _system_settings_path = target / "system-settings.json"
        print("Kalıcı volume bağlı değil; kullanıcı verileri ve genel ayarlar container diskinde.",
              flush=True)
    _storage_mode = "persistent" if volume_attached else "ephemeral"
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    _data_lock = (target / ".webui.lock").open("a")
    fcntl.flock(_data_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if _flex is not None and not Path(accounts.dosya).exists():
        _flex.restore()
    if not accounts.kurulu_mu() and Path(accounts.dosya).exists():
        raise RuntimeError("Kullanıcı kaydı boş veya okunamıyor; yeni hesap oluşturulmadı.")
    # No account is a normal first-run state. The WebUI asks the owner to
    # create the first administrator instead of relying on a shared password.
    settings = _system_settings_read()
    _model_instances_apply(settings)
    _register_lora_groups(settings)
    settings["default_download_order"] = [
        group for group in settings.get("default_download_order", [])
        if group in CATALOG and CATALOG[group].get("enabled", True)]
    _system_settings_write(settings)
    _apply_saved_links(settings)
    _ready = True
    if (settings.get("auto_download")
            and os.environ.get("WEBUI_AUTO_DOWNLOAD", "1") != "0"):
        order = list(settings.get("default_download_order", []))
        order.extend(entry["group"] for entry in settings.get("loras", [])
                     if entry.get("auto_download") and entry["group"] not in order)
        for group in order:
            try:
                start(group)
            except (ValueError, RuntimeError):
                continue


def validate_mount():
    mount = Path(os.environ.get("POD_VOLUME_MOUNT", "/workspace")).resolve()
    mounts = Path("/proc/self/mountinfo").read_text().splitlines()
    if mount != Path("/") and any(line.split()[4] == str(mount) for line in mounts):
        return mount
    if os.environ.get("POD_ALLOW_EPHEMERAL_DATA", "1") == "1":
        return Path("/")
    raise RuntimeError("Kalıcı volume bağlı değil.")


def checkpoint():
    if _flex is not None:
        _flex.save()


def refresh_data_lock():
    """Reacquire the container-data lock after a full data-tree replacement."""
    global _data_lock
    lock_path = Path(os.environ.get("WEBUI_DATA_ROOT", "/opt/runtime/user-data")) / ".webui.lock"
    replacement = lock_path.open("a")
    fcntl.flock(replacement, fcntl.LOCK_EX | fcntl.LOCK_NB)
    previous = _data_lock
    _data_lock = replacement
    if previous is not None:
        previous.close()


def storage_status():
    return {"storage_mode": _storage_mode,
            "storage_persistent": _storage_mode == "persistent"}


def status():
    with _lock:
        groups = []
        for group, item in CATALOG.items():
            if not item.get("enabled", True):
                continue
            state, detail = _state.get(group, ("idle", ""))
            if state == "idle" and _group_files_complete(group):
                state, detail = "installed", "Model dosyaları diskte hazır."
            if (state == "ready" and item["engine"] == "comfy"
                    and (_comfy_process is None or _comfy_process.poll() is not None)):
                state, detail = "installed", "Model dosyaları hazır; ComfyUI yeniden başlatılabilir."
                _state[group] = (state, detail)
            groups.append({"id": group, "ad": item["label"], "durum": state,
                           "ayrinti": detail, "engine": item["engine"],
                           "calisma_hazir": _runtime_available(group),
                           "dosyalar": [{"ad": file["name"],
                                         "toplam": file.get("size"),
                                         "opsiyonel": bool(file.get("optional")),
                                         "gated": bool(file.get("gated"))}
                                        for file in item["files"]],
                           "bagimliliklar": list(item.get("depends_on", []))})
        return {"gruplar": groups, "aktif": _active, "sirada": list(_queue),
                "ilerleme": dict(_active_progress), "hazir": _ready,
                **storage_status()}


def clear_rebuildable_storage():
    """Remove only regenerable Pod installs, model files, and caches.

    User data and the mounted Flex volume are deliberately outside the deletion
    allowlist. This is a clean reinstall action, not an account reset.
    """
    global _comfy_process
    with _lock:
        if _active is not None or _queue:
            raise RuntimeError("Önce devam eden model indirmelerini tamamlayın veya iptal edin.")
        process = _comfy_process

    root = ROOT.resolve()
    protected = set()
    data_root = Path(os.environ.get("WEBUI_DATA_ROOT", str(root / "user-data"))).resolve()
    volume = Path(os.environ.get("POD_VOLUME_MOUNT", "/workspace")).resolve()
    protected.update((data_root, volume))
    model_root = Path(os.environ.get("POD_MODEL_STORAGE", str(root / "models"))).resolve()

    def overlaps(path, protected_path):
        return (path == protected_path or path.is_relative_to(protected_path)
                or protected_path.is_relative_to(path))

    external_model_root = (model_root if model_root not in (root, Path("/"))
                           and not any(overlaps(model_root, item)
                                       for item in (root, *protected))
                           else None)

    candidates = [COMFY, root / "comfy-venv", root / "bin", root / "llama",
                  root / "hf-cache", root / "cache", root / "uv-cache",
                  root / "pip-cache"]
    if external_model_root is not None:
        candidates.append(external_model_root)
    for variable in ("HF_HOME", "XDG_CACHE_HOME", "UV_CACHE_DIR"):
        value = os.environ.get(variable)
        if value:
            candidates.append(Path(value).expanduser())
    candidates.extend(root / name for name in LLAMA_RELEASE)
    candidates.extend(root / name for name in
                      ("setup.log", "comfyui.log", "llama.log", "webui.log"))

    safe = []
    seen = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            under_runtime = resolved != root and resolved.is_relative_to(root)
            is_model_storage = (external_model_root is not None
                                and resolved == external_model_root)
            if (not (under_runtime or is_model_storage)
                    or any(overlaps(resolved, item) for item in protected)
                    or resolved in seen):
                continue
            seen.add(resolved)
            safe.append(candidate)
        except (OSError, RuntimeError):
            continue

    _stop_process(process)
    _comfy_process = None
    try:
        import llm
        llm.durdur()
    except Exception:
        pass
    try:
        import prompt_translation
        prompt_translation.modeli_bosalt()
    except Exception:
        pass

    removed = 0
    freed = 0
    for candidate in safe:
        try:
            if candidate.is_symlink():
                freed += candidate.lstat().st_size
                candidate.unlink()
                removed += 1
                continue
            if candidate.is_file():
                freed += candidate.stat().st_size
                candidate.unlink()
                removed += 1
                continue
            if not candidate.is_dir():
                continue
            for current, directories, files in os.walk(candidate, followlinks=False):
                for name in files:
                    try:
                        child = Path(current) / name
                        stat = child.lstat() if child.is_symlink() else child.stat()
                        freed += stat.st_size
                    except OSError:
                        pass
                for name in directories:
                    child = Path(current) / name
                    if child.is_symlink():
                        try:
                            freed += child.lstat().st_size
                        except OSError:
                            pass
            shutil.rmtree(candidate)
            removed += 1
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise RuntimeError("Yeniden kurulabilir dosyalar temizlenemedi (%s)." %
                               type(exc).__name__) from None

    with _lock:
        _state.clear()
        _active_progress.clear()
        _last_console_progress.clear()
    return {"removed": removed, "freed_bytes": freed,
            "preserved_user_data": True, "preserved_flex_settings": True}


def _group_files_complete(group):
    item = CATALOG[group]
    return all(file.get("optional") or
               _model_present(MODELS / file["dir"] / file["name"])
               for file in item["files"])


def _group_files_verified(group):
    item = CATALOG[group]
    return all(file.get("optional") and
               not _model_present(MODELS / file["dir"] / file["name"])
               or _model_verified(MODELS / file["dir"] / file["name"], file)
               for file in item["files"])


def _engine_ready(group):
    item = CATALOG[group]
    if not _group_files_verified(group):
        return False
    if item["engine"] == "comfy":
        return (_comfy_process is not None and _comfy_process.poll() is None
                and (ROOT / "comfy-venv/bin/python").is_file())
    if item["engine"] == "llama":
        if not _runtime_available(group):
            return False
        return bool(re.search(r"(?m)^\s*CUDA0:", _llama_cuda_devices()))
    return (COMFY / ".pod-ready").is_file()


def _runtime_available(group):
    item = CATALOG[group]
    if item["engine"] == "comfy":
        return (_comfy_process is not None and _comfy_process.poll() is None
                and (ROOT / "comfy-venv/bin/python").is_file())
    if item["engine"] == "llama":
        marker = ROOT / "llama/.pod-ready"
        try:
            return marker.read_text(encoding="ascii").strip() == LLAMA_TAG and Path(
                os.environ["LLAMA_SERVER_PATH"]).is_file()
        except (OSError, KeyError):
            return False
    return (COMFY / ".pod-ready").is_file()


def _transitive_dependents(group):
    result = {group}
    changed = True
    while changed:
        changed = False
        for candidate, item in CATALOG.items():
            if candidate not in result and any(dep in result for dep in item.get("depends_on", [])):
                result.add(candidate)
                changed = True
    return result


def is_ready():
    return _ready


def workflow(name):
    return dict(WORKFLOWS.get(name) or {})


def workflow_enabled(name):
    group = WORKFLOWS.get(name, {}).get("group")
    return bool(group in CATALOG and CATALOG[group].get("enabled", True))


def workflow_ready(name):
    group = WORKFLOWS.get(name, {}).get("group")
    return workflow_enabled(name) and _engine_ready(group)


def _bagimlilik_sirasi(group, gorulen=None):
    gorulen = gorulen or set()
    if group in gorulen:
        return []
    gorulen.add(group)
    result = []
    for dependency in CATALOG[group].get("depends_on", []):
        result.extend(_bagimlilik_sirasi(dependency, gorulen))
    result.append(group)
    return result


def start(group):
    global _thread
    if not isinstance(group, str) or group not in CATALOG:
        raise ValueError("Bilinmeyen model grubu.")
    if not CATALOG[group].get("enabled", True):
        raise ValueError("Bu model grubu kapalı.")
    if not _ready:
        raise RuntimeError("Pod veri hazırlığı tamamlanmadı; biraz sonra tekrar deneyin.")
    with _lock:
        for item in _bagimlilik_sirasi(group):
            if item == _active:
                if _active_cancel is not None and _active_cancel.is_set():
                    raise RuntimeError("Duraklatma veya iptal tamamlanınca devam edebilirsiniz.")
                _state[item] = ("ready", "")
                continue
            _pause_requested.discard(item)
            if item in _queue or _engine_ready(item):
                _state[item] = ("ready", "")
                continue
            _queue.append(item)
            _state[item] = ("queued", "")
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_run, name="pod-setup", daemon=True)
            _thread.start()


def move(group, direction):
    if direction not in ("yukari", "asagi"):
        raise ValueError("Bilinmeyen sıra yönü.")
    with _lock:
        if group not in _queue:
            raise RuntimeError("Yalnızca bekleyen modellerin sırası değiştirilebilir.")
        index = _queue.index(group)
        destination = index + (-1 if direction == "yukari" else 1)
        if 0 <= destination < len(_queue):
            other = _queue[destination]
            if (group in _transitive_dependents(other)
                    or other in _transitive_dependents(group)):
                raise RuntimeError("Bağımlı modellerin sırası birlikte korunmalı.")
            _queue[index], _queue[destination] = _queue[destination], _queue[index]


def _stop_process(process):
    if process is None or process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    except OSError:
        pass


def cancel(group):
    global _active_process
    if not isinstance(group, str) or group not in CATALOG:
        raise ValueError("Bilinmeyen model grubu.")
    process = None
    with _lock:
        if group != _active and group not in _queue and _state.get(group, ("idle",))[0] != "paused":
            raise RuntimeError("Bu model şu anda indirilmedi veya hazırlanmadı.")
        _pause_requested.discard(group)
        dependents = _transitive_dependents(group)
        queued_dependents = dependents.intersection(_queue)
        _queue[:] = [queued for queued in _queue if queued not in queued_dependents]
        for queued in queued_dependents:
            _state[queued] = ("cancelled", "Bağımlı model iptal edildi.")
        if group == _active:
            if _active_cancel is None:
                raise RuntimeError("İptal işareti bulunamadı.")
            _active_cancel.set()
            _active_progress["stage"] = "İptal ediliyor"
            process = _active_process
        else:
            _state[group] = ("cancelled", "Sıradan çıkarıldı.")
            return
    _stop_process(process)


def pause(group):
    global _active_process
    if not isinstance(group, str) or group not in CATALOG:
        raise ValueError("Bilinmeyen model grubu.")
    process = None
    with _lock:
        if group == _active:
            if _active_cancel is None:
                raise RuntimeError("Duraklatma işareti bulunamadı.")
            _pause_requested.add(group)
            _active_cancel.set()
            _active_progress["stage"] = "Duraklatılıyor"
            process = _active_process
        elif group in _queue:
            _queue.remove(group)
            _state[group] = ("paused", "Sıradaki iş duraklatıldı.")
            return
        elif _state.get(group, ("idle",))[0] == "paused":
            return
        else:
            raise RuntimeError("Yalnızca çalışan veya sıradaki indirme duraklatılabilir.")
    _stop_process(process)


def remove(group):
    if not isinstance(group, str) or group not in CATALOG:
        raise ValueError("Bilinmeyen model grubu.")
    with _lock:
        if _active is not None or _queue:
            raise RuntimeError("Devam eden model hazırlıkları tamamlanmalı.")
        for other in _transitive_dependents(group) - {group}:
            if (_state.get(other, ("idle",))[0] in ("ready", "installed")
                    or _group_files_complete(other)):
                raise RuntimeError("Önce bu modeli kullanan " + CATALOG[other]["label"] + " grubunu kaldırın.")
        item = CATALOG[group]
        if (_state.get(group, ("idle",))[0] not in ("ready", "installed", "error", "cancelled")
                and not any((MODELS / file["dir"] / file["name"]).exists()
                            for file in item["files"])):
            raise RuntimeError("Bu model kurulu görünmüyor.")
        if item["engine"] == "llama":
            import llm

            llm.durdur()
        elif item["engine"] == "translation":
            import prompt_translation

            prompt_translation.modeli_bosalt()
        shared = {(file["dir"], file["name"])
                  for other, config in CATALOG.items() if other != group
                  for file in config["files"]}
        for file in item["files"]:
            if (file["dir"], file["name"]) not in shared:
                target = MODELS / file["dir"] / file["name"]
                target.unlink(missing_ok=True)
                _verification_file(target).unlink(missing_ok=True)
                target.with_suffix(".part").unlink(missing_ok=True)
                if (file.get("source") or "hf") == "hf":
                    (MODELS / ".incoming" / file["path"]).unlink(missing_ok=True)
        _state[group] = ("idle", "")


def _progress(group, stage, *, name=None, downloaded=None, total=None, rate=None):
    now = time.monotonic()
    log_line = False
    log_rate = None
    with _lock:
        _state[group] = ("running", stage)
        _active_progress.update({"stage": stage, "file": name,
                                 "downloaded": downloaded, "total": total,
                                 "rate_mib_s": rate})
        progress_key = (group, name or "")
        previous = _last_console_progress.get(progress_key)
        if name and stage == "İndiriliyor":
            if not isinstance(previous, dict) or previous.get("stage") != stage:
                log_line = True
                previous = {"stage": stage, "logged_at": now,
                            "sample_at": now if rate is not None else None,
                            "sample_size": downloaded if rate is not None else None}
            else:
                if rate is None or not isinstance(downloaded, (int, float)):
                    previous["sample_at"] = None
                    previous["sample_size"] = None
                elif previous.get("sample_at") is None:
                    previous["sample_at"] = now
                    previous["sample_size"] = downloaded
                if now - previous["logged_at"] >= 5.0:
                    log_line = True
                    sample_at = previous.get("sample_at")
                    sample_size = previous.get("sample_size")
                    if (rate is not None and isinstance(downloaded, (int, float))
                            and isinstance(sample_at, (int, float))
                            and isinstance(sample_size, (int, float))
                            and now > sample_at and downloaded >= sample_size):
                        log_rate = ((downloaded - sample_size) / (now - sample_at)
                                    / (1024 * 1024))
                    previous["logged_at"] = now
                    previous["sample_at"] = now if rate is not None else None
                    previous["sample_size"] = downloaded if rate is not None else None
            _last_console_progress[progress_key] = previous
        else:
            log_line = (not isinstance(previous, dict)
                        or previous.get("stage") != stage)
            if log_line:
                _last_console_progress[progress_key] = {
                    "stage": stage, "logged_at": now,
                    "sample_at": None, "sample_size": None}
    if log_line:
        parts = [f"[pod-setup] {group}: {stage}"]
        if name:
            parts.append(str(name))
        if downloaded is not None:
            if isinstance(total, (int, float)) and total > 0:
                percent = max(0.0, min(100.0, 100.0 * downloaded / total))
                parts.append("%.1f MiB / %.1f MiB (%.0f%%)" %
                             (downloaded / (1024 * 1024),
                              total / (1024 * 1024), percent))
            else:
                parts.append("%.1f MiB" % (downloaded / (1024 * 1024)))
        if isinstance(log_rate, (int, float)):
            parts.append("%.1f MiB/sn" % max(0.0, log_rate))
            if (isinstance(downloaded, (int, float))
                    and isinstance(total, (int, float))
                    and total > downloaded and log_rate > 0):
                remaining = (total - downloaded) / (log_rate * 1024 * 1024)
                if remaining < 60:
                    eta = "kalan %.0f sn" % remaining
                elif remaining < 3600:
                    minutes, seconds = divmod(int(remaining), 60)
                    eta = "kalan %d dk %02d sn" % (minutes, seconds)
                else:
                    hours, remainder = divmod(int(remaining), 3600)
                    eta = "kalan %d sa %d dk" % (hours, remainder // 60)
                parts.append(eta)
        print(" · ".join(parts), flush=True)


def _install_custom_nodes(group, cancel):
    """Install pinned optional ComfyUI nodes only when their model group is requested."""
    global _comfy_process
    item = CATALOG[group]
    for node in item.get("custom_nodes", []) or []:
        name = node["name"]
        revision = node["revision"]
        destination = COMFY / "custom_nodes" / name
        marker = destination / ".pod-revision"
        try:
            installed_revision = marker.read_text(encoding="ascii").strip()
        except OSError:
            installed_revision = ""
        if installed_revision == revision:
            continue
        process = _comfy_process
        was_running = process is not None and process.poll() is None
        if process is not None and process.poll() is None:
            _stop_process(process)
            _comfy_process = None
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not (destination / ".git").is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                _command(["git", "init", str(destination)],
                         name + " eklentisi hazırlanıyor", cancel=cancel)
                _command(["git", "-C", str(destination), "remote", "add", "origin", node["repo"]],
                         name + " kaynak adresi", cancel=cancel)
            _command(["git", "-C", str(destination), "fetch", "--depth", "1",
                      "origin", revision], name + " eklentisi indiriliyor", cancel=cancel)
            _command(["git", "-C", str(destination), "checkout", "--detach", "FETCH_HEAD"],
                     name + " eklentisi sürümü", cancel=cancel)
            requirements = destination / "requirements.txt"
            if requirements.is_file():
                # Reuse ComfyUI's installed CUDA torch stack; installing an add-on's
                # unpinned torch/torchvision requirements can replace the GPU build.
                safe_requirements = destination / ".pod-requirements.txt"
                lines = requirements.read_text(encoding="utf-8").splitlines()
                safe_requirements.write_text("\n".join(
                    line for line in lines
                    if line.strip() and not re.match(
                        r"^(torch|torchvision|numpy)(?:[<=>!~;\[]|$)", line.strip(), re.I)
                ) + "\n", encoding="utf-8")
                uv = os.environ.get("UV_BIN", "uv")
                _command([uv, "pip", "install", "--python", str(ROOT / "comfy-venv/bin/python"),
                          "-r", str(safe_requirements)],
                         name + " Python paketleri", cancel=cancel, timeout=1800)
            marker.write_text(revision, encoding="ascii")
        except Exception:
            if was_running:
                try:
                    _launch_comfy()
                except Exception:
                    pass
            raise


def _run():
    global _active, _thread, _active_cancel, _active_process
    while True:
        with _lock:
            if not _queue:
                _thread = None
                return
            group = _active = _queue.pop(0)
            _active_cancel = threading.Event()
            _active_progress.clear()
            cancel_event = _active_cancel
        try:
            item = CATALOG[group]
            missing = [dep for dep in item.get("depends_on", [])
                       if not _engine_ready(dep)]
            if missing:
                raise DownloadCancelled("Bağımlı model hazır değil; bu iş güvenli biçimde durduruldu.")
            if item["engine"] == "comfy":
                _progress(group, "ComfyUI bağımlılıkları hazırlanıyor")
                _install_comfy(cancel_event)
                _install_custom_nodes(group, cancel_event)
                if cancel_event.is_set():
                    raise DownloadCancelled("ComfyUI hazırlığı iptal edildi.")
                _progress(group, "ComfyUI başlatılıyor")
                _launch_comfy()
            elif item["engine"] == "translation":
                _progress(group, "Çeviri çalışma ortamı hazırlanıyor")
                _install_comfy(cancel_event)
            elif item["engine"] == "llama":
                _progress(group, "llama-server CUDA sürücüsü kontrol ediliyor")
                _install_llama(cancel_event, group=group)
            else:
                raise RuntimeError("Model grubu için bilinmeyen çalışma ortamı.")
            optional_failures = []
            for file in item["files"]:
                if cancel_event.is_set():
                    raise DownloadCancelled("İndirme iptal edildi.")
                _progress(group, "İndiriliyor", name=file["name"],
                          downloaded=0, total=file.get("size"))
                try:
                    _model(file, cancel_event, group)
                except DownloadCancelled:
                    raise
                except Exception as exc:
                    if not file.get("optional"):
                        raise
                    optional_failures.append(file["name"])
                    with (ROOT / "setup.log").open("a", encoding="utf-8") as log:
                        log.write("[optional] %s atlandı (%s)\n" %
                                  (file["name"], type(exc).__name__))
                else:
                    target = MODELS / file["dir"] / file["name"]
                    if target.is_file():
                        _progress(group, "Dosya hazır", name=file["name"],
                                  downloaded=target.stat().st_size,
                                  total=file.get("size"))
            if item["engine"] == "comfy":
                _progress(group, "ComfyUI bağlantısı kontrol ediliyor")
                _wait_comfy(cancel_event)
            result = ("ready", "Ana model hazır; MTP isteğe bağlı olarak yüklenemedi."
                      if optional_failures else "")
        except DownloadCancelled as exc:
            with _lock:
                paused = group in _pause_requested
                _pause_requested.discard(group)
            result = ("paused", "Duraklatıldı; devam ettiğinde kısmi indirmeden sürer.") \
                if paused else ("cancelled", str(exc))
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            detail = str(exc) if type(exc) is RuntimeError else type(exc).__name__
            result = ("error", detail)
        except Exception as exc:
            result = ("error", type(exc).__name__)
        with _lock:
            _state[group] = result
            _active = None
            _active_cancel = None
            _active_process = None
            _active_progress.clear()
            for key in [key for key in _last_console_progress if key[0] == group]:
                _last_console_progress.pop(key, None)
        print(f"[pod-setup] {group}: {result[0]}", flush=True)


def _command(args, label, *, cwd=None, cancel=None, timeout=1800, progress=None):
    global _active_process
    ROOT.mkdir(parents=True, exist_ok=True)
    with (ROOT / "setup.log").open("ab") as log:
        process = subprocess.Popen(args, cwd=cwd, stdout=log,
                                   stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                   start_new_session=True)
        with _lock:
            _active_process = process
        start = time.monotonic()
        progress_time = start
        progress_size = 0
        progress_logged = 0.0
        try:
            while process.poll() is None:
                if cancel is not None and cancel.is_set():
                    _stop_process(process)
                    raise DownloadCancelled("Hazırlık iptal edildi.")
                now = time.monotonic()
                if now - start > timeout:
                    _stop_process(process)
                    raise RuntimeError(f"{label} zaman aşımına uğradı.")
                if progress:
                    path = Path(progress["path"])
                    size = path.stat().st_size if path.is_file() else 0
                    rate = max(0, size - progress_size) / max(now - progress_time, .001) / (1024 * 1024)
                    if now - progress_logged >= 0.8:
                        _progress(progress["group"], progress.get("stage", "İndiriliyor"),
                                  name=progress["name"], downloaded=size,
                                  total=progress.get("total"), rate=round(rate, 2))
                        progress_logged = now
                    progress_time, progress_size = now, size
                time.sleep(0.2)
            if cancel is not None and cancel.is_set():
                raise DownloadCancelled("Hazırlık iptal edildi.")
            if process.returncode:
                raise RuntimeError(f"{label} başarısız oldu (çıkış kodu {process.returncode}).")
            with (ROOT / "setup.log").open("a", encoding="utf-8") as log:
                log.write(f"[timing] {label}: {time.monotonic() - start:.1f} sn\n")
        finally:
            with _lock:
                if _active_process is process:
                    _active_process = None


def _install_comfy(cancel):
    marker = COMFY / ".pod-ready"
    if marker.exists() and marker.read_text() == COMFY_REF:
        return
    COMFY.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()

    def stage_done(label, before):
        elapsed = time.monotonic() - before
        with (ROOT / "setup.log").open("a", encoding="utf-8") as log:
            log.write(f"[timing] {label}: {elapsed:.1f} sn\n")

    before = time.monotonic()
    if not (COMFY / ".git").is_dir():
        _command(["git", "init", str(COMFY)], "ComfyUI klasörü", cancel=cancel)
    _command(["git", "fetch", "--depth", "1", "https://github.com/Comfy-Org/ComfyUI.git",
              COMFY_REF], "ComfyUI indirmesi", cwd=COMFY, cancel=cancel)
    _command(["git", "checkout", "--detach", "FETCH_HEAD"], "ComfyUI sürümü",
             cwd=COMFY, cancel=cancel)
    stage_done("ComfyUI kodu", before)
    env = ROOT / "comfy-venv"
    if env.exists():
        shutil.rmtree(env)
    uv = os.environ.get("UV_BIN", "uv")
    _command([uv, "venv", "--python", sys.executable, str(env)],
             "ComfyUI Python ortamı", cancel=cancel)
    pip = [uv, "pip", "install", "--python", str(env / "bin/python")]
    before = time.monotonic()
    try:
        driver = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
            text=True, timeout=10).splitlines()[0].strip()
        cuda_wheel = "cu130" if int(driver.split(".")[0]) >= 580 else "cu128"
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        cuda_wheel = "cu128"
    with (ROOT / "setup.log").open("a", encoding="utf-8") as log:
        log.write(f"[comfy] PyTorch sürümü: {cuda_wheel}\n")
    _command(pip + ["--index-url", f"https://download.pytorch.org/whl/{cuda_wheel}",
                    "torch==2.11.0", "torchvision==0.26.0"],
             "GPU bağımlılıkları", cancel=cancel)
    stage_done("CUDA PyTorch", before)
    before = time.monotonic()
    _command(pip + ["-r", str(COMFY / "requirements.txt"), "transformers>=4.50.3,<5",
                    "huggingface-hub<1"], "ComfyUI bağımlılıkları", cancel=cancel)
    stage_done("ComfyUI Python paketleri", before)
    marker.write_text(COMFY_REF)
    stage_done("Toplam ComfyUI hazırlığı", start)


def _gpu_memory():
    try:
        info = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            text=True, timeout=10)
        return int(info.splitlines()[0])
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        raise RuntimeError("NVIDIA GPU belleği tespit edilemedi.") from None


def _launch_comfy():
    global _comfy_process
    if _comfy_process is not None and _comfy_process.poll() is None:
        return
    vram = _gpu_memory()
    args = ["--reserve-vram", "1" if vram < 24576 else "1.5"]
    if vram < 12288:
        args += ["--lowvram"]
    if os.environ.get("POD_COMFY_FP16_ACCUMULATION") == "1":
        args += ["--fast", "fp16_accumulation"]
    print(f"[pod-setup] ComfyUI: {vram} MiB VRAM, {' '.join(args)}", flush=True)
    with (ROOT / "comfyui.log").open("ab") as log:
        _comfy_process = subprocess.Popen(
            [str(ROOT / "comfy-venv/bin/python"), str(COMFY / "main.py"),
             "--listen", "127.0.0.1", "--port", "8188", *args],
            cwd=COMFY, stdout=log, stderr=subprocess.STDOUT)


def _wait_comfy(cancel):
    for _ in range(180):
        if cancel.is_set():
            raise DownloadCancelled("ComfyUI hazırlığı iptal edildi.")
        if _comfy_process.poll() is not None:
            raise RuntimeError("ComfyUI başlatılamadı.")
        try:
            with urllib.request.urlopen("http://127.0.0.1:8188/system_stats", timeout=2):
                return
        except OSError:
            time.sleep(1)
    _comfy_process.terminate()
    raise RuntimeError("ComfyUI hazır olma süresini aştı.")


def _llama_cuda_devices(target=None):
    target = Path(target or os.environ["LLAMA_SERVER_PATH"])
    try:
        result = subprocess.run([str(target), "--list-devices"],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, timeout=30, check=False)
        output = (result.stdout or "").strip()
        if result.returncode:
            output += ("\n" if output else "") + "[çıkış kodu: %d]" % result.returncode
        return output
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", "replace")
        return (str(output).strip() + "\n" if output else "") + "[GPU listeleme 30 saniyede tamamlanmadı]"
    except OSError as exc:
        return "[GPU listeleme komutu başlatılamadı: %s: %s]" % (
            type(exc).__name__, str(exc)[:300])


def _record_llama_cuda_scan(output):
    """Record only the short device probe so GPU setup failures are diagnosable."""
    with (ROOT / "setup.log").open("a", encoding="utf-8") as log:
        log.write("[llama] --list-devices çıktısı:\n")
        for line in (output or "[boş çıktı]")[:4000].splitlines():
            log.write("[llama] " + line + "\n")


def _download_llama_package(archive, name, expected, cancel, group="llama"):
    """Resume first; retry from scratch and then use GitHub's asset API."""
    def valid():
        if not archive.is_file() or not archive.stat().st_size:
            return False
        size = archive.stat().st_size
        _progress(group, "llama-server paketi doğrulanıyor", name=name,
                  downloaded=size, total=LLAMA_RELEASE_ASSETS[name])
        with archive.open('rb') as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest() == expected

    if valid():
        return
    direct = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_TAG}/{name}"
    asset = LLAMA_RELEASE_ASSETS[name]
    api = f"https://api.github.com/repos/ggml-org/llama.cpp/releases/assets/{asset}"
    last_error = None
    for source, resume in ((direct, True), (direct, False), (api, False)):
        if not resume:
            archive.unlink(missing_ok=True)
        cmd = ["curl", "--fail", "--location", "--retry", "3", "--retry-all-errors",
               "--connect-timeout", "30", "--output", str(archive)]
        if resume:
            cmd.extend(["--continue-at", "-"])
        if source == api:
            cmd.extend(["--header", "Accept: application/octet-stream",
                        "--header", "User-Agent: RunPod-WebUI"])
        cmd.append(source)
        try:
            _command(cmd, "llama-server paketi", cancel=cancel,
                     progress={"group": group, "name": name, "path": str(archive),
                               "total": LLAMA_RELEASE_ASSETS[name],
                               "stage": "llama-server paketi indiriliyor"})
        except DownloadCancelled:
            raise
        except (OSError, RuntimeError) as exc:
            last_error = str(exc)
        else:
            if valid():
                return
            last_error = "İndirilen paketin bütünlüğü doğrulanamadı."
        with (ROOT / "setup.log").open("a", encoding="utf-8") as log:
            log.write("[llama] Paket indirmesi tekrar denenecek: %s\n" % last_error)
    raise RuntimeError("llama-server paketi indirilemedi. %s Kurulum logunu kontrol edin." %
                       (last_error or "Bağlantı hatası."))


def _install_llama(cancel, group="llama"):
    llama = ROOT / "llama"
    marker = llama / ".pod-ready"
    target = Path(os.environ["LLAMA_SERVER_PATH"])
    if marker.exists() and marker.read_text() == LLAMA_TAG:
        devices = _llama_cuda_devices(target)
        if re.search(r"(?m)^\s*CUDA0:", devices):
            return
        marker.unlink(missing_ok=True)
        shutil.rmtree(llama, ignore_errors=True)
        target.unlink(missing_ok=True)
    llama.mkdir(parents=True, exist_ok=True)
    for name, expected in LLAMA_RELEASE.items():
        archive = ROOT / name
        _download_llama_package(archive, name, expected, cancel, group=group)
        with tarfile.open(archive) as package:
            package.extractall(llama, filter="data")
        archive.unlink()
    binary = next(llama.rglob("llama-server"), None)
    if binary is None:
        raise RuntimeError("llama-server pakette bulunamadı.")
    # The upstream runtime archive has its own top-level directory.
    runtime = next(llama.rglob("libcudart.so.12"), None)
    if runtime is None:
        raise RuntimeError("llama-server CUDA çalışma kitaplığı pakette bulunamadı.")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    target.write_text(
        f'#!/bin/sh\nexport LD_LIBRARY_PATH="{binary.parent}:{runtime.parent}${{LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}}"\n'
        f'exec "{binary}" "$@"\n',
        encoding="utf-8",
    )
    target.chmod(0o755)
    _command([str(target), "--version"], "llama-server doğrulaması", cancel=cancel)
    devices = _llama_cuda_devices(target)
    _record_llama_cuda_scan(devices)
    if not re.search(r"(?m)^\s*CUDA0:", devices):
        detail = (devices or "llama-server cihaz listesi döndürmedi.").strip()[-1200:]
        raise RuntimeError("llama-server CUDA0 aygıtını göremedi; CPU ile yavaş çalışması "
                           "engellendi. Tanı: " + detail)
    with (ROOT / "setup.log").open("a", encoding="utf-8") as log:
        log.write("[llama] GPU aygıtı doğrulandı: " +
                  next(line.strip() for line in devices.splitlines()
                       if line.lstrip().startswith("CUDA0:")) + "\n")
    marker.write_text(LLAMA_TAG)


def _tree_size(directory):
    total = 0
    if not directory.exists():
        return 0
    for root, _, files in os.walk(directory):
        for filename in files:
            try:
                total += os.path.getsize(os.path.join(root, filename))
            except OSError:
                pass
    return total


def _process_io_bytes(pid):
    """Count actual process I/O, including Xet network reads, when available."""
    try:
        values = {}
        for line in Path('/proc/%d/io' % pid).read_text().splitlines():
            key, value = line.split(':', 1)
            if key in ('rchar', 'wchar'):
                values[key] = int(value.strip())
        return values['rchar'] + values['wchar']
    except (OSError, KeyError, ValueError):
        return None


def _model(file, cancel, group):
    global _active_process
    directory = MODELS / file["dir"]
    target = directory / file["name"]
    if target.is_file() and target.stat().st_size:
        _progress(group, "Dosya doğrulanıyor", name=file["name"],
                  downloaded=target.stat().st_size, total=file.get("size"))
        if _valid_model(target, file):
            _write_verification(target, file)
            return
        target.unlink(missing_ok=True)
        _verification_file(target).unlink(missing_ok=True)
    source_type = file.get("source") or "hf"
    if source_type == "civitai":
        _civitai_model(target, file, cancel, group)
        return
    if source_type == "url":
        _url_model(target, file, cancel, group)
        return
    xet_dir = directory / ".incoming"
    http_dir = directory / ".incoming-http"
    helper = """import json, os, sys
from huggingface_hub import hf_hub_download
from huggingface_hub.errors import HfHubHTTPError
c=json.loads(sys.stdin.read())
# Xet writes blocks out of order; the partial file length is not transfer progress.
# Capture the byte callbacks emitted by hf_xet instead.
try:
    import hf_xet, threading, time
    original_download = hf_xet.download_files
    callback_lock = threading.Lock()
    def measured_download(*args, **kwargs):
        callbacks = kwargs.get('progress_updater') or []
        transferred = [0.0]
        last_write = [0.0]
        def wrap(callback):
            def update(amount):
                callback(amount)
                with callback_lock:
                    transferred[0] += max(0, amount)
                    now = time.monotonic()
                    if now - last_write[0] >= 0.3:
                        tmp = c['progress'] + '.tmp'
                        with open(tmp, 'w') as f:
                            f.write(str(int(transferred[0])))
                        os.replace(tmp, c['progress'])
                        last_write[0] = now
            return update
        kwargs['progress_updater'] = [wrap(cb) for cb in callbacks]
        return original_download(*args, **kwargs)
    hf_xet.download_files = measured_download
except ImportError:
    pass
try:
    hf_hub_download(repo_id=c['repo'], revision=c.get('revision'),
                    filename=c['path'], local_dir=c['local_dir'],
                    token=os.environ.get('HF_TOKEN') or False)
except HfHubHTTPError as e:
    print('HTTP ' + str(getattr(getattr(e, 'response', None), 'status_code', 0)),
          file=sys.stderr, flush=True)
    sys.exit(42 if getattr(getattr(e, 'response', None), 'status_code', 0) in (401, 403) else 1)
"""
    root_log = ROOT / "setup.log"
    ROOT.mkdir(parents=True, exist_ok=True)
    try:
        stall_limit = max(5, min(1800, int(os.environ.get("POD_HF_STALL_SECONDS", "120"))))
    except ValueError:
        stall_limit = 120
    http_requested = os.environ.get("HF_HUB_DISABLE_XET", "").lower() in ("1", "true", "yes", "on")
    http_resume = (_hf_incomplete_file(http_dir, file["path"])
                   or (http_dir / file["path"]).is_file())
    modes = ["http"] if http_requested or http_resume else ["xet", "http"]
    for mode in modes:
        if cancel.is_set():
            raise DownloadCancelled("Model indirmesi iptal edildi; kaldığı yer korunup devam ettirilebilir.")
        incoming = http_dir if mode == "http" else xet_dir
        source = incoming / file["path"]
        source.parent.mkdir(parents=True, exist_ok=True)
        previous_time = time.monotonic()
        last_activity = previous_time
        partial = _hf_incomplete_file(incoming, file["path"])
        progress_file = partial if partial and partial.exists() else source
        previous_size = progress_file.stat().st_size if progress_file.exists() else 0
        previous_progress_path = partial
        process = None
        stalled = False
        xet_progress = incoming / ".xet-progress"
        xet_progress.unlink(missing_ok=True)
        env = os.environ.copy()
        if mode == "http":
            env["HF_HUB_DISABLE_XET"] = "1"
        try:
            with root_log.open("ab") as log:
                process = subprocess.Popen([sys.executable, "-c", helper], stdin=subprocess.PIPE,
                                           stdout=log, stderr=subprocess.STDOUT, cwd=ROOT,
                                           env=env, start_new_session=True)
                with _lock:
                    _active_process = process
                payload = json.dumps({"repo": file["repo"], "revision": file.get("revision"),
                                      "path": file["path"], "local_dir": str(incoming),
                                      "progress": str(xet_progress)}).encode("utf-8")
                process.stdin.write(payload)
                process.stdin.close()
                activity_io = _process_io_bytes(process.pid)
                while process.poll() is None:
                    if cancel.is_set():
                        _stop_process(process)
                        raise DownloadCancelled("Model indirmesi iptal edildi; kaldığı yer korunup devam ettirilebilir.")
                    time.sleep(1)
                    now = time.monotonic()
                    partial = _hf_incomplete_file(incoming, file["path"])
                    progress_file = partial if partial and partial.exists() else source
                    try:
                        current_size = progress_file.stat().st_size
                    except OSError:
                        current_size = previous_size
                    if mode == "xet":
                        try:
                            current_size = int(xet_progress.read_text())
                        except (OSError, ValueError):
                            # An Xet sparse file's length can jump ahead of actual progress.
                            current_size = 0
                    current_io = _process_io_bytes(process.pid)
                    if (current_size != previous_size or
                            (current_io is not None and activity_io is not None
                             and current_io - activity_io >= 65536)):
                        last_activity = now
                        activity_io = current_io
                    elif activity_io is None and current_io is not None:
                        activity_io = current_io
                    stable_partial = (mode == "xet" and xet_progress.exists()) or bool(
                        mode == "http" and partial and previous_progress_path == progress_file)
                    rate = (max(0, current_size - previous_size)
                            / max(now - previous_time, .001) / (1024 * 1024)
                            if stable_partial else None)
                    _progress(group, "İndiriliyor", name=file["name"],
                              downloaded=current_size, total=file.get("size"),
                              rate=round(rate, 2) if rate is not None else None)
                    previous_time, previous_size = now, current_size
                    previous_progress_path = partial
                    if now - last_activity >= stall_limit:
                        stalled = True
                        _stop_process(process)
                        break
        finally:
            if process is not None:
                with _lock:
                    if _active_process is process:
                        _active_process = None
        xet_progress.unlink(missing_ok=True)
        if cancel.is_set():
            raise DownloadCancelled("Model indirmesi iptal edildi; kaldığı yer korunup devam ettirilebilir.")
        if process.returncode == 42:
            raise RuntimeError("Model erişim izni gerekiyor. Pod’a HF_TOKEN ekleyin.")
        if not stalled and process.returncode == 0:
            _progress(group, "Dosya doğrulanıyor", name=file["name"],
                      downloaded=source.stat().st_size if source.is_file() else 0,
                      total=file.get("size"))
            if _valid_model(source, file):
                target.parent.mkdir(parents=True, exist_ok=True)
                source.replace(target)
                _write_verification(target, file)
                return
            source.unlink(missing_ok=True)
        if mode == "xet":
            with root_log.open("a", encoding="utf-8") as log:
                log.write("[pod-setup] Xet indirmesi ilerlemedi veya başarısız oldu; HTTP ile yeniden deneniyor.\n")
            # Xet's partial may contain out-of-order blocks; never resume it as HTTP.
            partial = _hf_incomplete_file(xet_dir, file["path"])
            if partial:
                partial.unlink(missing_ok=True)
            continue
        raise RuntimeError("Hugging Face indirmesi durdu veya doğrulanamadı; logları kontrol edin.")


def _civitai_model(target, file, cancel, group):
    global _active_process
    token = os.environ.get("CIVITAI_TOKEN")
    if not token or token.startswith("{{"):
        raise RuntimeError("Moody Anima için Pod’a CIVITAI_TOKEN ekleyin.")
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".part")
    helper = """import json, os, sys, urllib.error, urllib.request
c=json.loads(sys.stdin.read()); token=os.environ.get('CIVITAI_TOKEN')
if not token or token.startswith('{{'):
    print('missing-token', flush=True); sys.exit(42)
offset=os.path.getsize(c['part']) if os.path.exists(c['part']) else 0
headers={'User-Agent':'Mozilla/5.0','Accept-Encoding':'identity'}
if offset: headers['Range']='bytes=%d-' % offset
request=urllib.request.Request(c['url'], headers=headers)
request.add_unredirected_header('Authorization', 'Bearer '+token)
try:
    with urllib.request.urlopen(request, timeout=30) as response:
        append=bool(offset and response.status==206)
        with open(c['part'], 'ab' if append else 'wb') as output:
            while True:
                chunk=response.read(1024*1024)
                if not chunk: break
                output.write(chunk)
except urllib.error.HTTPError as error:
    print('HTTP %d' % error.code, flush=True)
    if error.code == 416: sys.exit(43)
    sys.exit(42 if error.code in (401,403) else 1)
except Exception:
    print('download failed', flush=True); sys.exit(1)
"""
    previous_time = time.monotonic()
    previous_size = temp.stat().st_size if temp.exists() else 0
    process = None
    host = file.get("host", "civitai.com")
    if host not in _CIVITAI_HOSTS:
        host = "civitai.com"
    if host.startswith("www."):
        host = host[4:]
    payload = json.dumps({"url": f"https://{host}/api/download/models/{int(file['version'])}",
                          "part": str(temp)}).encode("utf-8")
    try:
        with (ROOT / "setup.log").open("ab") as log:
            process = subprocess.Popen([sys.executable, "-c", helper],
                                       stdin=subprocess.PIPE, stdout=log,
                                       stderr=subprocess.STDOUT, cwd=ROOT,
                                       start_new_session=True)
            with _lock:
                _active_process = process
            process.stdin.write(payload)
            process.stdin.close()
            while process.poll() is None:
                if cancel.is_set():
                    _stop_process(process)
                    raise DownloadCancelled("Model indirmesi iptal edildi; kaldığı yer korunup devam ettirilebilir.")
                time.sleep(1)
                size = temp.stat().st_size if temp.exists() else 0
                now = time.monotonic()
                rate = max(0, size - previous_size) / max(now - previous_time, .001) / (1024 * 1024)
                _progress(group, "İndiriliyor", name=file["name"], downloaded=size,
                          total=file.get("size"), rate=round(rate, 2))
                previous_time, previous_size = now, size
    finally:
        with _lock:
            if _active_process is process:
                _active_process = None
    if cancel.is_set():
        raise DownloadCancelled("Model indirmesi iptal edildi; kaldığı yer korunup devam ettirilebilir.")
    if process.returncode == 43:
        _progress(group, "Dosya doğrulanıyor", name=file["name"],
                  downloaded=temp.stat().st_size if temp.is_file() else 0,
                  total=file.get("size"))
    if process.returncode == 43 and _valid_model(temp, file):
        temp.replace(target)
        _write_verification(target, file)
        return
    if process.returncode == 42:
        raise RuntimeError("Civitai model erişimi reddedildi; Pod Secret ayarını kontrol edin.")
    if process.returncode:
        raise RuntimeError("Civitai modeli indirilemedi; kurulum logunu kontrol edin.")
    _progress(group, "Dosya doğrulanıyor", name=file["name"],
              downloaded=temp.stat().st_size if temp.is_file() else 0,
              total=file.get("size"))
    if not _valid_model(temp, file):
        temp.unlink(missing_ok=True)
        raise RuntimeError("Model dosyasının bütünlüğü doğrulanamadı.")
    temp.replace(target)
    _write_verification(target, file)


def _url_model(target, file, cancel, group):
    """Direct HTTPS downloads use a cancellable worker and resume partial files."""
    global _active_process
    temp = target.with_suffix(".part")
    helper = """import json, os, sys, urllib.error, urllib.request
c=json.loads(sys.stdin.read()); offset=os.path.getsize(c['part']) if os.path.exists(c['part']) else 0
headers={'User-Agent':'Mozilla/5.0','Accept-Encoding':'identity'}
if offset: headers['Range']='bytes=%d-' % offset
request=urllib.request.Request(c['url'],headers=headers)
try:
    with urllib.request.urlopen(request,timeout=30) as response:
        append=bool(offset and response.status==206)
        with open(c['part'],'ab' if append else 'wb') as output:
            while True:
                chunk=response.read(1024*1024)
                if not chunk: break
                output.write(chunk)
except urllib.error.HTTPError as error:
    print('HTTP %d' % error.code,flush=True); sys.exit(1)
except Exception:
    print('download failed',flush=True); sys.exit(1)
"""
    previous_time = time.monotonic()
    previous_size = temp.stat().st_size if temp.exists() else 0
    process = None
    try:
        with (ROOT / "setup.log").open("ab") as log:
            process = subprocess.Popen([sys.executable, "-c", helper],
                                       stdin=subprocess.PIPE, stdout=log,
                                       stderr=subprocess.STDOUT, cwd=ROOT,
                                       start_new_session=True)
            with _lock:
                _active_process = process
            payload = json.dumps({"url": file["url"], "part": str(temp)}).encode("utf-8")
            process.stdin.write(payload)
            process.stdin.close()
            while process.poll() is None:
                if cancel.is_set():
                    _stop_process(process)
                    raise DownloadCancelled("Model indirmesi iptal edildi; kaldığı yer korunup devam ettirilebilir.")
                time.sleep(1)
                size = temp.stat().st_size if temp.exists() else 0
                now = time.monotonic()
                rate = max(0, size - previous_size) / max(now - previous_time, .001) / (1024 * 1024)
                _progress(group, "İndiriliyor", name=file["name"], downloaded=size,
                          total=file.get("size"), rate=round(rate, 2))
                previous_time, previous_size = now, size
    finally:
        with _lock:
            if _active_process is process:
                _active_process = None
    if cancel.is_set():
        raise DownloadCancelled("Model indirmesi iptal edildi; kaldığı yer korunup devam ettirilebilir.")
    if process.returncode:
        raise RuntimeError("Model indirilemedi; kurulum logunu kontrol edin.")
    _progress(group, "Dosya doğrulanıyor", name=file["name"],
              downloaded=temp.stat().st_size if temp.is_file() else 0,
              total=file.get("size"))
    if not _valid_model(temp, file):
        temp.unlink(missing_ok=True)
        raise RuntimeError("Model dosyasının bütünlüğü doğrulanamadı.")
    temp.replace(target)
    _write_verification(target, file)


def _valid_model(path, file):
    if not path.is_file() or not path.stat().st_size:
        return False
    expected = file.get("sha256")
    if expected is None:
        return True
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() == expected


def _verification_file(path):
    return path.with_name(path.name + ".verified")


def _model_present(path):
    return path.is_file() and path.stat().st_size > 0


def _model_verified(path, file):
    if not path.is_file() or not path.stat().st_size:
        return False
    expected = file.get("sha256")
    if not expected:
        return True
    try:
        return _verification_file(path).read_text(encoding="ascii").strip() == (
            expected.lower() + ":" + str(path.stat().st_size))
    except OSError:
        return False


def _write_verification(path, file):
    expected = file.get("sha256")
    if expected:
        _verification_file(path).write_text(
            expected.lower() + ":" + str(path.stat().st_size), encoding="ascii")


def _hf_incomplete_file(local_dir, filename):
    """Find Hugging Face's resumable partial file inside its local-dir cache."""
    try:
        from huggingface_hub._local_folder import get_local_download_paths

        paths = get_local_download_paths(Path(local_dir), filename)
        candidates = []
        try:
            metadata = paths.metadata_path.read_text(encoding="utf-8").splitlines()
            if len(metadata) >= 2 and metadata[1]:
                candidates.append(paths.incomplete_path(metadata[1]))
        except OSError:
            pass
        # huggingface_hub writes the metadata only after it moves the finished file.
        # While downloading, find its ETag-named temporary file by the stable filename hash.
        prefix = base64.urlsafe_b64encode(
            hashlib.sha1(paths.metadata_path.name.encode("utf-8")).digest()
        ).decode("ascii")
        candidates.extend(paths.metadata_path.parent.glob(prefix + ".*.incomplete"))
        existing = [path for path in candidates if path.is_file()]
        if existing:
            return max(existing, key=lambda path: path.stat().st_mtime_ns)
    except (ImportError, OSError, ValueError, IndexError, AttributeError):
        pass
    return None
