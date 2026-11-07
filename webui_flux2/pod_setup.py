"""Optional RunPod setup. The gallery does not depend on this worker."""

import hashlib
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
from pathlib import Path

import fcntl

ROOT = Path(os.environ["POD_RUNTIME_ROOT"])
COMFY = Path(os.environ["COMFYUI_ROOT"])
MODELS = COMFY / "models"
COMFY_REF = "b0b743566f65daafc423b4fea8a2fbda94b3384a"
_CATALOG_DATA = json.loads(Path(os.environ["POD_MODEL_CATALOG"]).read_text(encoding="utf-8"))
if _CATALOG_DATA.get("schema") != 2:
    raise RuntimeError("Model listesi desteklenmeyen biçimde.")
CATALOG = _CATALOG_DATA.get("groups") or {}
WORKFLOWS = _CATALOG_DATA.get("workflows") or {}
LLAMA_TAG = "b12128"
LLAMA_RELEASE = {
    f"llama-{LLAMA_TAG}-bin-ubuntu-cuda-12.8-x64.tar.gz":
        "dad00f56a9c5eeb0b99fc90350ae462da6985a986e0cbaab5d9cc333d4f41abc",
    f"cudart-llama-{LLAMA_TAG}-bin-ubuntu-cuda-12.8-x64.tar.gz":
        "d4db45f2cb9895ceaeb1f0e4d7885b60d1ec407ea212bce6131e8b714fd49497",
}
_lock = threading.Lock()
_queue = []
_active = None
_thread = None
_state = {}
_active_cancel = None
_active_process = None
_active_progress = {}
_comfy_process = None
_data_lock = None
_flex = None
_ready = False


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
                      file.get("sha256"))
            if target in targets and targets[target] != source:
                raise RuntimeError(f"{group}: ortak dosyanın kaynağı gruplar arasında farklı.")
            targets[target] = source
            if source_type not in ("hf", "civitai", "url"):
                raise RuntimeError(f"{group}: desteklenmeyen indirme kaynağı.")
            if source_type == "civitai":
                if not isinstance(file.get("version"), int):
                    raise RuntimeError(f"{group}: Civitai sürüm numarası eksik.")
            elif source_type == "url":
                parsed = urllib.parse.urlsplit(file.get("url") or "")
                if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                        or parsed.password or not file.get("sha256")):
                    raise RuntimeError(f"{group}: doğrudan indirme bağlantısı HTTPS ve SHA-256 gerektirir.")
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


def prepare_data(data_root, accounts):
    """Use one writer and require a mounted, explicitly selected data path."""
    global _data_lock, _flex, _ready
    mount = validate_mount()
    target = Path(data_root).resolve()
    flex_prefix = os.environ.get("POD_FLEX_PREFIX")
    if flex_prefix:
        from flex_store import FlexStore

        if not flex_prefix.isascii() or not flex_prefix.replace("-", "").isalnum():
            raise RuntimeError("Flex dizini tek bir güvenli addan oluşmalı.")
        if not target.is_relative_to(ROOT) or target == ROOT:
            raise RuntimeError("Flex çalışma dizini geçici diskte olmalı.")
        snapshots = (mount / flex_prefix).resolve()
        if snapshots.parent != mount:
            raise RuntimeError("Flex dizini seçilen bağlama noktasında olmalı.")
        _flex = FlexStore(target, snapshots)
    elif not target.is_relative_to(mount) or target == mount:
        raise RuntimeError("Kullanıcı dizini kalıcı diskin altında olmalı.")
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    _data_lock = (target / ".webui.lock").open("a")
    fcntl.flock(_data_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if _flex is not None and not Path(accounts.dosya).exists():
        _flex.restore()
    password = os.environ.pop("WEBUI_BOOTSTRAP_PASSWORD", "")
    if not accounts.kurulu_mu():
        if Path(accounts.dosya).exists():
            raise RuntimeError("Kullanıcı kaydı boş veya okunamıyor; yeni hesap oluşturulmadı.")
        if not password:
            raise RuntimeError("Hesap kaydı bulunamadı. İlk kurulumda bu Pod'a WEBUI_BOOTSTRAP_PASSWORD Secret'ı ekleyin; mevcut hesap bekliyorsanız Flex seçimini kontrol edin.")
        if password.startswith("{{") and password.endswith("}}"):
            raise RuntimeError("Başlangıç parolası için RunPod Secret çözülemedi; Secret adını kontrol edin.")
        accounts.ilk_admin(os.environ.get("WEBUI_BOOTSTRAP_USER", "user"), password)
        checkpoint()
    _ready = True


def validate_mount():
    mount = Path(os.environ.get("POD_VOLUME_MOUNT", "/workspace")).resolve()
    mounts = Path("/proc/self/mountinfo").read_text().splitlines()
    if not any(line.split()[4] == str(mount) for line in mounts):
        raise RuntimeError("Kalıcı disk bağlı değil.")
    return mount


def checkpoint():
    if _flex is not None:
        _flex.save()


def status():
    with _lock:
        groups = []
        for group, item in CATALOG.items():
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
                           "bagimliliklar": list(item.get("depends_on", []))})
        return {"gruplar": groups, "aktif": _active, "sirada": list(_queue),
                "ilerleme": dict(_active_progress), "hazir": _ready}


