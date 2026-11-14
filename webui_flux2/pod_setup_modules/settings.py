"""RunPod setup settings operations.

Shared runtime state and public compatibility API live in pod_setup.
"""

import pod_setup as _ctx

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
            if host in _ctx._CIVITAI_HOSTS:
                return {**source_file, "source": "civitai", "host": host,
                        "dir": "loras", "name": filename}
        if source == "url" and str(source_file.get("url", "")).startswith("https://"):
            return {**source_file, "source": "url", "dir": "loras", "name": filename}
    link = str(item.get("link") or "").strip()
    parsed = _ctx.urllib.parse.urlsplit(link)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        return None
    if parsed.hostname == "huggingface.co":
        try:
            source = _ctx._parse_hf_link(link)
        except ValueError:
            return None
        if _ctx.Path(source["path"]).suffix.lower() not in (".safetensors", ".pt"):
            return None
        return dict(source, source="hf", dir="loras", name=filename)
    if parsed.hostname not in _ctx._CIVITAI_HOSTS:
        return None
    query = _ctx.urllib.parse.parse_qs(parsed.query)
    version = None
    match = _ctx.re.fullmatch(r"/api/download/models/(\d+)/?", parsed.path)
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
                or _ctx._LORA_ID_RE.fullmatch(identifier)) or identifier in used:
            continue
        model = item.get("model")
        group = item.get("group")
        name = str(item.get("name") or "").strip()[:80]
        filename = item.get("file")
        if (model not in _ctx._LORA_MODELS or not isinstance(group, str)
                or not _ctx.re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,47}", group)
                or not isinstance(filename, str) or _ctx.Path(filename).name != filename
                or _ctx.Path(filename).suffix.lower() not in (".safetensors", ".pt")
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
                       "source_file": _ctx._legacy_lora_source(item, filename),
                       "link": str(item.get("link") or "")[:1000]})
        used.add(identifier)
    return result


def _register_lora_groups(settings):
    """Expose saved LoRAs as ordinary cancellable installer download groups."""
    entries = _ctx._lora_entries_clean(settings.get("loras"))
    groups = {entry["group"] for entry in entries
              if _ctx.workflow_enabled(entry["model"])
              and isinstance(entry.get("source_file"), dict)}
    for group in list(_ctx.CATALOG):
        if group.startswith("lora_") and group not in groups:
            _ctx.CATALOG.pop(group, None)
            _ctx._state.pop(group, None)
    for entry in entries:
        if not _ctx.workflow_enabled(entry["model"]):
            continue
        group = entry["group"]
        if group in _ctx.CATALOG:
            continue
        file = entry.get("source_file")
        if not isinstance(file, dict):
            continue
        _ctx.CATALOG[group] = {"label": "LoRA · " + entry["name"],
                          "engine": "comfy", "files": [dict(file)]}
    _ctx._validate_catalog()


def refresh_after_system_import():
    """Merge legacy backup settings with this image's current models and LoRAs."""
    with _ctx._system_settings_lock:
        settings = _ctx._system_settings_read()
        _ctx._model_instances_apply(settings)
        settings["loras"] = _ctx._lora_entries_clean(settings.get("loras"))
        _ctx._register_lora_groups(settings)
        settings["default_download_order"] = [
            group for group in settings.get("default_download_order", [])
            if group in _ctx.CATALOG and _ctx.CATALOG[group].get("enabled", True)]
        _ctx._apply_saved_links(settings)
        _ctx._system_settings_write(settings)
        order = list(settings.get("default_download_order", []))
        order.extend(entry["group"] for entry in settings.get("loras", [])
                     if entry.get("auto_download") and entry["group"] not in order)
        models = len(settings.get("model_instances", []))
        loras = len(settings.get("loras", []))
    queued = _ctx.start_order(order)
    return {"model_count": models, "lora_count": loras,
            "queued_after_restore": queued}


