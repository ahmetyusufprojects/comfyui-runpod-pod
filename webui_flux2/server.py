"""Flux2-Klein sade uretim/duzenleme mini web arayuzu.
Calistirma:
  1) Bu klasorde:  python server.py   (portable python da olur)
  2) Tarayici: http://127.0.0.1:8189

WebUI ComfyUI'den bagimsiz baslar; gorsel/video isleri icin ComfyUI gerekir.

Ozellikler: foto yukleme (duzenleme) / bos (saf uretim), prompt, cozunurluk
preset + maksimum boyuta olcekleme (en uzun kenar, 16'nin kati), steps/cfg/seed.
Upscale YOK: istek uzerine kaldirildi, sadece saf uretim + duzenleme.
"""
import base64
import asyncio
import contextlib
import datetime as _dt
import hashlib
import http.server
import json
import mimetypes
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse
import uuid

# Portable python ._pth ile sys.path'i kisitlar; yerel moduller icin bu
# dosyanin klasorunu basa ekle.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auth
import arsiv
import kasa
import llm
import prompt_store
import prompt_translation
from comfy_client import (COMFY, comfy_get_json, comfy_post_json,
                          comfy_request, upload_image_to_comfy)
from image_graphs import (DIFFUSION_FILE, TEXT_ENCODER_FILE, VAE_FILE,
                          Z_IMAGE_DIFFUSION_FILE, Z_IMAGE_TEXT_ENCODER_FILE,
                          Z_IMAGE_VAE_FILE, ANIMA_DIFFUSION_FILE,
                          MOODY_ANIMA_DIFFUSION_FILE,
                          ANIMA_TEXT_ENCODER_FILE, ANIMA_VAE_FILE,
                          build_prompt, build_z_image_prompt, build_anima_prompt,
                          build_flux3_prompt, build_krea2_prompt,
                          build_illustrious_prompt, build_qwen_image_edit_prompt)
from api.legacy_setup_routes import handle_setup_get as _handle_setup_get, handle_setup_post as _handle_setup_post
from api.legacy_main_routes import handle_main_get as _handle_main_get, handle_main_post as _handle_main_post
from image_utils import (exif_duzelt as _exif_duzelt,
                         fotograf_hazirla as _fotograf_hazirla,
                         scale_to_max_side)
from services import video_generation as _video_generation
from services import user_content as _user_content
from services import media_enhancement as _media_enhancement
from services import job_queue as _job_queue
from services import generation as _generation
from enhancement_graphs import (IMAGE_PRESETS, VIDEO_PRESETS, build_image_enhancement,
                                build_video_enhancement,
                                build_seedvr2_video_enhancement,
                                seedvr2_video_chunks)

_AYARLAR_KILIT = threading.RLock()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_ROOT = os.path.abspath(os.environ.get("WEBUI_DATA_ROOT", BASE_DIR))
RUNPOD_MODE = os.environ.get("WEBUI_RUNPOD") == "1"
SETUP_PORT = 8192
_setup_httpd = None
_asgi_servers = []
_startup_error = None
if RUNPOD_MODE:
    import pod_setup
    import app_update
    import r2_backups

_app_update_lock = threading.Lock()
_app_update_state = {"status": "idle"}
_system_backup_lock = threading.Lock()
_backup_progress_lock = threading.RLock()
_backup_restore_progress = {}
_BACKUP_UPLOAD_DIR = os.path.join(tempfile.gettempdir(), "webui-system-backup-imports")
_r2_jobs_lock = threading.RLock()
_r2_jobs = set()


def _backup_upload_path(token):
    if not isinstance(token, str) or not re.fullmatch(r"[a-f0-9]{32}", token):
        return None
    return os.path.join(_BACKUP_UPLOAD_DIR, "webui-backup-upload-" + token + ".gpg")


def _backup_progress_set(token, **state):
    with _backup_progress_lock:
        current = _backup_restore_progress.setdefault(token, {})
        current.update(state)
        current["updated"] = time.time()


def _backup_progress_get(token):
    with _backup_progress_lock:
        state = _backup_restore_progress.get(token)
        return dict(state) if state else None


def _r2_start_job(token, worker, complete_stage):
    """Run a long R2 transfer without keeping the installer request open."""
    with _r2_jobs_lock:
        if token in _r2_jobs:
            return False
        _r2_jobs.add(token)
    _backup_progress_set(token, status="running", stage="İş hazırlanıyor",
                         bytes=0, total=0, error="")

    def run():
        try:
            result = worker()
            _backup_progress_set(token, status="complete", stage=complete_stage,
                                 result=result or {}, error="")
        except Exception as exc:
            # R2Error never embeds endpoint credentials; the other expected
            # exceptions are local backup/IO errors and are kept short.
            message = str(exc).strip()[:240] or type(exc).__name__
            _backup_progress_set(token, status="error", stage="İşlem tamamlanamadı",
                                 error=message)
        finally:
            with _r2_jobs_lock:
                _r2_jobs.discard(token)

    threading.Thread(target=run, name="r2-backup-" + token[:8], daemon=True).start()
    return True


def _r2_progress_callback(token, stage, total):
    lock = threading.Lock()
    transferred = [0]
    last_reported = [0]

    def update(amount):
        with lock:
            transferred[0] += int(amount or 0)
            current = transferred[0]
            if current - last_reported[0] < 2 * 1024 * 1024 and current < total:
                return
            last_reported[0] = current
        _backup_progress_set(token, status="running", stage=stage,
                             bytes=min(current, total), total=total, error="")
    return update


@contextlib.contextmanager
def _sistem_yedegi_hazirla(parola, medya_haric=False):
    """Pause writes, seal every open account, then prepare one full-system archive."""
    if not _system_backup_lock.acquire(blocking=False):
        raise RuntimeError("Başka bir sistem yedeği işlemi sürüyor.")
    path = None
    paused = False
    try:
        with _isler_kilit, pod_setup._lock:
            if (_kuyruk_calismasi or _ceviri_calismasi or _kuyruk or _ceviri_kuyrugu
                    or pod_setup._active or pod_setup._queue):
                raise RuntimeError("Yedek öncesinde üretim ve indirme işlerinin bitmesini bekleyin.")
        with _http_yazma_kosulu:
            if _kapatma_istegi.is_set():
                raise RuntimeError("Sistem başka bir kapanış veya güncelleme işlemi yürütüyor.")
            _kapatma_istegi.set()
            paused = True
            if not _http_yazma_kosulu.wait_for(lambda: not _http_yazma_istekleri, timeout=30):
                _kapatma_istegi.clear()
                paused = False
                raise RuntimeError("Önceki kayıt işlemleri tamamlanmadı.")
        with _isler_kilit, pod_setup._lock:
            if (_kuyruk_calismasi or _ceviri_calismasi or _kuyruk or _ceviri_kuyrugu
                    or pod_setup._active or pod_setup._queue):
                raise RuntimeError("Yedek hazırlanırken yeni bir iş kuyruğa girdi.")
        snapshot_context = (kasa.sistem_yedegi_medya_haric_kasalar(DATA_ROOT)
                            if medya_haric else contextlib.nullcontext(None))
        with snapshot_context as filtered_vaults:
            closed = kasa.hepsini_kapat()
            if any(not value for value in closed.values()):
                raise RuntimeError("Bir veya daha fazla açık kasa mühürlenemedi.")
            pod_setup.checkpoint()
            path = kasa.sistem_yedegi_uret(
                DATA_ROOT, pod_setup._system_settings_path, parola,
                medya_haric_vaultlar=filtered_vaults)
            yield path
    finally:
        if path:
            try:
                os.remove(path)
            except OSError:
                pass
        if paused:
            _kapatma_istegi.clear()
        _system_backup_lock.release()

def _app_update_worker():
    global _app_update_state
    try:
        commit = app_update.prepare()
        with _isler_kilit, pod_setup._lock:
            if (_kuyruk_calismasi or _ceviri_calismasi or _kuyruk or _ceviri_kuyrugu
                    or pod_setup._active or pod_setup._queue):
                raise RuntimeError("Önce üretim ve indirme işlerinin tamamlanmasını bekleyin.")
            _kapatma_istegi.set()
        _app_update_state = {"status": "restarting", "commit": commit}
        duzgun_kapat()
        process = pod_setup._comfy_process
        if process is not None and process.poll() is None:
            (app_update.ROOT / 'restart-comfy').write_text('1', encoding='ascii')
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        (app_update.ROOT / 'apply.json').write_text(json.dumps({"commit": commit}), encoding='utf-8')
        os.kill(os.getpid(), signal.SIGTERM)
    except Exception as exc:
        _kapatma_istegi.clear()
        _app_update_state = {"status": "error", "message": str(exc)[:200]}
    finally:
        _app_update_lock.release()


def _system_event(message):
    """Write only short operational events; never include prompts or account names."""
    if not RUNPOD_MODE:
        return
    try:
        path = os.path.join(os.environ.get("POD_RUNTIME_ROOT", "/opt/runtime"), "webui.log")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as stream:
            stream.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), message))
    except OSError:
        pass


def _ram_bilgi():
    """Report the container limit and reclaimable cache when a limit exists."""
    import psutil
    ram = psutil.virtual_memory()
    total, available = ram.total, ram.available
    if RUNPOD_MODE:
        for limit_path, usage_path, stat_path in (
                ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory.current",
                 "/sys/fs/cgroup/memory.stat"),
                ("/sys/fs/cgroup/memory/memory.limit_in_bytes",
                 "/sys/fs/cgroup/memory/memory.usage_in_bytes",
                 "/sys/fs/cgroup/memory/memory.stat")):
            try:
                with open(limit_path, encoding="ascii") as stream:
                    limit = int(stream.read().strip())
                with open(usage_path, encoding="ascii") as stream:
                    used = int(stream.read().strip())
                cache = 0
                with open(stat_path, encoding="ascii") as stream:
                    for line in stream:
                        parts = line.split()
                        if len(parts) == 2 and parts[0] in ("inactive_file", "total_inactive_file"):
                            cache = max(cache, int(parts[1]))
                if 0 < limit < total:
                    total = limit
                    available = min(limit, max(0, limit - used + cache))
                break
            except (OSError, ValueError):
                continue
    return {"toplam_mb": round(total / (1024 * 1024)),
            "bos_mb": round(available / (1024 * 1024)),
            "yuzde": round(100 * (total - available) / total, 1) if total else None}


def _log_tail(source, count):
    """Read bounded operational logs and redact credentials before returning them."""
    runtime = os.environ.get("POD_RUNTIME_ROOT", "/opt/runtime")
    if RUNPOD_MODE:
        paths = {"llama": llm.LOG_PATH,
                 "comfy": os.path.join(runtime, "comfyui.log"),
                 "webui": os.path.join(runtime, "webui.log"),
                 "setup": os.path.join(runtime, "setup.log")}
    else:
        paths = {"llama": llm.LOG_PATH,
                 "comfy": os.path.join(BASE_DIR, "..", "baslatici", "comfy.log"),
                 "webui": os.path.join(BASE_DIR, "..", "baslatici", "webui.log"),
                 "setup": os.path.join(BASE_DIR, "..", "baslatici", "webui.log")}
    if source not in paths:
        raise ValueError("Bilinmeyen log kaynağı.")

    def read_lines(path):
        try:
            with open(path, "rb") as stream:
                stream.seek(0, os.SEEK_END)
                size = stream.tell()
                stream.seek(max(0, size - 30000))
                return [line for line in stream.read().decode("utf-8", "replace").splitlines()
                        if line.strip()]
        except OSError:
            return []

    lines = read_lines(paths[source])
    if RUNPOD_MODE and source == "llama":
        # Before llama-server starts, its own log is empty. Include the small,
        # tagged GPU installation diagnostics so failed CUDA setup is visible.
        setup_lines = read_lines(paths["setup"])
        diagnostics = [line for line in setup_lines
                       if line.startswith("[llama]") or line.startswith("[timing] llama-server")]
        lines = (diagnostics + lines)[-count:]
    secrets = sorted((value for name, value in os.environ.items()
                      if any(part in name.upper() for part in
                             ("TOKEN", "PASSWORD", "API_KEY", "SECRET"))
                      and len(value) >= 6), key=len, reverse=True)
    secret_pattern = re.compile(
        r"(?i)(\b(?:token|[a-z0-9_.-]+[_-]token|password|secret|api[_-]?key)"
        r"\s*[=:]\s*)[^\s,;]+")
    cleaned = []
    for line in lines[-count:]:
        for secret in secrets:
            line = line.replace(secret, "[REDACTED]")
        line = secret_pattern.sub(r"\1[REDACTED]", line)
        cleaned.append(line[:2000])
    return cleaned

