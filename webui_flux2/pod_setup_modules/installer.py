"""RunPod setup installer operations.

Shared runtime state and public compatibility API live in pod_setup.
"""

import pod_setup as _ctx

class DownloadCancelled(RuntimeError):
    pass


def start(group):
    if not isinstance(group, str) or group not in _ctx.CATALOG:
        raise ValueError("Bilinmeyen model grubu.")
    if not _ctx.CATALOG[group].get("enabled", True):
        raise ValueError("Bu model grubu kapalı.")
    if not _ctx._ready:
        raise RuntimeError("Pod veri hazırlığı tamamlanmadı; biraz sonra tekrar deneyin.")
    with _ctx._lock:
        for item in _ctx._bagimlilik_sirasi(group):
            if item == _ctx._active:
                if _ctx._active_cancel is not None and _ctx._active_cancel.is_set():
                    raise RuntimeError("Duraklatma veya iptal tamamlanınca devam edebilirsiniz.")
                _ctx._state[item] = ("ready", "")
                continue
            _ctx._pause_requested.discard(item)
            if item in _ctx._queue or _ctx._engine_ready(item):
                _ctx._state[item] = ("ready", "")
                continue
            _ctx._queue.append(item)
            _ctx._state[item] = ("queued", "")
        if _ctx._thread is None or not _ctx._thread.is_alive():
            _ctx._thread = _ctx.threading.Thread(target=_ctx._run, name="pod-setup", daemon=True)
            _ctx._thread.start()


def start_order(groups):
    """Queue the saved install order after restore, skipping prepared groups."""
    if not _ctx._ready:
        return 0
    added = 0
    seen = set()
    with _ctx._lock:
        for group in groups:
            if (not isinstance(group, str) or group not in _ctx.CATALOG
                    or not _ctx.CATALOG[group].get("enabled", True)
                    or group in seen or _ctx._installation_ready(group)):
                continue
            seen.add(group)
            for item in _ctx._bagimlilik_sirasi(group):
                if item == _ctx._active or item in _ctx._queue:
                    continue
                if _ctx._installation_ready(item):
                    continue
                _ctx._queue.append(item)
                _ctx._state[item] = ("queued", "")
                added += 1
        if added and (_ctx._thread is None or not _ctx._thread.is_alive()):
            _ctx._thread = _ctx.threading.Thread(target=_ctx._run, name="pod-setup", daemon=True)
            _ctx._thread.start()
    return added


def move(group, direction):
    if direction not in ("yukari", "asagi"):
        raise ValueError("Bilinmeyen sıra yönü.")
    with _ctx._lock:
        if group not in _ctx._queue:
            raise RuntimeError("Yalnızca bekleyen modellerin sırası değiştirilebilir.")
        index = _ctx._queue.index(group)
        destination = index + (-1 if direction == "yukari" else 1)
        if 0 <= destination < len(_ctx._queue):
            other = _ctx._queue[destination]
            if (group in _ctx._transitive_dependents(other)
                    or other in _ctx._transitive_dependents(group)):
                raise RuntimeError("Bağımlı modellerin sırası birlikte korunmalı.")
            _ctx._queue[index], _ctx._queue[destination] = _ctx._queue[destination], _ctx._queue[index]


def _stop_process(process):
    if process is None or process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=5)
    except _ctx.subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    except OSError:
        pass


def cancel(group):
    if not isinstance(group, str) or group not in _ctx.CATALOG:
        raise ValueError("Bilinmeyen model grubu.")
    process = None
    with _ctx._lock:
        if group != _ctx._active and group not in _ctx._queue and _ctx._state.get(group, ("idle",))[0] != "paused":
            raise RuntimeError("Bu model şu anda indirilmedi veya hazırlanmadı.")
        _ctx._pause_requested.discard(group)
        dependents = _ctx._transitive_dependents(group)
        queued_dependents = dependents.intersection(_ctx._queue)
        _ctx._queue[:] = [queued for queued in _queue if queued not in queued_dependents]
        for queued in queued_dependents:
            _ctx._state[queued] = ("cancelled", "Bağımlı model iptal edildi.")
        if group == _ctx._active:
            if _ctx._active_cancel is None:
                raise RuntimeError("İptal işareti bulunamadı.")
            _ctx._active_cancel.set()
            _ctx._active_progress["stage"] = "İptal ediliyor"
            process = _ctx._active_process
        else:
            _ctx._state[group] = ("cancelled", "Sıradan çıkarıldı.")
            return
    _ctx._stop_process(process)