def _group_files_complete(group):
    item = CATALOG[group]
    return all(_model_present(MODELS / file["dir"] / file["name"])
               for file in item["files"])


def _group_files_verified(group):
    item = CATALOG[group]
    return all(_model_verified(MODELS / file["dir"] / file["name"], file)
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
    if not _ready:
        raise RuntimeError("Pod veri hazırlığı tamamlanmadı; biraz sonra tekrar deneyin.")
    with _lock:
        for item in _bagimlilik_sirasi(group):
            if item == _active or item in _queue or _engine_ready(item):
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
        if group != _active and group not in _queue:
            raise RuntimeError("Bu model şu anda indirilmedi veya hazırlanmadı.")
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
    with _lock:
        _state[group] = ("running", stage)
        _active_progress.update({"stage": stage, "file": name,
                                 "downloaded": downloaded, "total": total,
                                 "rate_mib_s": rate})
    print(f"[pod-setup] {group}: {stage}", flush=True)


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
                if cancel_event.is_set():
                    raise DownloadCancelled("ComfyUI hazırlığı iptal edildi.")
                _progress(group, "ComfyUI başlatılıyor")
                _launch_comfy()
            elif item["engine"] == "translation":
                _progress(group, "Çeviri çalışma ortamı hazırlanıyor")
                _install_comfy(cancel_event)
            elif item["engine"] == "llama":
                _progress(group, "llama-server CUDA sürücüsü kontrol ediliyor")
                _install_llama(cancel_event)
            else:
                raise RuntimeError("Model grubu için bilinmeyen çalışma ortamı.")
            for file in item["files"]:
                if cancel_event.is_set():
                    raise DownloadCancelled("İndirme iptal edildi.")
                _progress(group, "İndiriliyor", name=file["name"],
                          downloaded=0, total=file.get("size"))
                _model(file, cancel_event, group)
            if item["engine"] == "comfy":
                _progress(group, "ComfyUI bağlantısı kontrol ediliyor")
                _wait_comfy(cancel_event)
            result = ("ready", "")
        except DownloadCancelled as exc:
            result = ("cancelled", str(exc))
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


def _command(args, label, *, cwd=None, cancel=None, timeout=1800):
    global _active_process
    ROOT.mkdir(parents=True, exist_ok=True)
    with (ROOT / "setup.log").open("ab") as log:
        process = subprocess.Popen(args, cwd=cwd, stdout=log,
                                   stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                   start_new_session=True)
        with _lock:
            _active_process = process
        start = time.monotonic()
        try:
            while process.poll() is None:
                if cancel is not None and cancel.is_set():
                    _stop_process(process)
                    raise DownloadCancelled("Hazırlık iptal edildi.")
                if time.monotonic() - start > timeout:
                    _stop_process(process)
                    raise RuntimeError(f"{label} zaman aşımına uğradı.")
                time.sleep(0.2)
            if cancel is not None and cancel.is_set():
                raise DownloadCancelled("Hazırlık iptal edildi.")
            if process.returncode:
                raise RuntimeError(f"{label} başarısız oldu.")
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


def _install_llama(cancel):
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
        url = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_TAG}/{name}"
        _command(["curl", "--fail", "--location", "--retry", "3", "--continue-at", "-",
                  "--output", str(archive), url], "llama-server paketi", cancel=cancel)
        with archive.open("rb") as file:
            if hashlib.file_digest(file, "sha256").hexdigest() != expected:
                archive.unlink()
                raise RuntimeError("llama-server paketinin bütünlüğü doğrulanamadı.")
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


