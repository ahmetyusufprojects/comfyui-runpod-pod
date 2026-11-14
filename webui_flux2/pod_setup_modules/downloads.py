"""RunPod setup downloads operations.

Shared runtime state and public compatibility API live in pod_setup.
"""

import pod_setup as _ctx

def _model(file, cancel, group):
    directory = _ctx.MODELS / file["dir"]
    target = directory / file["name"]
    if target.is_file() and target.stat().st_size:
        _ctx._progress(group, "Dosya doğrulanıyor", name=file["name"],
                  downloaded=target.stat().st_size, total=file.get("size"))
        if _ctx._valid_model(target, file):
            _ctx._write_verification(target, file)
            return
        target.unlink(missing_ok=True)
        _ctx._verification_file(target).unlink(missing_ok=True)
    source_type = file.get("source") or "hf"
    if source_type == "civitai":
        _ctx._civitai_model(target, file, cancel, group)
        return
    if source_type == "url":
        _ctx._url_model(target, file, cancel, group)
        return
    xet_dir = directory / ".incoming"
    http_dir = directory / ".incoming-http"
    helper = """import json, os, sys
from huggingface_hub import hf_hub_download
from huggingface_hub.errors import HfHubHTTPError
c=json.loads(sys.stdin.read())
# Xet writes blocks out of order; the partial file length is not transfer progress.
# Capture the byte callbacks emitted by hf_xet instead.
try:
    import hf_xet, threading, time
    original_download = hf_xet.download_files
    callback_lock = threading.Lock()
    def measured_download(*args, **kwargs):
        callbacks = kwargs.get('progress_updater') or []
        transferred = [0.0]
        last_write = [0.0]
        def wrap(callback):
            def update(amount):
                callback(amount)
                with callback_lock:
                    transferred[0] += max(0, amount)
                    now = time.monotonic()
                    if now - last_write[0] >= 0.3:
                        tmp = c['progress'] + '.tmp'
                        with open(tmp, 'w') as f:
                            f.write(str(int(transferred[0])))
                        os.replace(tmp, c['progress'])
                        last_write[0] = now
            return update
        kwargs['progress_updater'] = [wrap(cb) for cb in callbacks]
        return original_download(*args, **kwargs)
    hf_xet.download_files = measured_download
except ImportError:
    pass
try:
    hf_hub_download(repo_id=c['repo'], revision=c.get('revision'),
                    filename=c['path'], local_dir=c['local_dir'],
                    token=os.environ.get('HF_TOKEN') or False)
except HfHubHTTPError as e:
    print('HTTP ' + str(getattr(getattr(e, 'response', None), 'status_code', 0)),
          file=sys.stderr, flush=True)
    sys.exit(42 if getattr(getattr(e, 'response', None), 'status_code', 0) in (401, 403) else 1)
"""
    root_log = _ctx.ROOT / "setup.log"
    _ctx.ROOT.mkdir(parents=True, exist_ok=True)
    try:
        stall_limit = max(5, min(1800, int(_ctx.os.environ.get("POD_HF_STALL_SECONDS", "120"))))
    except ValueError:
        stall_limit = 120
    http_requested = _ctx.os.environ.get("HF_HUB_DISABLE_XET", "").lower() in ("1", "true", "yes", "on")
    http_resume = (_ctx._hf_incomplete_file(http_dir, file["path"])
                   or (http_dir / file["path"]).is_file())
    modes = ["http"] if http_requested or http_resume else ["xet", "http"]
    for mode in modes:
        if cancel.is_set():
            raise _ctx.DownloadCancelled("Model indirmesi iptal edildi; kaldığı yer korunup devam ettirilebilir.")
        incoming = http_dir if mode == "http" else xet_dir
        source = incoming / file["path"]
        source.parent.mkdir(parents=True, exist_ok=True)
        previous_time = _ctx.time.monotonic()
        last_activity = previous_time
        partial = _ctx._hf_incomplete_file(incoming, file["path"])
        progress_file = partial if partial and partial.exists() else source
        previous_size = progress_file.stat().st_size if progress_file.exists() else 0
        previous_progress_path = partial
        process = None
        stalled = False
        xet_progress = incoming / ".xet-progress"
        xet_progress.unlink(missing_ok=True)
        env = _ctx.os.environ.copy()
        if mode == "http":
            env["HF_HUB_DISABLE_XET"] = "1"
        try:
            with root_log.open("ab") as log:
                process = _ctx.subprocess.Popen([_ctx.sys.executable, "-c", helper], stdin=_ctx.subprocess.PIPE,
                                           stdout=log, stderr=_ctx.subprocess.STDOUT, cwd=_ctx.ROOT,
                                           env=env, start_new_session=True)
                with _ctx._lock:
                    _ctx._active_process = process
                payload = _ctx.json.dumps({"repo": file["repo"], "revision": file.get("revision"),
                                      "path": file["path"], "local_dir": str(incoming),
                                      "progress": str(xet_progress)}).encode("utf-8")
                process.stdin.write(payload)
                process.stdin.close()
                activity_io = _ctx._process_io_bytes(process.pid)
                while process.poll() is None:
                    if cancel.is_set():
                        _ctx._stop_process(process)
                        raise _ctx.DownloadCancelled("Model indirmesi iptal edildi; kaldığı yer korunup devam ettirilebilir.")
                    _ctx.time.sleep(1)
                    now = _ctx.time.monotonic()
                    partial = _ctx._hf_incomplete_file(incoming, file["path"])
                    progress_file = partial if partial and partial.exists() else source
                    try:
                        current_size = progress_file.stat().st_size
                    except OSError:
                        current_size = previous_size
                    if mode == "xet":
                        try:
                            current_size = int(xet_progress.read_text())
                        except (OSError, ValueError):
                            # An Xet sparse file's length can jump ahead of actual progress.
                            current_size = 0
                    current_io = _ctx._process_io_bytes(process.pid)
                    if (current_size != previous_size or
                            (current_io is not None and activity_io is not None
                             and current_io - activity_io >= 65536)):
                        last_activity = now
                        activity_io = current_io
                    elif activity_io is None and current_io is not None:
                        activity_io = current_io
                    stable_partial = (mode == "xet" and xet_progress.exists()) or bool(
                        mode == "http" and partial and previous_progress_path == progress_file)
                    rate = (max(0, current_size - previous_size)
                            / max(now - previous_time, .001) / (1024 * 1024)
                            if stable_partial else None)
                    _ctx._progress(group, "İndiriliyor", name=file["name"],
                              downloaded=current_size, total=file.get("size"),
                              rate=round(rate, 2) if rate is not None else None)
                    previous_time, previous_size = now, current_size
                    previous_progress_path = partial
                    if now - last_activity >= stall_limit:
                        stalled = True
                        _ctx._stop_process(process)
                        break
        finally:
            if process is not None:
                with _ctx._lock:
                    if _ctx._active_process is process:
                        _ctx._active_process = None
        xet_progress.unlink(missing_ok=True)
        if cancel.is_set():
            raise _ctx.DownloadCancelled("Model indirmesi iptal edildi; kaldığı yer korunup devam ettirilebilir.")
        if process.returncode == 42:
            raise RuntimeError("Model erişim izni gerekiyor. Pod’a HF_TOKEN ekleyin.")
        if not stalled and process.returncode == 0:
            _ctx._progress(group, "Dosya doğrulanıyor", name=file["name"],
                      downloaded=source.stat().st_size if source.is_file() else 0,
                      total=file.get("size"))
            if _ctx._valid_model(source, file):
                target.parent.mkdir(parents=True, exist_ok=True)
                source.replace(target)
                _ctx._write_verification(target, file)
                return
            source.unlink(missing_ok=True)
        if mode == "xet":
            with root_log.open("a", encoding="utf-8") as log:
                log.write("[pod-setup] Xet indirmesi ilerlemedi veya başarısız oldu; HTTP ile yeniden deneniyor.\n")
            # Xet's partial may contain out-of-order blocks; never resume it as HTTP.
            partial = _ctx._hf_incomplete_file(xet_dir, file["path"])
            if partial:
                partial.unlink(missing_ok=True)
            continue
        raise RuntimeError("Hugging Face indirmesi durdu veya doğrulanamadı; logları kontrol edin.")


