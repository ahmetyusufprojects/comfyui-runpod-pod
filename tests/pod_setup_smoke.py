import contextlib
import hashlib
import http.server
import builtins
import copy
import io
import json
import os
import pathlib
import re
import sys
import tempfile
import threading
import time
from unittest.mock import patch

sys.path.insert(0, "/opt/webui_flux2")
import pod_setup

assert pod_setup.workflow_enabled("flux3")
assert not pod_setup.workflow_ready("flux3")
assert "flux3" in {group["id"] for group in pod_setup.system_settings()["gruplar"]}

# Civitai page links must preserve the exact model version and work on both
# official hosts, including the mature-content .red domain.
with patch.object(pod_setup, "_civitai_resolve_metadata", return_value={"size": 123}):
    red_source, red_suffix = pod_setup._parse_lora_link(
        "https://civitai.red/models/2657057/moody-gal-momoka-krea2-zimage?modelVersionId=3125325")
    red_direct_source, _ = pod_setup._parse_lora_link(
        "https://civitai.red/api/download/models/3125325")
    com_source, com_suffix = pod_setup._parse_lora_link(
        "https://civitai.com/models/2657057/moody-gal-momoka-krea2-zimage?modelVersionId=3125325")
assert red_suffix == com_suffix == ".safetensors"
assert red_source["version"] == com_source["version"] == 3125325
assert red_source["host"] == "civitai.red" and com_source["host"] == "civitai.com"
assert red_direct_source["host"] == "civitai.red"
assert pod_setup._file_link(dict(red_source, dir="loras", name="fixture.safetensors")) == (
    "https://civitai.red/api/download/models/3125325")
model_selector = re.search(
    r'<select id="runpodImageModel">(.*?)</select>',
    pathlib.Path("/opt/webui_flux2/index.html").read_text(encoding="utf-8"),
    re.DOTALL,
)
assert model_selector and re.search(
    r'<option[^>]*value="flux3"', model_selector.group(1))

try:
    pod_setup.start("flux3-invalid")
except ValueError:
    pass
