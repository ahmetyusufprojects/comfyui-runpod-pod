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
    if os.environ.get("HF_HUB_DISABLE_XET") != "1":
        time.sleep(60)
    target = Path(local_dir) / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"synthetic model")
    return str(target)
''')
    (package / 'errors.py').write_text('class HfHubHTTPError(Exception): pass\n')
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

print('Xet stall falls back to HTTP and cancellation is respected')