def _dbg(msg):
    try:
        with open(os.path.join(BASE_DIR, "webui_error.log"), "a", encoding="utf-8") as f:
            f.write(f"[DBG {time.time():.0f}] {msg}\n")
    except Exception:
        pass

MODEL_FLUX2 = "flux2"
MODEL_Z_IMAGE = "z_image"
MODEL_ANIMA = "anima"
MODEL_MOODY_ANIMA = "moody_anima"
MODEL_FLUX3 = "flux3"
MODEL_KREA2 = "krea2"
MODEL_QWEN_IMAGE_EDIT = "qwen_image_edit"
MODEL_QWEN_IMAGE_EDIT_TURBO = "qwen_image_edit_turbo"
MODEL_ILLUSTRIOUS = "illustrious"

# --- Moduler is sistemi: yeni is tipi eklemek mevcut kodu kirmamali ---
# "goruntu-uretim" / "goruntu-duzenleme" simdi; "video-uretim" yakinda (sadece hazirlik).
IS_TIPI_GORUNTU = "goruntu-uretim"
IS_TIPI_DUZENLEME = "goruntu-duzenleme"
IS_TIPI_VIDEO = "video-uretim"
IS_TIPI_GORUNTU_IYILESTIR = "goruntu-iyilestirme"
IS_TIPI_VIDEO_IYILESTIR = "video-iyilestirme"
IS_TIPLERI = (IS_TIPI_GORUNTU, IS_TIPI_DUZENLEME, IS_TIPI_VIDEO,
              IS_TIPI_GORUNTU_IYILESTIR, IS_TIPI_VIDEO_IYILESTIR)

# --- Video (MiniMax H3 referans-video): sablon + sinirlar ---
# Sablon, UI workflow'una dokunulmadan video_r2v_api.json'dan okunur.
VIDEO_SABLON_DOSYA = os.path.join(BASE_DIR, "video_r2v_api.json")
WAN22_VIDEO_SABLON_DOSYA = os.path.join(BASE_DIR, "video_wan22_api.json")
VIDEO_SURESN_MIN, VIDEO_SURESN_MAX = (0.25 if RUNPOD_MODE else 1), 10
VIDEO_FPSLER = (8, 12, 24)
VIDEO_MAX_KENAR = 1344  # en uzun kenar; 32'nin kati (dugum semasi adim=32)

KUTUPHANE_DIR = os.path.join(BASE_DIR, "kutuphane")
GECMIS_DIR = os.path.join(BASE_DIR, "gecmis")
PROMPTLAR_DOSYA = os.path.join(BASE_DIR, "promptlar.json")
AYARLAR_DOSYA = os.path.join(BASE_DIR, "ayarlar.json")
# Yukaridaki 4 yol SADECE ilk-kurulum migrasyonu icin kullanilir (eski paylasimli
# veriler). Kurulum sonrasi tum okuma/yazma asagidaki kullanici-koklerinden yapilir;
# eski yola donus (fallback) yoktur.
KULLANICILAR_DOSYA = os.path.join(DATA_ROOT, "kullanicilar.json")
VERI_KOKU = os.path.join(DATA_ROOT, "veriler")
_auth_store = auth.AuthStore(KULLANICILAR_DOSYA)

_GUVENLI_AD = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _klasor_elin(dirpath):
    try:
        os.makedirs(dirpath, exist_ok=True)
    except Exception:
        pass


def _guvenli_dosya_adi(ad):
    """Kutuphane/gecmis sunumunda path-traversal engelle. Gecersizse None doner."""
    if not ad or not isinstance(ad, str):
        return None
    ad = ad.split("/")[-1].split("\\")[-1]
    if not _GUVENLI_AD.match(ad) or ad in (".", ".."):
        return None
    return ad


# --- Kullanici izolasyonu: tum veri erisimi bu koklerden cozulur ---
# Baska kullanicinin adi verilse bile kendi klasoru disina cikilamaz:
# ad allowlist disindaysa veya cozulen yol kok disina tasarsa ValueError.

def _kullanici_koku(kullanici):
    # Veri SADECE acik kasadan okunur/yazilir. Kasa kapaliysa ValueError.
    # Kilitli kasada ham klasore dusus (fallback) YOKTUR.
    if not auth.kullanici_adi_gecerli_mi(kullanici):
        raise ValueError("gecersiz kullanici")
    try:
        kok = kasa.calisma_koku(kullanici)
    except ValueError:
        raise ValueError("kasa kilitli")
    kok = os.path.abspath(kok) + os.sep
    if not kok.startswith(os.path.abspath(kasa.CALISMA_DIR) + os.sep):
        raise ValueError("gecersiz kullanici")
    return kok[:-1]


def _ham_kullanici_koku(kullanici):
    """Kasa ONCESI ham veri klasoru (kurulum/gecis icin). Normal akista kullanma."""
    if not auth.kullanici_adi_gecerli_mi(kullanici):
        raise ValueError("gecersiz kullanici")
    kok = os.path.abspath(os.path.join(VERI_KOKU, kullanici)) + os.sep
    if not kok.startswith(os.path.abspath(VERI_KOKU) + os.sep):
        raise ValueError("gecersiz kullanici")
    return kok[:-1]


def _kullanici_kutuphane(kullanici):
    return os.path.join(_kullanici_koku(kullanici), "kutuphane")


def _kullanici_gecmis(kullanici):
    return os.path.join(_kullanici_koku(kullanici), "gecmis")


def _kullanici_promptlar(kullanici):
    return os.path.join(_kullanici_koku(kullanici), "promptlar.json")


def _kullanici_ayar(kullanici):
    return os.path.join(_kullanici_koku(kullanici), "ayarlar.json")


def _kullanici_klasorleri(kullanici):
    # Kasa kurulmadan ONCEKI ham klasorler (kasa parolasi girilince kasaya alinir).
    kok = _ham_kullanici_koku(kullanici)
    for alt in ("kutuphane", "gecmis"):
        _klasor_elin(os.path.join(kok, alt))
    kasa.gizle(kok)


def _kullanim_hesapla(kullanici):
    """(kullanim_mb, kayit_sayisi): kütüphane+gecmis bayt toplami, kayit adedi.
    kayit = gecmis .json + kutuphane .png (hedef) + prompt sayisi."""
    kok = _kullanici_koku(kullanici)
    toplam_bayt = 0
    kayit = 0
    for alt in ("kutuphane", "gecmis"):
        d = os.path.join(kok, alt)
        try:
            for ad in os.listdir(d):
                yol = os.path.join(d, ad)
                if not os.path.isfile(yol):
                    continue
                try:
                    toplam_bayt += os.path.getsize(yol)
                except Exception:
                    pass
                if alt == "gecmis" and ad.endswith(".json"):
                    kayit += 1
                elif alt == "kutuphane" and ad.lower().endswith(".png") \
                        and not ad.endswith(("_orig.png", "_thumb.jpg")):
                    kayit += 1
        except Exception:
            pass
    try:
        kayit += len(promptlari_oku(kullanici))
    except Exception:
        pass
    return round(toplam_bayt / (1024 * 1024), 2), kayit


def _kota_kontrol(kullanici, yeni_bayt=0, yeni_kayit=0):
    """Asim varsa hata mesaji doner, yoksa None. 0 kota = sinirsiz."""
    kota_mb, kota_kayit = _auth_store.limitler(kullanici)
    if kota_mb <= 0 and kota_kayit <= 0:
        return None
    mb, kayit = _kullanim_hesapla(kullanici)
    if kota_mb > 0 and (mb + yeni_bayt / (1024 * 1024)) > kota_mb:
        return "kota doldu"
    if kota_kayit > 0 and (kayit + yeni_kayit) > kota_kayit:
        return "kota doldu"
    return None


def _ayar_oku(kullanici):
    try:
        with _AYARLAR_KILIT:
            with open(_kullanici_ayar(kullanici), "r", encoding="utf-8") as f:
                a = json.load(f)
                if isinstance(a, dict):
                    return a
    except Exception:
        pass
    return {}


def _ayar_yaz(kullanici, ayar):
    with _AYARLAR_KILIT:
        _klasor_elin(_kullanici_koku(kullanici))
        yol = _kullanici_ayar(kullanici)
        gecici = yol + ".tmp"
        try:
            with open(gecici, "w", encoding="utf-8") as f:
                json.dump(ayar, f, ensure_ascii=False, indent=2)
            os.replace(gecici, yol)
        finally:
            try:
                os.remove(gecici)
            except FileNotFoundError:
                pass


# Uretim formu kalici alanlari: ad -> (en_alt, en_ust). Reddetme yok,
# disindakiler atilir, sayilar araliga cekilir (kullanici dostu).
_FORM_SAYI = {"width": (64, 4096), "height": (64, 4096),
              "max_side": (256, 4096), "video_max_side": (0, 1344),
              "steps": (1, 50), "seed": (-1, 2 ** 31 - 1)}
_UI_ENUM = {
    "ps_length": ("kisa", "orta", "uzun"),
    "ps_language": ("en", "tr"),
    "ps_prompt_profile": ("auto", "general", "z_image", "flux3", "krea2", "illustrious", "minimaxh3", "wan22"),
    "history_view": ("izgara", "akis"),
    "log_source": ("llama", "comfy", "webui", "setup"),
    "cinematic_mode": ("off", "soft", "vivid"),
    "active_menu": ("uretim", "studyo", "kuyruk", "kutuphane", "arsiv",
                    "promptlar", "model", "izleme", "ayarlar", "yonetim"),
}
_UI_BOOL = {"ps_bbox_mode", "monitor_auto_refresh", "history_favorites",
            "history_albums", "continuous_generation", "auto_open_result"}
_UI_NUMBER = {"ps_creativity": (0.2, 1.2), "log_lines": (10, 500),
              "history_width": (220, 420)}


def _ui_ayar_temizle(ham):
    if not isinstance(ham, dict):
        raise ValueError("ui sozluk olmali")
    temiz = {}
    for key, value in ham.items():
        if key in _UI_ENUM:
            if value not in _UI_ENUM[key]:
                raise ValueError("%s degeri gecersiz" % key)
            temiz[key] = value
        elif key in _UI_BOOL:
            if not isinstance(value, bool):
                raise ValueError("%s boolean olmali" % key)
            temiz[key] = value
        elif key in _UI_NUMBER:
            if isinstance(value, bool):
                raise ValueError("%s sayi olmali" % key)
            try:
                number = float(value)
            except (TypeError, ValueError):
                raise ValueError("%s sayi olmali" % key)
            low, high = _UI_NUMBER[key]
            if not low <= number <= high:
                raise ValueError("%s aralik disinda" % key)
            if key != "ps_creativity" and not number.is_integer():
                raise ValueError("%s tam sayi olmali" % key)
            temiz[key] = (int(number) if key != "ps_creativity"
                          else round(number, 2))
        else:
            # Bilinmeyen/eski anahtarlar sessizce atlanir (orn. kaldirilan ps_mode).
            continue
    return temiz