def _model(file, cancel, group):
    global _active_process
    directory = MODELS / file["dir"]
    target = directory / file["name"]
    if _model_present(target):
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
    incoming = directory / ".incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    helper = """import json, os, sys
from huggingface_hub import hf_hub_download
from huggingface_hub.errors import HfHubHTTPError
c=json.loads(sys.stdin.read())
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
    source = incoming / file["path"]
    source.parent.mkdir(parents=True, exist_ok=True)
    previous_time = time.monotonic()
    previous_size = source.stat().st_size if source.exists() else 0
    process = None
    try:
        with root_log.open("ab") as log:
            process = subprocess.Popen([sys.executable, "-c", helper], stdin=subprocess.PIPE,
                                       stdout=log, stderr=subprocess.STDOUT, cwd=ROOT,
                                       start_new_session=True)
            with _lock:
                _active_process = process
            payload = json.dumps({"repo": file["repo"], "revision": file.get("revision"),
                                  "path": file["path"], "local_dir": str(incoming)}).encode("utf-8")
            process.stdin.write(payload)
            process.stdin.close()
            while process.poll() is None:
                if cancel.is_set():
                    _stop_process(process)
                    raise DownloadCancelled("Model indirmesi iptal edildi; kaldığı yer korunup devam ettirilebilir.")
                time.sleep(1)
                now = time.monotonic()
                current_size = source.stat().st_size if source.exists() else previous_size
                rate = max(0, current_size - previous_size) / max(now - previous_time, .001) / (1024 * 1024)
                _progress(group, "İndiriliyor", name=file["name"],
                          downloaded=current_size, total=file.get("size"), rate=round(rate, 2))
                previous_time, previous_size = now, current_size
    finally:
        if process is not None:
            with _lock:
                if _active_process is process:
                    _active_process = None
    if cancel.is_set():
        raise DownloadCancelled("Model indirmesi iptal edildi; kaldığı yer korunup devam ettirilebilir.")
    if process.returncode == 42:
        raise RuntimeError("Model erişim izni gerekiyor. Pod’a HF_TOKEN ekleyin.")
    if process.returncode:
        raise RuntimeError("Hugging Face model indirmesi başarısız oldu; logları kontrol edin.")
    if not _valid_model(source, file):
        source.unlink(missing_ok=True)
        raise RuntimeError("Model dosyasının bütünlüğü doğrulanamadı.")
    target.parent.mkdir(parents=True, exist_ok=True)
    source.replace(target)
    _write_verification(target, file)


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
    try:
        with (ROOT / "setup.log").open("ab") as log:
            process = subprocess.Popen([sys.executable, "-c", helper],
                                       stdin=subprocess.PIPE, stdout=log,
                                       stderr=subprocess.STDOUT, cwd=ROOT,
                                       start_new_session=True)
            with _lock:
                _active_process = process
            payload = json.dumps({"url": f"https://civitai.com/api/download/models/{int(file['version'])}",
                                  "part": str(temp)}).encode("utf-8")
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
    if process.returncode == 43 and _valid_model(temp, file):
        temp.replace(target)
        _write_verification(target, file)
        return
    if process.returncode == 42:
        raise RuntimeError("Civitai model erişimi reddedildi; Pod Secret ayarını kontrol edin.")
    if process.returncode:
        raise RuntimeError("Civitai modeli indirilemedi; kurulum logunu kontrol edin.")
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