def _system_settings_read():
    settings = _ctx.json.loads(_ctx.json.dumps(_ctx._SYSTEM_SETTINGS_DEFAULT))
    if _ctx._system_settings_path is None:
        return settings
    saved = None
    # Global Volume is object-backed storage, not a POSIX filesystem. Read
    # immutable, checksummed versions first; keep the old single-file format as
    # a migration fallback for settings written by earlier images.
    for timestamp, digest, candidate in _ctx._system_settings_versions():
        try:
            payload = candidate.read_bytes()
            if _ctx.hashlib.sha256(payload).hexdigest() != digest:
                continue
            value = _ctx.json.loads(payload.decode("utf-8"))
        except (OSError, ValueError, TypeError, UnicodeDecodeError):
            continue
        if isinstance(value, dict) and value.get("schema") == 1:
            saved = value
            break
    if saved is None:
        try:
            saved = _ctx.json.loads(_ctx._system_settings_path.read_text(encoding="utf-8"))
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
        saved_loras = _ctx._lora_entries_clean(saved["loras"])
        builtin = [entry for entry in settings.get("loras", [])
                   if entry.get("built_in")]
        saved_ids = {entry["id"] for entry in saved_loras}
        settings["loras"] = saved_loras + [entry for entry in builtin
                                             if entry["id"] not in saved_ids]
    return settings


def _system_settings_versions():
    """Return verified-version filenames in newest-first order."""
    if _ctx._system_settings_path is None:
        return []
    pattern = _ctx.re.compile(
        _ctx.re.escape(_ctx._system_settings_path.stem)
        + r"-(\d{19})-([a-f0-9]{16})-([a-f0-9]{64})\.json$")
    try:
        entries = list(_ctx._system_settings_path.parent.iterdir())
    except OSError:
        return []
    versions = []
    for candidate in entries:
        match = pattern.fullmatch(candidate.name)
        if match and candidate.is_file():
            versions.append((match.group(1), match.group(3), candidate))
    return sorted(versions, key=lambda item: (item[0], item[2].name), reverse=True)


