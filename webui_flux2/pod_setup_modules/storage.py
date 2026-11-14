"""RunPod setup storage operations.

Shared runtime state and public compatibility API live in pod_setup.
"""

import pod_setup as _ctx

def prepare_data(data_root, accounts):
    """Use a mounted volume, or keep the app on container disk if absent."""
    requested_mount = _ctx.Path(_ctx.os.environ.get("POD_VOLUME_MOUNT", "/workspace")).resolve()
    mount = _ctx.validate_mount()
    target = _ctx.Path(data_root).resolve()
    volume_attached = mount == requested_mount and mount != _ctx.Path("/")
    _ctx._flex = None
    flex_prefix = _ctx.os.environ.get("POD_FLEX_PREFIX")
    flex_key = _ctx.os.environ.get("POD_FLEX_ENCRYPTION_KEY", "")
    if volume_attached and flex_prefix and flex_key:
        from flex_store import FlexStore

        if not flex_prefix.isascii() or not flex_prefix.replace("-", "").isalnum():
            raise RuntimeError("Flex dizini tek bir güvenli addan oluşmalı.")
        if not target.is_relative_to(_ctx.ROOT) or target == _ctx.ROOT:
            raise RuntimeError("Flex çalışma dizini geçici diskte olmalı.")
        snapshots = (mount / flex_prefix).resolve()
        if snapshots.parent != mount:
            raise RuntimeError("Flex dizini seçilen bağlama noktasında olmalı.")
        snapshots.mkdir(mode=0o700, parents=True, exist_ok=True)
        _ctx._flex = FlexStore(target, snapshots)
        _ctx._flex.migrate_legacy()
        # Settings are written on the container disk and enter Flex only inside
        # the encrypted snapshot. Never write a plain JSON object to the volume.
        _ctx._system_settings_path = target / "system-settings.json"
    else:
        if volume_attached and target.is_relative_to(mount):
            raise RuntimeError(
                "Kullanıcı kayıt dizini ağ volume'ünde olamaz; container diskini seçin.")
        if not target.is_relative_to(_ctx.ROOT) or target == _ctx.ROOT:
            raise RuntimeError("Volume yokken kullanıcı verileri container diskinde olmalı.")
        _ctx._system_settings_path = target / "system-settings.json"
        if volume_attached and flex_prefix and not flex_key:
            print("Flex kalıcılığı kapalı; POD_FLEX_ENCRYPTION_KEY olmadan volume'a yazılmayacak.",
                  flush=True)
        elif volume_attached:
            print("Şifreli Flex ayarı yok; kullanıcı kayıtları ve ayarlar container diskinde.",
                  flush=True)
        else:
            print("Kalıcı volume bağlı değil; kullanıcı verileri ve genel ayarlar container diskinde.",
                  flush=True)
    _ctx._storage_mode = "persistent" if _ctx._flex is not None else "ephemeral"
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    _ctx._data_lock = (target / ".webui.lock").open("a")
    _ctx.fcntl.flock(_ctx._data_lock, _ctx.fcntl.LOCK_EX | _ctx.fcntl.LOCK_NB)
    if _ctx._flex is not None and not _ctx.Path(accounts.dosya).exists():
        _ctx._flex.restore()
    if (_ctx._flex is not None and not _ctx._system_settings_path.is_file()
            and not _ctx._system_settings_versions()):
        _ctx._flex.restore_settings()
    if not accounts.kurulu_mu() and _ctx.Path(accounts.dosya).exists():
        raise RuntimeError("Kullanıcı kaydı boş veya okunamıyor; yeni hesap oluşturulmadı.")
    # No account is a normal first-run state. The WebUI asks the owner to
    # create the first administrator instead of relying on a shared password.
    settings = _ctx._system_settings_read()
    _ctx._model_instances_apply(settings)
    _ctx._register_lora_groups(settings)
    settings["default_download_order"] = [
        group for group in settings.get("default_download_order", [])
        if group in _ctx.CATALOG and _ctx.CATALOG[group].get("enabled", True)]
    _ctx._system_settings_write(settings)
    _ctx._apply_saved_links(settings)
    _ctx._ready = True
    if _ctx._flex is not None:
        _ctx._flex.save()
    if (settings.get("auto_download")
            and _ctx.os.environ.get("WEBUI_AUTO_DOWNLOAD", "1") != "0"):
        order = list(settings.get("default_download_order", []))
        order.extend(entry["group"] for entry in settings.get("loras", [])
                     if entry.get("auto_download") and entry["group"] not in order)
        for group in order:
            try:
                _ctx.start(group)
            except (ValueError, RuntimeError):
                continue


def validate_mount():
    mount = _ctx.Path(_ctx.os.environ.get("POD_VOLUME_MOUNT", "/workspace")).resolve()
    mounts = _ctx.Path("/proc/self/mountinfo").read_text().splitlines()
    if mount != _ctx.Path("/") and any(line.split()[4] == str(mount) for line in mounts):
        return mount
    if _ctx.os.environ.get("POD_ALLOW_EPHEMERAL_DATA", "1") == "1":
        return _ctx.Path("/")
    raise RuntimeError("Kalıcı volume bağlı değil.")