def pause(group):
    if not isinstance(group, str) or group not in _ctx.CATALOG:
        raise ValueError("Bilinmeyen model grubu.")
    process = None
    with _ctx._lock:
        if group == _ctx._active:
            if _ctx._active_cancel is None:
                raise RuntimeError("Duraklatma işareti bulunamadı.")
            _ctx._pause_requested.add(group)
            _ctx._active_cancel.set()
            _ctx._active_progress["stage"] = "Duraklatılıyor"
            process = _ctx._active_process
        elif group in _ctx._queue:
            _ctx._queue.remove(group)
            _ctx._state[group] = ("paused", "Sıradaki iş duraklatıldı.")
            return
        elif _ctx._state.get(group, ("idle",))[0] == "paused":
            return
        else:
            raise RuntimeError("Yalnızca çalışan veya sıradaki indirme duraklatılabilir.")
    _ctx._stop_process(process)


def remove(group):
    if not isinstance(group, str) or group not in _ctx.CATALOG:
        raise ValueError("Bilinmeyen model grubu.")
    with _ctx._lock:
        if _ctx._active is not None or _ctx._queue:
            raise RuntimeError("Devam eden model hazırlıkları tamamlanmalı.")
        for other in _ctx._transitive_dependents(group) - {group}:
            if (_ctx._state.get(other, ("idle",))[0] in ("ready", "installed")
                    or _ctx._group_files_complete(other)):
                raise RuntimeError("Önce bu modeli kullanan " + _ctx.CATALOG[other]["label"] + " grubunu kaldırın.")
        item = _ctx.CATALOG[group]
        if (_ctx._state.get(group, ("idle",))[0] not in ("ready", "installed", "error", "cancelled")
                and not any((_ctx.MODELS / file["dir"] / file["name"]).exists()
                            for file in item["files"])):
            raise RuntimeError("Bu model kurulu görünmüyor.")
        if item["engine"] == "llama":
            import llm

            llm.durdur()
        elif item["engine"] == "translation":
            import prompt_translation

            prompt_translation.modeli_bosalt()
        shared = {(file["dir"], file["name"])
                  for other, config in _ctx.CATALOG.items() if other != group
                  for file in config["files"]}
        for file in item["files"]:
            if (file["dir"], file["name"]) not in shared:
                target = _ctx.MODELS / file["dir"] / file["name"]
                target.unlink(missing_ok=True)
                _ctx._verification_file(target).unlink(missing_ok=True)
                target.with_suffix(".part").unlink(missing_ok=True)
                if (file.get("source") or "hf") == "hf":
                    (_ctx.MODELS / ".incoming" / file["path"]).unlink(missing_ok=True)
        _ctx._state[group] = ("idle", "")