def _system_settings_write(settings):
    if _ctx._system_settings_path is None:
        raise RuntimeError("Sistem ayarlarının kaydedileceği depolama hazır değil.")
    payload = _ctx.json.dumps(settings, ensure_ascii=False, indent=2).encode("utf-8")
    digest = _ctx.hashlib.sha256(payload).hexdigest()
    timestamp = _ctx.time.time_ns()
    unique = _ctx.os.urandom(8).hex()
    destination = _ctx._system_settings_path.with_name(
        "%s-%019d-%s-%s.json" %
        (_ctx._system_settings_path.stem, timestamp, unique, digest))
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
            _ctx.time.sleep(min(0.2 * (2 ** attempt), 2.0))
    if not verified:
        message = "Kalıcı sistem ayarları doğrulanamadı."
        if last_error is not None:
            message += " (%s)" % last_error
        raise RuntimeError(message)
    versions = _ctx._system_settings_versions()
    valid = []
    invalid = []
    for _, expected_digest, old in versions:
        try:
            old_payload = old.read_bytes()
            old_settings = _ctx.json.loads(old_payload.decode("utf-8"))
            is_valid = (_ctx.hashlib.sha256(old_payload).hexdigest() == expected_digest
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
    repo = _ctx.urllib.parse.quote(file.get("repo", ""), safe="/")
    revision = _ctx.urllib.parse.quote(file.get("revision", ""), safe="")
    path = _ctx.urllib.parse.quote(file.get("path", ""), safe="/")
    return f"https://huggingface.co/{repo}/resolve/{revision}/{path}"


def _file_link(file):
    source = file.get("source") or "hf"
    if source == "hf":
        return _ctx._hf_link(file)
    if source == "civitai":
        host = file.get("host", "civitai.com")
        if host not in _ctx._CIVITAI_HOSTS:
            host = "civitai.com"
        return "https://%s/api/download/models/%s" % (host, int(file["version"]))
    return str(file.get("url") or "")


def _apply_saved_links(settings):
    overrides = settings.get("link_overrides") or {}
    for group, item in _ctx.CATALOG.items():
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
    _ctx._validate_catalog()


def _default_model_instances():
    instances = []
    for family, workflow in (_ctx._CATALOG_DATA.get("workflows") or {}).items():
        group_id = workflow.get("group")
        group = _ctx.CATALOG.get(group_id)
        if not group or not group.get("enabled", True):
            continue
        if group.get("engine") not in ("comfy", "llama"):
            continue
        instances.append({
            "id": family, "family": family, "name": group["label"],
            "group": group_id, "workflow": _ctx.copy.deepcopy(workflow),
            "files": _ctx.copy.deepcopy(group["files"]), "builtin": True,
        })
    return instances


def _model_instances_apply(settings):
    """Rebuild editable model variants from the checksummed universal settings."""
    saved = settings.get("model_instances") or []
    saved_variant_groups = {item.get("group") for item in saved
                            if isinstance(item, dict)
                            and _ctx.re.fullmatch(r"variant_[a-f0-9]{10}",
                                             str(item.get("group") or ""))}
    for group, item in list(_ctx.CATALOG.items()):
        if item.get("variant") and group not in saved_variant_groups:
            _ctx.CATALOG.pop(group, None)
    builtin_ids = set((_ctx._CATALOG_DATA.get("workflows") or {}).keys())
    saved_variant_ids = {item.get("id") for item in saved
                         if isinstance(item, dict)
                         and _ctx.re.fullmatch(r"variant_[a-f0-9]{10}",
                                          str(item.get("group") or ""))}
    for identifier in list(_ctx.WORKFLOWS):
        if identifier not in builtin_ids and identifier not in saved_variant_ids:
            _ctx.WORKFLOWS.pop(identifier, None)
    saved_by_id = {item.get("id"): item for item in saved
                   if isinstance(item, dict) and isinstance(item.get("id"), str)}
    records = _ctx._default_model_instances()
    for record in records:
        stored = saved_by_id.get(record["id"])
        if stored and isinstance(stored.get("name"), str):
            record["name"] = stored["name"][:80].strip() or record["name"]
        group = _ctx.CATALOG[record["group"]]
        group["label"] = record["name"]
        group["instance_id"] = record["id"]
        group["family"] = record["family"]
        _ctx.WORKFLOWS[record["id"]] = _ctx.copy.deepcopy(record["workflow"])
    known = {record["id"] for record in records}
    for stored in saved:
        if not isinstance(stored, dict):
            continue
        identifier = stored.get("id")
        group_id = stored.get("group")
        family = stored.get("family")
        if (identifier in known or not isinstance(identifier, str)
                or not _ctx.re.fullmatch(r"[a-z][a-z0-9_]{2,47}", identifier)
                or family not in _ctx.WORKFLOWS or not isinstance(group_id, str)
                or not _ctx.re.fullmatch(r"variant_[a-f0-9]{10}", group_id)):
            continue
        workflow = stored.get("workflow")
        files = stored.get("files")
        name = str(stored.get("name") or "")[:80].strip()
        if not name or not isinstance(workflow, dict) or not isinstance(files, list):
            continue
        base_group = _ctx.CATALOG.get((_ctx.WORKFLOWS.get(family) or {}).get("group"))
        if not base_group or not isinstance(workflow.get("group"), str):
            continue
        _ctx.CATALOG[group_id] = {"label": name, "engine": base_group["engine"],
                             "files": _ctx.copy.deepcopy(files), "instance_id": identifier,
                             "family": family, "variant": True}
        workflow = _ctx.copy.deepcopy(workflow)
        workflow["group"] = group_id
        _ctx.WORKFLOWS[identifier] = workflow
        records.append({"id": identifier, "family": family, "name": name,
                        "group": group_id, "workflow": workflow,
                        "files": _ctx.copy.deepcopy(files), "builtin": False})
        known.add(identifier)
    settings["model_instances"] = records
    _ctx._validate_catalog()


def model_options():
    settings = _ctx._system_settings_read()
    records = settings.get("model_instances") or _ctx._default_model_instances()
    result = []
    for record in records:
        group = _ctx.CATALOG.get(record.get("group")) or {}
        workflow = _ctx.WORKFLOWS.get(record["id"]) or {}
        primary_name = next((workflow.get(key) for key in
                             ("diffusion_file", "checkpoint_file", "unet_file", "model_file")
                             if workflow.get(key)), None)
        result.append({"id": record["id"], "family": record["family"],
                       "name": record["name"], "group": record["group"],
                       "engine": group.get("engine"),
                       "ready": _ctx.workflow_ready(record["id"]),
                       "builtin": bool(record.get("builtin")),
                       "primary_file": primary_name,
                       "files": [{"name": file["name"], "dir": file["dir"],
                                  "optional": bool(file.get("optional")),
                                  "workflow_key": next((key for key, value in workflow.items()
                                                        if value == file["name"]), None),
                                  "link": _ctx._file_link(file),
                                  "default_link": _ctx._file_link(
                                      file.get("_default_source") or file)}
                                 for file in group.get("files", [])]})
    return result


def model_instance(identifier):
    if not isinstance(identifier, str):
        return None
    for record in _ctx._system_settings_read().get("model_instances", []):
        if record.get("id") == identifier:
            return _ctx.copy.deepcopy(record)
    return None


def create_model_instance(payload):
    if not isinstance(payload, dict):
        raise ValueError("Model bilgileri geçersiz.")
    family = payload.get("family")
    base_workflow = _ctx.copy.deepcopy(_ctx._CATALOG_DATA.get("workflows", {}).get(family) or {})
    base_group_id = base_workflow.get("group")
    base_group = _ctx.copy.deepcopy(_ctx._CATALOG_DATA.get("groups", {}).get(base_group_id) or {})
    if (not base_workflow or not base_group or base_group.get("engine") not in ("comfy", "llama")
            or not base_group.get("enabled", True)
            or not _ctx.CATALOG.get(base_group_id, {}).get("enabled", True)):
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
    primary_link = next((_ctx._file_link(file) for file in base_group.get("files", [])
                         if file.get("name") == primary_file), None)
    if not isinstance(links.get(primary_file), str) or not links[primary_file].strip():
        raise ValueError("Yeni model için ana dosyanın bağlantısını gir.")
    if links[primary_file].strip() == primary_link:
        raise ValueError("Yeni model kaydında ana dosya bağlantısı farklı olmalı.")
    suffix = _ctx.uuid.uuid4().hex[:10]
    identifier = family + "_" + suffix
    group_id = "variant_" + suffix
    group = _ctx.copy.deepcopy(base_group)
    workflow = _ctx.copy.deepcopy(base_workflow)
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
            path = _ctx.Path(old_name)
            new_name = "%s__%s%s" % (path.stem, suffix, path.suffix)
            file["name"] = new_name
            for key, value in list(workflow.items()):
                if value == old_name:
                    workflow[key] = new_name
            if requested_link:
                source, metadata = _ctx._parse_model_link(requested_link)
                for field in ("source", "repo", "revision", "path", "url",
                              "version", "host", "size", "sha256"):
                    file.pop(field, None)
                file.update(source, **metadata)
        rewritten.append(file)
    group["files"] = rewritten
    workflow["group"] = group_id
    record = {"id": identifier, "family": family, "name": name,
              "group": group_id, "workflow": workflow,
              "files": _ctx.copy.deepcopy(rewritten), "builtin": False}
    with _ctx._system_settings_lock:
        settings = _ctx._system_settings_read()
        settings["model_instances"] = settings.get("model_instances") or _ctx._default_model_instances()
        _ctx.CATALOG[group_id] = group
        _ctx.WORKFLOWS[identifier] = workflow
        settings["model_instances"].append(record)
        _ctx._validate_catalog()
        _ctx._system_settings_write(settings)
    return _ctx.system_settings()


def rename_model_instance(identifier, name):
    name = str(name or "").strip()
    if not name or len(name) > 80:
        raise ValueError("Model adı 1-80 karakter olmalı.")
    with _ctx._system_settings_lock:
        settings = _ctx._system_settings_read()
        records = settings.get("model_instances") or _ctx._default_model_instances()
        record = next((item for item in records if item.get("id") == identifier), None)
        if record is None:
            raise ValueError("Model bulunamadı.")
        record["name"] = name
        record["files"] = _ctx.copy.deepcopy(_ctx.CATALOG[record["group"]]["files"])
        record["workflow"] = _ctx.copy.deepcopy(_ctx.WORKFLOWS[identifier])
        _ctx.CATALOG[record["group"]]["label"] = name
        _ctx._system_settings_write(settings)
    return _ctx.system_settings()


def delete_model_instance(identifier):
    with _ctx._system_settings_lock:
        settings = _ctx._system_settings_read()
        records = settings.get("model_instances") or _ctx._default_model_instances()
        record = next((item for item in records if item.get("id") == identifier), None)
        if record is None or record.get("builtin"):
            raise ValueError("Varsayılan model kaydı silinemez.")
        group = record["group"]
        if _ctx._active == group or group in _ctx._queue:
            raise RuntimeError("Model indirilirken kaydı silinemez.")
        has_group_files = any((_ctx.MODELS / file["dir"] / file["name"]).exists()
                              for file in _ctx.CATALOG.get(group, {}).get("files", []))
        if has_group_files or _ctx._state.get(group, ("idle",))[0] != "idle":
            _ctx.remove(group)
        records = [item for item in records if item.get("id") != identifier]
        settings["model_instances"] = records
        _ctx.CATALOG.pop(group, None)
        _ctx.WORKFLOWS.pop(identifier, None)
        _ctx._system_settings_write(settings)
    return _ctx.system_settings()


def system_settings():
    """Return universal configuration without account-specific settings."""
    with _ctx._system_settings_lock:
        settings = _ctx._system_settings_read()
        groups = []
        for group, item in _ctx.CATALOG.items():
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
                    "link": _ctx._file_link(file),
                    "default_link": _ctx._file_link(original),
                })
            groups.append({"id": group, "label": item["label"], "files": files})
        return {"ayarlar": settings, "gruplar": groups,
                "model_instances": _ctx.model_options(),
                "model_families": [
                    {"id": family, "name": (_ctx.CATALOG.get(workflow.get("group")) or {}).get("label", family),
                     "engine": (_ctx.CATALOG.get(workflow.get("group")) or {}).get("engine")}
                    for family, workflow in _CATALOG_DATA.get("workflows", {}).items()
                    if workflow.get("group") in _ctx.CATALOG
                    and _ctx.CATALOG[workflow["group"]].get("enabled", True)
                    and _ctx.CATALOG[workflow["group"]].get("engine") in ("comfy", "llama")
                ], "lora_durum": _ctx.lora_status()}


