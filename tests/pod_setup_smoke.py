import http.server
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

print("model download and cancellation smoke checks passed")