def checkpoint():
    if _ctx._flex is not None:
        _ctx._flex.save()


def refresh_data_lock():
    """Reacquire the container-data lock after a full data-tree replacement."""
    lock_path = _ctx.Path(_ctx.os.environ.get("WEBUI_DATA_ROOT", "/opt/runtime/user-data")) / ".webui.lock"
    replacement = lock_path.open("a")
    _ctx.fcntl.flock(replacement, _ctx.fcntl.LOCK_EX | _ctx.fcntl.LOCK_NB)
    previous = _ctx._data_lock
    _ctx._data_lock = replacement
    if previous is not None:
        previous.close()


def storage_status():
    return {"storage_mode": _ctx._storage_mode,
            "storage_persistent": _ctx._storage_mode == "persistent"}


def status():
    with _ctx._lock:
        groups = []
        for group, item in _ctx.CATALOG.items():
            if not item.get("enabled", True):
                continue
            state, detail = _ctx._state.get(group, ("idle", ""))
            if state == "idle" and _ctx._group_files_complete(group):
                state, detail = "installed", "Model dosyaları diskte hazır."
            if (state == "ready" and item["engine"] == "comfy"
                    and (_ctx._comfy_process is None or _ctx._comfy_process.poll() is not None)):
                state, detail = "installed", "Model dosyaları hazır; ComfyUI yeniden başlatılabilir."
                _ctx._state[group] = (state, detail)
            groups.append({"id": group, "ad": item["label"], "durum": state,
                           "ayrinti": detail, "engine": item["engine"],
                           "calisma_hazir": _ctx._runtime_available(group),
                           "dosyalar": [{"ad": file["name"],
                                         "toplam": file.get("size"),
                                         "opsiyonel": bool(file.get("optional")),
                                         "gated": bool(file.get("gated"))}
                                        for file in item["files"]],
                           "bagimliliklar": list(item.get("depends_on", []))})
        return {"gruplar": groups, "aktif": _ctx._active, "sirada": list(_ctx._queue),
                "ilerleme": dict(_ctx._active_progress), "hazir": _ctx._ready,
                **_ctx.storage_status()}


def clear_rebuildable_storage():
    """Remove only regenerable Pod installs, model files, and caches.

    User data and the mounted Flex volume are deliberately outside the deletion
    allowlist. This is a clean reinstall action, not an account reset.
    """
    with _ctx._lock:
        if _ctx._active is not None or _ctx._queue:
            raise RuntimeError("Önce devam eden model indirmelerini tamamlayın veya iptal edin.")
        process = _ctx._comfy_process

    root = _ctx.ROOT.resolve()
    protected = set()
    data_root = _ctx.Path(_ctx.os.environ.get("WEBUI_DATA_ROOT", str(root / "user-data"))).resolve()
    volume = _ctx.Path(_ctx.os.environ.get("POD_VOLUME_MOUNT", "/workspace")).resolve()
    protected.update((data_root, volume))
    model_root = _ctx.Path(_ctx.os.environ.get("POD_MODEL_STORAGE", str(root / "models"))).resolve()

    def overlaps(path, protected_path):
        return (path == protected_path or path.is_relative_to(protected_path)
                or protected_path.is_relative_to(path))

    external_model_root = (model_root if model_root not in (root, _ctx.Path("/"))
                           and not any(overlaps(model_root, item)
                                       for item in (root, *protected))
                           else None)

    candidates = [_ctx.COMFY, root / "comfy-venv", root / "bin", root / "llama",
                  root / "hf-cache", root / "cache", root / "uv-cache",
                  root / "pip-cache"]
    if external_model_root is not None:
        candidates.append(external_model_root)
    for variable in ("HF_HOME", "XDG_CACHE_HOME", "UV_CACHE_DIR"):
        value = _ctx.os.environ.get(variable)
        if value:
            candidates.append(_ctx.Path(value).expanduser())
    candidates.extend(root / name for name in _ctx.LLAMA_RELEASE)
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

    _ctx._stop_process(process)
    _ctx._comfy_process = None
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
            for current, directories, files in _ctx.os.walk(candidate, followlinks=False):
                for name in files:
                    try:
                        child = _ctx.Path(current) / name
                        stat = child.lstat() if child.is_symlink() else child.stat()
                        freed += stat.st_size
                    except OSError:
                        pass
                for name in directories:
                    child = _ctx.Path(current) / name
                    if child.is_symlink():
                        try:
                            freed += child.lstat().st_size
                        except OSError:
                            pass
            _ctx.shutil.rmtree(candidate)
            removed += 1
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise RuntimeError("Yeniden kurulabilir dosyalar temizlenemedi (%s)." %
                               type(exc).__name__) from None

    with _ctx._lock:
        _ctx._state.clear()
        _ctx._active_progress.clear()
        _ctx._last_console_progress.clear()
    return {"removed": removed, "freed_bytes": freed,
            "preserved_user_data": True, "preserved_flex_settings": True}
