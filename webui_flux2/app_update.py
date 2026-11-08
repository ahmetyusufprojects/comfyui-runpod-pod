"""Stage only source files from the latest successfully built gallery image."""
import io
import json
import os
import py_compile
import re
import shutil
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(os.environ.get('POD_RUNTIME_ROOT', '/opt/runtime')) / 'app-update'
REPO = 'ahmetyusufprojects/comfyui-runpod-pod'
BRANCH = 'runpod%2Fgallery-first'

def _fetch(url, limit):
    req = urllib.request.Request(url, headers={'User-Agent': 'RunPod-WebUI-Updater',
                                              'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(req, timeout=45) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise RuntimeError('Güncelleme paketi çok büyük.')
    return data

def prepare():
    api = f'https://api.github.com/repos/{REPO}/actions/workflows/gallery-image.yml/runs?branch={BRANCH}&status=success&per_page=1'
    runs = json.loads(_fetch(api, 1024 * 1024)).get('workflow_runs') or []
    commit = runs[0].get('head_sha') if runs else ''
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise RuntimeError('Başarılı bir imaj sürümü bulunamadı.')
    archive = _fetch(f'https://codeload.github.com/{REPO}/tar.gz/{commit}', 32 * 1024 * 1024)
    ROOT.mkdir(parents=True, exist_ok=True)
    stage = ROOT / 'stage'
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as tar:
        members = {}
        for member in tar:
            parts = Path(member.name).parts
            if len(parts) < 2 or not member.isfile():
                continue
            name = '/'.join(parts[1:])
            if name == 'docker/Dockerfile.gallery' or name == 'docker/models.json' or name.startswith('webui_flux2/'):
                if '..' in parts or member.size > 4 * 1024 * 1024:
                    raise RuntimeError('Güncelleme paketinde geçersiz dosya var.')
                members[name] = tar.extractfile(member).read()
    baseline = Path('/opt/pod/base.Dockerfile').read_bytes()
    if members.get('docker/Dockerfile.gallery') != baseline:
        raise RuntimeError('Bu sürüm temel imaj değişikliği içeriyor; yeni Pod açılması gerekiyor.')
    required = ['arsiv.py', 'auth.py', 'comfy_client.py', 'flex_store.py', 'image_graphs.py',
                'image_utils.py', 'kasa.py', 'llm.py', 'pod_setup.py', 'app_update.py',
                'prompt_store.py', 'prompt_translation.py', 'server.py', 'index.html',
                'setup.html', 'video_r2v_api.json', 'static/app.js', 'static/app.css',
                'static/setup.js', 'static/setup.css', 'model_prompts/flux.json',
                'model_prompts/krea2.txt', 'model_prompts/minimaxh3.txt', 'model_prompts/z_image.txt']
    if any('webui_flux2/' + name not in members for name in required) or 'docker/models.json' not in members:
        raise RuntimeError('Güncelleme paketi eksik.')
    for relative in required:
        data = members['webui_flux2/' + relative]
        dest = stage / 'webui_flux2' / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        if dest.suffix == '.py':
            py_compile.compile(str(dest), doraise=True)
    json.loads(members['docker/models.json'])
    (stage / 'models.json').write_bytes(members['docker/models.json'])
    return commit