def _progress(group, stage, *, name=None, downloaded=None, total=None, rate=None):
    now = _ctx.time.monotonic()
    log_line = False
    log_rate = None
    with _ctx._lock:
        _ctx._state[group] = ("running", stage)
        _ctx._active_progress.update({"stage": stage, "file": name,
                                 "downloaded": downloaded, "total": total,
                                 "rate_mib_s": rate})
        progress_key = (group, name or "")
        previous = _ctx._last_console_progress.get(progress_key)
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
            _ctx._last_console_progress[progress_key] = previous
        else:
            log_line = (not isinstance(previous, dict)
                        or previous.get("stage") != stage)
            if log_line:
                _ctx._last_console_progress[progress_key] = {
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
    item = _ctx.CATALOG[group]
    for node in item.get("custom_nodes", []) or []:
        name = node["name"]
        revision = node["revision"]
        destination = _ctx.COMFY / "custom_nodes" / name
        marker = destination / ".pod-revision"
        try:
            installed_revision = marker.read_text(encoding="ascii").strip()
        except OSError:
            installed_revision = ""
        if installed_revision == revision:
            continue
        process = _ctx._comfy_process
        was_running = process is not None and process.poll() is None
        if process is not None and process.poll() is None:
            _ctx._stop_process(process)
            _ctx._comfy_process = None
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not (destination / ".git").is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                _ctx._command(["git", "init", str(destination)],
                         name + " eklentisi hazırlanıyor", cancel=cancel)
                _ctx._command(["git", "-C", str(destination), "remote", "add", "origin", node["repo"]],
                         name + " kaynak adresi", cancel=cancel)
            _ctx._command(["git", "-C", str(destination), "fetch", "--depth", "1",
                      "origin", revision], name + " eklentisi indiriliyor", cancel=cancel)
            _ctx._command(["git", "-C", str(destination), "checkout", "--detach", "FETCH_HEAD"],
                     name + " eklentisi sürümü", cancel=cancel)
            requirements = destination / "requirements.txt"
            if requirements.is_file():
                # Reuse ComfyUI's installed CUDA torch stack; installing an add-on's
                # unpinned torch/torchvision requirements can replace the GPU build.
                safe_requirements = destination / ".pod-requirements.txt"
                lines = requirements.read_text(encoding="utf-8").splitlines()
                safe_requirements.write_text("\n".join(
                    line for line in lines
                    if line.strip() and not _ctx.re.match(
                        r"^(torch|torchvision|numpy)(?:[<=>!~;\[]|$)", line.strip(), _ctx.re.I)
                ) + "\n", encoding="utf-8")
                uv = _ctx.os.environ.get("UV_BIN", "uv")
                _ctx._command([uv, "pip", "install", "--python", str(_ctx.ROOT / "comfy-venv/bin/python"),
                          "-r", str(safe_requirements)],
                         name + " Python paketleri", cancel=cancel, timeout=1800)
            marker.write_text(revision, encoding="ascii")
        except Exception:
            if was_running:
                try:
                    _ctx._launch_comfy()
                except Exception:
                    pass
            raise


def _run():
    while True:
        with _ctx._lock:
            if not _ctx._queue:
                _ctx._thread = None
                return
            group = _ctx._active = _ctx._queue.pop(0)
            _ctx._active_cancel = _ctx.threading.Event()
            _ctx._active_progress.clear()
            cancel_event = _ctx._active_cancel
        try:
            item = _ctx.CATALOG[group]
            missing = [dep for dep in item.get("depends_on", [])
                       if not _ctx._engine_ready(dep)]
            if missing:
                raise _ctx.DownloadCancelled("Bağımlı model hazır değil; bu iş güvenli biçimde durduruldu.")
            if item["engine"] == "comfy":
                _ctx._progress(group, "ComfyUI bağımlılıkları hazırlanıyor")
                _ctx._install_comfy(cancel_event)
                _ctx._install_custom_nodes(group, cancel_event)
                if cancel_event.is_set():
                    raise _ctx.DownloadCancelled("ComfyUI hazırlığı iptal edildi.")
                _ctx._progress(group, "ComfyUI başlatılıyor")
                _ctx._launch_comfy()
            elif item["engine"] == "translation":
                _ctx._progress(group, "Çeviri çalışma ortamı hazırlanıyor")
                _ctx._install_comfy(cancel_event)
            elif item["engine"] == "llama":
                _ctx._progress(group, "llama-server CUDA sürücüsü kontrol ediliyor")
                _ctx._install_llama(cancel_event, group=group)
            else:
                raise RuntimeError("Model grubu için bilinmeyen çalışma ortamı.")
            optional_failures = []
            for file in item["files"]:
                if cancel_event.is_set():
                    raise _ctx.DownloadCancelled("İndirme iptal edildi.")
                _ctx._progress(group, "İndiriliyor", name=file["name"],
                          downloaded=0, total=file.get("size"))
                try:
                    _ctx._model(file, cancel_event, group)
                except _ctx.DownloadCancelled:
                    raise
                except Exception as exc:
                    if not file.get("optional"):
                        raise
                    optional_failures.append(file["name"])
                    with (_ctx.ROOT / "setup.log").open("a", encoding="utf-8") as log:
                        log.write("[optional] %s atlandı (%s)\n" %
                                  (file["name"], type(exc).__name__))
                else:
                    target = _ctx.MODELS / file["dir"] / file["name"]
                    if target.is_file():
                        _ctx._progress(group, "Dosya hazır", name=file["name"],
                                  downloaded=target.stat().st_size,
                                  total=file.get("size"))
            if item["engine"] == "comfy":
                _ctx._progress(group, "ComfyUI bağlantısı kontrol ediliyor")
                _ctx._wait_comfy(cancel_event)
            result = ("ready", "Ana model hazır; MTP isteğe bağlı olarak yüklenemedi."
                      if optional_failures else "")
        except _ctx.DownloadCancelled as exc:
            with _ctx._lock:
                paused = group in _ctx._pause_requested
                _ctx._pause_requested.discard(group)
            result = ("paused", "Duraklatıldı; devam ettiğinde kısmi indirmeden sürer.") \
                if paused else ("cancelled", str(exc))
        except (OSError, RuntimeError, _ctx.subprocess.SubprocessError) as exc:
            detail = str(exc) if type(exc) is RuntimeError else type(exc).__name__
            result = ("error", detail)
        except Exception as exc:
            result = ("error", type(exc).__name__)
        with _ctx._lock:
            _ctx._state[group] = result
            _ctx._active = None
            _ctx._active_cancel = None
            _ctx._active_process = None
            _ctx._active_progress.clear()
            for key in [key for key in _ctx._last_console_progress if key[0] == group]:
                _ctx._last_console_progress.pop(key, None)
        print(f"[pod-setup] {group}: {result[0]}", flush=True)


def _command(args, label, *, cwd=None, cancel=None, timeout=1800, progress=None):
    _ctx.ROOT.mkdir(parents=True, exist_ok=True)
    with (_ctx.ROOT / "setup.log").open("ab") as log:
        process = _ctx.subprocess.Popen(args, cwd=cwd, stdout=log,
                                   stderr=_ctx.subprocess.STDOUT, stdin=_ctx.subprocess.DEVNULL,
                                   start_new_session=True)
        with _ctx._lock:
            _ctx._active_process = process
        start = _ctx.time.monotonic()
        progress_time = start
        progress_size = 0
        progress_logged = 0.0
        try:
            while process.poll() is None:
                if cancel is not None and cancel.is_set():
                    _ctx._stop_process(process)
                    raise _ctx.DownloadCancelled("Hazırlık iptal edildi.")
                now = _ctx.time.monotonic()
                if now - start > timeout:
                    _ctx._stop_process(process)
                    raise RuntimeError(f"{label} zaman aşımına uğradı.")
                if progress:
                    path = _ctx.Path(progress["path"])
                    size = path.stat().st_size if path.is_file() else 0
                    rate = max(0, size - progress_size) / max(now - progress_time, .001) / (1024 * 1024)
                    if now - progress_logged >= 0.8:
                        _ctx._progress(progress["group"], progress.get("stage", "İndiriliyor"),
                                  name=progress["name"], downloaded=size,
                                  total=progress.get("total"), rate=round(rate, 2))
                        progress_logged = now
                    progress_time, progress_size = now, size
                _ctx.time.sleep(0.2)
            if cancel is not None and cancel.is_set():
                raise _ctx.DownloadCancelled("Hazırlık iptal edildi.")
            if process.returncode:
                raise RuntimeError(f"{label} başarısız oldu (çıkış kodu {process.returncode}).")
            with (_ctx.ROOT / "setup.log").open("a", encoding="utf-8") as log:
                log.write(f"[timing] {label}: {_ctx.time.monotonic() - start:.1f} sn\n")
        finally:
            with _ctx._lock:
                if _ctx._active_process is process:
                    _ctx._active_process = None


def _install_comfy(cancel):
    marker = _ctx.COMFY / ".pod-ready"
    if marker.exists() and marker.read_text() == _ctx.COMFY_REF:
        return
    _ctx.COMFY.mkdir(parents=True, exist_ok=True)
    start = _ctx.time.monotonic()

    def stage_done(label, before):
        elapsed = _ctx.time.monotonic() - before
        with (_ctx.ROOT / "setup.log").open("a", encoding="utf-8") as log:
            log.write(f"[timing] {label}: {elapsed:.1f} sn\n")

    before = _ctx.time.monotonic()
    if not (_ctx.COMFY / ".git").is_dir():
        _ctx._command(["git", "init", str(_ctx.COMFY)], "ComfyUI klasörü", cancel=cancel)
    _ctx._command(["git", "fetch", "--depth", "1", "https://github.com/Comfy-Org/ComfyUI.git",
              _ctx.COMFY_REF], "ComfyUI indirmesi", cwd=_ctx.COMFY, cancel=cancel)
    _ctx._command(["git", "checkout", "--detach", "FETCH_HEAD"], "ComfyUI sürümü",
             cwd=_ctx.COMFY, cancel=cancel)
    stage_done("ComfyUI kodu", before)
    env = _ctx.ROOT / "comfy-venv"
    if env.exists():
        _ctx.shutil.rmtree(env)
    uv = _ctx.os.environ.get("UV_BIN", "uv")
    _ctx._command([uv, "venv", "--python", _ctx.sys.executable, str(env)],
             "ComfyUI Python ortamı", cancel=cancel)
    pip = [uv, "pip", "install", "--python", str(env / "bin/python")]
    before = _ctx.time.monotonic()
    try:
        driver = _ctx.subprocess.check_output(
            ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
            text=True, timeout=10).splitlines()[0].strip()
        cuda_wheel = "cu130" if int(driver.split(".")[0]) >= 580 else "cu128"
    except (OSError, ValueError, IndexError, _ctx.subprocess.SubprocessError):
        cuda_wheel = "cu128"
    with (_ctx.ROOT / "setup.log").open("a", encoding="utf-8") as log:
        log.write(f"[comfy] PyTorch sürümü: {cuda_wheel}\n")
    _ctx._command(pip + ["--index-url", f"https://download.pytorch.org/whl/{cuda_wheel}",
                    "torch==2.11.0", "torchvision==0.26.0"],
             "GPU bağımlılıkları", cancel=cancel)
    stage_done("CUDA PyTorch", before)
    before = _ctx.time.monotonic()
    _ctx._command(pip + ["-r", str(_ctx.COMFY / "requirements.txt"), "transformers>=4.50.3,<5",
                    "huggingface-hub<1"], "ComfyUI bağımlılıkları", cancel=cancel)
    stage_done("ComfyUI Python paketleri", before)
    marker.write_text(_ctx.COMFY_REF)
    stage_done("Toplam ComfyUI hazırlığı", start)


def _gpu_memory():
    try:
        info = _ctx.subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            text=True, timeout=10)
        return int(info.splitlines()[0])
    except (OSError, ValueError, IndexError, _ctx.subprocess.SubprocessError):
        raise RuntimeError("NVIDIA GPU belleği tespit edilemedi.") from None


