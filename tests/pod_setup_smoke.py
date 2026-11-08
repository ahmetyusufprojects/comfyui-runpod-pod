import contextlib
import http.server
import builtins
import copy
import io
import json
import os
import pathlib
import sys
import tempfile
import threading
import time
from unittest.mock import patch

sys.path.insert(0, "/opt/webui_flux2")
import pod_setup


with tempfile.TemporaryDirectory(prefix="pod-setup-test-") as temporary:
    scratch = pathlib.Path(temporary)
    pod_setup.ROOT = scratch / "runtime"
    pod_setup.MODELS = scratch / "ComfyUI" / "models"

    real_python = sys.executable
    fake_python = scratch / "fake-python"
    fake_python.write_text(
        "#!" + real_python + "\n"
        "import json, pathlib, sys\n"
        "request = json.loads(sys.stdin.read())\n"
        "source = pathlib.Path(request['local_dir']) / request['path']\n"
        "source.parent.mkdir(parents=True, exist_ok=True)\n"
        "source.write_bytes(b'synthetic-model')\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    sys.executable = str(fake_python)
    try:
        hf_file = {
            "repo": "synthetic/repository",
            "revision": "0123456789abcdef",
            "path": "fixture/weights.bin",
            "dir": "checkpoints",
            "name": "fixture.bin",
        }
        pod_setup._model(hf_file, threading.Event(), "synthetic-hf")
        target = pod_setup.MODELS / "checkpoints" / "fixture.bin"
        assert target.read_bytes() == b"synthetic-model"
    finally:
        sys.executable = real_python

    # Hugging Face keeps local_dir transfer bytes in a resumable .incomplete
    # file; the installer must measure that file before it is renamed.
    from huggingface_hub._local_folder import get_local_download_paths
    hf_progress_root = scratch / "hf-progress"
    hf_progress_root.mkdir()
    hf_progress_paths = get_local_download_paths(hf_progress_root, "fixture/weights.bin")
    hf_progress_paths.metadata_path.write_text("0123456789abcdef\nfixture-etag\n0\n",
                                               encoding="utf-8")
    hf_partial = hf_progress_paths.incomplete_path("fixture-etag")
    hf_partial.write_bytes(b"partial model bytes")
    assert pod_setup._hf_incomplete_file(hf_progress_root, "fixture/weights.bin") == hf_partial

    payload_size = 16 * 1024 * 1024

    class SlowHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(payload_size))
            self.end_headers()
            chunk = b"x" * (64 * 1024)
            for _ in range(payload_size // len(chunk)):
                try:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    break
                time.sleep(0.02)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    cancel_target = pod_setup.MODELS / "synthetic" / "cancel.bin"
    cancel_target.parent.mkdir(parents=True, exist_ok=True)
    cancel = threading.Event()
    result = {}

    def download():
        try:
            pod_setup._url_model(
                cancel_target,
                {"url": f"http://127.0.0.1:{server.server_port}/slow",
                 "name": "cancel.bin", "size": payload_size},
                cancel,
                "synthetic-cancel",
            )
        except BaseException as error:
            result["error"] = error

    worker = threading.Thread(target=download)
    worker.start()
    partial = cancel_target.with_suffix(".part")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if partial.is_file() and partial.stat().st_size:
            break
        if not worker.is_alive():
            break
        time.sleep(0.05)
    assert partial.is_file() and partial.stat().st_size, "download did not begin"
    cancel.set()
    worker.join(10)
    server.shutdown()
    server.server_close()
    serving.join(5)
    assert not worker.is_alive(), "cancelled downloader did not stop"
    assert isinstance(result.get("error"), pod_setup.DownloadCancelled), result
    assert partial.stat().st_size < payload_size, "cancel did not stop the transfer"

    # CUDA discovery must preserve the real diagnostic instead of turning every
    # failure into an unexplained empty result.
    fake_llama = scratch / "fake-llama-server"
    fake_llama.write_text(
        "#!" + real_python + "\n"
        "import sys\n"
        "print('CUDA backend unavailable: synthetic missing driver', file=sys.stderr)\n"
        "raise SystemExit(17)\n",
        encoding="utf-8",
    )
    fake_llama.chmod(0o755)
    devices = pod_setup._llama_cuda_devices(fake_llama)
    assert "synthetic missing driver" in devices and "17" in devices, devices

    # Exercise the same bounded log reader used by both WebUI log screens with
    # only temporary, synthetic files. No account or user data is opened.
    log_root = scratch / "runtime-logs"
    log_root.mkdir()
    os.environ["WEBUI_RUNPOD"] = "1"
    os.environ["POD_RUNTIME_ROOT"] = str(log_root)
    os.environ["WEBUI_DATA_ROOT"] = str(scratch / "synthetic-user-data")
    os.environ["COMFYUI_ROOT"] = str(scratch / "ComfyUI")
    os.environ["LLAMA_SERVER_PATH"] = str(fake_llama)
    os.environ["SYNTHETIC_TOKEN"] = "synthetic-secret-value"
    (log_root / "setup.log").write_text(
        "[llama] --list-devices çıktısı:\n"
        "[llama] CUDA backend unavailable: synthetic missing driver\n"
        "[llama] hf_token=synthetic-secret-value\n"
        "[llama] tokenizers==4.57.6\n",
        encoding="utf-8",
    )
    (log_root / "comfyui.log").write_text("synthetic ComfyUI output\n", encoding="utf-8")
    (log_root / "webui.log").write_text("WebUI 8189 portunda hazır.\n", encoding="utf-8")
    import server
    server.llm.LOG_PATH = str(log_root / "llm_server.log")
    llama_logs = "\n".join(server._log_tail("llama", 20))
    comfy_logs = "\n".join(server._log_tail("comfy", 20))
    webui_logs = "\n".join(server._log_tail("webui", 20))
    assert "synthetic missing driver" in llama_logs, llama_logs
    assert "[REDACTED]" in llama_logs and "synthetic-secret-value" not in llama_logs, llama_logs
    assert "tokenizers==4.57.6" in llama_logs, llama_logs
    assert "synthetic ComfyUI output" in comfy_logs, comfy_logs
    assert "WebUI 8189 portunda hazır" in webui_logs, webui_logs

    # Container logs include download size, percentage, speed and ETA without
    # flooding the terminal on every one-second polling iteration.
    saved_progress_log = dict(pod_setup._last_console_progress)
    pod_setup._last_console_progress.clear()
    speed_log = io.StringIO()
    try:
        with contextlib.redirect_stdout(speed_log), \
                patch.object(pod_setup.time, "monotonic",
                             side_effect=(100.0, 102.0, 105.0)):
            for downloaded in (1, 3, 6):
                pod_setup._progress(
                    "synthetic-speed", "İndiriliyor", name="weights.safetensors",
                    downloaded=downloaded * 1024 * 1024,
                    total=10 * 1024 * 1024, rate=2.0)
        speed_text = speed_log.getvalue()
        assert speed_text.count("synthetic-speed") == 2, speed_text
        assert "1.0 MiB / 10.0 MiB (10%)" in speed_text
        assert "6.0 MiB / 10.0 MiB (60%)" in speed_text
        assert "2.0 MiB/sn" in speed_text and "kalan 2 sn" in speed_text
    finally:
        pod_setup._last_console_progress.clear()
        pod_setup._last_console_progress.update(saved_progress_log)

    # Both selectable video modes must build valid graphs from the real catalog.
    pod_setup.status = lambda: {"gruplar": [
        {"id": "video", "durum": "ready"},
        {"id": "video_quality", "durum": "ready"},
    ]}
    turbo = server._video_sablon_yukle("quality")
    normal = server._video_sablon_yukle("normal")
    assert turbo["124"]["inputs"]["steps"] == 8
    assert turbo["126"]["inputs"]["model"] == ["145", 0]
    assert normal["124"]["inputs"]["steps"] == 20
    assert normal["126"]["inputs"]["model"] == ["127", 0]
    assert "145" not in normal
    server.RUNPOD_MODE = False
    windows_normal = server._video_sablon_yukle("normal")
    windows_turbo = server._video_sablon_yukle("quality")
    assert windows_normal["124"]["inputs"]["steps"] == 20
    assert windows_normal["126"]["inputs"]["model"] == ["127", 0]
    assert "145" not in windows_normal
    assert windows_turbo["124"]["inputs"]["steps"] == 8
    assert windows_turbo["126"]["inputs"]["model"] == ["145", 0]

    # A constrained Pod should report its own memory limit, with inactive
    # filesystem cache counted as reclaimable, rather than the host's RAM.
    import psutil
    import types
    original_open = builtins.open
    original_virtual_memory = psutil.virtual_memory
    gib = 1024 ** 3
    fixture = {
        "/sys/fs/cgroup/memory.max": str(48 * gib),
        "/sys/fs/cgroup/memory.current": str(25 * gib),
        "/sys/fs/cgroup/memory.stat": "inactive_file %d\n" % (2 * gib),
    }
    def fixture_open(path, *args, **kwargs):
        if str(path) in fixture:
            return io.StringIO(fixture[str(path)])
        if str(path).startswith("/sys/fs/cgroup/"):
            raise FileNotFoundError(str(path))
        return original_open(path, *args, **kwargs)

    try:
        server.RUNPOD_MODE = True
        psutil.virtual_memory = lambda: types.SimpleNamespace(total=512 * gib, available=400 * gib)
        builtins.open = fixture_open
        constrained = server._ram_bilgi()
        assert constrained["toplam_mb"] == 48 * 1024, constrained
        assert constrained["bos_mb"] == 25 * 1024, constrained
        fixture["/sys/fs/cgroup/memory.max"] = "max"
        unlimited = server._ram_bilgi()
        assert unlimited["toplam_mb"] == 512 * 1024, unlimited
    finally:
        builtins.open = original_open
        psutil.virtual_memory = original_virtual_memory

    # Model switching keeps llama-server when live free VRAM covers the image
    # model plus headroom, and stops it when the measured space is insufficient.
    started_images = []
    stopped_llms = []
    with patch.object(server, "RUNPOD_MODE", True), \
            patch.object(server.llm, "durum", return_value={"canli": True}), \
            patch.object(server, "_isi_calistir",
                         side_effect=lambda job: started_images.append(job)), \
            patch.object(server, "_llm_kapat_ve_bekle",
                         side_effect=lambda: stopped_llms.append(True) or True):
        for model, free_mb, keep_llm in (
                ("flux3", 13700, True), ("flux3", 13699, False),
                ("krea2", 23700, True), ("krea2", 23699, False)):
            job = {"iptal": False, "tur": "gorsel",
                   "istek": {"model": model}}
            with patch.object(server, "_vram_bos_mb", return_value=free_mb):
                server._gpu_isini_calistir(job)
            assert bool(job.get("_llama_bellekte_kaldi")) is keep_llm, job
            assert bool(job.get("_llama_canliydi"))
        assert len(stopped_llms) == 2, stopped_llms
        assert len(started_images) == 4, started_images

    # Queue pause/resume/cancel transitions use only an enabled group and a
    # dummy worker handle; no real model job or user data is touched.
    saved_ready = pod_setup._ready
    saved_thread = pod_setup._thread
    saved_active = pod_setup._active
    saved_queue = list(pod_setup._queue)
    saved_state = dict(pod_setup._state)
    class DummyThread:
        def is_alive(self):
            return True
    try:
        pod_setup._ready = True
        pod_setup._thread = DummyThread()
        pod_setup._active = None
        pod_setup._queue.clear()
        pod_setup.start("translation")
        assert pod_setup._queue == ["translation"]
        pod_setup.pause("translation")
        assert not pod_setup._queue
        assert pod_setup._state["translation"][0] == "paused"
        pod_setup.start("translation")
        assert pod_setup._queue == ["translation"]
        pod_setup.cancel("translation")
        assert not pod_setup._queue
        assert pod_setup._state["translation"][0] == "cancelled"
    finally:
        pod_setup._ready = saved_ready
        pod_setup._thread = saved_thread
        pod_setup._active = saved_active
        pod_setup._queue[:] = saved_queue
        pod_setup._state.clear()
        pod_setup._state.update(saved_state)

    # The selected model prompt is loaded beside the fixed application JSON
    # contract; bbox parsing receives the official four-coordinate array.
    profile_prompt = server.llm._uretim_sistem_mesaji(
        "fikir", "en", "orta", model_prompt_profile="flux3", bbox_mode=True)
    assert "element table" in profile_prompt and "positive_tr" in profile_prompt
    for profile, marker in (("krea2", "Character-Centric Image Prompt Architect"),
                            ("minimaxh3", "Full-Reference Mode Rewrite Output Format Guide")):
        profile_prompt = server.llm._uretim_sistem_mesaji(
            "video" if profile == "minimaxh3" else "fikir",
            "en", "orta", model_prompt_profile=profile)
        assert marker in profile_prompt
        for field in ("positive", "positive_tr", "negative", "caption_tr", "tags"):
            assert field in profile_prompt, (profile, field)
    from image_graphs import _flux3_layout_elements

    bbox_caption, bbox_elements = _flux3_layout_elements(
        'A harbor at dawn. [{"id":"obj_1","desc":"red boat",'
        '"bbox":[120,180,640,820]}]', 1024, 1024)
    assert bbox_caption == "A harbor at dawn."
    assert bbox_elements == [{"type":"obj","bbox":[120,180,640,820],"desc":"red boat"}]

    # Exercise universal settings, model-link reset, and editable LoRA records
    # using an isolated JSON file plus temporary model paths.
    saved_settings_path = pod_setup._system_settings_path
    saved_catalog = copy.deepcopy(pod_setup.CATALOG)
    original_parse_model_link = pod_setup._parse_model_link
    original_parse_lora_link = pod_setup._parse_lora_link
    # Global Volume does not promise atomic rename. Verify versioned immutable
    # settings can be written, reread after a restart-style rebind, and recover
    # the previous valid version if the newest object is incomplete.
    legacy_settings = scratch / "global-volume" / "system-settings.json"
    legacy_settings.parent.mkdir()
    old_settings = copy.deepcopy(pod_setup._SYSTEM_SETTINGS_DEFAULT)
    assert old_settings["auto_download"] is True
    old_settings["auto_download"] = False
    legacy_settings.write_text(json.dumps(old_settings), encoding="utf-8")
    pod_setup._system_settings_path = legacy_settings
    assert pod_setup._system_settings_read()["auto_download"] is False
    with patch.object(pathlib.Path, "replace",
                      side_effect=AssertionError("Global Volume rename must not be used")):
        pod_setup.update_system_settings({"auto_download": True})
        pod_setup.update_system_settings({
            "default_download_order": ["llm_26b", "zimage"]})
    versions = pod_setup._system_settings_versions()
    assert len(versions) == 2, versions
    pod_setup._system_settings_path = pathlib.Path(str(legacy_settings))
    persisted = pod_setup._system_settings_read()
    assert persisted["auto_download"] is True
    assert persisted["default_download_order"] == ["llm_26b", "zimage"]
    versions[0][2].write_text("{incomplete", encoding="utf-8")
    recovered = pod_setup._system_settings_read()
    assert recovered["auto_download"] is True
    assert recovered["default_download_order"] != ["llm_26b", "zimage"]
    try:
        pod_setup._system_settings_path = scratch / "universal-settings.json"
        pod_setup.update_system_settings({
            "auto_download": True,
            "default_download_order": ["zimage"],
            "llm_defaults": {"image": {"flux3": "llm_12b"}, "chat": "llm_26b"},
        })
        saved = pod_setup._system_settings_read()
        assert saved["auto_download"] is True
        assert saved["default_download_order"] == ["zimage"]
        assert saved["llm_defaults"]["image"]["flux3"] == "llm_12b"
        model_file = pod_setup.CATALOG["zimage"]["files"][0]
        pod_setup._parse_model_link = lambda value: (
            {"source": "hf", "repo": "fixture/replacement", "revision": "deadbeef",
             "path": "weights.safetensors"},
            {"size": 123, "sha256": "a" * 64},
        )
        pod_setup.update_model_link("zimage", model_file["name"],
                                    "https://huggingface.co/fixture/replacement/resolve/main/weights.safetensors")
        assert model_file["repo"] == "fixture/replacement"
        assert model_file["size"] == 123
        pod_setup.update_model_link("zimage", model_file["name"], "", restore=True)
        assert model_file["repo"] == saved_catalog["zimage"]["files"][0]["repo"]
        pod_setup._parse_lora_link = lambda value: (
            {"source": "hf", "repo": "fixture/lora", "revision": "deadbeef",
             "path": "adapter.safetensors"}, ".safetensors")
        lora_settings = pod_setup.add_lora({
            "model": "flux3", "name": "Synthetic LoRA",
            "link": "https://huggingface.co/fixture/lora/resolve/main/adapter.safetensors"})
        custom = next(item for item in lora_settings["ayarlar"]["loras"]
                      if item["name"] == "Synthetic LoRA")
        pod_setup.configure_lora(custom["id"], {
            "enabled": True, "default_selected": True,
            "auto_download": True, "weight": 1.25})
        changed = pod_setup._system_settings_read()
        custom = next(item for item in changed["loras"]
                      if item["name"] == "Synthetic LoRA")
        assert custom["weight"] == 1.25 and custom["group"] in changed["default_download_order"]
        pod_setup.delete_lora(custom["id"])
        assert all(item["name"] != "Synthetic LoRA"
                   for item in pod_setup._system_settings_read()["loras"])
    finally:
        pod_setup._system_settings_path = saved_settings_path
        pod_setup._parse_model_link = original_parse_model_link
        pod_setup._parse_lora_link = original_parse_lora_link
        pod_setup.CATALOG.clear()
        pod_setup.CATALOG.update(saved_catalog)

    # A fresh deployment starts the saved download order automatically, using
    # only an isolated synthetic account record and volume prefix.
    saved_ready = pod_setup._ready
    saved_flex = pod_setup._flex
    saved_data_lock = pod_setup._data_lock
    saved_settings_path = pod_setup._system_settings_path
    saved_validate_mount = pod_setup.validate_mount
    saved_prefix = os.environ.get("POD_FLEX_PREFIX")
    saved_bootstrap = os.environ.pop("WEBUI_BOOTSTRAP_PASSWORD", None)
    setup_volume = scratch / "auto-download-volume"
    setup_volume.mkdir()
    account_file = scratch / "auto-download-account.json"
    account_file.write_text("{}", encoding="utf-8")

    class InstalledSyntheticAccount:
        dosya = str(account_file)

        @staticmethod
        def kurulu_mu():
            return True

    started_on_boot = []
    try:
        os.environ["POD_FLEX_PREFIX"] = "gallery-auto-test"
        pod_setup.validate_mount = lambda: setup_volume
        pod_setup._ready = False
        pod_setup._flex = None
        pod_setup._data_lock = None
        with patch.object(pod_setup, "start",
                          side_effect=lambda group: started_on_boot.append(group)):
            pod_setup.prepare_data(pod_setup.ROOT / "auto-download-user-data",
                                   InstalledSyntheticAccount())
        assert started_on_boot == ["zimage", "llama", "llm_12b", "llm_26b"], started_on_boot
    finally:
        if pod_setup._data_lock is not None and pod_setup._data_lock is not saved_data_lock:
            pod_setup._data_lock.close()
        pod_setup._ready = saved_ready
        pod_setup._flex = saved_flex
        pod_setup._data_lock = saved_data_lock
        pod_setup._system_settings_path = saved_settings_path
        pod_setup.validate_mount = saved_validate_mount
        if saved_prefix is None:
            os.environ.pop("POD_FLEX_PREFIX", None)
        else:
            os.environ["POD_FLEX_PREFIX"] = saved_prefix
        if saved_bootstrap is not None:
            os.environ["WEBUI_BOOTSTRAP_PASSWORD"] = saved_bootstrap

print("model download, terminal speed logs, cancellation, LoRA/settings, prompts, GPU diagnostics, and log retrieval checks passed")