def _parse_hf_link(link):
    parsed = _ctx.urllib.parse.urlsplit(str(link or "").strip())
    if (parsed.scheme != "https" or parsed.hostname != "huggingface.co"
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Yalnızca Hugging Face resolve bağlantısı kullanılabilir.")
    parts = [_ctx.urllib.parse.unquote(part) for part in parsed.path.strip("/").split("/")]
    if len(parts) < 5 or parts[2] != "resolve" or not parts[3]:
        raise ValueError("Bağlantı biçimi: huggingface.co/{hesap}/{depo}/resolve/{sürüm}/{dosya}")
    repo = "/".join(parts[:2])
    path = "/".join(parts[4:])
    if (not _ctx.re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo)
            or any(part in ("", ".", "..") for part in path.split("/"))):
        raise ValueError("Hugging Face depo veya dosya yolu geçersiz.")
    return {"repo": repo, "revision": parts[3], "path": path}


def update_system_settings(payload):
    if not isinstance(payload, dict):
        raise ValueError("Sistem ayarları sözlük olmalı.")
    with _ctx._system_settings_lock:
        settings = _ctx._system_settings_read()
        if "auto_download" in payload:
            if not isinstance(payload["auto_download"], bool):
                raise ValueError("Otomatik indirme seçeneği açık veya kapalı olmalı.")
            settings["auto_download"] = payload["auto_download"]
        if "default_download_order" in payload:
            order = payload["default_download_order"]
            if (not isinstance(order, list) or len(order) > len(_ctx.CATALOG)
                    or any(group not in _ctx.CATALOG or not _ctx.CATALOG[group].get("enabled", True)
                           for group in order) or len(set(order)) != len(order)):
                raise ValueError("Varsayılan indirme sırası geçersiz.")
            settings["default_download_order"] = order
        _ctx._system_settings_write(settings)
        return _ctx.system_settings()