def _launch_comfy():
    if _ctx._comfy_process is not None and _ctx._comfy_process.poll() is None:
        return
    vram = _ctx._gpu_memory()
    args = ["--reserve-vram", "1" if vram < 24576 else "1.5"]
    if vram < 12288:
        args += ["--lowvram"]
    if _ctx.os.environ.get("POD_COMFY_FP16_ACCUMULATION") == "1":
        args += ["--fast", "fp16_accumulation"]
    print(f"[pod-setup] ComfyUI: {vram} MiB VRAM, {' '.join(args)}", flush=True)
    with (_ctx.ROOT / "comfyui.log").open("ab") as log:
        _ctx._comfy_process = _ctx.subprocess.Popen(
            [str(_ctx.ROOT / "comfy-venv/bin/python"), str(_ctx.COMFY / "main.py"),
             "--listen", "127.0.0.1", "--port", "8188", *args],
            cwd=_ctx.COMFY, stdout=log, stderr=_ctx.subprocess.STDOUT)


def _wait_comfy(cancel):
    for _ in range(180):
        if cancel.is_set():
            raise _ctx.DownloadCancelled("ComfyUI hazırlığı iptal edildi.")
        if _ctx._comfy_process.poll() is not None:
            raise RuntimeError("ComfyUI başlatılamadı.")
        try:
            with _ctx.urllib.request.urlopen("http://127.0.0.1:8188/system_stats", timeout=2):
                return
        except OSError:
            _ctx.time.sleep(1)
    _ctx._comfy_process.terminate()
    raise RuntimeError("ComfyUI hazır olma süresini aştı.")


