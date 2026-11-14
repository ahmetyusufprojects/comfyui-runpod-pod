"""Exercise a stalled Xet download, HTTP recovery and user cancellation."""
import hashlib
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, '/opt/webui_flux2')
import pod_setup

with tempfile.TemporaryDirectory(prefix='hf-fallback-') as temp:
    root = Path(temp)
    package = root / 'fake' / 'huggingface_hub'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text('''
import os, time
from pathlib import Path
def hf_hub_download(repo_id, revision, filename, local_dir, token):
    if os.environ.get("HF_FAKE_PROGRESS") == "1":
        import hf_xet
        hf_xet.download_files(progress_updater=[lambda amount: None])
    elif os.environ.get("HF_HUB_DISABLE_XET") != "1":
        time.sleep(60)
    target = Path(local_dir) / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"synthetic model")
    return str(target)
''')
    (package / 'errors.py').write_text('class HfHubHTTPError(Exception): pass\n')
    (package.parent / 'hf_xet.py').write_text('''
import time
def download_files(progress_updater):
    for _ in range(6):
        progress_updater[0](1048576)
        time.sleep(0.6)
''')
    os.environ['PYTHONPATH'] = str(package.parent)
    os.environ['POD_HF_STALL_SECONDS'] = '5'
    os.environ.pop('HF_HUB_DISABLE_XET', None)
    pod_setup.ROOT = root / 'runtime'
    pod_setup.MODELS = root / 'models'
    model = {'repo': 'example/model', 'revision': 'main', 'path': 'weights.bin',
             'dir': 'test', 'name': 'weights.bin',
             'sha256': hashlib.sha256(b'synthetic model').hexdigest()}
    start = time.monotonic()
    pod_setup._model(model, threading.Event(), 'synthetic')
    assert 5 <= time.monotonic() - start < 30
    assert (pod_setup.MODELS / 'test' / 'weights.bin').read_bytes() == b'synthetic model'
    assert 'HTTP ile yeniden deneniyor' in (pod_setup.ROOT / 'setup.log').read_text()

    model['name'] = 'cancelled.bin'
    cancel = threading.Event()
    timer = threading.Timer(1, cancel.set)
    timer.start()
    try:
        try:
            pod_setup._model(model, cancel, 'synthetic-cancel')
        except pod_setup.DownloadCancelled:
            pass
        else:
            raise AssertionError('Cancelled transfer continued')
    finally:
        timer.cancel()
    assert not (pod_setup.MODELS / 'test' / 'cancelled.bin').exists()

    os.environ['HF_FAKE_PROGRESS'] = '1'
    model['name'] = 'progress.bin'
    failure = []
    def download():
        try:
            pod_setup._model(model, threading.Event(), 'synthetic-progress')
        except Exception as exc:
            failure.append(exc)
    worker = threading.Thread(target=download)
    worker.start()
    observed = []
    while worker.is_alive():
        with pod_setup._lock:
            progress = dict(pod_setup._active_progress)
        if progress.get('rate_mib_s', 0) and progress.get('downloaded', 0) > 0:
            observed.append(progress)
        time.sleep(.2)
    worker.join()
    assert not failure, failure
    assert observed, 'Xet callbacks did not update transfer speed'

print('Xet stall recovery, cancellation and transfer progress are verified')