def update_model_link(group, name, link, restore=False):
    if group not in _ctx.CATALOG or not _ctx.CATALOG[group].get("enabled", True):
        raise ValueError("Bu model grubu düzenlenemez.")
    file = next((item for item in _ctx.CATALOG[group]["files"] if item["name"] == name), None)
    if file is None:
        raise ValueError("Dosya bulunamadı.")
    with _ctx._system_settings_lock:
        affected = [(candidate, item) for candidate, catalog in _ctx.CATALOG.items()
                    for item in catalog.get("files", [])
                    if item["dir"] == file["dir"] and item["name"] == name]
        affected_groups = {candidate for candidate, _ in affected}
        if _ctx._active in affected_groups or affected_groups.intersection(_ctx._queue):
            raise RuntimeError("İndirme kuyruğu çalışırken bağlantı değiştirilemez.")
        settings = _ctx._system_settings_read()
        overrides = settings.setdefault("link_overrides", {})
        target = _ctx.MODELS / file["dir"] / file["name"]
        installed_groups = ([candidate for candidate in affected_groups
                             if _ctx._group_files_complete(candidate)]
                            if _ctx._model_verified(target, file) else [])
        if restore:
            for candidate, shared in affected:
                overrides.pop(candidate + "/" + name, None)
                original_files = (_ctx._CATALOG_DATA.get("groups", {}).get(candidate, {})
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
            source, metadata = _ctx._parse_model_link(link)
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
            _ctx._verification_file(target).unlink(missing_ok=True)
        # A partial transfer belongs to the previous URL and must not be resumed.
        target.with_suffix(".part").unlink(missing_ok=True)
        _ctx._validate_catalog()
        _ctx._system_settings_write(settings)
    for candidate in installed_groups:
        try:
            _ctx.start(candidate)
        except RuntimeError:
            # A shared installed dependency can already be queued by another
            # affected group; the first queued job will restore that dependency.
            pass
    result = _ctx.system_settings()
    result["restarted_groups"] = installed_groups
    return result


def _hf_resolve_metadata(source):
    """Read file size and Git-LFS digest without downloading its model weights."""
    path = "/%s/resolve/%s/%s" % (source["repo"],
                                  _ctx.urllib.parse.quote(source["revision"], safe=""),
                                  _ctx.urllib.parse.quote(source["path"], safe="/"))
    request = _ctx.urllib.request.Request("https://huggingface.co" + path,
                                     method="HEAD", headers={"User-Agent": "RunPod-WebUI/1"})
    token = _ctx.os.environ.get("HF_TOKEN")
    if token:
        request.add_header("Authorization", "Bearer " + token)
    try:
        with _ctx.urllib.request.urlopen(request, timeout=20) as response:
            headers = response.headers
    except (OSError, _ctx.urllib.error.URLError, _ctx.urllib.error.HTTPError):
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
    if _ctx.re.fullmatch(r"[0-9a-f]{64}", digest):
        result["sha256"] = digest
    return result


def _parse_model_link(link):
    """Resolve HF, Civitai, or direct HTTPS sources without changing the target."""
    value = str(link or "").strip()
    parsed = _ctx.urllib.parse.urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.fragment):
        raise ValueError("Model bağlantısı kimlik bilgisi içermeyen HTTPS olmalı.")
    if parsed.hostname == "huggingface.co":
        source = _ctx._parse_hf_link(value)
        source["source"] = "hf"
        return source, _ctx._hf_resolve_metadata(source)
    if parsed.hostname in _ctx._CIVITAI_HOSTS:
        source, _ = _ctx._parse_lora_link(value)
        return source, {key: source[key] for key in ("size", "sha256")
                        if key in source}
    if any(key.casefold() in ("token", "api_key", "apikey", "key")
           for key in urllib.parse.parse_qs(parsed.query)):
        raise ValueError("Gizli anahtar bağlantıya eklenmemeli; Pod Secret kullanılır.")
    request = _ctx.urllib.request.Request(value, method="HEAD",
                                     headers={"User-Agent": "RunPod-WebUI/1"})
    metadata = {}
    try:
        with _ctx.urllib.request.urlopen(request, timeout=20) as response:
            size = response.headers.get("Content-Length")
            if size and int(size) > 0:
                metadata["size"] = int(size)
    except (OSError, ValueError, _ctx.urllib.error.URLError, _ctx.urllib.error.HTTPError):
        pass
    return {"source": "url", "url": value}, metadata