else:
    raise AssertionError("Unknown model groups must remain rejected")


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
    hf_partial_without_metadata = hf_progress_paths.incomplete_path("active-etag")
    hf_partial_without_metadata.write_bytes(b"in progress")
    assert pod_setup._hf_incomplete_file(
        hf_progress_root, "fixture/weights.bin") == hf_partial_without_metadata
    hf_partial_without_metadata.unlink()
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

    # A rejected resume must retry without Range, then use the GitHub asset API.
    package = scratch / 'llama-package.part'
    package.write_bytes(b'partial')
    expected = hashlib.sha256(b'complete-package').hexdigest()
    release_name = next(iter(pod_setup.LLAMA_RELEASE_ASSETS))
    attempts = []
    def fake_package_command(args, label, **kwargs):
        attempts.append(args)
        if len(attempts) < 3:
            raise RuntimeError('synthetic transfer failure')
        package.write_bytes(b'complete-package')
    with patch.object(pod_setup, '_command', side_effect=fake_package_command):
        pod_setup._download_llama_package(package, release_name, expected, threading.Event())
    assert len(attempts) == 3 and '--continue-at' in attempts[0]
    assert '--continue-at' not in attempts[1] and 'Accept: application/octet-stream' in attempts[2]
    with patch.object(pod_setup, '_command', side_effect=AssertionError('valid package should be reused')):
        pod_setup._download_llama_package(package, release_name, expected, threading.Event())

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
    with patch.object(server, "RUNPOD_MODE", True):
        for retired_model in (server.MODEL_ANIMA, server.MODEL_MOODY_ANIMA):
            rejected = server._is_dogrula({"model": retired_model})
            assert rejected and "Anima kapatıldı" in rejected.get("hata", ""), rejected
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
        assert "1.0 MiB/sn" in speed_text and "kalan 4 sn" in speed_text
        pod_setup._last_console_progress.clear()
        undetected_speed = io.StringIO()
        with contextlib.redirect_stdout(undetected_speed), \
                patch.object(pod_setup.time, "monotonic",
                             side_effect=(200.0, 205.0)):
            pod_setup._progress(
                "synthetic-speed", "İndiriliyor", name="cached.bin",
                downloaded=0, total=10 * 1024 * 1024)
            pod_setup._progress(
                "synthetic-speed", "İndiriliyor", name="cached.bin",
                downloaded=10 * 1024 * 1024, total=10 * 1024 * 1024)
        assert "MiB/sn" not in undetected_speed.getvalue(), undetected_speed.getvalue()
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
    assert turbo["121"]["class_type"] == "VAEDecodeAudio"
    assert turbo["130"]["inputs"]["audio"] == ["121", 0]
    assert normal["124"]["inputs"]["steps"] == 20
    assert normal["126"]["inputs"]["model"] == ["127", 0]
    assert normal["121"]["class_type"] == "VAEDecodeAudio"
    assert normal["130"]["inputs"]["audio"] == ["121", 0]
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
    previous_runpod_mode = server.RUNPOD_MODE
    server.RUNPOD_MODE = True
    try:
        default_context = {"llm_ctx": 8192}
        assert server._llm_istek_baglami(
            default_context, "llm_prompt",
            {"model_prompt_profile": "minimaxh3"}) == 16384
        assert server._llm_istek_baglami(
            default_context, "goruntu",
            {"prompt_destek": True, "is_tipi": "video-uretim"}) == 16384
        assert server._llm_istek_baglami(
            default_context, "goruntu",
            {"prompt_destek": True, "is_tipi": "goruntu-uretim"}) == 8192
        assert server._llm_istek_baglami(
            {"llm_ctx": 32768}, "llm_prompt",
            {"model_prompt_profile": "minimaxh3"}) == 32768
        assert default_context["llm_ctx"] == 8192
    finally:
        server.RUNPOD_MODE = previous_runpod_mode
    from image_graphs import _flux3_layout_elements, build_flux3_prompt

    bbox_caption, bbox_elements = _flux3_layout_elements(
        'A harbor at dawn. [{"id":"obj_1","desc":"red boat",'
        '"bbox":[120,180,640,820]}]', 1024, 1024)
    assert bbox_caption == "A harbor at dawn."
    assert bbox_elements == [{"type":"obj","bbox":[120,180,640,820],"desc":"red boat"}]

    flux3_base = {"prompt": "A harbor at dawn. [{\"id\":\"boat_1\",\"bbox\":[120,180,640,820],\"desc\":\"red boat\"}]",
                  "seed": 7, "width": 1024, "height": 1024,
                  "steps": 16, "cfg": 1.0, "sampler": "euler",
                  "scheduler": "simple", "flux3_bbox_mode": True}
    graph, seed = build_flux3_prompt(flux3_base)
    assert seed == 7 and graph["7"]["inputs"]["steps"] == 16
    assert "CreateBoundingBoxes" in {node["class_type"] for node in graph.values()}
    assert graph["4"]["inputs"]["text"] == ["15", 0]

    flux3_edit = dict(flux3_base, prompt=(
        'Change the boat <boat_1> in <ref_image_0> to red. '
        '[{"id":"boat_1","from":"ref_image_0","src_bbox":[100,120,500,600],'
        '"tgt_bbox":[120,180,640,820],"desc":"red boat"}]'))
    edit_graph, _ = build_flux3_prompt(flux3_edit, "reference.png")
    assert "CreateBoundingBoxes" not in {node["class_type"] for node in edit_graph.values()}
    assert "src_bbox" in edit_graph["4"]["inputs"]["text"]
    assert "tgt_bbox" in edit_graph["4"]["inputs"]["text"]
    assert edit_graph["7"]["inputs"]["positive"][0] == "21"

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
    assert old_settings["auto_download"] is False
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
        })
        saved = pod_setup._system_settings_read()
        assert saved["auto_download"] is True
        assert saved["default_download_order"] == ["zimage"]
        assert saved["model_instances"] == []
        assert "llm_defaults" not in saved
        assert pod_setup.model_options()
        primary_file = pod_setup.WORKFLOWS["z_image"]["diffusion_file"]
        added = pod_setup.create_model_instance({
            "family": "z_image", "name": "Smoke · alternatif",
            "links": {primary_file:
                "https://huggingface.co/fixture/replacement/resolve/main/weights.safetensors"},
        })
        variant = next(item for item in added["model_instances"]
                       if item["name"] == "Smoke · alternatif")
        assert variant["family"] == "z_image" and not variant["ready"]
        assert variant["id"] != "z_image" and variant["group"].startswith("variant_")
        llm_workflow = pod_setup.WORKFLOWS["llm_26b"]
        llm_instance = next(item for item in pod_setup.model_options()
                            if item["id"] == "llm_26b")
        llm_files = {item["name"]: item for item in llm_instance["files"]}
        main_file = llm_workflow["model_file"]
        mmproj_file = llm_workflow["mmproj_file"]
        mtp_file = llm_workflow["mtp_file"]
        assert llm_files[mmproj_file]["optional"]
        assert llm_files[mtp_file]["optional"]
        with patch.object(pod_setup, "_hf_resolve_metadata", return_value={}):
            qwen = pod_setup.create_model_instance({
                "family": "llm_26b", "name": "Qwen · görüntülü",
                "links": {
                    main_file: "https://huggingface.co/DavidAU/Qwen3.8-27B-TURBO-Fable-Cold-Fusion-735-882-Heretic-Uncensored-NEO-CODER-MAX-MTP-GGUF/resolve/main/Qwen3.8-27B-TurboFCFusion-735-882-Here-Uncen-NEO-CODER-MAX-MTP-Q4_K_M.gguf",
                    mmproj_file: "https://huggingface.co/DavidAU/Qwen3.8-27B-TURBO-Fable-Cold-Fusion-735-882-Heretic-Uncensored-NEO-CODER-MAX-MTP-GGUF/resolve/main/mmproj-F32.gguf",
                },
                "exclude_files": [mtp_file],
            })
        qwen_option = next(item for item in qwen["model_instances"]
                           if item["name"] == "Qwen · görüntülü")
        qwen_record = pod_setup.model_instance(qwen_option["id"])
        assert "mtp_file" not in qwen_record["workflow"]
        assert qwen_record["workflow"]["mmproj_file"] != mmproj_file
        assert mtp_file not in {item["name"] for item in qwen_record["files"]}
        with patch.object(pod_setup, "_hf_resolve_metadata", return_value={}):
            text_only = pod_setup.create_model_instance({
                "family": "llm_26b", "name": "Yalnız metin",
                "links": {main_file: "https://huggingface.co/fixture/text/resolve/main/model.gguf"},
                "exclude_files": [mmproj_file, mtp_file],
            })
        text_only_option = next(item for item in text_only["model_instances"]
                                if item["name"] == "Yalnız metin")
        text_only_record = pod_setup.model_instance(text_only_option["id"])
        assert "mmproj_file" not in text_only_record["workflow"]
        assert "mtp_file" not in text_only_record["workflow"]
        assert len(text_only_record["files"]) == 1
        pod_setup.delete_model_instance(qwen_record["id"])
        pod_setup.delete_model_instance(text_only_record["id"])
        renamed = pod_setup.rename_model_instance(variant["id"], "Smoke · yeniden adlandırıldı")
        assert any(item["id"] == variant["id"] and
                   item["name"] == "Smoke · yeniden adlandırıldı"
                   for item in renamed["model_instances"])
        deleted = pod_setup.delete_model_instance(variant["id"])
        assert all(item["id"] != variant["id"] for item in deleted["model_instances"])
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
        # A saved replacement without metadata must not inherit the original digest
        # after restarting the setup worker.
        override_key = "zimage/" + model_file["name"]
        model_file["size"] = 999
        model_file["sha256"] = "b" * 64
        pod_setup._apply_saved_links({"link_overrides": {override_key: {
            "source": "hf", "repo": "fixture/replacement", "revision": "main",
            "path": "replacement.safetensors"}}})
        assert "size" not in model_file and "sha256" not in model_file
        pod_setup.update_model_link("zimage", model_file["name"], "", restore=True)
        assert model_file["repo"] == saved_catalog["zimage"]["files"][0]["repo"]
        # The LLM must validate the replacement, rather than the image's old
        # catalog entry, even when the link changed after startup.
        import hashlib
        import llm
        workflow = pod_setup.WORKFLOWS["llm_26b"]
        replacement = next(f for f in pod_setup.CATALOG[workflow["group"]]["files"]
                           if f["name"] == workflow["model_file"])
        before = dict(replacement)
        model_path = scratch / "llm-test" / "models" / replacement["dir"] / replacement["name"]
        model_path.parent.mkdir(parents=True, exist_ok=True)
        model_path.write_bytes(b"different sized replacement")
        try:
            replacement["size"] = model_path.stat().st_size
            replacement["sha256"] = hashlib.sha256(model_path.read_bytes()).hexdigest()
            with patch.dict(os.environ, {"COMFYUI_ROOT": str(scratch / "llm-test")}), \
                    patch.object(pod_setup, "_ready", True), \
                    patch.object(llm, "MULTIMODAL_ENABLED", False):
                llm.model_ayarla("llm_26b")
                llm.model_dogrula()
                assert llm._RUNPOD_PROFILES["llm_26b"]["model_size"] == model_path.stat().st_size
        finally:
            replacement.clear()
            replacement.update(before)
            llm._refresh_runpod_profiles()
        pod_setup._parse_lora_link = lambda value: (
            {"source": "hf", "repo": "fixture/lora", "revision": "deadbeef",
             "path": "adapter.safetensors"}, ".safetensors")
        lora_settings = pod_setup.add_lora({
            "model": "z_image", "name": "Synthetic LoRA",
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

    # A fresh deployment does not download until the saved setting enables it.
    # An enabled order starts automatically, using isolated synthetic data.
    saved_ready = pod_setup._ready
    saved_flex = pod_setup._flex
    saved_data_lock = pod_setup._data_lock
    saved_settings_path = pod_setup._system_settings_path
    saved_validate_mount = pod_setup.validate_mount
    saved_prefix = os.environ.get("POD_FLEX_PREFIX")
    saved_volume_mount = os.environ.get("POD_VOLUME_MOUNT")
    saved_bootstrap = os.environ.pop("WEBUI_BOOTSTRAP_PASSWORD", None)
    saved_auto_download = os.environ.get("WEBUI_AUTO_DOWNLOAD")
    setup_volume = scratch / "auto-download-volume"
    setup_volume.mkdir()
    settings_dir = setup_volume / "gallery-auto-test"
    settings_dir.mkdir()
    enabled_settings = copy.deepcopy(pod_setup._SYSTEM_SETTINGS_DEFAULT)
    enabled_settings["auto_download"] = True
    (settings_dir / "system-settings.json").write_text(
        json.dumps(enabled_settings), encoding="utf-8")
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
        os.environ["POD_VOLUME_MOUNT"] = str(setup_volume)
        os.environ["WEBUI_AUTO_DOWNLOAD"] = "1"
        pod_setup.validate_mount = lambda: setup_volume
        pod_setup._ready = False
        pod_setup._flex = None
        pod_setup._data_lock = None
        with patch.object(pod_setup, "start",
                          side_effect=lambda group: started_on_boot.append(group)):
            pod_setup.prepare_data(pod_setup.ROOT / "auto-download-user-data",
                                   InstalledSyntheticAccount())
        assert started_on_boot == ["zimage", "llm_26b"], started_on_boot
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
        if saved_volume_mount is None:
            os.environ.pop("POD_VOLUME_MOUNT", None)
        else:
            os.environ["POD_VOLUME_MOUNT"] = saved_volume_mount
        if saved_bootstrap is not None:
            os.environ["WEBUI_BOOTSTRAP_PASSWORD"] = saved_bootstrap
        if saved_auto_download is None:
            os.environ.pop("WEBUI_AUTO_DOWNLOAD", None)
        else:
            os.environ["WEBUI_AUTO_DOWNLOAD"] = saved_auto_download

print("model download, terminal speed logs, cancellation, LoRA/settings, prompts, GPU diagnostics, and log retrieval checks passed")