def _civitai_model(target, file, cancel, group):
    token = _ctx.os.environ.get("CIVITAI_TOKEN")
    if not token or token.startswith("{{"):
        raise RuntimeError("Moody Anima için Pod’a CIVITAI_TOKEN ekleyin.")
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".part")
    helper = """import json, os, sys, urllib.error, urllib.request
c=json.loads(sys.stdin.read()); token=os.environ.get('CIVITAI_TOKEN')
if not token or token.startswith('{{'):
    print('missing-token', flush=True); sys.exit(42)
offset=os.path.getsize(c['part']) if os.path.exists(c['part']) else 0
headers={'User-Agent':'Mozilla/5.0','Accept-Encoding':'identity'}
if offset: headers['Range']='bytes=%d-' % offset
request=urllib.request.Request(c['url'], headers=headers)
request.add_unredirected_header('Authorization', 'Bearer '+token)
try:
    with urllib.request.urlopen(request, timeout=30) as response:
        append=bool(offset and response.status==206)
        with open(c['part'], 'ab' if append else 'wb') as output:
            while True:
                chunk=response.read(1024*1024)
                if not chunk: break
                output.write(chunk)
except urllib.error.HTTPError as error:
    print('HTTP %d' % error.code, flush=True)
    if error.code == 416: sys.exit(43)
    sys.exit(42 if error.code in (401,403) else 1)
except Exception:
    print('download failed', flush=True); sys.exit(1)
"""
    previous_time = _ctx.time.monotonic()
    previous_size = temp.stat().st_size if temp.exists() else 0
    process = None
    host = file.get("host", "civitai.com")
    if host not in _ctx._CIVITAI_HOSTS:
        host = "civitai.com"
    if host.startswith("www."):
        host = host[4:]
    payload = _ctx.json.dumps({"url": f"https://{host}/api/download/models/{int(file['version'])}",
                          "part": str(temp)}).encode("utf-8")
    try:
        with (_ctx.ROOT / "setup.log").open("ab") as log:
            process = _ctx.subprocess.Popen([_ctx.sys.executable, "-c", helper],
                                       stdin=_ctx.subprocess.PIPE, stdout=log,
                                       stderr=_ctx.subprocess.STDOUT, cwd=_ctx.ROOT,
                                       start_new_session=True)
            with _ctx._lock:
                _ctx._active_process = process
            process.stdin.write(payload)
            process.stdin.close()
            while process.poll() is None:
                if cancel.is_set():
                    _ctx._stop_process(process)
                    raise _ctx.DownloadCancelled("Model indirmesi iptal edildi; kaldığı yer korunup devam ettirilebilir.")
                _ctx.time.sleep(1)
                size = temp.stat().st_size if temp.exists() else 0
                now = _ctx.time.monotonic()
                rate = max(0, size - previous_size) / max(now - previous_time, .001) / (1024 * 1024)
                _ctx._progress(group, "İndiriliyor", name=file["name"], downloaded=size,
                          total=file.get("size"), rate=round(rate, 2))
                previous_time, previous_size = now, size
    finally:
        with _ctx._lock:
            if _ctx._active_process is process:
                _ctx._active_process = None
    if cancel.is_set():
        raise _ctx.DownloadCancelled("Model indirmesi iptal edildi; kaldığı yer korunup devam ettirilebilir.")
    if process.returncode == 43:
        _ctx._progress(group, "Dosya doğrulanıyor", name=file["name"],
                  downloaded=temp.stat().st_size if temp.is_file() else 0,
                  total=file.get("size"))
    if process.returncode == 43 and _ctx._valid_model(temp, file):
        temp.replace(target)
        _ctx._write_verification(target, file)
        return
    if process.returncode == 42:
        raise RuntimeError("Civitai model erişimi reddedildi; Pod Secret ayarını kontrol edin.")
    if process.returncode:
        raise RuntimeError("Civitai modeli indirilemedi; kurulum logunu kontrol edin.")
    _ctx._progress(group, "Dosya doğrulanıyor", name=file["name"],
              downloaded=temp.stat().st_size if temp.is_file() else 0,
              total=file.get("size"))
    if not _ctx._valid_model(temp, file):
        temp.unlink(missing_ok=True)
        raise RuntimeError("Model dosyasının bütünlüğü doğrulanamadı.")
    temp.replace(target)
    _ctx._write_verification(target, file)