def _civitai_resolve_metadata(version, host="civitai.com"):
    token = _ctx.os.environ.get("CIVITAI_TOKEN")
    if not token or token.startswith("{{"):
        return {}
    if host not in _ctx._CIVITAI_HOSTS:
        host = "civitai.com"
    if host.startswith("www."):
        host = host[4:]
    request = _ctx.urllib.request.Request(
        "https://%s/api/v1/model-versions/%d" % (host, int(version)),
        headers={"Authorization": "Bearer " + token,
                 "User-Agent": "RunPod-WebUI/1"})
    try:
        with _ctx.urllib.request.urlopen(request, timeout=20) as response:
            data = _ctx.json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
        files = data.get("files") or []
        chosen = next((item for item in files if item.get("primary")),
                      files[0] if files else {})
        result = {}
        size = chosen.get("sizeKB")
        if size and float(size) > 0:
            result["size"] = int(float(size) * 1024)
        digest = ((chosen.get("hashes") or {}).get("SHA256") or "").lower()
        if _ctx.re.fullmatch(r"[0-9a-f]{64}", digest):
            result["sha256"] = digest
        return result
    except (OSError, ValueError, TypeError, _ctx.urllib.error.URLError,
            _ctx.urllib.error.HTTPError):
        return {}


def _parse_lora_link(link):
    value = str(link or "").strip()
    parsed = _ctx.urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("LoRA bağlantısı HTTPS olmalı.")
    if parsed.hostname == "huggingface.co":
        source = _ctx._parse_hf_link(value)
        suffix = _ctx.Path(source["path"]).suffix.lower()
        if suffix not in (".safetensors", ".pt"):
            raise ValueError("Hugging Face LoRA dosyası .safetensors veya .pt olmalı.")
        return dict(source, source="hf", **_ctx._hf_resolve_metadata(source)), suffix
    if parsed.hostname not in _ctx._CIVITAI_HOSTS:
        raise ValueError("Yalnızca Hugging Face veya Civitai LoRA bağlantısı desteklenir.")
    query = _ctx.urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    if any(key.casefold() in ("token", "api_key", "apikey") for key in query):
        raise ValueError("Civitai anahtarı bağlantıya eklenmemeli; Pod Secret kullanılır.")
    match = _ctx.re.fullmatch(r"/api/download/models/(\d+)/?", parsed.path)
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
    return dict(source, **_ctx._civitai_resolve_metadata(version, host)), ".safetensors"