def _form_temizle(ham):
    temiz = {}
    if not isinstance(ham, dict):
        return temiz
    for k, v in ham.items():
        try:
            if k in ("prompt", "negatif"):
                s = str(v or "")[:prompt_translation.MAX_SOURCE_CHARS if k == "prompt" else 2000]
                if s.strip():
                    temiz[k] = s
            elif k in ("workflow",):
                if v in ("tek", "cift", "video"):
                    temiz[k] = v
            elif k == "video_profile":
                if v in ("fast", "quality", "normal"):
                    temiz[k] = v
            elif k == "model":
                if v in (MODEL_FLUX2, MODEL_Z_IMAGE, MODEL_ANIMA,
                         MODEL_MOODY_ANIMA, MODEL_FLUX3, MODEL_KREA2,
                         MODEL_QWEN_IMAGE_EDIT, MODEL_QWEN_IMAGE_EDIT_TURBO,
                         MODEL_ILLUSTRIOUS):
                    temiz[k] = v
            elif k in ("image_model", "model_instance"):
                allowed = ("auto", MODEL_FLUX2, MODEL_Z_IMAGE, MODEL_FLUX3,
                           MODEL_KREA2, MODEL_QWEN_IMAGE_EDIT,
                           MODEL_QWEN_IMAGE_EDIT_TURBO, MODEL_ILLUSTRIOUS)
                if v in allowed or (RUNPOD_MODE and pod_setup.model_instance(v)):
                    temiz[k] = v
            elif k == "flux3_bbox_mode":
                if isinstance(v, bool):
                    temiz[k] = v
            elif k == "loras":
                if isinstance(v, list) and len(v) <= 8:
                    selected = []
                    for item in v:
                        if not isinstance(item, dict):
                            continue
                        identifier = item.get("id")
                        if not isinstance(identifier, str) or not re.fullmatch(
                                r"(?:[a-f0-9]{12}|anime-desire-illustrious)", identifier):
                            continue
                        try:
                            weight = max(0.0, min(2.0, float(item.get("weight", 1))))
                        except (TypeError, ValueError, OverflowError):
                            continue
                        selected.append({"id": identifier, "weight": round(weight, 2)})
                    temiz[k] = selected
            elif k in ("fps",):
                n = int(v)
                if n in (8, 12, 24):
                    temiz[k] = n
            elif k in ("max_olcek",):
                if isinstance(v, bool):
                    temiz[k] = v
            elif k == "anima_model":
                if isinstance(v, bool):
                    temiz[k] = v
            elif k == "moody_anima_model":
                if isinstance(v, bool):
                    temiz[k] = v
            elif k == "cfg":
                n = float(v)
                if 0 <= n <= 100:
                    temiz[k] = n
            elif k == "suresn":
                n = float(v)
                lo = VIDEO_SURESN_MIN if RUNPOD_MODE else 1
                if lo <= n <= VIDEO_SURESN_MAX:
                    temiz[k] = round(n, 2)
            elif k == "aspect_ratio":
                if v is None:
                    temiz[k] = None
                else:
                    n = float(v)
                    if 0 < n <= 100:
                        temiz[k] = n
            elif k == "sampler":
                if v in ("euler", "euler_ancestral", "dpmpp_2m",
                         "uni_pc", "res_multistep"):
                    temiz[k] = v
            elif k in _FORM_SAYI:
                lo, hi = _FORM_SAYI[k]
                temiz[k] = max(lo, min(hi, int(v)))
        except Exception:
            continue
    return temiz


def _ayar_degisikliklerini_uygula(ayar, req):
    if "gecmis_limiti" in req:
        try:
            ayar["gecmis_limiti"] = max(0, int(req.get("gecmis_limiti", 0)))
        except (TypeError, ValueError, OverflowError):
            raise ValueError("gecmis_limiti sayi olmali")
    if "form" in req:
        if not isinstance(req["form"], dict):
            raise ValueError("form sozluk olmali")
        ayar["form"] = _form_temizle(req["form"])
    if "ui" in req:
        temiz_ui = _ui_ayar_temizle(req["ui"])
        ui_ayarlari = ayar.get("ui", {})
        if not isinstance(ui_ayarlari, dict):
            raise ValueError("kayitli ui ayarlari gecersiz")
        ui_ayarlari.update(temiz_ui)
        ayar["ui"] = ui_ayarlari
    if "llm_sistem" in req:
        s = req.get("llm_sistem") or ""
        if not isinstance(s, str):
            raise ValueError("llm_sistem metin olmali")
        if len(s.strip()) > 4000:
            raise ValueError("sistem promptu en fazla 4000 karakter")
        ayar["llm_sistem"] = s.strip()
    if "llm_sohbet_sistem" in req:
        s = req.get("llm_sohbet_sistem") or ""
        if not isinstance(s, str):
            raise ValueError("llm_sohbet_sistem metin olmali")
        if len(s.strip()) > 4000:
            raise ValueError("sohbet sistem promptu en fazla 4000 karakter")
        ayar["llm_sohbet_sistem"] = s.strip()
    if "llm_mod" in req:
        m = req.get("llm_mod")
        if not isinstance(m, dict):
            raise ValueError("llm_mod sozluk olmali")
        temiz_m = {}
        for k in ("fikir", "gorsel", "birlestir", "ceviri",
                  "sohbet"):
            if k in m and m[k] is not None:
                v = str(m[k])
                if len(v.strip()) > 2000:
                    raise ValueError(
                        "mod talimati en fazla 2000 karakter")
                if v.strip():
                    temiz_m[k] = v.strip()
        ayar["llm_mod"] = temiz_m
    if "llm_streaming" in req:
        if not isinstance(req.get("llm_streaming"), bool):
            raise ValueError("llm_streaming boolean olmali")
        ayar["llm_streaming"] = req["llm_streaming"]
    if "llm_low_bandwidth" in req:
        if not isinstance(req.get("llm_low_bandwidth"), bool):
            raise ValueError("llm_low_bandwidth boolean olmali")
        ayar["llm_low_bandwidth"] = req["llm_low_bandwidth"]
    if "llm_ctx" in req:
        try:
            ctx_deger = int(req.get("llm_ctx"))
        except (TypeError, ValueError, OverflowError):
            raise ValueError("llm_ctx sayi olmali")
        if ctx_deger < 1024 or ctx_deger > 32768:
            raise ValueError("llm_ctx 1024-32768 araliginda olmali")
        ayar["llm_ctx"] = ctx_deger
    if "llm_model" in req:
        model = req.get("llm_model")
        allowed = ({item["id"] for item in llm.model_profilleri()}
                   if RUNPOD_MODE else {"llama"})
        if not isinstance(model, str) or model not in allowed:
            raise ValueError("llm_model seçimi geçersiz")
        ayar["llm_model"] = model
    if "llm_model_tuning" in req:
        profiles = req.get("llm_model_tuning")
        if not isinstance(profiles, dict) or len(profiles) > 100:
            raise ValueError("llm_model_tuning model ayarları sözlüğü olmalı")
        allowed = ({item["id"] for item in llm.model_profilleri()}
                   if RUNPOD_MODE else {"llama"})
        temiz_profiller = {}
        cache_types = {"f32", "f16", "bf16", "q8_0", "q4_0", "q4_1",
                       "iq4_nl", "q5_0", "q5_1"}
        for profile, values in profiles.items():
            if profile not in allowed or not isinstance(values, dict):
                raise ValueError("LLM model ayarı geçersiz")
            tune = {}
            if "gpu_layers" in values:
                layers = values["gpu_layers"]
                if layers in ("auto", "all"):
                    tune["gpu_layers"] = layers
                elif isinstance(layers, int) and not isinstance(layers, bool) and 0 <= layers <= 512:
                    tune["gpu_layers"] = layers
                else:
                    raise ValueError("GPU katman ayarı geçersiz")
            integer_ranges = {
                "context": (1024, 32768), "threads": (1, 64),
                "batch": (128, 8192), "ubatch": (128, 8192),
                "top_k": (0, 1000), "reasoning_budget": (-1, 32768),
                "mtp_draft_tokens": (1, 16),
            }
            for key, (low, high) in integer_ranges.items():
                if key in values:
                    try:
                        raw = values[key]
                        number = int(raw)
                    except (TypeError, ValueError, OverflowError):
                        raise ValueError("LLM ayarı sayı olmalı: " + key)
                    try:
                        tam_sayi = float(raw) == number
                    except (TypeError, ValueError, OverflowError):
                        tam_sayi = False
                    if isinstance(raw, bool) or not tam_sayi or not low <= number <= high:
                        raise ValueError("LLM ayarı aralık dışında: " + key)
                    tune[key] = number
            float_ranges = {
                "top_p": (0.05, 1.0), "repeat_penalty": (0.8, 2.0),
                "temperature": (0.1, 2.0),
            }
            for key, (low, high) in float_ranges.items():
                if key in values:
                    try:
                        raw = values[key]
                        number = float(raw)
                    except (TypeError, ValueError, OverflowError):
                        raise ValueError("LLM ayarı sayı olmalı: " + key)
                    if isinstance(raw, bool) or not low <= number <= high:
                        raise ValueError("LLM ayarı aralık dışında: " + key)
                    tune[key] = round(number, 4)
            for key in ("use_mtp", "multimodal"):
                if key in values:
                    if not isinstance(values[key], bool):
                        raise ValueError("LLM ayarı doğru/yanlış olmalı: " + key)
                    tune[key] = values[key]
            for key, choices in (
                    ("flash_attn", {"on", "off", "auto"}),
                    ("cache_type_k", cache_types), ("cache_type_v", cache_types),
                    ("reasoning", {"on", "off", "auto"}),
                    ("reasoning_effort", {"default", "minimal", "low", "medium",
                                           "high", "xhigh", "max"})):
                if key in values:
                    if not isinstance(values[key], str) or values[key] not in choices:
                        raise ValueError("LLM ayarı geçersiz: " + key)
                    tune[key] = values[key]
            temiz_profiller[profile] = tune
        ayar["llm_model_tuning"] = temiz_profiller
    for key, low, high in (("llm_threads", 1, 64), ("llm_batch", 128, 8192),
                           ("llm_top_k", 0, 1000)):
        if key in req:
            try:
                value = int(req[key])
            except (TypeError, ValueError, OverflowError):
                raise ValueError(key + " sayı olmalı")
            if not low <= value <= high:
                raise ValueError(key + " aralık dışında")
            ayar[key] = value
    for key, low, high in (("llm_top_p", 0.05, 1.0),
                           ("llm_repeat_penalty", 0.8, 2.0)):
        if key in req:
            try:
                value = float(req[key])
            except (TypeError, ValueError, OverflowError):
                raise ValueError(key + " sayı olmalı")
            if not low <= value <= high:
                raise ValueError(key + " aralık dışında")
            ayar[key] = round(value, 3)
    if "llm_temperature" in req:
        try:
            temperature = float(req["llm_temperature"])
        except (TypeError, ValueError, OverflowError):
            raise ValueError("llm_temperature sayı olmalı")
        if not 0.1 <= temperature <= 2.0:
            raise ValueError("llm_temperature 0.1-2.0 aralığında olmalı")
        ayar["llm_temperature"] = round(temperature, 3)
    if "llm_gpu_layers" in req:
        value = req.get("llm_gpu_layers")
        if value == "all":
            ayar["llm_gpu_layers"] = value
        else:
            try:
                layers = int(value)
            except (TypeError, ValueError, OverflowError):
                raise ValueError("GPU katman sayısı geçersiz")
            if not 0 <= layers <= 512:
                raise ValueError("GPU katmanı 0-512 aralığında olmalı")
            ayar["llm_gpu_layers"] = layers
    for key in ("llm_use_mtp", "llm_multimodal"):
        if key in req:
            if not isinstance(req[key], bool):
                raise ValueError(key + " boolean olmalı")
            ayar[key] = req[key]
    if "llm_prompt_destek_yaraticilik" in req:
        try:
            sicaklik = float(req.get("llm_prompt_destek_yaraticilik"))
        except (TypeError, ValueError):
            raise ValueError("prompt desteği yaratıcılığı sayı olmalı")
        ayar["llm_prompt_destek_yaraticilik"] = max(0.2, min(1.2, sicaklik))
    if "llm_prompt_destek_uzunluk" in req:
        uzunluk = req.get("llm_prompt_destek_uzunluk")
        if uzunluk not in ("kisa", "orta", "uzun"):
            raise ValueError("prompt desteği uzunluğu geçersiz")
        ayar["llm_prompt_destek_uzunluk"] = uzunluk
    if "uretim_varsayilan_steps" in req:
        try:
            varsayilan_adim = int(req.get("uretim_varsayilan_steps"))
        except (TypeError, ValueError):
            raise ValueError("varsayılan step sayı olmalı")
        ayar["uretim_varsayilan_steps"] = max(1, min(50, varsayilan_adim))
    if "uretim_varsayilan_seed" in req:
        try:
            varsayilan_seed = int(req.get("uretim_varsayilan_seed"))
        except (TypeError, ValueError):
            raise ValueError("varsayılan seed sayı olmalı")
        ayar["uretim_varsayilan_seed"] = max(-1, min(2 ** 31 - 1, varsayilan_seed))
    if "uretim_varsayilan_max_side" in req:
        try:
            varsayilan_max_side = int(req.get("uretim_varsayilan_max_side"))
        except (TypeError, ValueError):
            raise ValueError("varsayılan maksimum kenar sayı olmalı")
        ayar["uretim_varsayilan_max_side"] = max(256, min(4096, varsayilan_max_side))
    if "uretim_prompt_destek" in req:
        if not isinstance(req.get("uretim_prompt_destek"), bool):
            raise ValueError("uretim_prompt_destek boolean olmalı")
        ayar["uretim_prompt_destek"] = req["uretim_prompt_destek"]
    if "uretim_prompt_ceviri" in req:
        if not isinstance(req.get("uretim_prompt_ceviri"), bool):
            raise ValueError("uretim_prompt_ceviri boolean olmalı")
        ayar["uretim_prompt_ceviri"] = req["uretim_prompt_ceviri"]
    if "uretim_prompt_duzenle" in req:
        if not isinstance(req.get("uretim_prompt_duzenle"), bool):
            raise ValueError("uretim_prompt_duzenle boolean olmalı")
        ayar["uretim_prompt_duzenle"] = req["uretim_prompt_duzenle"]
    if (ayar.get("uretim_prompt_ceviri")
            and ayar.get("uretim_prompt_destek")):
        raise ValueError(
            "Prompt çevirisi ve Prompt Stüdyosu desteği aynı anda açılamaz")


