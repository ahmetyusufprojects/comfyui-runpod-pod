"""Optional RunPod setup. The gallery does not depend on this worker."""

import hashlib
import json
import os
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import fcntl

ROOT = Path(os.environ["POD_RUNTIME_ROOT"])
COMFY = Path(os.environ["COMFYUI_ROOT"])
MODELS = COMFY / "models"
COMFY_REF = "b0b743566f65daafc423b4fea8a2fbda94b3384a"
CATALOG = json.loads(Path(os.environ["POD_MODEL_CATALOG"]).read_text(encoding="utf-8"))
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
_comfy_process = None
_data_lock = None
_flex = None


def prepare_data(data_root, accounts):
    """Use one writer and require a mounted, explicitly selected data path."""
    global _data_lock, _flex
    mount = Path(os.environ.get("POD_VOLUME_MOUNT", "/workspace")).resolve()
    target = Path(data_root).resolve()
    mounts = Path("/proc/self/mountinfo").read_text().splitlines()
    if not any(line.split()[4] == str(mount) for line in mounts):
        raise RuntimeError("Kalıcı disk bağlı değil.")
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
        if Path(accounts.dosya).exists() or not password:
            raise RuntimeError("Kullanıcı kaydı eksik veya boş. İlk hesap ayrı olarak kurulmalı.")
        accounts.ilk_admin(os.environ.get("WEBUI_BOOTSTRAP_USER", "user"), password)
        checkpoint()


def checkpoint():
    if _flex is not None:
        _flex.save()


def status():
    with _lock:
        groups = []
        for group, item in CATALOG.items():
            state, detail = _state.get(group, ("idle", ""))
            if (state == "ready" and item["engine"] == "comfy"
                    and (_comfy_process is None or _comfy_process.poll() is not None)):
                state, detail = "error", "ComfyUI durdu. Yeniden başlatabilirsiniz."
            groups.append({"id": group, "ad": item["label"], "durum": state, "ayrinti": detail})
        return {"gruplar": groups, "aktif": _active, "sirada": list(_queue)}


def start(group):
    global _thread
    if not isinstance(group, str) or group not in CATALOG:
        raise ValueError("Bilinmeyen model grubu.")
    with _lock:
        if group == _active or group in _queue:
            return
        _queue.append(group)
        _state[group] = ("queued", "")
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
            _queue[index], _queue[destination] = _queue[destination], _queue[index]


def remove(group):
    if not isinstance(group, str) or group not in CATALOG:
        raise ValueError("Bilinmeyen model grubu.")
    with _lock:
        if group in _queue:
            _queue.remove(group)
            _state.pop(group, None)
            return
        if _active is not None or _queue:
            raise RuntimeError("Devam eden model hazırlıkları tamamlanmalı.")
        item = CATALOG[group]
        if item["engine"] == "llama":
            import llm

            llm.durdur()
        elif item["engine"] == "translation":
            import prompt_translation

            prompt_translation.modeli_bosalt()
        shared = {(file["dir"], file["name"])
                  for other, config in CATALOG.items()
                  if other != group and _state.get(other, ("idle",))[0] == "ready"
                  for file in config["files"]}
        for file in item["files"]:
            if (file["dir"], file["name"]) not in shared:
                (MODELS / file["dir"] / file["name"]).unlink(missing_ok=True)
        _state.pop(group, None)


def _progress(group, detail):
    with _lock:
        _state[group] = ("running", detail)
    print(f"[pod-setup] {group}: {detail}", flush=True)


def _run():
    global _active, _thread
    while True:
        with _lock:
            if not _queue:
                _thread = None
                return
            group = _active = _queue.pop(0)
        try:
            item = CATALOG[group]
            if item["engine"] == "comfy":
                _progress(group, "ComfyUI hazırlanıyor")
                _install_comfy()
                _progress(group, "ComfyUI başlatılıyor, model indirmeleri başlıyor")
                _launch_comfy()
            elif item["engine"] == "translation":
                _progress(group, "Çeviri bağımlılıkları hazırlanıyor")
                _install_comfy()
            elif item["engine"] == "llama":
                _progress(group, "llama-server hazırlanıyor")
                _install_llama()
            else:
                raise RuntimeError("Model grubu için bilinmeyen çalışma ortamı.")
            for file in item["files"]:
                _progress(group, "İndiriliyor: " + file["name"])
                _model(file)
            if item["engine"] == "comfy":
                _progress(group, "ComfyUI bağlantısı kontrol ediliyor")
                _wait_comfy()
            result = ("ready", "")
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            detail = str(exc) if type(exc) is RuntimeError else type(exc).__name__
            result = ("error", detail)
        except Exception as exc:
            result = ("error", type(exc).__name__)
        with _lock:
            _state[group] = result
            _active = None


def _command(args, label, *, cwd=None):
    ROOT.mkdir(parents=True, exist_ok=True)
    with (ROOT / "setup.log").open("ab") as log:
        result = subprocess.run(args, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                                timeout=1800)
    if result.returncode:
        raise RuntimeError(f"{label} başarısız oldu.")