def add_lora(payload):
    if not isinstance(payload, dict):
        raise ValueError("LoRA bilgileri geçersiz.")
    model = payload.get("model")
    name = str(payload.get("name") or "").strip()[:80]
    link = str(payload.get("link") or "").strip()
    if (model not in _ctx._LORA_MODELS or not _ctx.workflow_enabled(model)
            or not name or len(link) > 2000):
        raise ValueError("LoRA adı, modeli veya bağlantısı geçersiz.")
    source, suffix = _ctx._parse_lora_link(link)
    digest = _ctx.hashlib.sha256((model + "\0" + name + "\0" + link + "\0"
                             + str(_ctx.time.time_ns())).encode("utf-8")).hexdigest()[:12]
    slug = _ctx.re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")[:36] or "lora"
    filename = f"{slug}-{digest}{suffix}"
    group = "lora_" + digest
    source_file = dict(source, dir="loras", name=filename)
    entry = {"id": digest, "name": name, "model": model, "group": group,
             "file": filename, "weight": 1.0, "enabled": True,
             "default_selected": False, "auto_download": False,
             "source_file": source_file, "link": link}
    with _ctx._system_settings_lock:
        settings = _ctx._system_settings_read()
        if any(item.get("id") == digest for item in settings.get("loras", [])):
            raise RuntimeError("Bu LoRA zaten kayıtlı.")
        settings["loras"] = _ctx._lora_entries_clean(settings.get("loras", []) + [entry])
        _ctx.CATALOG[group] = {"label": "LoRA · " + name,
                          "engine": "comfy", "files": [source_file]}
        _ctx._validate_catalog()
        _ctx._system_settings_write(settings)
        return _ctx.system_settings()


