"""Keep the Pod alive while the application is replaced and checked."""
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

root = Path('/opt/runtime/app-update')
web = Path('/opt/webui_flux2')
catalog = Path('/opt/pod/models.json')
stage = root / 'stage'
backup = root / 'backup'
request = root / 'apply.json'
result = root / 'result.json'
child = None
stopping = False

def stop(signum, frame):
    global stopping
    stopping = True
    if child and child.poll() is None:
        child.send_signal(signum)

def launch():
    return subprocess.Popen([sys.executable, str(web / 'server.py')], cwd=str(web))

def healthy(process):
    for _ in range(90):
        if process.poll() is not None:
            return False
        try:
            with urllib.request.urlopen('http://127.0.0.1:8189/api/sunucu-hazir', timeout=1) as response:
                if json.load(response).get('hazir'):
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False

signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
root.mkdir(parents=True, exist_ok=True)
child = launch()
while True:
    code = child.wait()
    if stopping:
        sys.exit(code)
    if not request.is_file():
        sys.exit(code)
    try:
        info = json.loads(request.read_text(encoding='utf-8'))
        request.unlink()
        if backup.exists():
            shutil.rmtree(backup)
        backup.mkdir()
        os.replace(web, backup / 'webui_flux2')
        os.replace(catalog, backup / 'models.json')
        os.replace(stage / 'webui_flux2', web)
        os.replace(stage / 'models.json', catalog)
        child = launch()
        if not healthy(child):
            raise RuntimeError('Yeni sürüm başlatılamadı')
        result.write_text(json.dumps({'status': 'ready', 'commit': info['commit']}), encoding='utf-8')
        shutil.rmtree(backup)
        shutil.rmtree(stage, ignore_errors=True)
        continue
    except Exception as exc:
        if child and child.poll() is None:
            child.terminate()
            try: child.wait(timeout=30)
            except subprocess.TimeoutExpired: child.kill(); child.wait()
        if (backup / 'webui_flux2').exists():
            if web.exists(): shutil.rmtree(web)
            os.replace(backup / 'webui_flux2', web)
        if (backup / 'models.json').exists():
            os.replace(backup / 'models.json', catalog)
        result.write_text(json.dumps({'status': 'error', 'message': str(exc)[:200]}), encoding='utf-8')
        child = launch()
