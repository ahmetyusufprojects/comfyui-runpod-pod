"""Exercise supervisor replacement and rollback without a GPU or user data."""
import json
import io
import os
import shutil
import signal
import time
import tarfile
import urllib.request
from pathlib import Path

root = Path('/opt/runtime/app-update')
web = Path('/opt/webui_flux2')
model = Path('/opt/runtime/ComfyUI/models/synthetic-update-test.bin')
model.parent.mkdir(parents=True, exist_ok=True)
model.write_bytes(b'unchanged model')

import sys
sys.path.insert(0, '/opt/webui_flux2')
import app_update
archive = io.BytesIO()
with tarfile.open(fileobj=archive, mode='w:gz') as tar:
    tar.add(web, arcname='repo/webui_flux2', filter=lambda m: None if '__pycache__' in m.name else m)
    tar.add('/opt/pod/models.json', arcname='repo/docker/models.json')
    tar.add('/opt/pod/base.Dockerfile', arcname='repo/docker/Dockerfile.gallery')
app_update._fetch = lambda url, limit: (json.dumps({'workflow_runs': [{'head_sha': 'a' * 40}]}).encode()
                                       if 'api.github.com' in url else archive.getvalue())
assert app_update.prepare() == 'a' * 40
assert (root / 'stage/webui_flux2/server.py').is_file()

def pid():
    with urllib.request.urlopen('http://127.0.0.1:8189/api/sunucu-hazir', timeout=2) as response:
        data = json.load(response)
        return data['pid'] if data.get('hazir') else None

def update(broken=False):
    before = pid()
    stage = root / 'stage'
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    shutil.copytree(web, stage / 'webui_flux2', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copy2('/opt/pod/models.json', stage / 'models.json')
    if broken:
        (stage / 'webui_flux2/server.py').write_text('raise SystemExit(2)\n', encoding='utf-8')
    (root / 'result.json').unlink(missing_ok=True)
    (root / 'apply.json').write_text(json.dumps({'commit': 'a' * 40}), encoding='utf-8')
    os.kill(before, signal.SIGTERM)
    for _ in range(120):
        try:
            state = json.loads((root / 'result.json').read_text(encoding='utf-8'))
            after = pid()
            if after and after != before and state['status'] == ('error' if broken else 'ready'):
                assert model.read_bytes() == b'unchanged model'
                return
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(1)
    raise AssertionError('update/rollback did not recover')

update()
update(broken=True)
print('Application update and rollback preserve model files')