def configure_lora(identifier, payload):
    if not isinstance(payload, dict):
        raise ValueError("LoRA ayarları geçersiz.")
    with _ctx._system_settings_lock:
        settings = _ctx._system_settings_read()
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
        _ctx._system_settings_write(settings)
        return _ctx.system_settings()


def delete_lora(identifier):
    with _ctx._system_settings_lock:
        settings = _ctx._system_settings_read()
        entry = next((item for item in settings.get("loras", [])
                      if item.get("id") == identifier), None)
        if entry is None:
            raise ValueError("LoRA bulunamadı.")
        if entry.get("built_in"):
            raise ValueError("Uygulamayla gelen temel LoRA silinemez; devre dışı bırakılabilir.")
        group = entry["group"]
        if group in _ctx._queue or group == _ctx._active:
            raise RuntimeError("LoRA indirilirken kaydı silinemez.")
        settings["loras"] = [item for item in settings["loras"]
                              if item.get("id") != identifier]
        settings["default_download_order"] = [item for item in
                                                settings.get("default_download_order", [])
                                                if item != group]
        group_config = _ctx.CATALOG.get(group)
        if group_config:
            for file in group_config["files"]:
                target = _ctx.MODELS / file["dir"] / file["name"]
                target.unlink(missing_ok=True)
                _ctx._verification_file(target).unlink(missing_ok=True)
                target.with_suffix(".part").unlink(missing_ok=True)
            _ctx.CATALOG.pop(group, None)
            _ctx._state.pop(group, None)
        _ctx._validate_catalog()
        _ctx._system_settings_write(settings)
        return _ctx.system_settings()


def lora_status(model=None):
    settings = _ctx._system_settings_read()
    result = []
    for entry in settings.get("loras", []):
        if not _ctx.workflow_enabled(entry.get("model")):
            continue
        if model and entry.get("model") != model:
            continue
        group = entry.get("group")
        file = next((item for item in _ctx.CATALOG.get(group, {}).get("files", [])
                     if item.get("name") == entry.get("file")), None)
        target = _ctx.MODELS / file["dir"] / file["name"] if file else None
        ready = bool(file and target and _ctx._model_verified(target, file))
        state, detail = _ctx._state.get(group, ("idle", ""))
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
    settings = _ctx._system_settings_read()
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
        file = next((item for item in _ctx.CATALOG[entry["group"]]["files"]
                     if item["name"] == entry["file"]), None)
        if file is None:
            raise RuntimeError("LoRA dosyasının indirme kaydı bulunamadı.")
        target = _ctx.MODELS / file["dir"] / file["name"]
        if not _ctx._model_verified(target, file):
            _ctx.start(entry["group"])
            deadline = _ctx.time.monotonic() + 3600
            while _ctx.time.monotonic() < deadline:
                if job is not None and job.get("iptal"):
                    raise RuntimeError("İş durduruldu; LoRA indirmesi kurulum kuyruğunda kalabilir.")
                if _ctx._model_verified(target, file):
                    break
                state, detail = _ctx._state.get(entry["group"], ("idle", ""))
                if state == "error":
                    raise RuntimeError("LoRA indirilemedi: " + str(detail)[:300])
                if job is not None:
                    job["ilerleme"] = {"asama": "lora",
                                       "calisan": entry["name"] + " indiriliyor",
                                       "oran": 0.08}
                _ctx.time.sleep(0.5)
            else:
                raise TimeoutError("LoRA indirmesi 60 dakika içinde tamamlanmadı.")
        resolved.append({"name": entry["file"], "weight": weight})
    return resolved