def _url_model(target, file, cancel, group):
    """Direct HTTPS downloads use a cancellable worker and resume partial files."""
    temp = target.with_suffix(".part")
    helper = """import json, os, sys, urllib.error, urllib.request
c=json.loads(sys.stdin.read()); offset=os.path.getsize(c['part']) if os.path.exists(c['part']) else 0
headers={'User-Agent':'Mozilla/5.0','Accept-Encoding':'identity'}
if offset: headers['Range']='bytes=%d-' % offset
request=urllib.request.Request(c['url'],headers=headers)
try:
    with urllib.request.urlopen(request,timeout=30) as response:
        append=bool(offset and response.status==206)
        with open(c['part'],'ab' if append else 'wb') as output:
            while True:
                chunk=response.read(1024*1024)
                if not chunk: break
                output.write(chunk)
except urllib.error.HTTPError as error:
    print('HTTP %d' % error.code,flush=True); sys.exit(1)
except Exception:
    print('download failed',flush=True); sys.exit(1)
"""
    previous_time = _ctx.time.monotonic()
    previous_size = temp.stat().st_size if temp.exists() else 0
    process = None
    try:
        with (_ctx.ROOT / "setup.log").open("ab") as log:
            process = _ctx.subprocess.Popen([_ctx.sys.executable, "-c", helper],
                                       stdin=_ctx.subprocess.PIPE, stdout=log,
                                       stderr=_ctx.subprocess.STDOUT, cwd=_ctx.ROOT,
                                       start_new_session=True)
            with _ctx._lock:
                _ctx._active_process = process
            payload = _ctx.json.dumps({"url": file["url"], "part": str(temp)}).encode("utf-8")
            process.stdin.write(payload)
            process.stdin.close()
            while process.poll() is None:
                if cancel.is_set():
                    _ctx._stop_process(process)
                    raise _ctx.DownloadCancelled("Model indirmesi iptal edildi; kaldığı yer korunup devam ettirilebilir.")
                _ctx.time.sleep(1)
                size = temp.stat().st_size if temp.exists() else 0
                now = _ctx.time.monotonic()
                rate = max(0, size - previous_size) / max(now - previous_time, .001) / (1024 * 1024)
                _ctx._progress(group, "İndiriliyor", name=file["name"], downloaded=size,
                          total=file.get("size"), rate=round(rate, 2))
                previous_time, previous_size = now, size
    finally:
        with _ctx._lock:
            if _ctx._active_process is process:
                _ctx._active_process = None
    if cancel.is_set():
        raise _ctx.DownloadCancelled("Model indirmesi iptal edildi; kaldığı yer korunup devam ettirilebilir.")
    if process.returncode:
        raise RuntimeError("Model indirilemedi; kurulum logunu kontrol edin.")
    _ctx._progress(group, "Dosya doğrulanıyor", name=file["name"],
              downloaded=temp.stat().st_size if temp.is_file() else 0,
              total=file.get("size"))
    if not _ctx._valid_model(temp, file):
        temp.unlink(missing_ok=True)
        raise RuntimeError("Model dosyasının bütünlüğü doğrulanamadı.")
    temp.replace(target)
    _ctx._write_verification(target, file)