def _gecmis_limiti(kullanici):
    try:
        lim = int(_ayar_oku(kullanici).get("gecmis_limiti", 0))
    except Exception:
        return 0
    return lim if lim > 0 else 0


def _oran_hesapla(ilerleme):
    """Ilerleme yuzdesi adima degil orana dayanir. Donus: 0.0-1.0 veya None."""
    if not isinstance(ilerleme, dict):
        return None
    if ilerleme.get("bitti"):
        return 1.0
    try:
        if ilerleme.get("adim") is not None and ilerleme.get("max"):
            return max(0.0, min(1.0, float(ilerleme["adim"]) / float(ilerleme["max"])))
    except Exception:
        pass
    if ilerleme.get("kuyrukta"):
        return 0.0
    return ilerleme.get("oran")


def _bind_service_function(function):
    def call(*args, **kwargs):
        kwargs.setdefault("runtime", sys.modules[__name__])
        return function(*args, **kwargs)
    call.__name__ = function.__name__
    call.__doc__ = function.__doc__
    return call


def _bind_runtime_first(function):
    def call(*args, **kwargs):
        return function(sys.modules[__name__], *args, **kwargs)
    call.__name__ = function.__name__
    call.__doc__ = function.__doc__
    return call


globals()['_isi_calistir'] = _bind_runtime_first(_generation._isi_calistir)


for _service_name in ('_bekleyen_isi_iptal_et', '_is_budala_kilitli', '_is_canli_ilerleme', '_is_detay', '_is_dogrula', '_is_istegi_temizle', '_is_ozet', '_is_sonucu_baslat', '_isi_iptal_et', '_kullanici_islerini_durdur', '_kuyruga_ekle'):
    globals()[_service_name] = _bind_runtime_first(getattr(_job_queue, _service_name))


for _service_name in ('_gecmis_album_id', '_gecmis_buda', '_gecmis_dosya_favorili', '_gecmis_kayit_bilgisi', '_sohbet_baslik', '_sohbet_dosya', '_sohbet_kaydet', '_sohbet_oku', '_sohbet_sil', '_sohbet_temizle', '_sohbet_yaz', '_video_kapak_uret', 'gecmis_kaydi_sil', 'gecmis_kaydi_yildiz', 'gecmis_listesi', 'gecmis_temizle', 'gecmise_kaydet', 'kutuphane_listesi', 'kutuphane_temizle', 'kutuphaneden_sil', 'kutuphaneye_kaydet', 'prompt_gecmisi_dosya', 'prompt_gecmisi_oku', 'prompt_gecmisi_sil', 'prompt_gecmisi_temizle', 'prompt_gecmisi_yaz', 'prompt_gecmisi_yildiz', 'prompt_kaydet', 'prompt_sil', 'promptlari_oku'):
    globals()[_service_name] = _bind_runtime_first(getattr(_user_content, _service_name))
for _service_name in ('_dosya_isit', '_imge_modeli_isitme_karari', '_iyilestirme_cikti_temizle', '_iyilestirme_comfy_isini_yurut', '_iyilestirme_ffprobe', '_iyilestirme_istegi_dogrula', '_iyilestirme_kaydi', '_iyilestirme_modelini_hazirla', '_medyayi_iyilestir', '_uretim_sonrasi_iyilestir', '_uretim_sonrasi_iyilestirme_istegi_dogrula'):
    globals()[_service_name] = _bind_runtime_first(getattr(_media_enhancement, _service_name))


for _service_name in (
    "is_tipini_belirle", "video_parametreleri", "_video_fps_yuvarla",
    "_video_kare_sayisi", "_wan22_kare_sayisi", "_video_boyut",
    "_video_sablon_yukle", "_wan22_video_sablon_yukle",
    "_video_diskten_bul", "_video_uret",
):
    globals()[_service_name] = _bind_service_function(
        getattr(_video_generation, _service_name)
    )

COMFY_KOK = os.path.abspath(os.environ.get(
    "COMFYUI_ROOT", os.path.join(BASE_DIR, "..", "ComfyUI")))


def _simdiki_zaman_damgasi():
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")

_aktif = {"prompt_id": None, "ilerleme": None, "baslangic": 0, "nodlar": {},
          "sahip": None}
_aktif_llm = {"is_id": None, "sahip": None, "tur": None,
             "durum": None, "baslangic": 0}

# --- Is kuyrugu: sayfa yenilense bile isler sunucuda surer, sonuc gecmis+is kaydinda durur ---
_isler = {}
_isler_kilit = threading.Lock()
_llm_is_gecis_kilit = threading.Lock()
_sohbet_kilit = threading.Lock()
_http_yazma_kosulu = threading.Condition()
_http_yazma_istekleri = set()
_kasa_yazma_istekleri = {}
_kuyruk = []  # bekleyen is_id'ler (FIFO)
_kuyruk_calismasi = False
_ceviri_kuyrugu = []  # CPU hazırlığı, GPU kuyruğunun sırasını değiştirmez.
_ceviri_calismasi = False
_LLM_VARSAYILAN_CTX = llm.CTX
_SAKLANAN_IS_SAYISI = 10  # bitmis islerden en fazla bu kadar/sonuclariyla (dataURL) tutulur


def _ceviri_worker():
    """Tek CPU işçisi, sıradaki görsel işlerinin çevirisini önceden hazırlar."""
    global _ceviri_calismasi
    while True:
        with _isler_kilit:
            if not _ceviri_kuyrugu:
                _ceviri_calismasi = False
                return
            job = _ceviri_kuyrugu.pop(0)
            if job["iptal"]:
                job["ceviri_hazir"].set()
                continue
            req = job["istek"]
            metin = str(req.get("prompt_girdi") or req.get("prompt") or "")
            job["ceviri_durum"] = "calisiyor"
        try:
            sonuc = prompt_translation.cevir(
                metin, job["sahip"], iptal=lambda: job["iptal"])
            with _isler_kilit:
                if not job["iptal"]:
                    job["ceviri_sonuc"] = sonuc
                    job["ceviri_durum"] = "hazir"
        except Exception as e:
            with _isler_kilit:
                if not job["iptal"]:
                    job["ceviri_hata"] = str(e)[:1000]
                    job["ceviri_durum"] = "hata"
        finally:
            job["ceviri_hazir"].set()


def _llm_prompt_gecmisine_yaz(sahip, req, sonuc):
    try:
        prompt_gecmisi_yaz(sahip, {
            "id": uuid.uuid4().hex[:12],
            "tarih": _dt.datetime.now().isoformat(timespec="seconds"),
            "mod": req.get("mod"), "metin": (req.get("metin") or "")[:2000],
            "dil": req.get("dil"), "yaraticilik": req.get("yaraticilik"),
            "uzunluk": req.get("uzunluk"),
            "gorsel_sayisi": len(req.get("gorseller") or []),
            "positive": sonuc.get("positive"), "positive_tr": sonuc.get("positive_tr"),
            "negative": sonuc.get("negative"), "caption_tr": sonuc.get("caption_tr"),
            "model_prompt_profile": req.get("model_prompt_profile") or "auto",
            "bbox_mode": bool(req.get("bbox_mode")),
            "tags": sonuc.get("tags"), "sure_ms": sonuc.get("sure_ms"),
            "token": sonuc.get("token"), "tok_s": sonuc.get("tok_s"),
            "prompt_token": sonuc.get("prompt_token"),
            "yukleme_ms": sonuc.get("yukleme_ms"), "deneme": sonuc.get("deneme"),
            "ctx": sonuc.get("ctx")})
    except Exception:
        pass


RUNPOD_VIDEO_PROMPT_CTX = 16384


def _llm_istek_baglami(ayar, tur, req):
    """Keep long RunPod video prompts inside the model context without saving a user setting."""
    profile = req.get("llm_model") or ayar.get("llm_model")
    tuning = (ayar.get("llm_model_tuning") or {}).get(profile, {})
    try:
        context = int(tuning.get("context") or ayar.get("llm_ctx") or
                      next((item.get("tuning_defaults", {}).get("context")
                            for item in llm.model_profilleri()
                            if item.get("id") == profile), _LLM_VARSAYILAN_CTX))
    except (TypeError, ValueError, OverflowError):
        context = _LLM_VARSAYILAN_CTX
    if not RUNPOD_MODE:
        return context
    video_prompt = (tur == "llm_prompt"
                    and (req.get("model_prompt_profile") in ("minimaxh3", "wan22")
                         or req.get("mod") == "video"))
    if tur == "goruntu" and req.get("prompt_destek"):
        references = int(bool(req.get("image_base64"))) + int(bool(req.get("image2_base64")))
        video_prompt = is_tipini_belirle(req, references) == IS_TIPI_VIDEO
    return max(context, RUNPOD_VIDEO_PROMPT_CTX) if video_prompt else context