def _llama_cuda_devices(target=None):
    target = _ctx.Path(target or _ctx.os.environ["LLAMA_SERVER_PATH"])
    try:
        result = _ctx.subprocess.run([str(target), "--list-devices"],
                                stdout=_ctx.subprocess.PIPE, stderr=_ctx.subprocess.STDOUT,
                                text=True, timeout=30, check=False)
        output = (result.stdout or "").strip()
        if result.returncode:
            output += ("\n" if output else "") + "[çıkış kodu: %d]" % result.returncode
        return output
    except _ctx.subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", "replace")
        return (str(output).strip() + "\n" if output else "") + "[GPU listeleme 30 saniyede tamamlanmadı]"
    except OSError as exc:
        return "[GPU listeleme komutu başlatılamadı: %s: %s]" % (
            type(exc).__name__, str(exc)[:300])


def _record_llama_cuda_scan(output):
    """Record only the short device probe so GPU setup failures are diagnosable."""
    with (_ctx.ROOT / "setup.log").open("a", encoding="utf-8") as log:
        log.write("[llama] --list-devices çıktısı:\n")
        for line in (output or "[boş çıktı]")[:4000].splitlines():
            log.write("[llama] " + line + "\n")


def _download_llama_package(archive, name, expected, cancel, group="llama"):
    """Resume first; retry from scratch and then use GitHub's asset API."""
    def valid():
        if not archive.is_file() or not archive.stat().st_size:
            return False
        size = archive.stat().st_size
        _ctx._progress(group, "llama-server paketi doğrulanıyor", name=name,
                  downloaded=size, total=_ctx.LLAMA_RELEASE_ASSETS[name])
        with archive.open('rb') as stream:
            return _ctx.hashlib.file_digest(stream, 'sha256').hexdigest() == expected

    if valid():
        return
    direct = f"https://github.com/ggml-org/llama.cpp/releases/download/{_ctx.LLAMA_TAG}/{name}"
    asset = _ctx.LLAMA_RELEASE_ASSETS[name]
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
            _ctx._command(cmd, "llama-server paketi", cancel=cancel,
                     progress={"group": group, "name": name, "path": str(archive),
                               "total": _ctx.LLAMA_RELEASE_ASSETS[name],
                               "stage": "llama-server paketi indiriliyor"})
        except _ctx.DownloadCancelled:
            raise
        except (OSError, RuntimeError) as exc:
            last_error = str(exc)
        else:
            if valid():
                return
            last_error = "İndirilen paketin bütünlüğü doğrulanamadı."
        with (_ctx.ROOT / "setup.log").open("a", encoding="utf-8") as log:
            log.write("[llama] Paket indirmesi tekrar denenecek: %s\n" % last_error)
    raise RuntimeError("llama-server paketi indirilemedi. %s Kurulum logunu kontrol edin." %
                       (last_error or "Bağlantı hatası."))