def _valid_model(path, file):
    if not path.is_file() or not path.stat().st_size:
        return False
    expected = file.get("sha256")
    if expected is None:
        return True
    with path.open("rb") as stream:
        return _ctx.hashlib.file_digest(stream, "sha256").hexdigest() == expected


def _verification_file(path):
    return path.with_name(path.name + ".verified")


def _model_present(path):
    return path.is_file() and path.stat().st_size > 0


def _model_verified(path, file):
    if not path.is_file() or not path.stat().st_size:
        return False
    expected = file.get("sha256")
    if not expected:
        return True
    try:
        return _ctx._verification_file(path).read_text(encoding="ascii").strip() == (
            expected.lower() + ":" + str(path.stat().st_size))
    except OSError:
        return False


def _write_verification(path, file):
    expected = file.get("sha256")
    if expected:
        _ctx._verification_file(path).write_text(
            expected.lower() + ":" + str(path.stat().st_size), encoding="ascii")


def _hf_incomplete_file(local_dir, filename):
    """Find Hugging Face's resumable partial file inside its local-dir cache."""
    try:
        from huggingface_hub._local_folder import get_local_download_paths

        paths = get_local_download_paths(_ctx.Path(local_dir), filename)
        candidates = []
        try:
            metadata = paths.metadata_path.read_text(encoding="utf-8").splitlines()
            if len(metadata) >= 2 and metadata[1]:
                candidates.append(paths.incomplete_path(metadata[1]))
        except OSError:
            pass
        # huggingface_hub writes the metadata only after it moves the finished file.
        # While downloading, find its ETag-named temporary file by the stable filename hash.
        prefix = _ctx.base64.urlsafe_b64encode(
            _ctx.hashlib.sha1(paths.metadata_path.name.encode("utf-8")).digest()
        ).decode("ascii")
        candidates.extend(paths.metadata_path.parent.glob(prefix + ".*.incomplete"))
        existing = [path for path in candidates if path.is_file()]
        if existing:
            return max(existing, key=lambda path: path.stat().st_mtime_ns)
    except (ImportError, OSError, ValueError, IndexError, AttributeError):
        pass
    return None