def _llm_kuyruk_isini_calistir(job):
    global _aktif_llm
    req = job.get("istek") or {}
    sahip = job.get("sahip")
    tur = job.get("tur")
    with _llm_is_gecis_kilit:
        if job["iptal"]:
            raise RuntimeError("İş durduruldu")
        _aktif_llm = {"is_id": job["is_id"], "sahip": sahip, "tur": tur,
                      "durum": "Hazırlanıyor", "baslangic": job["baslangic"]}
    def gpu_hazirla():
        _dbg("chat_bosalti_basla")
        ok, neden = _comfy_bosalti_ve_dogrula(job=job)
        _dbg(f"chat_bosalti_bitis ok={ok}")
        if job.get("iptal") or neden == "iptal":
            raise RuntimeError("İş durduruldu")
        if not ok:
            raise RuntimeError(neden or "GPU belleği hazırlanamadı")

    try:
        if job.get("iptal"):
            raise RuntimeError("iptal edildi")
        ayar = _ayar_oku(sahip)
        llm_secimi = req.get("llm_model") or ayar.get(
            "llm_model", "llm_26b" if RUNPOD_MODE else "llama")
        if RUNPOD_MODE:
            profiles = {item["id"]: item for item in llm.model_profilleri()}
            if llm_secimi not in profiles:
                raise RuntimeError("Seçilen LLM modeli bu Pod'da tanımlı değil.")
            if not pod_setup.workflow_ready(llm_secimi):
                raise RuntimeError("Seçilen LLM henüz indirilmedi. Kurulum ekranından bu modeli hazırlayın.")
            llm.model_ayarla(llm_secimi)
            llm.ayarlarini_uygula(ayar, llm_secimi)
        canli = llm.durum()["canli"]
        try:
            llm.baglam_ayarla(_llm_istek_baglami(ayar, tur, req))
        except llm.LLMHatasi:
            pass
        if llm.baglam_degisti() and not llm.istek_suruyor():
            # Baglam ayari degisti: bos duran sunucuyu kapat, bu is yeni
            # baglamla yeniden baslatir. Aktif istek kesilmez (FIFO tek is).
            llm.durdur()
            canli = False
        if canli:
            job["ilerleme"] = {"asama": "hazirlik",
                               "calisan": "Model hazır",
                               "oran": 0.1}
        else:
            job["ilerleme"] = {"asama": "yukleme",
                               "calisan": "Model yükleniyor", "oran": 0.2}
        if tur == "llm_preload":
            _aktif_llm["durum"] = "Model yükleniyor"
            sonuc = llm.hazirla(gpu_hazirla=gpu_hazirla)
            _is_sonucu_baslat(job)
            job["sonuc"] = sonuc
        elif tur == "llm_prompt":
            if not canli:
                job["ilerleme"] = {"asama": "yukleme",
                                   "calisan": "Prompt hazırlanıyor",
                                   "oran": 0.2}
            _aktif_llm["durum"] = "Prompt hazırlanıyor"
            sonuc = llm.uret(req.get("mod"), req.get("metin"),
                             req.get("gorseller") or [], req.get("dil"),
                             req.get("yaraticilik"), req.get("uzunluk"),
                             sistem_ozel=ayar.get("llm_sistem"),
                             mod_ozel=(ayar.get("llm_mod") or {}).get(req.get("mod")),
                             model_prompt_profile=(req.get("model_prompt_profile")
                                                   if req.get("model_prompt_profile") in
                                                   ("general", "z_image", "flux3", "krea2", "illustrious", "minimaxh3", "wan22") else None),
                             bbox_mode=bool(req.get("bbox_mode")),
                             gpu_hazirla=gpu_hazirla)
            _is_sonucu_baslat(job)
            _llm_prompt_gecmisine_yaz(sahip, req, sonuc)
            job["sonuc"] = sonuc
        elif tur == "goruntu" and req.get("prompt_destek"):
            job["ilerleme"] = {"asama": "prompt", "calisan": "AI prompt oluşturuyor", "oran": 0.08}
            _aktif_llm["tur"] = "llm_prompt"
            _aktif_llm["durum"] = "AI prompt oluşturuyor"
            metin = str(req.get("prompt") or "").strip()
            n_ref = (1 if req.get("image_base64") else 0) + (1 if req.get("image2_base64") else 0)
            is_video = is_tipini_belirle(req, n_ref) == IS_TIPI_VIDEO
            mod = ("video" if is_video else
                   ("birlestir" if n_ref == 2 else ("gorsel" if n_ref else "fikir")))
            gorseller = []
            if req.get("image_base64"):
                gorseller.append({"mime": req.get("image_mime") or "image/jpeg",
                                  "veri": req["image_base64"]})
            if req.get("image2_base64"):
                gorseller.append({"mime": req.get("image2_mime") or "image/jpeg",
                                  "veri": req["image2_base64"]})
            uzunluk = ayar.get("llm_prompt_destek_uzunluk", "orta")
            yaraticilik = ayar.get("llm_prompt_destek_yaraticilik", 0.7)
            sonuc = llm.uret(
                mod, metin, gorseller, "en", yaraticilik, uzunluk,
                sistem_ozel=ayar.get("llm_sistem"),
                mod_ozel=(ayar.get("llm_mod") or {}).get(mod),
                gpu_hazirla=gpu_hazirla,
                model_prompt_profile=(
                    ("wan22" if req.get("video_model_instance") == "wan22" else "minimaxh3") if is_video else
                    (req.get("model") if req.get("model") in
                     ("z_image", "flux3", "krea2", "illustrious") else None)),
                bbox_mode=bool(req.get("flux3_bbox_mode")))
            _llm_prompt_gecmisine_yaz(sahip, {
                "mod": mod, "metin": metin, "dil": "en",
                "yaraticilik": yaraticilik, "uzunluk": uzunluk,
                "gorseller": gorseller}, sonuc)
            if job.get("iptal"):
                raise RuntimeError("iptal edildi")
            req["prompt_girdi"] = metin
            req["prompt_en"] = sonuc.get("positive") or metin
            req["prompt"] = sonuc.get("positive") or metin
            if sonuc.get("negative"):
                req["negative"] = sonuc["negative"]
            req.pop("gorseller", None)
            req.pop("metin", None)
            job["ilerleme"] = {"asama": "gorsel", "calisan": "AI prompt hazır · görsel üretime geçiliyor", "oran": 0.18}
            job["_prompt_destek_devam"] = True
        else:
            if not canli:
                job["ilerleme"] = {"asama": "yukleme",
                                   "calisan": "Sohbet yanıtı hazırlanıyor",
                                   "oran": 0.2}
            _aktif_llm["durum"] = "Sohbet yanıtı hazırlanıyor"
            eski_sistem = llm.SISTEM_PROMPTU
            eski_mod = dict(llm.MOD_TALIMATI)
            try:
                sohbet_sistem = ayar.get("llm_sohbet_sistem") or llm.SOHBET_SISTEM_PROMPTU
                llm.SISTEM_PROMPTU = sohbet_sistem
                sohbet_ozel = (ayar.get("llm_mod") or {}).get("sohbet")
                if sohbet_ozel:
                    llm.MOD_TALIMATI["sohbet"] = sohbet_ozel
                    llm.SISTEM_PROMPTU += "\n" + llm.MOD_TALIMATI["sohbet"]
                sohbet_id = req.get("sohbet_id") or uuid.uuid4().hex[:12]

                # Mesajlari temizle ve dogrula: sadece role/content'li gecerli ogelari gecir.
                ham_mesajlar = req.get("mesajlar") or []
                temiz_mesajlar = []
                if isinstance(ham_mesajlar, list):
                    for m in ham_mesajlar:
                        if not isinstance(m, dict):
                            continue
                        rol = m.get("role")
                        if rol not in ("user", "assistant"):
                            continue
                        icerik = m.get("content")
                        if isinstance(icerik, str):
                            temiz_mesajlar.append({"role": rol, "content": icerik})
                        elif isinstance(icerik, list):
                            temiz_mesajlar.append({"role": rol, "content": icerik})
                if not llm.MULTIMODAL_ENABLED and any(
                        isinstance(message.get("content"), list)
                        and any(isinstance(part, dict) and part.get("type") == "image_url"
                                for part in message["content"])
                        for message in temiz_mesajlar):
                    raise RuntimeError(
                        "Seçili model ayarlarında görsel anlama kapalı. "
                        "Ayarlar → LLM Ayarları bölümünden açın.")
                # Ek talimat (orn. sohbeti prompta cevirme) yalniz modele gider,
                # kaydedilen sohbet gecmisine yazilmaz.
                ek_talimat = str(req.get("ek_talimat") or "").strip()[:2000]
                istek_mesajlari = list(temiz_mesajlar)
                if ek_talimat:
                    istek_mesajlari.append({"role": "user", "content": ek_talimat})

                def dusunme_guncelle(parca):
                    if job["iptal"]:
                        raise RuntimeError("İş durduruldu")
                    sonuc = job.get("sonuc")
                    if sonuc is None or not isinstance(sonuc, dict):
                        sonuc = job["sonuc"] = {}
                    sonuc["dusunme"] = (sonuc.get("dusunme") or "") + parca
                    sonuc["sohbet_id"] = sohbet_id
                    job["ilerleme"] = {"asama": "dusunme",
                                       "calisan": "Düşünüyor", "oran": 0.5}

                def parca_guncelle(parca):
                    if job["iptal"]:
                        raise RuntimeError("İş durduruldu")
                    sonuc = job.get("sonuc")
                    if sonuc is None or not isinstance(sonuc, dict):
                        sonuc = job["sonuc"] = {}
                    sonuc["parca"] = (sonuc.get("parca") or "") + parca
                    sonuc["sohbet_id"] = sohbet_id
                    job["ilerleme"] = {"asama": "yanit",
                                       "calisan": "Yanıt yazılıyor", "oran": 0.6}

                job["sonuc"] = {}
                model_tuning = (ayar.get("llm_model_tuning") or {}).get(llm_secimi, {})
                yanit, olcum = llm.sohbet_et(
                    istek_mesajlari, req.get("yaraticilik", model_tuning.get(
                        "temperature", ayar.get("llm_temperature", 0.7))),
                    req.get("max_tokens"), parca_geri=parca_guncelle,
                    dusunme_geri=dusunme_guncelle,
                    stream=bool(ayar.get("llm_streaming", True)),
                    gpu_hazirla=gpu_hazirla)
            finally:
                llm.SISTEM_PROMPTU = eski_sistem
                llm.MOD_TALIMATI.clear()
                llm.MOD_TALIMATI.update(eski_mod)
            _is_sonucu_baslat(job)
            olcum = dict(olcum or {})
            olcum["yanit"] = yanit
            olcum["sohbet_id"] = sohbet_id
            job["sonuc"] = olcum
            try:
                _sohbet_kaydet(sahip, temiz_mesajlar, yanit, olcum, req.get("baslik"), req.get("sohbet_id"))
            except Exception:
                pass
        if job.get("_prompt_destek_devam"):
            job["ilerleme"] = {"calisan": "AI prompt hazır · görsel üretime geçiliyor",
                               "oran": 0.18}
        else:
            job["ilerleme"] = {"bitti": True, "oran": 1.0}
            job["durum"] = "bitti"
    except Exception as e:
        durduruldu = (job.get("iptal") or
                      (isinstance(e, llm.LLMHatasi) and e.kod == "iptal") or
                      str(e).strip().casefold() == "iptal")
        if not durduruldu:
            traceback.print_exc()
            try:
                with open(os.path.join(BASE_DIR, "webui_error.log"), "a", encoding="utf-8") as ef:
                    ef.write("===== %s =====\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
                    traceback.print_exc(file=ef)
                    ef.write("\n\n")
            except Exception:
                pass
        job["durum"] = "durduruldu" if durduruldu else "hata"
        job["hata"] = "İş durduruldu" if durduruldu else str(e)[:1000]
    finally:
        job["bitis"] = time.time()
        with _llm_is_gecis_kilit:
            _aktif_llm = {"is_id": None, "sahip": None, "tur": None,
                          "durum": None, "baslangic": 0}


def _kuyruk_worker():
    """ComfyUI ve llama-server aynı GPU'yu sırayla kullanır."""
    global _kuyruk_calismasi
    while True:
        with _isler_kilit:
            if not _kuyruk:
                _kuyruk_calismasi = False
                return
            job = _isler[_kuyruk.pop(0)]
            job["durum"] = "calisiyor"
            job["baslangic"] = time.time()
            job["ilerleme"] = {"calisan": "Hazırlanıyor", "oran": 0.0}
        try:
            _gpu_isini_calistir(job)
        except Exception as e:
            job["durum"] = "durduruldu" if job["iptal"] else "hata"
            job["hata"] = str(e)[:1000]
            job["bitis"] = time.time()
        finally:
            with _isler_kilit:
                job.pop("ceviri_sonuc", None)
                job.pop("ceviri_hata", None)
                if job["iptal"] and "ceviri_hazir" in job:
                    job["ceviri_durum"] = "durduruldu"
                _is_istegi_temizle(job)
                job["olay"].set()


def _gpu_isini_calistir(job):
    if job["iptal"]:
        raise RuntimeError("İş durduruldu")
    tur = job["tur"]
    if tur in ("llm_prompt", "llm_chat", "llm_preload"):
        _llm_kuyruk_isini_calistir(job)
        return

    if "ceviri_hazir" in job:
        job["ilerleme"] = {"asama": "ceviri",
                           "calisan": "Türkçe prompt İngilizceye çevriliyor",
                           "oran": 0.04}
        while not job["ceviri_hazir"].wait(0.2):
            if job["iptal"]:
                raise RuntimeError("İş durduruldu")
        if job.get("ceviri_hata"):
            raise RuntimeError(job["ceviri_hata"])
    if job["iptal"]:
        raise RuntimeError("İş durduruldu")

    if job["istek"].get("prompt_destek"):
        _llm_kuyruk_isini_calistir(job)
        if not job.pop("_prompt_destek_devam", False):
            return
        if job["iptal"]:
            raise RuntimeError("İş durduruldu")
        job["ilerleme"] = {"calisan": "Görsel üretime gönderiliyor", "oran": 0.2}

    if llm.durum()["canli"]:
        req = job.get("istek") or {}
        model = "enhance" if req.get("postprocess") in ("image", "video") else (
            req.get("model") or req.get("image_model"))
        if model in (None, "auto"):
            refs = bool(req.get("image_base64") or req.get("image2_base64"))
            model = MODEL_FLUX2 if refs else MODEL_Z_IMAGE
        if is_tipini_belirle(
                req, int(bool(req.get("image_base64"))) +
                int(bool(req.get("image2_base64")))) == IS_TIPI_VIDEO:
            model = "video"
        vram_bos = _vram_bos_mb()
        gerekli_gorsel = RUNPOD_GORSEL_VRAM_MB.get(
            model, RUNPOD_GORSEL_VRAM_MB.get("z_image", 12000))
        # LLM GPU'da kalırken görsel modelin de açılabileceği ölçülüyorsa
        # gereksiz yeniden yüklemeyi önle. Ölçüm yoksa güvenli tarafta kal.
        llama_kalabilir = (RUNPOD_MODE and vram_bos is not None
                           and vram_bos >= gerekli_gorsel + 1200)
        if llama_kalabilir:
            job["_llama_canliydi"] = True
            job["_llama_bellekte_kaldi"] = True
            job["ilerleme"] = {"asama": "bellek",
                               "calisan": "Sohbet modeli bellekte tutuluyor",
                               "oran": 0.03}
        else:
            job["_llama_canliydi"] = True
            if not _llm_kapat_ve_bekle():
                raise RuntimeError("llama-server kapanmadı; görsel işi başlatılamadı")
    if job["iptal"]:
        raise RuntimeError("İş durduruldu")
    _isi_calistir(job)


def _kasa_durum_bilgi(kullanici):
    """Arayuzun gosterdigi kasa durumu. Ozel icerik tasimaz."""
    try:
        d = kasa.durum(kullanici)
    except Exception:
        d = {"acik": False, "kapaniyor": False, "kalan_sn": 0}
    try:
        kasa_var = os.path.isfile(os.path.join(kasa.KASALAR_DIR, kullanici + ".tar.gpg"))
    except Exception:
        kasa_var = False
    try:
        calisma = os.path.join(kasa.CALISMA_DIR, kullanici)
        kurtarma = (not d.get("acik")) and os.path.isdir(calisma) \
            and bool(os.listdir(calisma))
    except Exception:
        kurtarma = False
    d.update({"kasa_var": kasa_var, "kurtarma": kurtarma})
    return d


def _comfy_isini_kes(prompt_id):
    # Önce bekleyen işi sil; o arada başlamışsa kimliğiyle kes.
    for yol, istek in (("/queue", {"delete": [prompt_id]}),
                        ("/interrupt", {"prompt_id": prompt_id})):
        try:
            comfy_post_json(yol, istek)
        except Exception:
            pass


def _kasa_isteklerini_bekle(kullanici):
    with _http_yazma_kosulu:
        return _http_yazma_kosulu.wait_for(
            lambda: not _kasa_yazma_istekleri.get(kullanici), timeout=30)


def _kasa_islerini_tamamla(kullanici):
    if not _kasa_isteklerini_bekle(kullanici):
        raise RuntimeError("Kasa yazımları tamamlanmadı")
    if not _kullanici_islerini_durdur(kullanici, bekle=True, tavan_sn=20):
        raise RuntimeError("Kasa işleri tamamlanmadı")
    prompt_translation.onbellek_temizle(kullanici)


def _kasa_sure_dolumu(kullanici):
    """Bekçi çağırır: önce yazımları ve üretimi tamamla, sonra mühürle."""
    if _kapatma_istegi.is_set():
        kasa.kapanis_iptal(kullanici)
        return
    _kasa_islerini_tamamla(kullanici)
    kasa.kapat(kullanici)


def _kasa_kapat_arka(kullanici):
    """Cikis/kapat: bayragi koy, isleri durdur, arka planda muhurle. Hemen doner."""
    try:
        if not kasa.kapanis_baslat(kullanici):
            return False
    except Exception as e:
        print("Kasa kapanışı başlatılamadı (%s): %s" % (kullanici, e), flush=True)
        raise

    def _arka():
        try:
            _kasa_islerini_tamamla(kullanici)
        except Exception as e:
            print("Kasa işleri durdurulamadı (%s): %s" % (kullanici, e), flush=True)
            kasa.kapanis_iptal(kullanici)
            return
        try:
            kasa.kapat(kullanici)
        except Exception as e:
            print("Kasa kapatma başarısız (%s): %s" % (kullanici, e), flush=True)
    thread = threading.Thread(target=_arka, daemon=True)
    try:
        thread.start()
    except Exception:
        kasa.kapanis_iptal(kullanici)
        raise
    return True


kasa.bekci_baslat(_kasa_sure_dolumu)


def _ws_ilerleme_dinle(prompt_id, client_id, nodlar):
    """ComfyUI websocket'ine abone olup adim/node ilerlemesini _aktif'e yazar."""
    import socket as _sock, base64 as _b64, os as _os, struct as _st

    def _oku(s, n):
        buf = b""
        while len(buf) < n:
            c = s.recv(n - len(buf))
            if not c:
                raise ConnectionError("ws kapandi")
            buf += c
        return buf

    s = None
    try:
        s = _sock.create_connection(("127.0.0.1", 8188), timeout=10)
        key = _b64.b64encode(_os.urandom(16)).decode()
        s.sendall(("GET /ws?clientId=%s HTTP/1.1\r\nHost: 127.0.0.1:8188\r\n"
                   "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                   "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n"
                   % (client_id, key)).encode())
        head = b""
        while b"\r\n\r\n" not in head:
            head += s.recv(1024)
        if b"101" not in head.split(b"\r\n")[0]:
            return
        s.settimeout(3)
        while _aktif.get("prompt_id") == prompt_id:
            try:
                h = _oku(s, 2)
            except Exception:
                continue
            op, ln = h[0] & 0x0F, h[1] & 0x7F
            if ln == 126:
                ln = _st.unpack(">H", _oku(s, 2))[0]
            elif ln == 127:
                ln = _st.unpack(">Q", _oku(s, 8))[0]
            payload = _oku(s, ln) if ln else b""
            if op == 0x9:
                s.sendall(b"\x8a\x00")
                continue
            if op == 0x8 or op != 0x1:
                if op == 0x8:
                    break
                continue
            try:
                msg = json.loads(payload.decode("utf-8"))
            except Exception:
                continue
            t, d = msg.get("type"), msg.get("data") or {}
            if d.get("prompt_id") != prompt_id:
                continue
            if t == "progress":
                try:
                    _oran = float(d.get("value")) / float(d.get("max"))
                    _oran = max(0.0, min(1.0, _oran))
                except Exception:
                    _oran = None
                _aktif["ilerleme"] = {"adim": d.get("value"), "max": d.get("max"),
                                      "node": nodlar.get(str(d.get("node")), d.get("node")),
                                      "oran": _oran}
            elif t == "executing":
                nd = d.get("node")
                if nd is None:
                    _aktif["ilerleme"] = {"bitti": True, "oran": 1.0}
                    break
                _onceki = _aktif.get("ilerleme") or {}
                _aktif["ilerleme"] = {"calisan": nodlar.get(str(nd), str(nd)),
                                      "oran": _oran_hesapla(_onceki)}
    except Exception:
        pass
    finally:
        try:
            if s is not None:
                s.close()
        except Exception:
            pass


# llama.cpp needs room for its full 38-layer profile: it owns ~3.7 GB of the
# 4 GB card on its own (observed load ramp). ComfyUI's own CUDA context +
# buffers stay resident after /free, so ~3.4 GB free is the realistic best
# case; starting llama with far less makes the WDDM driver spill allocations
# into shared GPU memory, which turns every load and every token into a
# PCIe crawl. Gate high and stop waiting once VRAM stops growing.
# RunPod loads the full Gemma model, projection and context onto the GPU.
# Leave headroom for CUDA allocations after ComfyUI releases its models.
LLM_VRAM_GEREK_MB = 9000 if RUNPOD_MODE else 2600
RUNPOD_GORSEL_VRAM_MB = {"z_image": 10000, "flux2": 16500, "flux3": 12500,
                         "krea2": 22500, "qwen_image_edit": 23000,
                         "qwen_image_edit_turbo": 23000,
                         "illustrious": 10500, "video": 26000, "enhance": 8000}
GPU_BOSALTMA_TAVAN_SN = 20
GPU_BOSALTMA_BEKLE = 0.25   # nvidia-smi tek cagri ~80ms; sik poll yuklemeyi hizlandirir
LLM_KAPATMA_BEKLE_SN = 30  # llama durmazsa goruntu isi bu kadar bekler
_bellek_bosaltma_aktif = False


def _vram_bos_mb():
    """GPU bos MB. Once nvidia-smi (ComfyUI'den bagimsiz), olmazsa
    ComfyUI /system_stats. Ikisi de yoksa None."""
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.free",
             "--format=csv,noheader,nounits"],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=3).decode("utf-8", "replace")
        return max(0, int(out.strip().splitlines()[0].split()[0]))
    except Exception:
        pass
    try:
        bilgi = comfy_get_json("/system_stats")
        cihazlar = bilgi.get("devices") or []
        if not cihazlar:
            return None
        return max(0, int((cihazlar[0].get("vram_free") or 0) / (1024 * 1024)))
    except Exception:
        return None