def _install_llama(cancel, group="llama"):
    llama = _ctx.ROOT / "llama"
    marker = llama / ".pod-ready"
    target = _ctx.Path(_ctx.os.environ["LLAMA_SERVER_PATH"])
    if marker.exists() and marker.read_text() == _ctx.LLAMA_TAG:
        devices = _ctx._llama_cuda_devices(target)
        if _ctx.re.search(r"(?m)^\s*CUDA0:", devices):
            return
        marker.unlink(missing_ok=True)
        _ctx.shutil.rmtree(llama, ignore_errors=True)
        target.unlink(missing_ok=True)
    llama.mkdir(parents=True, exist_ok=True)
    for name, expected in _ctx.LLAMA_RELEASE.items():
        archive = _ctx.ROOT / name
        _ctx._download_llama_package(archive, name, expected, cancel, group=group)
        with _ctx.tarfile.open(archive) as package:
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
    _ctx._command([str(target), "--version"], "llama-server doğrulaması", cancel=cancel)
    devices = _ctx._llama_cuda_devices(target)
    _ctx._record_llama_cuda_scan(devices)
    if not _ctx.re.search(r"(?m)^\s*CUDA0:", devices):
        detail = (devices or "llama-server cihaz listesi döndürmedi.").strip()[-1200:]
        raise RuntimeError("llama-server CUDA0 aygıtını göremedi; CPU ile yavaş çalışması "
                           "engellendi. Tanı: " + detail)
    with (_ctx.ROOT / "setup.log").open("a", encoding="utf-8") as log:
        log.write("[llama] GPU aygıtı doğrulandı: " +
                  next(line.strip() for line in devices.splitlines()
                       if line.lstrip().startswith("CUDA0:")) + "\n")
    marker.write_text(_ctx.LLAMA_TAG)


def _tree_size(directory):
    total = 0
    if not directory.exists():
        return 0
    for root, _, files in _ctx.os.walk(directory):
        for filename in files:
            try:
                total += _ctx.os.path.getsize(_ctx.os.path.join(root, filename))
            except OSError:
                pass
    return total


def _process_io_bytes(pid):
    """Count actual process I/O, including Xet network reads, when available."""
    try:
        values = {}
        for line in _ctx.Path('/proc/%d/io' % pid).read_text().splitlines():
            key, value = line.split(':', 1)
            if key in ('rchar', 'wchar'):
                values[key] = int(value.strip())
        return values['rchar'] + values['wchar']
    except (OSError, KeyError, ValueError):
        return None