def _install_comfy():
    marker = COMFY / ".pod-ready"
    if marker.exists() and marker.read_text() == COMFY_REF:
        return
    COMFY.mkdir(parents=True, exist_ok=True)
    if not (COMFY / ".git").is_dir():
        _command(["git", "init", str(COMFY)], "ComfyUI klasörü")
    _command(["git", "fetch", "--depth", "1", "https://github.com/Comfy-Org/ComfyUI.git",
              COMFY_REF], "ComfyUI indirmesi", cwd=COMFY)
    _command(["git", "checkout", "--detach", "FETCH_HEAD"], "ComfyUI sürümü", cwd=COMFY)
    env = ROOT / "comfy-venv"
    _command([sys.executable, "-m", "venv", str(env)], "ComfyUI Python ortamı")
    pip = [str(env / "bin/python"), "-m", "pip", "install", "--no-cache-dir"]
    _command(pip + ["--index-url", "https://download.pytorch.org/whl/cu128",
                    "torch==2.11.0", "torchvision==0.26.0"], "GPU bağımlılıkları")
    _command(pip + ["-r", str(COMFY / "requirements.txt"), "transformers>=4.50.3,<5",
                    "huggingface-hub<1"], "ComfyUI bağımlılıkları")
    marker.write_text(COMFY_REF)


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
    print(f"[pod-setup] ComfyUI: {vram} MiB VRAM, {' '.join(args)}", flush=True)
    with (ROOT / "comfyui.log").open("ab") as log:
        _comfy_process = subprocess.Popen(
            [str(ROOT / "comfy-venv/bin/python"), str(COMFY / "main.py"),
             "--listen", "127.0.0.1", "--port", "8188", *args],
            cwd=COMFY, stdout=log, stderr=subprocess.STDOUT)


def _wait_comfy():
    for _ in range(180):
        if _comfy_process.poll() is not None:
            raise RuntimeError("ComfyUI başlatılamadı.")
        try:
            with urllib.request.urlopen("http://127.0.0.1:8188/system_stats", timeout=2):
                return
        except OSError:
            time.sleep(1)
    _comfy_process.terminate()
    raise RuntimeError("ComfyUI hazır olma süresini aştı.")


def _install_llama():
    llama = ROOT / "llama"
    marker = llama / ".pod-ready"
    if marker.exists() and marker.read_text() == LLAMA_TAG:
        return
    llama.mkdir(parents=True, exist_ok=True)
    for name, expected in LLAMA_RELEASE.items():
        archive = ROOT / name
        url = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_TAG}/{name}"
        _command(["curl", "--fail", "--location", "--retry", "3", "--continue-at", "-",
                  "--output", str(archive), url], "llama-server paketi")
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
    target = Path(os.environ["LLAMA_SERVER_PATH"])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    target.write_text(f'#!/bin/sh\nexport LD_LIBRARY_PATH="{binary.parent}:{llama}:${{LD_LIBRARY_PATH:-}}"\nexec "{binary}" "$@"\n')
    target.chmod(0o755)
    _command([str(target), "--version"], "llama-server doğrulaması")
    marker.write_text(LLAMA_TAG)


def _model(file):
    from huggingface_hub import hf_hub_download
    from huggingface_hub.errors import HfHubHTTPError

    directory = MODELS / file["dir"]
    target = directory / file["name"]
    if _valid_model(target, file):
        return
    if file.get("source") == "civitai":
        _civitai_model(target, file)
        return
    try:
        source = Path(hf_hub_download(
            repo_id=file["repo"], revision=file.get("revision"), filename=file["path"],
            local_dir=directory / ".incoming", token=os.environ.get("HF_TOKEN") or False))
    except HfHubHTTPError as exc:
        code = getattr(exc.response, "status_code", None)
        if code in (401, 403):
            raise RuntimeError("Model erişim izni gerekiyor. Pod’a HF_TOKEN ekleyin.") from None
        raise RuntimeError(f"Model kaynağı yanıtı: HTTP {code}.") from None
    if not _valid_model(source, file):
        source.unlink(missing_ok=True)
        raise RuntimeError("Model dosyasının bütünlüğü doğrulanamadı.")
    source.replace(target)


def _civitai_model(target, file):
    token = os.environ.get("CIVITAI_TOKEN")
    if not token or token.startswith("{{"):
        raise RuntimeError("Moody Anima için Pod’a CIVITAI_TOKEN ekleyin.")
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".part")
    request = urllib.request.Request(
        f"https://civitai.com/api/download/models/{int(file['version'])}")
    request.add_unredirected_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(request, timeout=90) as response, temp.open("wb") as output:
            import shutil

            shutil.copyfileobj(response, output, 1024 * 1024)
        if not _valid_model(temp, file):
            raise RuntimeError("Moody Anima dosyasının bütünlüğü doğrulanamadı.")
        temp.replace(target)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Civitai indirme yanıtı: HTTP {exc.code}.") from None
    finally:
        temp.unlink(missing_ok=True)


def _valid_model(path, file):
    if not path.is_file() or not path.stat().st_size:
        return False
    expected = file.get("sha256")
    if expected is None:
        return True
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() == expected