def _llm_gerekli_vram_mb():
    return llm.profil_vram_mb() if RUNPOD_MODE else LLM_VRAM_GEREK_MB


def _comfy_bosalti_ve_dogrula(job=None, zorla=False):
    """ComfyUI bellegini bosaltir; llama icin yeterli VRAM olustugunu olcer."""
    if job is not None and job.get("iptal"):
        return False, "iptal"
    gerekli_vram = _llm_gerekli_vram_mb()
    vram_bos = _vram_bos_mb()
    if vram_bos is None and not zorla:
        return False, "Bos VRAM olculemedi; LLM baslatilmadi"
    if not zorla and vram_bos is not None and vram_bos >= gerekli_vram:
        return True, ""
    try:
        comfy_kuyruk = comfy_get_json("/queue")
    except Exception as e:
        return False, "ComfyUI kuyrugu dogrulanamadi; bellek bosaltilmadi: %s" % e
    if comfy_kuyruk.get("queue_running") or comfy_kuyruk.get("queue_pending"):
        return False, "ComfyUI kuyrugunda is varken modeller bosaltilamaz"
    if job is not None:
        job["ilerleme"] = {"calisan": "ComfyUI modelleri bosaltiliyor",
                           "oran": 0.08}
    try:
        status, _ = comfy_request(
            "POST", "/free",
            body=json.dumps({"unload_models": True, "free_memory": True}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        if status != 200:
            return False, "ComfyUI bellek bosaltma istegi basarisiz (%s)" % status
    except Exception as e:
        return False, "ComfyUI bellek bosaltma istegi basarisiz: %s" % e
    son_tarih = time.monotonic() + GPU_BOSALTMA_TAVAN_SN
    tur = 0
    while time.monotonic() < son_tarih:
        if job is not None and job.get("iptal"):
            return False, "iptal"
        time.sleep(min(GPU_BOSALTMA_BEKLE,
                       max(0, son_tarih - time.monotonic())))
        vram_bos = _vram_bos_mb()
        tur += 1
        if vram_bos is not None and vram_bos >= gerekli_vram:
            if job is not None:
                job["ilerleme"] = {"calisan": "VRAM hazir (%d MB bos)" % vram_bos,
                                   "oran": 0.12}
            return True, ""
        if job is not None and tur % 5 == 0:
            job["ilerleme"] = {"calisan": "VRAM bekleniyor (%s MB bos)" % vram_bos,
                               "oran": 0.08}
    if zorla and vram_bos is not None:
        return True, ""
    return False, ("VRAM yetersiz: %s MB bos, secilen LLM icin en az %d MB "
                   "gerekiyor. ComfyUI islerinin bitmesini bekleyin."
                   % (vram_bos, gerekli_vram))

def _llm_kapat_ve_bekle(tavan_sn=None):
    """llama-server'i durdurur, port bosalana dek bekler.
    _bekleyen_prompt kilidi altinda cagrilir; art arda islerde VRAM'in
    bosa ciktigini nvidia-smi ile dogrular."""
    if tavan_sn is None:
        tavan_sn = LLM_KAPATMA_BEKLE_SN
    try:
        llm.durdur()
    except Exception:
        pass
    t0 = time.monotonic()
    while time.monotonic() - t0 < tavan_sn:
        try:
            with urllib.request.urlopen(
                    "http://127.0.0.1:%d/health" % llm.PORT, timeout=3):
                time.sleep(0.5)
                continue
        except Exception:
            return True
    return False


_GECMIS_KILIT = threading.RLock()


# --- Ilk-kurulum migrasyonu: eski paylasimli veriler ilk admin'e tasinir ---
# Kaynakta iz birakilmaz, eski yola donus yoktur. Sayilar 2. bolumdeki notla
# (kutuphane 9 dosya, gecmis 60 dosya) karsilastirilir.

def _eski_veri_say():
    bilgi = {}
    for ad, yol in (("kutuphane", KUTUPHANE_DIR), ("gecmis", GECMIS_DIR)):
        try:
            dosyalar = [f for f in os.listdir(yol)
                        if os.path.isfile(os.path.join(yol, f))]
            bilgi[ad] = {"dosya": len(dosyalar),
                         "bayt": sum(os.path.getsize(os.path.join(yol, f))
                                     for f in dosyalar)}
        except Exception:
            bilgi[ad] = {"dosya": 0, "bayt": 0}
    return bilgi


def _ilk_migrasyon(admin):
    """Eski dosyalari veriler/<admin>/ altina TASIR. Donus: tasinan sayilar."""
    _kullanici_klasorleri(admin)
    sonuc = _eski_veri_say()
    ham = _ham_kullanici_koku(admin)
    for eski, yeni in ((KUTUPHANE_DIR, os.path.join(ham, "kutuphane")),
                       (GECMIS_DIR, os.path.join(ham, "gecmis"))):
        if os.path.isdir(eski):
            for ad in os.listdir(eski):
                kaynak = os.path.join(eski, ad)
                hedef = os.path.join(yeni, ad)
                try:
                    if os.path.isfile(kaynak) and not os.path.exists(hedef):
                        shutil.move(kaynak, hedef)
                except Exception:
                    pass
            try:
                if not os.listdir(eski):
                    os.rmdir(eski)
            except Exception:
                pass
    for eski, yeni in ((PROMPTLAR_DOSYA, os.path.join(ham, "promptlar.json")),
                       (AYARLAR_DOSYA, os.path.join(ham, "ayarlar.json"))):
        try:
            if os.path.isfile(eski) and not os.path.exists(yeni):
                shutil.move(eski, yeni)
        except Exception:
            pass
    return sonuc


# Cocuklara yonelik cinsel icerik + rizasiz ciplaklik istemleri bu makinede
# barindirilmaz: migrasyonda elenir, yeni kayitlarda reddedilir.
_ZARARLI_ISTEM_PARCALARI = (
    "phedopil", "pedofil", "9 years", "school chield", "school festival",
    "erotic clothes", "transparan", "remove all of her clothes",
    "make she nude", "hold her breasts", "small tits",
)


def _istem_zararli_mi(kayit):
    try:
        metin = " ".join(str(kayit.get(k) or "") for k in ("ad", "prompt", "negative"))
        metin = metin.lower()
        return any(p in metin for p in _ZARARLI_ISTEM_PARCALARI)
    except Exception:
        return False


def _zararli_prompt_temizle(kullanici):
    """Donus: (toplam, silinen). Kurulum sirasinda ham klasorde calisir."""
    yol = os.path.join(_ham_kullanici_koku(kullanici), "promptlar.json")
    try:
        with open(yol, "r", encoding="utf-8") as f:
            liste = json.load(f)
            if not isinstance(liste, list):
                liste = []
    except Exception:
        return 0, 0
    temiz = [k for k in liste if not _istem_zararli_mi(k)]
    silinen = len(liste) - len(temiz)
    if silinen:
        with open(yol, "w", encoding="utf-8") as f:
            json.dump(temiz, f, ensure_ascii=False, indent=2)
    return len(liste), silinen


def wait_result(prompt_id, timeout=900, job=None):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if job is not None and job.get("iptal"):
            raise RuntimeError("uretim durduruldu.")
        if _aktif.get("prompt_id") != prompt_id:
            raise RuntimeError("uretim durduruldu.")
        try:
            h = comfy_get_json("/history/%s" % prompt_id)
        except Exception:
            time.sleep(1.0)
            continue
        if prompt_id in h:
            entry = h[prompt_id]
            if entry.get("status", {}).get("completed"):
                return entry
            if entry.get("status", {}).get("status_str") == "error":
                raise RuntimeError("ComfyUI hatasi: %s" % json.dumps(entry)[:800])
        time.sleep(1.0)
    raise TimeoutError("ComfyUI %ss içinde bitirmedi." % timeout)


def fetch_view_image(img):
    q = urllib.parse.urlencode({"filename": img["filename"],
                                "subfolder": img.get("subfolder", ""),
                                "type": img.get("type", "output")})
    status, data = comfy_request("GET", "/view?%s" % q)
    if status != 200:
        raise RuntimeError("view -> %s" % status)
    return data


_IMGE_ISITMA_OKUMA = 8 * 1024 * 1024
_IMGE_ISITMA_ATLA_SN = 120   # son imge isinden yakin varsa dosyalar henuz sicaktir
_TE_DOSYALARI = {
    MODEL_FLUX2: TEXT_ENCODER_FILE,
    MODEL_Z_IMAGE: Z_IMAGE_TEXT_ENCODER_FILE,
    MODEL_ANIMA: ANIMA_TEXT_ENCODER_FILE,
    MODEL_MOODY_ANIMA: ANIMA_TEXT_ENCODER_FILE,
}
_son_goruntu_etkilesim = None


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "Flux2WebUI/1.0"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json", ek_baslik=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # Ozel veri diskte onbellege yazilmasin.
        self.send_header("Cache-Control", "no-store")
        for k, v in (ek_baslik or []):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path, filename, ctype="application/octet-stream"):
        size = os.path.getsize(path)
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", filename)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", 'attachment; filename="%s"' % safe)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with open(path, "rb") as stream:
            shutil.copyfileobj(stream, self.wfile, length=1024 * 1024)

    def _serve_react_preview(self, parsed):
        root = os.path.join(BASE_DIR, "frontend-dist")
        if parsed.path in ("/", "/index.html"):
            path = os.path.join(root, "index.html")
            content_type = "text/html; charset=utf-8"
        elif parsed.path.startswith("/assets/"):
            name = parsed.path[len("/assets/"):]
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
                self._send(404, '{"hata":"bulunamadi"}')
                return
            path = os.path.join(root, "assets", name)
            content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
            if content_type in ("text/javascript", "text/css"):
                content_type += "; charset=utf-8"
        else:
            self._send(404, '{"hata":"bulunamadi"}')
            return
        try:
            with open(path, "rb") as stream:
                self._send(200, stream.read(), content_type)
        except OSError:
            self._send(404, '{"hata":"bulunamadi"}')

    # --- Auth guard'lari: tum veri/is endpoint'leri buradan gecer ---
    def _cerez_token(self):
        try:
            for parca in (self.headers.get("Cookie") or "").split(";"):
                ad, _, deger = parca.strip().partition("=")
                if ad.strip() == auth.COKIE_ADI and deger.strip():
                    return deger.strip()
        except Exception:
            pass
        return None

    def _oturum_kullanici(self):
        return _auth_store.oturum_kullanici(self._cerez_token())

    def _auth_gerekli(self):
        kullanici = self._oturum_kullanici()
        if kullanici is None:
            self._send(401, '{"hata":"giris gerekli"}')
            return None
        return kullanici

    def _admin_gerekli(self):
        kullanici = self._auth_gerekli()
        if kullanici is None:
            return None
        if _auth_store.rol(kullanici) != "admin":
            self._send(403, '{"hata":"yetki yok"}')
            return None
        return kullanici

    def _setup_parolasiz(self):
        return (RUNPOD_MODE
                and getattr(self.server, "server_port", 8189) == SETUP_PORT
                and os.environ.get("WEBUI_SETUP_PASSWORDLESS") == "1")

    def _setup_yetkili(self):
        """Passwordless access is restricted to the installer port and routes."""
        return True if self._setup_parolasiz() else self._admin_gerekli() is not None

    def _kasa_gerekli(self, kullanici):
        """Kasa acik degilse 423 doner. Aciksa kullanim saatini tazeler."""
        with _http_yazma_kosulu:
            try:
                durum = kasa.durum(kullanici)
            except Exception:
                durum = {}
            izinli = durum.get("acik") and not durum.get("kapaniyor")
            if izinli and self.command == "POST":
                self._kasa_yazma_kullanicisi = kullanici
                _kasa_yazma_istekleri.setdefault(kullanici, set()).add(self)
        if not izinli:
            self._send(423, '{"hata":"kasa kilitli veya kapanıyor"}')
            return None
        try:
            kasa.dokun(kullanici)
        except Exception:
            pass
        return kullanici

    def _kasa_kapaniyor_mu(self, kullanici):
        """Kasa kapanirken yeni is alinmaz."""
        try:
            if kasa.durum(kullanici).get("kapaniyor"):
                self._send(423, '{"hata":"kasa kapaniyor, yeni is alinmiyor"}')
                return True
        except Exception:
            pass
        return False

    def _json_oku(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
        except Exception:
            length = 0
        try:
            ham = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
            veri = json.loads(ham or "{}")
            return veri if isinstance(veri, dict) else {}
        except Exception as e:
            return {"__hata__": "istek okunamadi: %s" % e}

    def _origin_kontrol(self):
        """POST'lara CSRF perdesi: Origin/Referer varsa Host ile ayni olmali.
        Baslik yoksa (curl) gecer. Uymazsa 403 doner, True/False verir."""
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        pod_id = os.environ.get("RUNPOD_POD_ID", "") if RUNPOD_MODE else ""
        proxy_port = getattr(self.server, "server_port", 8189)
        proxy_host = f"{pod_id.lower()}-{proxy_port}.proxy.runpod.net" if pod_id else None
        for baslik in ("Origin", "Referer"):
            deger = self.headers.get(baslik)
            if not deger:
                continue
            try:
                parca = urllib.parse.urlparse(deger)
                origin_host = (parca.hostname or "").lower()
                pod_proxy = (origin_host == proxy_host and parca.scheme == "https"
                             and parca.port in (None, 443))
                if origin_host != host and not pod_proxy:
                    self._send(403, '{"hata":"origin hatasi"}')
                    return False
            except Exception:
                self._send(403, '{"hata":"origin hatasi"}')
                return False
        return True

    def _setup_get(self, parsed):
        return _handle_setup_get(self, parsed, sys.modules[__name__])

    def _setup_post(self, parsed):
        return _handle_setup_post(self, parsed, sys.modules[__name__])

    @staticmethod
    def _oturum_cerezi(token):
        return [("Set-Cookie", "%s=%s; HttpOnly; Path=/; SameSite=Lax; Max-Age=%d"
                % (auth.COKIE_ADI, token, auth.OTURUM_SURE_SN))]

    @staticmethod
    def _cerez_sil():
        return [("Set-Cookie", "%s=; HttpOnly; Path=/; SameSite=Lax; Max-Age=0"
                % auth.COKIE_ADI)]

    def parse_request(self):
        ok = super().parse_request()
        return ok and self.server.istek_basladi(self.connection)

    def do_GET(self):
        return _handle_main_get(self, sys.modules[__name__])

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if RUNPOD_MODE and getattr(self.server, "server_port", 8189) == SETUP_PORT:
            self._setup_post(parsed)
            return
        if parsed.path == "/api/sunucu-kapat" or (RUNPOD_MODE and parsed.path == "/api/backup-export"):
            self._do_POST(parsed)
            return
        with _http_yazma_kosulu:
            kapaniyor = _kapatma_istegi.is_set()
            if not kapaniyor:
                _http_yazma_istekleri.add(self)
        if kapaniyor:
            self._send(503, '{"hata":"WebUI güvenli biçimde kapanıyor"}')
            return
        try:
            self._do_POST(parsed)
        finally:
            with _http_yazma_kosulu:
                _http_yazma_istekleri.discard(self)
                kullanici = getattr(self, "_kasa_yazma_kullanicisi", None)
                if kullanici in _kasa_yazma_istekleri:
                    istekler = _kasa_yazma_istekleri[kullanici]
                    istekler.discard(self)
                    if not istekler:
                        del _kasa_yazma_istekleri[kullanici]
                _http_yazma_kosulu.notify_all()

    def _do_POST(self):
        return _handle_main_post(self, sys.modules[__name__])


_httpd = None
_duzgun_kapat_kilit = threading.Lock()
_duzgun_kapatildi = False
_kapatma_istegi = threading.Event()
_kapatma_istek_kilit = threading.Lock()


def _main_health_state():
    kapaniyor = _kapatma_istegi.is_set()
    hazir = not kapaniyor and (not RUNPOD_MODE or pod_setup.is_ready())
    return (503 if kapaniyor else 200), {"pid": os.getpid(), "hazir": hazir}


def _setup_health_state():
    ready = pod_setup.is_ready()
    storage = pod_setup.storage_status()
    return 200, {"hazir": ready,
                 "hata": bool(_startup_error),
                 "parolasiz": (RUNPOD_MODE
                               and os.environ.get("WEBUI_SETUP_PASSWORDLESS") == "1"),
                 **storage}


def duzgun_kapat(muhurlensin=True):
    """Isleri durdur; istenirse acik kasalari basariyla muhurlendikten sonra cik."""
    global _duzgun_kapatildi
    with _duzgun_kapat_kilit:
        if _duzgun_kapatildi:
            return
        with _http_yazma_kosulu:
            _kapatma_istegi.set()
            if not _http_yazma_kosulu.wait_for(lambda: not _http_yazma_istekleri, timeout=30):
                raise RuntimeError("Açık yazma istekleri tamamlanmadı")
        islem = "kasalar mühürleniyor" if muhurlensin else "kasalar açık bırakılıyor"
        print("Kapanıyor: işler durduruluyor, %s..." % islem, flush=True)
        kullanicilar = kasa.aciklar()
        for kullanici in kullanicilar:
            _kasa_islerini_tamamla(kullanici)
            # Arka muhur isini bitirene kadar bekle; o is bittiginde bayrak
            # kendiliginden iner. Tavan asiminda kapanis_iptal cagirmayiz:
            # o bayragi yalnizca muhur sureci bitmisken indirir, aksi halde
            # hepsini_kapat kasa hala acik sanip hata verir.
            kasa.kapanis_bekle(kullanici, tavan_sn=60)
        if muhurlensin:
            sonuc = kasa.hepsini_kapat()
            basarisiz = [k for k, kapandi in sonuc.items() if not kapandi]
            if basarisiz:
                raise RuntimeError("Kasalar mühürlenemedi: %s" % ", ".join(basarisiz))
            print("Mühür sonucu: %s" % sonuc, flush=True)
        if RUNPOD_MODE:
            pod_setup.checkpoint()
        try:
            llm.durdur()
        except Exception as e:
            print("llama-server durdurma hatası: %s" % e, flush=True)
        _duzgun_kapatildi = True
        print("Kapandı.", flush=True)


def _sunucu_kapat_arka(muhurlensin=True):
    """Başlatıcı isteğinde işleri bitir, istenen kasa politikasını uygula, sunucuyu durdur."""
    try:
        duzgun_kapat(muhurlensin=muhurlensin)
    except Exception as e:
        print("Güvenli kapanış başarısız; WebUI çalışır bırakıldı: %s" % e, flush=True)
        _kapatma_istegi.clear()
        return
    try:
        if _httpd is not None:
            _httpd.shutdown()
            if _setup_httpd is not None:
                _setup_httpd.shutdown()
        for server in tuple(_asgi_servers):
            server.should_exit = True
    except Exception as e:
        print("WebUI sunucusu durdurulamadı: %s" % e, flush=True)


class WebUIServer(http.server.ThreadingHTTPServer):
    # Başlıkları tamamlanmamış bağlantılar uygulama işi sayılmaz.
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        self._istekler = set()
        self._istek_kosulu = threading.Condition()
        self._dinleme_bitti = False
        super().__init__(*args, **kwargs)

    def get_request(self):
        conn, address = super().get_request()
        conn.settimeout(30)
        return conn, address

    def istek_basladi(self, conn):
        with self._istek_kosulu:
            if self._dinleme_bitti:
                return False
            self._istekler.add(conn)
            return True

    def shutdown_request(self, conn):
        try:
            super().shutdown_request(conn)
        finally:
            with self._istek_kosulu:
                self._istekler.discard(conn)
                if not self._istekler:
                    self._istek_kosulu.notify_all()

    def server_close(self):
        with self._istek_kosulu:
            self._dinleme_bitti = True
            self._istek_kosulu.wait_for(lambda: not self._istekler)
        super().server_close()


def _runpod_prepare():
    """Prepare RunPod persistence and services while installer stays available."""
    global _startup_error
    try:
        pod_setup.prepare_data(DATA_ROOT, _auth_store)
        kasa.kalici_kopya = pod_setup.checkpoint
        _auth_store.kalici_kopya = pod_setup.checkpoint
        comfy_restart = app_update.ROOT / 'restart-comfy'
        if comfy_restart.is_file():
            try:
                pod_setup._launch_comfy()
                comfy_restart.unlink()
            except Exception as exc:
                _system_event('ComfyUI yeniden başlatılamadı: ' + type(exc).__name__)
    except Exception as exc:
        _startup_error = type(exc).__name__
        detail = " ".join(str(exc).split())
        detail = re.sub(r"(?i)(hf_[a-z0-9_-]{12,}|(?:token|password|secret)\s*[=:]\s*\S+)",
                        "[REDACTED]", detail)[:240]
        diagnostic = _startup_error + (": " + detail if detail else "")
        _system_event("Pod hazırlığı tamamlanamadı (" + diagnostic + ").")
        print("Pod hazırlığı tamamlanamadı (%s); 8192 portundaki durum ekranı açık kalacak."
              % diagnostic, flush=True)


async def _run_asgi_servers():
    """Serve both existing RunPod ports through FastAPI's compatibility seam."""
    import uvicorn
    from api import create_apps

    apps = create_apps(Handler, main_health=_main_health_state,
                       setup_health=_setup_health_state)
    config = {
        "host": "0.0.0.0", "access_log": False, "log_level": "warning",
        "lifespan": "off", "timeout_keep_alive": 10,
    }

    class _SetupServer(uvicorn.Server):
        # Only the main server owns process signal handlers; both share one loop.
        def install_signal_handlers(self):
            return None

    setup_server = _SetupServer(uvicorn.Config(apps.setup, port=SETUP_PORT, **config))
    main_server = uvicorn.Server(uvicorn.Config(apps.main, port=8189, **config))
    _asgi_servers[:] = [main_server, setup_server]
    # The installer starts before the main listener so it can report progress.
    # Handle a stop request during that interval without interrupting the
    # synchronous data preparation halfway through a snapshot write.
    stop_requested = asyncio.Event()

    def request_stop(_signum, _frame):
        stop_requested.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    setup_task = asyncio.create_task(setup_server.serve())
    tasks = [setup_task]

    async def wait_for_start(server, task):
        deadline = time.monotonic() + 30
        while not server.started:
            if task.done():
                await task
                raise RuntimeError("ASGI sunucusu başlatılamadı.")
            if time.monotonic() >= deadline:
                raise TimeoutError("Kurucu ASGI sunucusu 30 saniyede başlamadı.")
            await asyncio.sleep(0.05)

    try:
        await wait_for_start(setup_server, setup_task)
        _system_event("Kurulum ekranı 8192 portunda hazır.")
        print("Kurulum arayüzü: http://0.0.0.0:%d" % SETUP_PORT, flush=True)
        if stop_requested.is_set():
            return
        # Keep 8192 responsive while volume checks or startup reconciliation run.
        await asyncio.to_thread(_runpod_prepare)
        if stop_requested.is_set():
            return
        main_task = asyncio.create_task(main_server.serve())
        tasks.append(main_task)
        await wait_for_start(main_server, main_task)
        _system_event("WebUI 8189 portunda hazır.")
        print("Flux2 WebUI: http://0.0.0.0:8189 (RunPod ASGI)", flush=True)
        done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for server in _asgi_servers:
            server.should_exit = True
        results = await asyncio.gather(*tasks, return_exceptions=True)
        failure = next((result for result in results if isinstance(result, BaseException)), None)
        if failure is not None:
            raise failure
    finally:
        for server in _asgi_servers:
            server.should_exit = True
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        _asgi_servers.clear()
        if not _duzgun_kapatildi:
            await asyncio.to_thread(duzgun_kapat)


if __name__ == "__main__":
    if RUNPOD_MODE:
        os.umask(0o077)
        pod_setup.validate_mount()
        if os.environ.get("WEBUI_ASGI") == "1":
            try:
                asyncio.run(_run_asgi_servers())
            except KeyboardInterrupt:
                if not _duzgun_kapatildi:
                    duzgun_kapat()
            raise SystemExit(0)
        _setup_httpd = WebUIServer(("0.0.0.0", SETUP_PORT), Handler)
        threading.Thread(target=_setup_httpd.serve_forever,
                         kwargs={"poll_interval": 0.2}, daemon=True,
                         name="setup-webui").start()
        _system_event("Kurulum ekranı 8192 portunda hazır.")
        print("Kurulum arayüzü: http://0.0.0.0:%d" % SETUP_PORT, flush=True)
        try:
            pod_setup.prepare_data(DATA_ROOT, _auth_store)
            kasa.kalici_kopya = pod_setup.checkpoint
            _auth_store.kalici_kopya = pod_setup.checkpoint
            comfy_restart = app_update.ROOT / 'restart-comfy'
            if comfy_restart.is_file():
                try:
                    pod_setup._launch_comfy()
                    comfy_restart.unlink()
                except Exception as exc:
                    _system_event('ComfyUI yeniden başlatılamadı: ' + type(exc).__name__)
        except Exception as exc:
            _startup_error = type(exc).__name__
            detail = " ".join(str(exc).split())
            detail = re.sub(r"(?i)(hf_[a-z0-9_-]{12,}|(?:token|password|secret)\s*[=:]\s*\S+)",
                            "[REDACTED]", detail)[:240]
            diagnostic = _startup_error + (": " + detail if detail else "")
            _system_event("Pod hazırlığı tamamlanamadı (" + diagnostic + ").")
            print("Pod hazırlığı tamamlanamadı (%s); 8192 portundaki durum ekranı açık kalacak."
                  % diagnostic, flush=True)
        signal.signal(signal.SIGTERM, signal.default_int_handler)
    with WebUIServer(("0.0.0.0", 8189), Handler) as httpd:
        _httpd = httpd
        httpd.allow_reuse_address = True
        if RUNPOD_MODE:
            _system_event("WebUI 8189 portunda hazır.")
        if not RUNPOD_MODE:
            prompt_translation.onisit()
        print("Flux2 WebUI: http://0.0.0.0:8189  (yerel agdan telefonla erisim acik; ComfyUI: %s)" % COMFY,
              flush=True)
        try:
            if hasattr(signal, "SIGBREAK"):
                signal.signal(signal.SIGBREAK, signal.default_int_handler)
        except Exception:
            pass
        try:
            while True:
                try:
                    httpd.serve_forever(poll_interval=0.1)
                    break
                except KeyboardInterrupt:
                    try:
                        duzgun_kapat()
                        break
                    except Exception as e:
                        _kapatma_istegi.clear()
                        print("Güvenli kapanış başarısız; yeniden denenecek: %s" % e,
                              flush=True)
        finally:
            if not _duzgun_kapatildi:
                duzgun_kapat()
