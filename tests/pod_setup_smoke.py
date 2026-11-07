import http.server
import builtins
import copy
import io
import os
import pathlib
import sys
import tempfile
import threading
import time

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

print("model download, pause/resume/cancellation, LoRA/settings, model prompts, GPU diagnostics, and log retrieval checks passed")
