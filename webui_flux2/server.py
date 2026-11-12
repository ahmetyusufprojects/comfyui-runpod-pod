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
                          build_illustrious_prompt)
from image_utils import (exif_duzelt as _exif_duzelt,
                         fotograf_hazirla as _fotograf_hazirla,
                         scale_to_max_side)

_AYARLAR_KILIT = threading.RLock()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_ROOT = os.path.abspath(os.environ.get("WEBUI_DATA_ROOT", BASE_DIR))
RUNPOD_MODE = os.environ.get("WEBUI_RUNPOD") == "1"
SETUP_PORT = 8192
_setup_httpd = None
_startup_error = None
if RUNPOD_MODE:
    import pod_setup
    import app_update

_app_update_lock = threading.Lock()
_app_update_state = {"status": "idle"}

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
MODEL_ILLUSTRIOUS = "illustrious"

# --- Moduler is sistemi: yeni is tipi eklemek mevcut kodu kirmamali ---
# "goruntu-uretim" / "goruntu-duzenleme" simdi; "video-uretim" yakinda (sadece hazirlik).
IS_TIPI_GORUNTU = "goruntu-uretim"
IS_TIPI_DUZENLEME = "goruntu-duzenleme"
IS_TIPI_VIDEO = "video-uretim"
IS_TIPLERI = (IS_TIPI_GORUNTU, IS_TIPI_DUZENLEME, IS_TIPI_VIDEO)

# --- Video (MiniMax H3 referans-video): sablon + sinirlar ---
# Sablon, UI workflow'una dokunulmadan video_r2v_api.json'dan okunur.
VIDEO_SABLON_DOSYA = os.path.join(BASE_DIR, "video_r2v_api.json")
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
    "ps_prompt_profile": ("auto", "general", "z_image", "flux3", "krea2", "illustrious", "minimaxh3"),
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
                         MODEL_ILLUSTRIOUS):
                    temiz[k] = v
            elif k in ("image_model", "model_instance"):
                allowed = ("auto", MODEL_FLUX2, MODEL_Z_IMAGE, MODEL_FLUX3,
                           MODEL_KREA2, MODEL_ILLUSTRIOUS)
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


def is_tipini_belirle(req, n_ref):
    """Istek + referans sayisindan is tipini sec. Moduler nokta: yeni tip buraya eklenir."""
    ham = (req.get("is_tipi") or "").strip() if isinstance(req.get("is_tipi"), str) else ""
    if ham in IS_TIPLERI:
        return ham
    if ham in ("video", "video-uret", "video-uretim-hazirlik"):
        return IS_TIPI_VIDEO
    return IS_TIPI_GORUNTU if n_ref == 0 else IS_TIPI_DUZENLEME


def video_parametreleri(req):
    """Video alanlari: simdilik goruntu islerinde yoksayilir, semada tasinir."""
    try:
        suresn = float(req.get("suresn", 0) or 0)
    except Exception:
        suresn = 0
    try:
        fps = float(req.get("fps", 0) or 0)
    except Exception:
        fps = 0
    return {"suresn": suresn if suresn > 0 else 0, "fps": fps if fps > 0 else 0}


def _video_fps_yuvarla(fps):
    """Sablon disi fps'i en yakin secenege yuvarlar (8/12/24)."""
    try:
        fps = float(fps)
    except Exception:
        return 24
    if fps <= 0:
        return 24
    return min(VIDEO_FPSLER, key=lambda s: (abs(s - fps), s))


def _video_kare_sayisi(suresn, fps):
    """Kare = sure*fps; modelin 17k+5 izgara kosuluna yukari yuvarlanir."""
    try:
        n = max(5, int(round(float(suresn) * float(fps))))
    except Exception:
        n = 124
    n = max(5, min(3600, n))
    while n % 17 != 5:
        n += 1
    return n


def _video_boyut(w, h, max_kenar=VIDEO_MAX_KENAR):
    """En uzun kenari max_kenar'a esitle (oran korunur, 32'nin kati)."""
    try:
        w, h = int(w), int(h)
    except Exception:
        w, h = max_kenar, 768
    if w < 32 or h < 32:
        w, h = max_kenar, 768
    f = max_kenar / float(max(w, h))
    w2 = max(32, int(round(w * f / 32.0)) * 32)
    h2 = max(32, int(round(h * f / 32.0)) * 32)
    return w2, h2


def _video_sablon_yukle(profile_name=None):
    """video_r2v_api.json'u okur; '_' ile baslayan aciklama anahtarlarini atar."""
    with open(VIDEO_SABLON_DOSYA, "r", encoding="utf-8") as f:
        ham = json.load(f)
    if RUNPOD_MODE:
        workflow = pod_setup.workflow("video")
        profiles = workflow.get("profiles") or {}
        requested = "quality" if profile_name == "fast" else (profile_name or workflow.get("default_profile", "quality"))
        profile = profiles.get(requested) or profiles.get(workflow.get("default_profile", "quality"))
        if not profile:
            raise RuntimeError("Video modeli models.json içinde tanımlı değil.")
        group_state = {item["id"]: item["durum"] for item in pod_setup.status()["gruplar"]}
        if group_state.get(profile.get("group")) not in ("ready", "installed"):
            raise RuntimeError("Seçilen video profili kurulu değil. Önce ayrı kurulum ekranından model grubunu hazırlayın.")
        for node, field, key in (("127", "unet_name", "unet_file"),
                                 ("128", "clip_name", "text_encoder_file"),
                                 ("119", "vae_name", "video_vae_file"),
                                 ("120", "vae_name", "audio_vae_file")):
            if workflow.get(key):
                ham[node]["inputs"][field] = workflow[key]
        if profile.get("lora_file"):
            ham["145"]["inputs"]["lora_name"] = profile["lora_file"]
        else:
            ham["126"]["inputs"]["model"] = ["127", 0]
            ham.pop("145", None)
        ham["124"]["inputs"]["steps"] = int(profile.get("steps", 20))
        # Sessiz taslak üretiminde örneklenen ses latent'ini çözmek ve eklemek gereksizdir.
        ham.pop("121", None)
        ham["130"]["inputs"].pop("audio", None)
    elif profile_name == "normal":
        ham["126"]["inputs"]["model"] = ["127", 0]
        ham.pop("145", None)
        ham["124"]["inputs"]["steps"] = 20
    return {k: v for k, v in ham.items() if not k.startswith("_")}


# Video dugum sinifi -> Turkce asama adi. Yalniz video islerinde, _aktif["nodlar"]
# kurulurken uygulanir; goruntu hattinin metinleri aynen kalir.
_VIDEO_ASAMA = {
    "LoadImage": "Referanslar okunuyor",
    "UNETLoader": "Video modeli yukleniyor",
    "CLIPLoader": "Metin kodlayici yukleniyor",
    "VAELoader": "VAE yukleniyor",
    "LoraLoaderModelOnly": "LoRA yukleniyor",
    "MiniMaxH3ReferenceToVideo": "Referanslar isleniyor",
    "KSamplerSelect": "Ornekleyici hazirlaniyor",
    "BasicScheduler": "Zamanlama kuruluyor",
    "RandomNoise": "Gurultu hazirlaniyor",
    "BasicGuider": "Yonlendirici hazirlaniyor",
    "SamplerCustomAdvanced": "Video ornekleniyor",
    "VAEDecode": "Video cozuluyor",
    "CreateVideo": "Video birlestiriliyor",
    "SaveVideo": "Video kaydediliyor",
}

COMFY_KOK = os.path.abspath(os.environ.get(
    "COMFYUI_ROOT", os.path.join(BASE_DIR, "..", "ComfyUI")))


def _video_diskten_bul(sonrasi_sn):
    """SaveVideo history'ye dosya adi koymazsa diske bak: bu isten sonra
    yazilmis en yeni MiniMax_H3 mp4'un baytlarini doner (yoksa None).
    Tek worker oldugundan taze dosya bu ise aittir."""
    klasor = os.path.join(COMFY_KOK, "output", "video")
    try:
        adaylar = []
        for ad in os.listdir(klasor):
            if not ad.startswith("MiniMax_H3") or not ad.lower().endswith(".mp4"):
                continue
            yol = os.path.join(klasor, ad)
            try:
                mt = os.path.getmtime(yol)
            except Exception:
                continue
            if os.path.isfile(yol) and mt >= sonrasi_sn - 5:
                adaylar.append((mt, yol))
        if not adaylar:
            return None
        adaylar.sort()
        with open(adaylar[-1][1], "rb") as f:
            return f.read()
    except Exception:
        return None


def _video_uret(job, req):
    """Video uretim govdesi: sablon doldur, /prompt gonder, mp4 cek, kaydet.
    Goruntu govdesine dokunmaz; hata/kota durumunda yari dosya birakmaz."""
    sahip = job.get("sahip")
    try:
        vid = video_parametreleri(req)
        try:
            default_duration = 0.5 if RUNPOD_MODE else 5
            suresn = float(vid.get("suresn") or default_duration)
        except Exception:
            suresn = 0.5 if RUNPOD_MODE else 5
        suresn = max(VIDEO_SURESN_MIN, min(VIDEO_SURESN_MAX, suresn))
        fps = _video_fps_yuvarla(vid.get("fps") or (8 if RUNPOD_MODE else 24))
        try:
            w0 = int(req.get("width", VIDEO_MAX_KENAR))
        except Exception:
            w0 = VIDEO_MAX_KENAR
        try:
            h0 = int(req.get("height", 768))
        except Exception:
            h0 = 768
        try:
            vmax = int(req.get("video_max_side") or (320 if RUNPOD_MODE else VIDEO_MAX_KENAR))
        except Exception:
            vmax = VIDEO_MAX_KENAR
        vmax = max(320, min(VIDEO_MAX_KENAR, vmax))
        w, h = _video_boyut(w0, h0, vmax)
        frames = _video_kare_sayisi(suresn, fps)
        gercek_suresn = round(frames / fps, 3)
        try:
            seed = int(req.get("seed", -1))
        except Exception:
            seed = -1
        if seed < 0:
            seed = random.randint(0, 2 ** 31 - 1)
        prompt = req.get("prompt", "") or ""
        # Referanslar: PNG'ye cevrilir (orijinal cozunurluk korunur, 16'ya hizali),
        # ComfyUI'ye yuklenir, kutuphaneye eklenir (goruntu hatti ile ayni).
        yuklenen = []
        kutular = []
        hamlar = []
        kaynak_boyutu = None
        for anahtar in ("image_base64", "image2_base64"):
            if not req.get(anahtar):
                continue
            ham = base64.b64decode(req[anahtar])
            hamlar.append(ham)
            hazir, boyut, _, _ = _fotograf_hazirla(ham, VIDEO_MAX_KENAR, False)
            if kaynak_boyutu is None and boyut:
                kaynak_boyutu = boyut
            fn = "webui_video_%s.png" % uuid.uuid4().hex[:8]
            up = upload_image_to_comfy(hazir, fn)
            yuklenen.append(up.get("name", fn))
            kutular.append((ham, hazir))
        if not yuklenen:
            job["durum"] = "hata"
            job["hata"] = "video-uretim icin en az 1 referans fotograf gerekli"
            job["bitis"] = time.time()
            return
        if RUNPOD_MODE and kaynak_boyutu:
            # H3 çıktısını referans fotoğrafın en-boy oranında tut; kare 1344 px
            # varsayılanı gereksiz yere çok daha fazla GPU belleği ve hesap ister.
            w0, h0 = kaynak_boyutu
            w, h = _video_boyut(w0, h0, vmax)
        profile_name = req.get("video_profile") or "quality"
        sablon = _video_sablon_yukle(profile_name)
        g = sablon["136"]["inputs"]
        g["prompt"] = prompt
        g["width"] = w
        g["height"] = h
        g["length"] = frames
        sablon["129"]["inputs"]["noise_seed"] = seed
        sablon["130"]["inputs"]["fps"] = fps
        sablon["137"]["inputs"]["image"] = yuklenen[0]
        if len(yuklenen) > 1:
            sablon["139"]["inputs"]["image"] = yuklenen[1]
        else:
            sablon.pop("139", None)
            g.pop("ref_images.ref_image_1", None)
        cid = uuid.uuid4().hex
        if job["iptal"]:
            raise RuntimeError("İş durduruldu")
        res = comfy_post_json("/prompt", {"prompt": sablon, "client_id": cid})
        if "node_errors" in res and res["node_errors"]:
            job["durum"] = "hata"
            job["hata"] = "node hatasi: %s" % json.dumps(res["node_errors"])[:500]
            job["bitis"] = time.time()
            return
        job["prompt_id"] = res["prompt_id"]
        _aktif["prompt_id"] = res["prompt_id"]
        _aktif["sahip"] = sahip
        _aktif["baslangic"] = time.time()
        _aktif["ilerleme"] = {"kuyrukta": True, "oran": 0.0}
        _aktif["nodlar"] = {nid: _VIDEO_ASAMA.get(v.get("class_type", nid),
                                                 v.get("class_type", nid))
                            for nid, v in sablon.items()}
        threading.Thread(target=_ws_ilerleme_dinle,
                         args=(res["prompt_id"], cid, _aktif["nodlar"]),
                         daemon=True).start()
        try:
            if job["iptal"]:
                _comfy_isini_kes(res["prompt_id"])
            entry = wait_result(res["prompt_id"], job=job)
        finally:
            if _aktif.get("prompt_id") == res["prompt_id"]:
                _aktif["prompt_id"] = None
                _aktif["sahip"] = None
        mp4 = None
        for node_id, out in (entry.get("outputs") or {}).items():
            adaylar = list(out.get("gifs", []) or [])
            # SaveVideo ciktisi kimi surumde "images" altinda mp4 olarak doner.
            for im in out.get("images", []) or []:
                try:
                    if str(im.get("filename", "")).lower().endswith(".mp4"):
                        adaylar.append(im)
                except Exception:
                    pass
            for im in adaylar:
                mp4 = fetch_view_image(im)
                break
            if mp4:
                break
        if not mp4:
            # SaveVideo kimi surumde history'ye dosya adi koymaz; diskteki
            # taze ciktiya bak (temizlik finally'de calisir, once okunur).
            mp4 = _video_diskten_bul(job.get("baslangic") or time.time())
        if not mp4:
            job["durum"] = "hata"
            job["hata"] = "video ciktisi bulunamadi"
            job["bitis"] = time.time()
            return
        # Kota: yazmadan once kontrol; asimda temiz hata, yari dosya yok.
        kota_hata = _kota_kontrol(sahip,
                                  yeni_bayt=len(mp4) + sum(len(h) for h in hamlar),
                                  yeni_kayit=1 + len(hamlar))
        if kota_hata:
            job["durum"] = "hata"
            job["hata"] = kota_hata
            job["bitis"] = time.time()
            return
        _is_sonucu_baslat(job)
        kut1 = kut2 = None
        try:
            if len(kutular) > 0:
                kut1 = kutuphaneye_kaydet(sahip, kutular[0][0], kutular[0][1], 0, 0)
        except Exception:
            kut1 = None
        try:
            if len(kutular) > 1:
                kut2 = kutuphaneye_kaydet(sahip, kutular[1][0], kutular[1][1], 0, 0)
        except Exception:
            kut2 = None
        gecmis_kayit, gecmis_dosyalar = None, []
        video_poster = None
        try:
            meta = {"prompt": prompt, "width": w, "height": h, "seed": seed,
                    "frames": frames, "suresn": gercek_suresn, "istenen_suresn": suresn, "fps": fps,
                    "mod": "referans-video", "is_tipi": IS_TIPI_VIDEO,
                    "workflow": "video",
                    "foto_kutuphane": kut1, "foto_kutuphane2": kut2}
            video_poster = _video_kapak_uret(mp4)
            gecmis_kayit, gecmis_dosyalar = gecmise_kaydet(
                sahip, [mp4], meta, video=True, poster_blob=video_poster)
        except Exception:
            gecmis_kayit, gecmis_dosyalar = None, []
        job["sonuc"] = {"video_url": ("/gecmis/%s" % urllib.parse.quote(gecmis_dosyalar[0])
                                      if gecmis_dosyalar else None),
                        "video_poster_url": ("/gecmis/%s" % urllib.parse.quote(
                            gecmis_kayit + "_poster.jpg")
                            if gecmis_kayit and video_poster else None),
                        "seed": seed, "width": w, "height": h,
                        "frames": frames, "suresn": gercek_suresn, "istenen_suresn": suresn, "fps": fps,
                        "mod": "referans-video", "is_tipi": IS_TIPI_VIDEO,
                        "kutuphane": kut1, "kutuphane2": kut2,
                        "gecmis": gecmis_kayit,
                        "gecmis_dosyalar": gecmis_dosyalar,
                        "is_id": job["is_id"]}
        job["ilerleme"] = {"bitti": True, "oran": 1.0}
        job["durum"] = "bitti"
        job["bitis"] = time.time()
    except Exception as e:
        if "durduruldu" in str(e):
            job["durum"] = "durduruldu"
        else:
            job["durum"] = "hata"
            job["hata"] = str(e)[:1000]
        job["bitis"] = time.time()


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


def _is_dogrula(req):
    """Kuyruga girmeden once hizli ret. Hata dict'i doner veya None."""
    if "prompt_ceviri" in req and not isinstance(req.get("prompt_ceviri"), bool):
        return {"hata": "prompt_ceviri boolean olmali"}
    if req.get("prompt_ceviri") and req.get("prompt_destek"):
        return {"hata": "Prompt çevirisi ve Prompt Stüdyosu desteği aynı anda kullanılamaz"}
    if req.get("llm_model") is not None:
        allowed_llm = {item["id"] for item in llm.model_profilleri()}
        if req.get("llm_model") not in allowed_llm:
            return {"hata": "Seçilen LLM modeli bulunamadı. Ayarlardan geçerli bir model seç."}
    try:
        n_ref = (1 if req.get("image_base64") else 0) \
            + (1 if req.get("image2_base64") else 0)
    except Exception:
        n_ref = 0
    if is_tipini_belirle(req, n_ref) == IS_TIPI_VIDEO:
        if req.get("prompt_ceviri"):
            return {"hata": "Prompt çevirisi yalnızca görsel üretiminde kullanılabilir",
                    "is_tipi": IS_TIPI_VIDEO}
        # Referanssiz metinden-video bu planda yok; aralik disi sure/fps
        # reddedilmez, uretimde en yakin degere duzeltilir (3. bolum).
        if n_ref == 0:
            return {"hata": "video-uretim icin en az 1 referans fotograf gerekli",
                    "is_tipi": IS_TIPI_VIDEO}
        return None
    model = req.get("model", MODEL_FLUX2)
    if model in (MODEL_ANIMA, MODEL_MOODY_ANIMA) and RUNPOD_MODE:
        return {"hata": "Anima kapatıldı. Görsel üretim ayarlarından Illustrious veya başka bir modeli seç."}
    if model not in (MODEL_FLUX2, MODEL_Z_IMAGE, MODEL_ANIMA,
                     MODEL_MOODY_ANIMA, MODEL_FLUX3, MODEL_KREA2,
                     MODEL_ILLUSTRIOUS):
        return {"hata": "Görsel modeli tanınmadı."}
    if model in (MODEL_KREA2, MODEL_ILLUSTRIOUS) and n_ref:
        return {"hata": "Seçilen model şu anda yalnızca sıfırdan görsel üretir. Referansla Flux 2 kullan."}
    if req.get("flux3_bbox_mode") and model != MODEL_FLUX3:
        return {"hata": "Kutu yerleşimi yalnızca Flux 3 Klein ile kullanılabilir."}
    runpod_workflow = {MODEL_FLUX2: "flux2", MODEL_Z_IMAGE: "z_image",
                       MODEL_FLUX3: "flux3", MODEL_KREA2: "krea2",
                       MODEL_ILLUSTRIOUS: "illustrious"}.get(model)
    if RUNPOD_MODE and runpod_workflow:
        instance_id = req.get("model_instance") or runpod_workflow
        instance = pod_setup.model_instance(instance_id)
        if instance is None or instance.get("family") != runpod_workflow:
            return {"hata": "Seçilen model kaydı bu üretim türüyle eşleşmiyor."}
        if not pod_setup.workflow_enabled(instance_id):
            return {"hata": "Bu görsel modeli şu anda geçici olarak devre dışı."}
        if not pod_setup.workflow_ready(instance_id):
            return {"hata": "Bu görsel modeli henüz hazır değil. 8192 portundaki Kurulum panelinden modeli indir."}
    return None


def _is_budala_kilitli():
    bitmis = [j for j in _isler.values()
              if j["olay"].is_set()]
    bitmis.sort(key=lambda j: j.get("bitis", 0))
    for j in bitmis[:max(0, len(bitmis) - _SAKLANAN_IS_SAYISI)]:
        _isler.pop(j["is_id"], None)


def _kuyruga_ekle(req, sahip, tur="goruntu"):
    job = {"is_id": uuid.uuid4().hex[:12], "durum": "kuyrukta", "istek": req,
           "tur": tur,
           "sahip": sahip,
           "olustu": time.time(), "baslangic": 0, "bitis": 0,
           "prompt_id": None, "ilerleme": {"kuyrukta": True, "oran": 0.0},
           "sonuc": None, "hata": None, "iptal": False,
           "olay": threading.Event()}
    global _kuyruk_calismasi, _ceviri_calismasi
    with _isler_kilit:
        if _bellek_bosaltma_aktif or _kapatma_istegi.is_set():
            return None, 0
        kasa_durumu = kasa.durum(sahip)
        if not kasa_durumu["acik"] or kasa_durumu["kapaniyor"]:
            return None, 0
        _isler[job["is_id"]] = job
        _kuyruk.append(job["is_id"])
        _is_budala_kilitli()
        sira = len(_kuyruk)
        if tur == "goruntu" and req.get("prompt_ceviri"):
            job["ceviri_hazir"] = threading.Event()
            job["ceviri_durum"] = "bekliyor"
            _ceviri_kuyrugu.append(job)
            if not _ceviri_calismasi:
                _ceviri_calismasi = True
                threading.Thread(target=_ceviri_worker, daemon=True).start()
        if not _kuyruk_calismasi:
            _kuyruk_calismasi = True
            threading.Thread(target=_kuyruk_worker, daemon=True).start()
    return job, sira


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
                    and (req.get("model_prompt_profile") == "minimaxh3"
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
                                                   ("general", "z_image", "flux3", "krea2", "illustrious", "minimaxh3") else None),
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
                    "minimaxh3" if is_video else
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
        model = req.get("model") or req.get("image_model")
        if model in (None, "auto"):
            refs = bool(req.get("image_base64") or req.get("image2_base64"))
            model = MODEL_FLUX2 if refs else MODEL_Z_IMAGE
        model = "video" if is_tipini_belirle(
            req, int(bool(req.get("image_base64"))) +
            int(bool(req.get("image2_base64")))) == IS_TIPI_VIDEO else model
        vram_bos = _vram_bos_mb()
        gerekli_gorsel = RUNPOD_GORSEL_VRAM_MB.get(
            model, RUNPOD_GORSEL_VRAM_MB["z_image"])
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


def _is_canli_ilerleme(job):
    if job.get("durum") == "calisiyor" and job.get("prompt_id") \
            and _aktif.get("prompt_id") == job.get("prompt_id"):
        il = _aktif.get("ilerleme") or {}
        if isinstance(il, dict) and "oran" not in il:
            il = dict(il)
            il["oran"] = _oran_hesapla(il)
        return il
    return job.get("ilerleme")


def _is_ozet(job):
    try:
        with _isler_kilit:
            sira = _kuyruk.index(job["is_id"]) + 1 if job["durum"] == "kuyrukta" else 0
    except ValueError:
        sira = 0
    istek = job.get("istek") or {}
    tur = job.get("tur", "goruntu")
    if tur == "llm_prompt":
        _tip = "llm-prompt"
        prompt = (istek.get("metin") or "")[:80]
    elif tur == "llm_chat":
        _tip = "llm-sohbet"
        prompt = "Sohbet"
    elif tur == "llm_preload":
        _tip = "llm-hazirla"
        prompt = "Model hazırlığı"
    else:
        try:
            _n_ref = (1 if istek.get("image_base64") else 0) \
                + (1 if istek.get("image2_base64") else 0)
            _tip = is_tipini_belirle(istek, _n_ref)
        except Exception:
            _tip = None
        prompt = (istek.get("prompt") or "")[:80]
    return {"is_id": job["is_id"], "durum": job["durum"], "sira": sira,
            "ilerleme": _is_canli_ilerleme(job), "hata": job.get("hata"),
            "ceviri_durum": job.get("ceviri_durum"),
            "gecen": round(time.time() - job["baslangic"], 1) if job.get("baslangic") else 0,
            "prompt": prompt, "tur": tur,
            "model": istek.get("model", MODEL_FLUX2),
            "workflow": istek.get("workflow", "tek"),
            "is_tipi": _tip,
            "seed": (job.get("sonuc") or {}).get("seed")}


def _is_detay(job):
    d = _is_ozet(job)
    d["sonuc"] = job.get("sonuc")
    istek = job.get("istek") or {}
    goruntu_prompt_destek = job.get("tur") == "goruntu" \
        and bool(istek.get("prompt_destek"))
    if (job.get("tur") in ("llm_prompt", "llm_chat", "llm_preload")
            or goruntu_prompt_destek) and job.get("durum") == "calisiyor":
        d["llm_canli"] = llm.canli_olcum()
    return d


def _is_istegi_temizle(job):
    req = job.get("istek")
    if isinstance(req, dict):
        for key in ("image_base64", "image2_base64", "metin", "gorseller", "mesajlar"):
            req.pop(key, None)


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


def _is_sonucu_baslat(job):
    with _isler_kilit:
        if job["iptal"]:
            raise RuntimeError("İş durduruldu")
        job["sonuc_yaziliyor"] = True


def _bekleyen_isi_iptal_et(job):
    """Yalnız kuyruk kilidi altındayken çağrılır."""
    _kuyruk.remove(job["is_id"])
    job["iptal"] = True
    job["durum"] = "durduruldu"
    job["bitis"] = time.time()
    if "ceviri_hazir" in job:
        if job in _ceviri_kuyrugu:
            _ceviri_kuyrugu.remove(job)
            job["ceviri_hazir"].set()
        job["ceviri_durum"] = "durduruldu"
        job.pop("ceviri_sonuc", None)
        job.pop("ceviri_hata", None)
    _is_istegi_temizle(job)
    job["olay"].set()


def _comfy_isini_kes(prompt_id):
    # Önce bekleyen işi sil; o arada başlamışsa kimliğiyle kes.
    for yol, istek in (("/queue", {"delete": [prompt_id]}),
                        ("/interrupt", {"prompt_id": prompt_id})):
        try:
            comfy_post_json(yol, istek)
        except Exception:
            pass


def _isi_iptal_et(job):
    with _isler_kilit:
        if job["durum"] == "kuyrukta":
            _bekleyen_isi_iptal_et(job)
            return True
        if job["durum"] != "calisiyor" or job.get("sonuc_yaziliyor"):
            return False
        if job["iptal"]:
            return True
        job["iptal"] = True
        prompt_id = job["prompt_id"]
    if prompt_id:
        _comfy_isini_kes(prompt_id)
    with _llm_is_gecis_kilit:
        llm_is_active = _aktif_llm.get("is_id") == job["is_id"]
        llm_faz = _aktif_llm.get("durum")
    if llm_is_active:
        # Generation requests observe this event while reading the response;
        # keep the loaded model alive. Model preload has no request to interrupt,
        # so stop its server process to release the GPU and RAM.
        if not llm.iptal_istegi() and (
                job.get("tur") == "llm_preload"
                or llm_faz in ("Hazırlanıyor", "Model yükleniyor")):
            llm.durdur()
    return True


def _kullanici_islerini_durdur(kullanici, bekle=True, tavan_sn=None):
    """Kullanıcının işlerini iptal eder ve istenirse son yazımları bekler."""
    with _isler_kilit:
        isler = [j for j in _isler.values() if j["sahip"] == kullanici
                 and not j["olay"].is_set()]
    for job in reversed(isler):
        _isi_iptal_et(job)
    if not bekle:
        return True
    son = None if tavan_sn is None else time.monotonic() + tavan_sn
    for job in isler:
        kalan = None if son is None else max(0, son - time.monotonic())
        if not job["olay"].wait(kalan):
            return False
    return True


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
                         "krea2": 22500, "illustrious": 10500, "video": 26000}
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


def kutuphaneye_kaydet(kullanici, orijinal_blob, hazir_bytes, w1, h1):
    """Yuklenen fotoyu SADECE bu kullanicinin kutuphane/ altina saklar
    (hedef + orijinal + thumb). Ayni icerik varsa yeniden kaydetmez.
    Donus: dosya adi (hedef) veya None. Cakismamasi icin uuid kuyrugu vardir."""
    try:
        kutuphane_dir = _kullanici_kutuphane(kullanici)
        _klasor_elin(kutuphane_dir)
        try:
            ozet = hashlib.sha256(hazir_bytes).hexdigest()
            for ad in os.listdir(kutuphane_dir):
                if ad.endswith(("_orig.png", "_thumb.jpg")):
                    continue
                if not ad.lower().endswith(".png"):
                    continue
                yol = os.path.join(kutuphane_dir, ad)
                if not os.path.isfile(yol):
                    continue
                try:
                    with open(yol, "rb") as f:
                        ayni = hashlib.sha256(f.read()).hexdigest() == ozet
                except Exception:
                    continue
                if ayni:
                    taban = ad[:-4] if ad.lower().endswith(".png") else ad
                    for kardes in (ad, taban + "_orig.png", taban + "_thumb.jpg"):
                        try:
                            os.utime(os.path.join(kutuphane_dir, kardes), None)
                        except Exception:
                            pass
                    return ad
        except Exception:
            pass
        taban = "%s_%s" % (_simdiki_zaman_damgasi(), uuid.uuid4().hex[:6])
        hedef_ad = taban + ".png"
        with open(os.path.join(kutuphane_dir, hedef_ad), "wb") as f:
            f.write(hazir_bytes)
        try:
            from PIL import Image as _PILImage
            import io as _bio
            with _PILImage.open(_bio.BytesIO(orijinal_blob)) as _im:
                _exif_duzelt(_im).convert("RGB").save(
                    os.path.join(kutuphane_dir, taban + "_orig.png"), format="PNG")
            with _PILImage.open(_bio.BytesIO(hazir_bytes)) as _im:
                _im.thumbnail((512, 512), _PILImage.LANCZOS)
                _im.convert("RGB").save(
                    os.path.join(kutuphane_dir, taban + "_thumb.jpg"),
                    format="JPEG", quality=85)
        except Exception:
            pass
        return hedef_ad
    except Exception:
        return None


def kutuphane_listesi(kullanici):
    kutuphane_dir = _kullanici_kutuphane(kullanici)
    _klasor_elin(kutuphane_dir)
    kayitlar = []
    try:
        for ad in sorted(os.listdir(kutuphane_dir)):
            if ad.endswith(("_orig.png", "_thumb.jpg")):
                continue
            if not ad.lower().endswith(".png"):
                continue
            yol = os.path.join(kutuphane_dir, ad)
            if not os.path.isfile(yol):
                continue
            st = os.stat(yol)
            boyut, mod, bicim = None, None, None
            try:
                from PIL import Image as _PILImage
                with _PILImage.open(yol) as _im:
                    boyut = [_im.width, _im.height]
                    mod = _im.mode
                    bicim = _im.format
            except Exception:
                pass
            taban = ad[:-4] if ad.lower().endswith(".png") else ad
            orijinal = None
            o_yol = os.path.join(kutuphane_dir, taban + "_orig.png")
            if os.path.isfile(o_yol):
                try:
                    o_st = os.stat(o_yol)
                    o_boyut = None
                    from PIL import Image as _PILImage
                    with _PILImage.open(o_yol) as _im:
                        o_boyut = [_im.width, _im.height]
                    orijinal = {"boyut": o_boyut, "bayt": o_st.st_size}
                except Exception:
                    try:
                        o_bayt = os.path.getsize(o_yol)
                    except OSError:
                        o_bayt = None
                    orijinal = {"boyut": None, "bayt": o_bayt}
            kayitlar.append((st.st_mtime, {
                "ad": ad, "boyut": boyut, "mod": mod, "bicim": bicim,
                "bayt": st.st_size, "orijinal": orijinal,
                "tarih": _dt.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
                "thumb": "/kutuphane/%s?thumb=1" % urllib.parse.quote(ad),
                "url": "/kutuphane/%s" % urllib.parse.quote(ad)}))
    except Exception:
        pass
    kayitlar.sort(key=lambda k: k[0], reverse=True)
    return [k[1] for k in kayitlar]


def kutuphaneden_sil(kullanici, ad):
    ad = _guvenli_dosya_adi(ad)
    if not ad:
        return False
    kutuphane_dir = _kullanici_kutuphane(kullanici)
    taban = ad[:-4] if ad.lower().endswith(".png") else ad
    silindi = False
    for aday in (ad, taban + "_orig.png", taban + "_thumb.jpg"):
        yol = os.path.abspath(os.path.join(kutuphane_dir, aday))
        if not yol.startswith(os.path.abspath(kutuphane_dir) + os.sep):
            continue
        try:
            if os.path.isfile(yol):
                os.remove(yol)
                silindi = True
        except Exception:
            pass
    return silindi


def kutuphane_temizle(kullanici):
    """Bu kullanicinin kutuphanesindeki tum yonetilen dosyalari siler."""
    kutuphane_dir = _kullanici_kutuphane(kullanici)
    _klasor_elin(kutuphane_dir)
    kok = os.path.abspath(kutuphane_dir) + os.sep
    n = 0
    try:
        for ad in os.listdir(kutuphane_dir):
            if not ad.lower().endswith((".png", ".jpg", ".jpeg")):
                continue
            yol = os.path.abspath(os.path.join(kutuphane_dir, ad))
            if not yol.startswith(kok):
                continue
            try:
                if os.path.isfile(yol):
                    os.remove(yol)
                    n += 1
            except Exception:
                pass
    except Exception:
        pass
    return n


def promptlari_oku(kullanici):
    try:
        with open(_kullanici_promptlar(kullanici), "r", encoding="utf-8") as f:
            veri = json.load(f)
            if isinstance(veri, list):
                return veri
    except Exception:
        pass
    return []


def prompt_kaydet(kullanici, kayit):
    liste = promptlari_oku(kullanici)
    ad = (kayit.get("ad") or "").strip()
    liste = [k for k in liste if (k.get("ad") or "") != ad]
    liste.append(kayit)
    _klasor_elin(_kullanici_koku(kullanici))
    with open(_kullanici_promptlar(kullanici), "w", encoding="utf-8") as f:
        json.dump(liste, f, ensure_ascii=False, indent=2)
    return liste


def prompt_gecmisi_dosya(kullanici):
    try:
        kok = os.path.abspath(kasa.calisma_koku(kullanici))
    except Exception:
        try:
            kok = os.path.abspath(_ham_kullanici_koku(kullanici))
        except Exception:
            kok = None
    if not kok:
        raise ValueError("kasa kilitli")
    return os.path.join(kok, "prompt_gecmisi.json")


def prompt_gecmisi_oku(kullanici, limit=50, arama="", mod=""):
    try:
        yol = prompt_gecmisi_dosya(kullanici)
    except Exception:
        return []
    return prompt_store.oku(yol, limit, arama, mod)


def prompt_gecmisi_sil(kullanici, kimlik):
    return prompt_store.sil(prompt_gecmisi_dosya(kullanici), kimlik)


def prompt_gecmisi_yildiz(kullanici, kimlik, deger):
    return prompt_store.yildizla(prompt_gecmisi_dosya(kullanici), kimlik, deger)


def prompt_gecmisi_temizle(kullanici):
    return prompt_store.temizle(prompt_gecmisi_dosya(kullanici))


def _sohbet_dosya(kullanici):
    try:
        kok = os.path.abspath(kasa.calisma_koku(kullanici))
    except Exception:
        raise ValueError("kasa kilitli")
    return os.path.join(kok, "sohbetler.json")


def _sohbet_oku(kullanici):
    try:
        with open(_sohbet_dosya(kullanici), "r", encoding="utf-8") as f:
            veri = json.load(f)
    except Exception:
        return []
    if not isinstance(veri, list):
        return []
    return sorted(veri, key=lambda k: k.get("guncelleme") or k.get("tarih") or "",
                  reverse=True)


def _sohbet_yaz(kullanici, kayit):
    yol = _sohbet_dosya(kullanici)
    _klasor_elin(_kullanici_koku(kullanici))
    with _sohbet_kilit:
        try:
            with open(yol, "r", encoding="utf-8") as f:
                liste = json.load(f)
            if not isinstance(liste, list):
                liste = []
        except Exception:
            liste = []
        liste = [k for k in liste if k.get("id") != kayit.get("id")]
        liste.insert(0, kayit)
        liste = liste[:200]
        with open(yol, "w", encoding="utf-8") as f:
            json.dump(liste, f, ensure_ascii=False, indent=2)
    return kayit.get("id")


def _sohbet_sil(kullanici, kimlik):
    with _sohbet_kilit:
        try:
            with open(_sohbet_dosya(kullanici), "r", encoding="utf-8") as f:
                liste = json.load(f)
            if not isinstance(liste, list):
                return 0
        except Exception:
            return 0
        yeni = [k for k in liste if k.get("id") != kimlik]
        if len(yeni) == len(liste):
            return 0
        with open(_sohbet_dosya(kullanici), "w", encoding="utf-8") as f:
            json.dump(yeni, f, ensure_ascii=False, indent=2)
    return len(liste) - len(yeni)


def _sohbet_temizle(kullanici):
    with _sohbet_kilit:
        try:
            with open(_sohbet_dosya(kullanici), "w", encoding="utf-8") as f:
                json.dump([], f, ensure_ascii=False, indent=2)
        except Exception:
            return False
    return True


def _sohbet_baslik(mesajlar):
    for m in mesajlar or []:
        if isinstance(m, dict) and str(m.get("role") or "") == "user":
            metin = str(m.get("content") or "").strip()
            if metin:
                return metin[:60]
    return "Sohbet"


def _sohbet_kaydet(kullanici, mesajlar, yanit, olcum, baslik=None, sohbet_id=None):
    mesajlar = list(mesajlar or [])
    sohbet_id = sohbet_id or olcum.get("sohbet_id") or uuid.uuid4().hex[:12]
    simdi = _dt.datetime.now().isoformat(timespec="seconds")
    asistan = {"role": "assistant", "content": yanit or ""}
    rc = olcum.get("reasoning_content")
    if rc:
        asistan["thinking"] = rc
    kayit = {
        "id": sohbet_id,
        "tarih": simdi,
        "guncelleme": simdi,
        "baslik": baslik or _sohbet_baslik(mesajlar),
        "mesajlar": mesajlar + [asistan],
        "sonuc": {
            "sure_ms": olcum.get("sure_ms"),
            "token": olcum.get("token"),
            "tok_s": olcum.get("tok_s"),
            "yukleme_ms": olcum.get("yukleme_ms"),
            "finish": olcum.get("finish"),
            "model": olcum.get("model"),
            "reasoning_content": olcum.get("reasoning_content"),
        },
    }
    _sohbet_yaz(kullanici, kayit)
    return sohbet_id


def prompt_gecmisi_yaz(kullanici, kayit):
    yol = prompt_gecmisi_dosya(kullanici)
    _klasor_elin(_kullanici_koku(kullanici))
    prompt_store.yaz(yol, kayit)


def prompt_sil(kullanici, ad):
    liste = promptlari_oku(kullanici)
    yeni = [k for k in liste if (k.get("ad") or "") != ad]
    _klasor_elin(_kullanici_koku(kullanici))
    with open(_kullanici_promptlar(kullanici), "w", encoding="utf-8") as f:
        json.dump(yeni, f, ensure_ascii=False, indent=2)
    return len(yeni) != len(liste)


_GECMIS_KILIT = threading.RLock()


def _video_kapak_uret(blob):
    """Return a small JPEG thumbnail for a generated video on RunPod."""
    if not RUNPOD_MODE or not blob:
        return None
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
             "-ss", "0.05", "-frames:v", "1", "-vf", "scale=480:-2",
             "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"],
            input=blob, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=30, check=False)
        return result.stdout if result.returncode == 0 and result.stdout.startswith(b"\xff\xd8") else None
    except (OSError, subprocess.SubprocessError):
        return None


def gecmise_kaydet(kullanici, blob_listesi, meta, video=False, poster_blob=None):
    """Her uretimi BU kullanicinin gecmis/ altina yazar: goruntu PNG, video MP4.
    Ayni taban adli .json meta ile birlikte; budama uygulanir."""
    gecmis_dir = _kullanici_gecmis(kullanici)
    _klasor_elin(gecmis_dir)
    uzanti = ".mp4" if video else ".png"
    taban = "%s_seed%s_%s" % (_simdiki_zaman_damgasi(), meta.get("seed", "x"),
                              uuid.uuid4().hex[:6])
    dosyalar = []
    if len(blob_listesi) == 1:
        dosyalar = [taban + uzanti]
    else:
        dosyalar = ["%s_%d%s" % (taban, i, uzanti)
                    for i in range(len(blob_listesi))]
    for ad, blob in zip(dosyalar, blob_listesi):
        with open(os.path.join(gecmis_dir, ad), "wb") as f:
            f.write(blob)
    meta = dict(meta)
    poster_file = None
    if video and poster_blob:
        poster_file = taban + "_poster.jpg"
        with open(os.path.join(gecmis_dir, poster_file), "wb") as f:
            f.write(poster_blob)
    baytlar = {}
    for ad in dosyalar + ([poster_file] if poster_file else []):
        try:
            baytlar[ad] = os.path.getsize(os.path.join(gecmis_dir, ad))
        except OSError:
            pass
    meta.update({"dosyalar": dosyalar, "poster_file": poster_file,
                 "dosya_bayt": baytlar,
                 "tarih": _dt.datetime.now().isoformat(timespec="seconds")})
    with _GECMIS_KILIT:
        with open(os.path.join(gecmis_dir, taban + ".json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    _gecmis_buda(kullanici)
    return taban, dosyalar


def _gecmis_buda(kullanici):
    lim = _gecmis_limiti(kullanici)
    if lim <= 0:
        return
    gecmis_dir = _kullanici_gecmis(kullanici)
    try:
        jsonlar = sorted(a for a in os.listdir(gecmis_dir) if a.endswith(".json"))
        while len(jsonlar) > lim:
            eski = jsonlar.pop(0)
            try:
                with open(os.path.join(gecmis_dir, eski), "r", encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception:
                meta = {}
            for aday in ([eski] + list(meta.get("dosyalar") or []) +
                         [meta.get("poster_file")]):
                if not aday:
                    continue
                yol = os.path.abspath(os.path.join(gecmis_dir, aday))
                if not yol.startswith(os.path.abspath(gecmis_dir) + os.sep):
                    continue
                try:
                    if os.path.isfile(yol):
                        os.remove(yol)
                except Exception:
                    pass
    except Exception:
        pass


def gecmis_kaydi_sil(kullanici, kayit):
    """Tek gecmis kaydini (.json + png'leri) siler."""
    kayit = _guvenli_dosya_adi(kayit)
    if not kayit or not kayit.endswith(".json"):
        return False
    gecmis_dir = _kullanici_gecmis(kullanici)
    kok = os.path.abspath(gecmis_dir) + os.sep
    yol = os.path.abspath(os.path.join(gecmis_dir, kayit))
    if not yol.startswith(kok) or not os.path.isfile(yol):
        return False
    try:
        with open(yol, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except Exception:
        meta = {}
    ok = False
    for aday in ([kayit] + list(meta.get("dosyalar") or []) +
                 [meta.get("poster_file")]):
        if not aday:
            continue
        p = os.path.abspath(os.path.join(gecmis_dir, aday))
        if not p.startswith(kok):
            continue
        try:
            if os.path.isfile(p):
                os.remove(p)
                ok = True
        except Exception:
            pass
    return ok


def gecmis_temizle(kullanici):
    """Bu kullanicinin gecmisindeki tum kayitlari siler. Donus: silinen dosya adedi."""
    gecmis_dir = _kullanici_gecmis(kullanici)
    _klasor_elin(gecmis_dir)
    kok = os.path.abspath(gecmis_dir) + os.sep
    n = 0
    try:
        for ad in os.listdir(gecmis_dir):
            if not (ad.lower().endswith((".png", ".mp4", ".json")) or
                    ad.lower().endswith("_poster.jpg")):
                continue
            yol = os.path.abspath(os.path.join(gecmis_dir, ad))
            if not yol.startswith(kok):
                continue
            try:
                if os.path.isfile(yol):
                    os.remove(yol)
                    n += 1
            except Exception:
                pass
    except Exception:
        pass
    return n


def _gecmis_album_id(meta):
    if not isinstance(meta, dict) or not meta.get("prompt"):
        return None
    dosyalar = meta.get("dosyalar") or []
    # A history record is one generated result. Never group a video result
    # with still images, regardless of which video container it uses.
    if any(str(dosya).lower().endswith((".mp4", ".webm", ".mov", ".m4v"))
           for dosya in dosyalar):
        return None
    refs = meta.get("foto_hashes")
    if not isinstance(refs, list) or not refs:
        refs = [meta.get("foto_kutuphane"), meta.get("foto_kutuphane2")]
    refs = [str(ref) for ref in refs if ref]
    anahtar = json.dumps(
        [str(meta.get("prompt")), str(meta.get("model", MODEL_FLUX2)), refs],
        ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(anahtar.encode("utf-8")).hexdigest()


def _gecmis_dosya_favorili(meta, dosya):
    if str(dosya).lower().endswith((".mp4", ".webm", ".mov")):
        return False
    favoriler = meta.get("yildiz_dosyalar")
    if isinstance(favoriler, list):
        return dosya in favoriler
    return bool(meta.get("yildiz"))


def _gecmis_kayit_bilgisi(gecmis_dir, ad, meta, dosya=None):
    dosyalar = meta.get("dosyalar") or []
    ilk = dosya or (dosyalar[0] if dosyalar else (ad[:-5] + ".png"))
    try:
        dosya_bayt = os.path.getsize(os.path.join(gecmis_dir, ilk))
    except OSError:
        dosya_bayt = None
    poster_file = meta.get("poster_file")
    if (not isinstance(poster_file, str) or os.path.basename(poster_file) != poster_file
            or not poster_file.lower().endswith("_poster.jpg")
            or not os.path.isfile(os.path.join(gecmis_dir, poster_file))):
        poster_file = None
    return {
        "kayit": ad, "dosya": ilk, "dosyalar": dosyalar,
        "tur": "video" if ilk.lower().endswith(".mp4") else "goruntu",
        "tarih": meta.get("tarih"), "seed": meta.get("seed"),
        "mod": meta.get("mod"), "is_tipi": meta.get("is_tipi"),
        "model": meta.get("model", MODEL_FLUX2),
        "workflow": meta.get("workflow"),
        "prompt": (meta.get("prompt") or "")[:300],
        "negative": (meta.get("negative") or "")[:300],
        "width": meta.get("width"), "height": meta.get("height"),
        "steps": meta.get("steps"), "cfg": meta.get("cfg"),
        "sampler": meta.get("sampler"),
        "dosya_bayt": dosya_bayt,
        "frames": meta.get("frames"), "suresn": meta.get("suresn"),
        "fps": meta.get("fps"),
        "yildiz": _gecmis_dosya_favorili(meta, ilk),
        "video_poster_url": ("/gecmis/%s" % urllib.parse.quote(poster_file)
                             if poster_file else None),
        "url": "/gecmis/%s" % urllib.parse.quote(ilk)}


def gecmis_listesi(kullanici, sayfa, adet, arama="", favoriler=False,
                   albume_gore=False, album_id=None):
    gecmis_dir = _kullanici_gecmis(kullanici)
    _klasor_elin(gecmis_dir)
    try:
        jsonlar = sorted((a for a in os.listdir(gecmis_dir) if a.endswith(".json")),
                         reverse=True)
    except Exception:
        jsonlar = []
    q = str(arama or "").strip().casefold()
    adaylar = []
    tum_kayitlar = []
    for ad in jsonlar:
        try:
            with open(os.path.join(gecmis_dir, ad), "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        tum_kayitlar.append((ad, meta))
        if q and q not in " ".join(str(meta.get(k) or "")
                                   for k in ("prompt", "negative", "model", "mod",
                                             "seed", "width", "height", "tarih")).casefold():
            continue
        adaylar.append((ad, meta))
    album_konumlari = {}
    album_foto_sayilari = {}
    for ad, meta in tum_kayitlar:
        album_id_for_record = _gecmis_album_id(meta)
        if album_id_for_record is None:
            continue
        files = meta.get("dosyalar") or [ad[:-5] + ".png"]
        for file in files:
            album_konumlari[(ad, file)] = (
                album_id_for_record,
                album_foto_sayilari.get(album_id_for_record, 0))
            album_foto_sayilari[album_id_for_record] = (
                album_foto_sayilari.get(album_id_for_record, 0) + 1)
    if album_id is not None:
        if not re.fullmatch(r"[a-f0-9]{64}", str(album_id)):
            return {"toplam": 0, "sayfa": 1, "adet": 100, "kayitlar": []}
        album_eslesmeleri = []
        for ad, meta in adaylar:
            if _gecmis_album_id(meta) == album_id:
                files = meta.get("dosyalar") or [ad[:-5] + ".png"]
                album_eslesmeleri.append((ad, meta, files))
        toplam = sum(len(files) for _, _, files in album_eslesmeleri)
        sayfa = max(1, sayfa)
        adet = max(1, min(100, adet))
        baslangic = (sayfa - 1) * adet
        bitis = baslangic + adet
        album_kayitlari = []
        album_indeksi = 0
        for ad, meta, files in album_eslesmeleri:
            for file in files:
                if baslangic <= album_indeksi < bitis:
                    item = _gecmis_kayit_bilgisi(gecmis_dir, ad, meta, file)
                    item["album_index"] = album_indeksi
                    item["album_id"] = album_id
                    album_kayitlari.append(item)
                album_indeksi += 1
                if album_indeksi >= bitis:
                    break
            if album_indeksi >= bitis:
                break
        return {"toplam": toplam, "sayfa": sayfa, "adet": adet,
                "kayitlar": album_kayitlari}
    if favoriler:
        kayitlar = []
        for ad, meta in adaylar:
            files = meta.get("dosyalar") or [ad[:-5] + ".png"]
            for file in files:
                if not _gecmis_dosya_favorili(meta, file):
                    continue
                item = _gecmis_kayit_bilgisi(gecmis_dir, ad, meta, file)
                album_position = album_konumlari.get((ad, file))
                if album_position and albume_gore \
                        and album_foto_sayilari.get(album_position[0], 0) >= 2:
                    item.update({"album_member": True,
                                 "album_id": album_position[0],
                                 "album_index": album_position[1],
                                 "album_count": album_foto_sayilari.get(
                                     album_position[0], 0)})
                kayitlar.append((ad, item))
        adaylar = kayitlar
        toplam = len(adaylar)
        sayfa = max(1, sayfa)
        adet = max(1, min(100, adet))
        dilim = adaylar[(sayfa - 1) * adet:sayfa * adet]
        return {"toplam": toplam, "sayfa": sayfa, "adet": adet,
                "kayitlar": [item for _, item in dilim]}
    if albume_gore:
        albumler = {}
        gorunum = []
        for ad, meta in adaylar:
            album_id = _gecmis_album_id(meta)
            if album_id is None:
                gorunum.append(("tek", [(ad, meta)]))
            else:
                if album_id not in albumler:
                    albumler[album_id] = []
                    gorunum.append((album_id, albumler[album_id]))
                albumler[album_id].append((ad, meta))
        adaylar = []
        for album_id, uyeler in gorunum:
            photo_count = sum(len(meta.get("dosyalar") or [1])
                              for _, meta in uyeler)
            if album_id == "tek" or photo_count < 2:
                adaylar.extend(uyeler)
                continue
            kapak = None
            for uye_ad, uye_meta in uyeler:
                dosyalar = uye_meta.get("dosyalar") or [uye_ad[:-5] + ".png"]
                kapak_dosyasi = next(
                    (file for file in dosyalar
                     if _gecmis_dosya_favorili(uye_meta, file)), None)
                if kapak_dosyasi:
                    kapak = (uye_ad, uye_meta, kapak_dosyasi)
                    break
                if kapak is None:
                    kapak = (uye_ad, uye_meta, dosyalar[0])
            oge = _gecmis_kayit_bilgisi(
                gecmis_dir, kapak[0], kapak[1], kapak[2])
            oge.update({"album": True, "album_id": album_id,
                        "album_count": photo_count})
            adaylar.append((kapak[0], oge))
    else:
        fotograf_kayitlari = []
        for ad, meta in adaylar:
            dosyalar = meta.get("dosyalar") or [ad[:-5] + ".png"]
            fotograf_kayitlari.extend(
                (ad, _gecmis_kayit_bilgisi(gecmis_dir, ad, meta, dosya))
                for dosya in dosyalar)
        adaylar = fotograf_kayitlari
    toplam = len(adaylar)
    sayfa = max(1, sayfa)
    adet = max(1, min(100, adet))
    dilim = adaylar[(sayfa - 1) * adet:sayfa * adet]
    kayitlar = []
    for ad, meta in dilim:
        if meta.get("album") or "dosya" in meta:
            kayitlar.append(meta)
        else:
            kayitlar.append(_gecmis_kayit_bilgisi(gecmis_dir, ad, meta))
    return {"toplam": toplam, "sayfa": sayfa, "adet": adet, "kayitlar": kayitlar}


def gecmis_kaydi_yildiz(kullanici, kayit, yildiz, dosya=None):
    kayit = _guvenli_dosya_adi(kayit)
    if not kayit or not kayit.endswith(".json"):
        return False
    gecmis_dir = _kullanici_gecmis(kullanici)
    kok = os.path.abspath(gecmis_dir) + os.sep
    yol = os.path.abspath(os.path.join(gecmis_dir, kayit))
    if not yol.startswith(kok) or not os.path.isfile(yol):
        return False
    try:
        with _GECMIS_KILIT:
            with open(yol, "r", encoding="utf-8") as f:
                meta = json.load(f)
            if not isinstance(meta, dict):
                return False
            if dosya is None:
                meta["yildiz"] = bool(yildiz)
                meta.pop("yildiz_dosyalar", None)
            else:
                dosya = _guvenli_dosya_adi(dosya)
                dosyalar = meta.get("dosyalar") or [kayit[:-5] + ".png"]
                if not dosya or dosya not in dosyalar \
                        or dosya.lower().endswith((".mp4", ".webm", ".mov")):
                    return False
                mevcut = meta.get("yildiz_dosyalar")
                if not isinstance(mevcut, list):
                    mevcut = list(dosyalar) if meta.get("yildiz") else []
                if yildiz and dosya not in mevcut:
                    mevcut.append(dosya)
                elif not yildiz:
                    mevcut = [ad for ad in mevcut if ad != dosya]
                meta["yildiz_dosyalar"] = mevcut
                meta["yildiz"] = bool(mevcut)
            gecici = yol + ".tmp"
            with open(gecici, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(gecici, yol)
            return True
    except (OSError, ValueError):
        return False


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


def _dosya_isit(yol, job):
    """Model dosyasini sirali okuyup sayfa onbelligine koyar.

    ComfyUI model dosyalarini mmap-fault yoluyla yukler; sicak cache'te bu
    yukleme ~2-3 kat daha hizli tamamlanir (llama'da olculdu: sicak 14 sn,
    soguk 26-33 sn). Sirali NVMe okuma ~2400 MB/s, fault yolu 350-600 MB/s."""
    try:
        f = open(yol, "rb", buffering=0)
    except OSError:
        return
    blok = bytearray(_IMGE_ISITMA_OKUMA)
    mv = memoryview(blok)
    try:
        while not job.get("iptal"):
            if not f.readinto(mv):
                break
    finally:
        f.close()


def _imge_modeli_isitme_karari(model, llama_canliydi):
    """Isitilacak text encoder dosya yolu veya None döner."""
    global _son_goruntu_etkilesim
    if RUNPOD_MODE:
        workflow_key = {MODEL_FLUX2: "flux2", MODEL_Z_IMAGE: "z_image",
                        MODEL_FLUX3: "flux3", MODEL_KREA2: "krea2"}.get(model)
        encoder = pod_setup.workflow(workflow_key).get("text_encoder_file") \
            if workflow_key else None
        if not encoder:
            return None
    else:
        encoder = _TE_DOSYALARI.get(model, TEXT_ENCODER_FILE)
    yol = os.path.join(COMFY_KOK, "models", "text_encoders", encoder)
    simdi = time.monotonic()
    if not llama_canliydi and _son_goruntu_etkilesim is not None \
            and simdi - _son_goruntu_etkilesim < _IMGE_ISITMA_ATLA_SN:
        return None
    _son_goruntu_etkilesim = simdi
    return yol


def _isi_calistir(job):
    """Kuyruk iscisi tek uretimi bastan sona yurutur (eski /api/uret govdesi).
    Istemci baglantiyi kesse bile devam eder; sonuc is kaydinda + gecmiste durur."""
    req = job.get("istek") or {}
    job["durum"] = "calisiyor"
    if not req.get("prompt_destek") and not job.get("baslangic"):
        job["baslangic"] = time.time()
    try:
        hata = _is_dogrula(req)
        if hata:
            job["durum"] = "hata"
            job["hata"] = hata.get("hata", "gecersiz istek")
            job["bitis"] = time.time()
            return
        w = max(64, min(4096, int(req.get("width", 1024))))
        h = max(64, min(4096, int(req.get("height", 1024))))
        max_side = max(256, min(4096, int(req.get("max_side", 1536))))
        raw = None
        raw2 = None
        ham1 = None
        ham2 = None
        foto_kaynak = None
        foto_hedef = None
        foto_kaynak2 = None
        foto_hedef2 = None
        kucultuldu = False
        olcekle = req.get("max_olcek", True)
        # Is tipi moduler noktadan secilir; video uretimi _video_uret'e gider.
        n_ref_istek = (1 if req.get("image_base64") else 0) \
            + (1 if req.get("image2_base64") else 0)
        is_tipi = is_tipini_belirle(req, n_ref_istek)
        vid = video_parametreleri(req)
        if is_tipi == IS_TIPI_VIDEO:
            _video_uret(job, req)
            return
        if req.get("image_base64"):
            ham1 = base64.b64decode(req["image_base64"])
            raw, foto_kaynak, foto_hedef, k1 = _fotograf_hazirla(ham1, max_side, olcekle)
            kucultuldu = kucultuldu or k1
        if req.get("image2_base64"):
            ham2 = base64.b64decode(req["image2_base64"])
            raw2, foto_kaynak2, foto_hedef2, k2 = _fotograf_hazirla(ham2, max_side, olcekle)
            kucultuldu = kucultuldu or k2
        # olcu onceligi: 1. foto > 2. foto > alanlar (Moody ciktiyi 1. fotodan alir)
        baz = foto_hedef or foto_hedef2
        if baz is not None:
            # duzenleme modu: olcu kucultulmus fotodan alinir
            w, h = baz
        else:
            # Saf üretimde ölçüler doğrudan çözünürlük alanlarından gelir.
            # Gizli referans-ölçekleme ayarı kayıtlı max_side değeriyle bunları
            # küçültmemeli veya büyütmemeli.
            w, h = max(16, (w // 16) * 16), max(16, (h // 16) * 16)
        model = req.get("model")
        if model in (MODEL_ANIMA, MODEL_MOODY_ANIMA) and n_ref_istek:
            model = MODEL_FLUX2
        elif RUNPOD_MODE and model in (MODEL_ANIMA, MODEL_MOODY_ANIMA):
            model = MODEL_ILLUSTRIOUS
        if model not in (MODEL_FLUX2, MODEL_Z_IMAGE, MODEL_ANIMA,
                         MODEL_MOODY_ANIMA, MODEL_FLUX3, MODEL_KREA2,
                         MODEL_ILLUSTRIOUS):
            model = MODEL_FLUX2
        model_instance = req.get("model_instance") or model
        workflow = (pod_setup.workflow(model_instance) if RUNPOD_MODE else {})
        default_sampler = workflow.get("sampler") or (
            "res_multistep" if model == MODEL_Z_IMAGE else "euler")
        default_steps = workflow.get("steps") or (
            9 if model == MODEL_Z_IMAGE else
            8 if model == MODEL_ANIMA else 4)
        default_cfg = workflow.get("cfg", 1.0)
        prompt_girdi = str(req.get("prompt_girdi") or req.get("prompt") or "")
        prompt_servis = str(req.get("prompt") or "")
        ceviri = None
        if req.get("prompt_ceviri"):
            job["ilerleme"] = {"asama": "ceviri",
                               "calisan": "Türkçe prompt İngilizceye çevriliyor",
                               "oran": 0.04}
            ceviri = job["ceviri_sonuc"]
            prompt_servis = ceviri["metin"]
            req["prompt_girdi"] = prompt_girdi
            req["prompt_en"] = prompt_servis
        p = {"prompt": prompt_servis,
             "negative": str(req.get("negative") or "").strip()[:2000],
             "width": w, "height": h, "seed": int(req.get("seed", -1)),
             "steps": max(1, min(50, int(req.get("steps", default_steps)))),
             "cfg": float(req.get("cfg", default_cfg)),
             "sampler": req.get("sampler", default_sampler),
             "flux3_bbox_mode": bool(req.get("flux3_bbox_mode"))}
        if RUNPOD_MODE:
            workflow_key = {MODEL_FLUX2: "flux2", MODEL_Z_IMAGE: "z_image",
                            MODEL_FLUX3: "flux3", MODEL_KREA2: "krea2",
                            MODEL_ILLUSTRIOUS: "illustrious"}[model]
            if model in (MODEL_FLUX2, MODEL_Z_IMAGE, MODEL_FLUX3, MODEL_KREA2):
                p["model_files"] = {
                    "diffusion": workflow.get("diffusion_file"),
                    "text_encoder": workflow.get("text_encoder_file"),
                    "vae": workflow.get("vae_file"),
                }
            elif model == MODEL_ILLUSTRIOUS:
                p["model_files"] = {"checkpoint": workflow.get("checkpoint_file")}
            p["loras"] = pod_setup.ensure_loras(
                workflow_key, req.get("loras") if "loras" in req else None,
                job=job)
        uretim_baslangic = time.perf_counter()
        image_name = None
        image_name2 = None
        if raw is not None:
            fn = "webui_%s.png" % uuid.uuid4().hex[:8]
            up = upload_image_to_comfy(raw, fn)
            image_name = up.get("name", fn)
        if raw2 is not None:
            fn = "webui2_%s.png" % uuid.uuid4().hex[:8]
            up = upload_image_to_comfy(raw2, fn)
            image_name2 = up.get("name", fn)
        if model == MODEL_ANIMA:
            prompt, seed = build_anima_prompt(p)
        elif model == MODEL_MOODY_ANIMA:
            prompt, seed = build_anima_prompt(p, MOODY_ANIMA_DIFFUSION_FILE)
        elif model == MODEL_FLUX3:
            prompt, seed = build_flux3_prompt(p, image_name, image_name2)
        elif model == MODEL_KREA2:
            prompt, seed = build_krea2_prompt(p)
        elif model == MODEL_ILLUSTRIOUS:
            prompt, seed = build_illustrious_prompt(p)
        else:
            prompt_builder = build_z_image_prompt if model == MODEL_Z_IMAGE else build_prompt
            prompt, seed = prompt_builder(p, image_name, image_name2)
        if (model == MODEL_Z_IMAGE and not image_name and not image_name2
                and p.get("output_width") and p.get("output_height")):
            w, h = p["output_width"], p["output_height"]
        cid = uuid.uuid4().hex
        isitma_yolu = _imge_modeli_isitme_karari(
            model, bool(job.get("_llama_canliydi")))
        if isitma_yolu:
            job["ilerleme"] = {"asama": "isitma",
                               "calisan": "Model dosyası önbelleğe alınıyor",
                               "oran": 0.02}
            _dosya_isit(isitma_yolu, job)
        if job["iptal"]:
            raise RuntimeError("İş durduruldu")
        res = comfy_post_json("/prompt", {"prompt": prompt, "client_id": cid})
        if "node_errors" in res and res["node_errors"]:
            job["durum"] = "hata"
            job["hata"] = "node hatasi: %s" % json.dumps(res["node_errors"])[:500]
            job["bitis"] = time.time()
            return
        job["prompt_id"] = res["prompt_id"]
        _aktif["prompt_id"] = res["prompt_id"]
        _aktif["sahip"] = job.get("sahip")
        _aktif["baslangic"] = time.time()
        _aktif["ilerleme"] = {"kuyrukta": True, "oran": 0.0}
        _aktif["nodlar"] = {nid: v.get("class_type", nid)
                            for nid, v in prompt.items()}
        threading.Thread(target=_ws_ilerleme_dinle,
                         args=(res["prompt_id"], cid, _aktif["nodlar"]),
                         daemon=True).start()
        try:
            if job["iptal"]:
                _comfy_isini_kes(res["prompt_id"])
            entry = wait_result(res["prompt_id"], job=job)
        finally:
            if _aktif.get("prompt_id") == res["prompt_id"]:
                _aktif["prompt_id"] = None
                _aktif["sahip"] = None
        imgs = []
        bloblar = []
        for node_id, out in (entry.get("outputs") or {}).items():
            for im in out.get("images", []) or []:
                blob = fetch_view_image(im)
                bloblar.append(blob)
                imgs.append("data:image/png;base64,"
                            + base64.b64encode(blob).decode("ascii"))
        generation_ms = round((time.perf_counter() - uretim_baslangic) * 1000)
        sure_ms = round((time.time() - job["baslangic"]) * 1000) \
            if job.get("baslangic") else generation_ms
        prompt_islem = ("ceviri" if ceviri else
                        ("prompt_destek" if req.get("prompt_destek") else None))
        prompt_en = p["prompt"] if prompt_islem else None
        n_ref = (1 if image_name else 0) + (1 if image_name2 else 0)
        mod = "saf-uretim" if n_ref == 0 else ("cift-referans" if n_ref == 2 else "duzenleme")
        sahip = job.get("sahip")
        # Kota: yazmadan once kontrol; asimda is temiz hatayla biter, yari dosya kalmaz.
        kota_hata = _kota_kontrol(sahip,
                                  yeni_bayt=sum(len(b) for b in bloblar)
                                  + (len(raw) if raw else 0)
                                  + (len(raw2) if raw2 else 0),
                                  yeni_kayit=len(bloblar) + (1 if raw else 0)
                                  + (1 if raw2 else 0))
        if kota_hata:
            job["durum"] = "hata"
            job["hata"] = kota_hata
            job["bitis"] = time.time()
            return
        # Kutuphane: yuklenen fotolari tarih-adla sakla (uretim basarili olunca).
        _is_sonucu_baslat(job)
        kut1 = kut2 = None
        try:
            if ham1 is not None and raw is not None and foto_hedef:
                kut1 = kutuphaneye_kaydet(sahip, ham1, raw, foto_hedef[0], foto_hedef[1])
        except Exception:
            kut1 = None
        try:
            if ham2 is not None and raw2 is not None and foto_hedef2:
                kut2 = kutuphaneye_kaydet(sahip, ham2, raw2, foto_hedef2[0], foto_hedef2[1])
        except Exception:
            kut2 = None
        # Gecmis: PNG + ayni adli .json (tum parametreler, seed, mod, foto adlari).
        gecmis_kayit = None
        gecmis_dosyalar = []
        try:
            meta = {"prompt": p["prompt"], "prompt_tr": prompt_girdi
                    if prompt_islem else None, "prompt_en": prompt_en,
                    "prompt_islem": prompt_islem,
                    "prompt_ceviri_model": ceviri["model"] if ceviri else None,
                    "prompt_ceviri_device": ceviri["cihaz"] if ceviri else None,
                    "prompt_ceviri_ms": ceviri["sure_ms"] if ceviri else None,
                    "generation_ms": generation_ms, "sure_ms": sure_ms,
                    "negative": p["negative"],
                    "width": w, "height": h, "seed": seed,
                    "steps": p["steps"], "cfg": p["cfg"], "sampler": p["sampler"],
                    "model": model,
                    "workflow": req.get("workflow", "tek"), "max_side": max_side,
                    "max_olcek": olcekle, "mod": mod, "is_tipi": is_tipi,
                    "suresn": vid["suresn"], "fps": vid["fps"],
                    "foto_hashes": [hashlib.sha256(raw_image).hexdigest()
                                    for raw_image in (ham1, ham2)
                                    if raw_image is not None],
                    "foto_kutuphane": kut1, "foto_kutuphane2": kut2}
            gecmis_kayit, gecmis_dosyalar = gecmise_kaydet(sahip, bloblar, meta)
        except Exception:
            gecmis_kayit, gecmis_dosyalar = None, []
        job["sonuc"] = {"gorseller": imgs, "seed": seed,
                        "width": w, "height": h,
                        "model": model,
                        "prompt": p["prompt"], "negative": p["negative"],
                        "prompt_girdi": prompt_girdi, "prompt_en": prompt_en,
                        "prompt_islem": prompt_islem,
                        "prompt_ceviri_model": ceviri["model"] if ceviri else None,
                        "prompt_ceviri_device": ceviri["cihaz"] if ceviri else None,
                        "prompt_ceviri_ms": ceviri["sure_ms"] if ceviri else None,
                        "generation_ms": generation_ms, "sure_ms": sure_ms,
                        "prompt_destek": bool(req.get("prompt_destek")),
                        "foto_kaynak": foto_kaynak, "foto_hedef": foto_hedef,
                        "foto_kaynak2": foto_kaynak2, "foto_hedef2": foto_hedef2,
                        "foto_kucultuldu": kucultuldu,
                        "foto_boy": foto_hedef, "foto_boy2": foto_hedef2,
                        "mod": mod, "is_tipi": is_tipi,
                        "suresn": vid["suresn"], "fps": vid["fps"],
                        "kutuphane": kut1, "kutuphane2": kut2,
                        "gecmis": gecmis_kayit,
                        "gecmis_dosyalar": gecmis_dosyalar,
                        "is_id": job["is_id"]}
        job["ilerleme"] = {"bitti": True, "oran": 1.0}
        job["durum"] = "bitti"
        job["bitis"] = time.time()
    except Exception as e:
        if "durduruldu" in str(e):
            job["durum"] = "durduruldu"
        else:
            job["durum"] = "hata"
            job["hata"] = str(e)[:1000]
        job["bitis"] = time.time()
    finally:
        try:
            # Is nasil biterse bitsin ComfyUI'deki ozel dosyalar silinir.
            kasa.temizle_comfy_artiklari()
        except Exception:
            pass
        try:
            # Ayni sekilde /history kaydi da temizlenir (cikti dosyalari silinmis olur).
            comfy_request("POST", "/history", body=b'{"clear": true}',
                          headers={"Content-Type": "application/json"})
        except Exception:
            pass


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
        if parsed.path in ("/", "/index.html"):
            with open(os.path.join(BASE_DIR, "setup.html"), "rb") as stream:
                self._send(200, stream.read(), "text/html; charset=utf-8")
            return
        if parsed.path.startswith("/static/"):
            name = parsed.path[len("/static/"):]
            if name not in ("setup.js", "setup.css"):
                self._send(404, '{"hata":"bulunamadi"}')
                return
            try:
                with open(os.path.join(BASE_DIR, "static", name), "rb") as stream:
                    body = stream.read()
            except OSError:
                self._send(404, '{"hata":"bulunamadi"}')
                return
            content_type = ("application/javascript; charset=utf-8" if name.endswith(".js")
                            else "text/css; charset=utf-8")
            self._send(200, body, content_type)
            return
        if parsed.path in ("/api/sunucu-hazir", "/api/setup-state"):
            ready = pod_setup.is_ready()
            self._send(200, json.dumps({"hazir": ready,
                                       "hata": bool(_startup_error),
                                       "parolasiz": self._setup_parolasiz()},
                                  ensure_ascii=False))
            return
        if parsed.path == "/api/kurulum-durumu":
            if not pod_setup.is_ready():
                self._send(503, '{"hata":"Sistem hazırlanıyor."}')
            else:
                self._send(200, json.dumps({"kurulu": _auth_store.kurulu_mu(),
                                           "runpod": True}))
            return
        if not pod_setup.is_ready():
            self._send(503, '{"hata":"Sistem hazırlanıyor."}')
            return
        if parsed.path == "/api/ben":
            if self._setup_parolasiz():
                self._send(200, json.dumps({"kullanici": "Kurulum paneli",
                                           "rol": "admin", "parolasiz": True},
                                          ensure_ascii=False))
                return
            kullanici = self._oturum_kullanici()
            if kullanici is None:
                self._send(401, '{"hata":"giris gerekli"}')
            else:
                self._send(200, json.dumps({"kullanici": kullanici,
                                            "rol": _auth_store.rol(kullanici)}))
            return
        if parsed.path == "/api/pod-kurulum":
            if not self._setup_yetkili():
                return
            self._send(200, json.dumps(pod_setup.status(), ensure_ascii=False))
            return
        if parsed.path == "/api/app-update":
            if not self._setup_yetkili():
                return
            state = dict(_app_update_state)
            if state.get('status') == 'idle':
                try:
                    state = json.loads((app_update.ROOT / 'result.json').read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    pass
            self._send(200, json.dumps(state, ensure_ascii=False))
            return
        if parsed.path == "/api/system-settings":
            if not self._setup_yetkili():
                return
            self._send(200, json.dumps(pod_setup.system_settings(), ensure_ascii=False))
            return
        if parsed.path == "/api/loras" and RUNPOD_MODE:
            if self._auth_gerekli() is None:
                return
            query = urllib.parse.parse_qs(parsed.query or "")
            model = (query.get("model") or [None])[0]
            self._send(200, json.dumps({"loras": pod_setup.lora_status(model)},
                                       ensure_ascii=False))
            return
        if parsed.path == "/api/log":
            if not self._setup_yetkili():
                return
            query = urllib.parse.parse_qs(parsed.query or "")
            source = (query.get("kaynak") or ["setup"])[0]
            try:
                count = max(10, min(500, int((query.get("satir") or ["200"])[0])))
                lines = _log_tail(source, count)
            except ValueError as exc:
                self._send(400, json.dumps({"hata": str(exc)}))
                return
            self._send(200, json.dumps({"kaynak": source, "satirlar": lines},
                                       ensure_ascii=False))
            return
        self._send(404, '{"hata":"bulunamadi"}')

    def _setup_post(self, parsed):
        global _app_update_state
        if not self._origin_kontrol():
            return
        if not pod_setup.is_ready():
            self._send(503, '{"hata":"Sistem hazırlanıyor."}')
            return
        if parsed.path == "/api/giris":
            if self._setup_parolasiz():
                self._send(409, '{"hata":"Kurulum panelinde giriş gerekmiyor."}')
                return
            req = self._json_oku()
            if "__hata__" in req:
                self._send(400, json.dumps({"hata": req["__hata__"]}))
                return
            try:
                username = (req.get("kullanici") or "").strip()
                token, role = _auth_store.giris(username, req.get("sifre") or "",
                                                self.client_address[0])
            except auth.AuthHatasi:
                self._send(401, '{"hata":"Kullanıcı adı veya şifre hatalı."}')
                return
            self._send(200, json.dumps({"kullanici": username, "rol": role}),
                       ek_baslik=self._oturum_cerezi(token))
            return
        if parsed.path == "/api/cikis":
            _auth_store.cikis(self._cerez_token())
            self._send(200, '{"cikis":true}', ek_baslik=self._cerez_sil())
            return
        if parsed.path == "/api/app-update":
            if not self._setup_yetkili():
                return
            if os.environ.get('WEBUI_UPDATE_SUPERVISOR') != '1':
                self._send(409, '{"hata":"Bu imaj güncelleme başlatıcısını içermiyor; yeni Pod açın."}')
                return
            if not _app_update_lock.acquire(blocking=False):
                self._send(409, '{"hata":"Güncelleme zaten sürüyor."}')
                return
            _app_update_state = {"status": "preparing"}
            threading.Thread(target=_app_update_worker, daemon=True).start()
            self._send(202, '{"status":"preparing"}')
            return
        if parsed.path == "/api/pod-kurulum":
            if not self._setup_yetkili():
                return
            if _kapatma_istegi.is_set():
                self._send(409, '{"hata":"Güncelleme için uygulama kapanıyor."}')
                return
            req = self._json_oku()
            try:
                if req.get("islem") == "iptal":
                    pod_setup.cancel(req.get("grup"))
                elif req.get("islem") == "duraklat":
                    pod_setup.pause(req.get("grup"))
                elif req.get("islem") == "devam":
                    pod_setup.start(req.get("grup"))
                elif req.get("islem") == "kaldir":
                    pod_setup.remove(req.get("grup"))
                elif req.get("islem") == "sira":
                    pod_setup.move(req.get("grup"), req.get("yon"))
                else:
                    pod_setup.start(req.get("grup"))
            except ValueError as exc:
                self._send(400, json.dumps({"hata": str(exc)}, ensure_ascii=False))
            except (OSError, RuntimeError) as exc:
                self._send(409, json.dumps({"hata": str(exc)}, ensure_ascii=False))
            else:
                self._send(202, json.dumps(pod_setup.status(), ensure_ascii=False))
            return
        if parsed.path == "/api/system-settings":
            if not self._setup_yetkili():
                return
            req = self._json_oku()
            if "__hata__" in req:
                self._send(400, json.dumps({"hata": req["__hata__"]}))
                return
            try:
                action = req.get("islem", "kaydet")
                if action == "baglanti":
                    result = pod_setup.update_model_link(
                        req.get("grup"), req.get("dosya"), req.get("baglanti"),
                        restore=False)
                elif action == "varsayilan":
                    result = pod_setup.update_model_link(
                        req.get("grup"), req.get("dosya"), "", restore=True)
                elif action == "kaydet":
                    result = pod_setup.update_system_settings(req)
                elif action == "lora_ekle":
                    result = pod_setup.add_lora(req)
                elif action == "lora_ayar":
                    result = pod_setup.configure_lora(req.get("id"), req)
                elif action == "lora_sil":
                    result = pod_setup.delete_lora(req.get("id"))
                elif action == "model_ekle":
                    result = pod_setup.create_model_instance(req)
                elif action == "model_ad":
                    result = pod_setup.rename_model_instance(req.get("id"), req.get("name"))
                elif action == "model_sil":
                    result = pod_setup.delete_model_instance(req.get("id"))
                else:
                    raise ValueError("Bilinmeyen sistem ayarı işlemi.")
            except ValueError as exc:
                self._send(400, json.dumps({"hata": str(exc)}, ensure_ascii=False))
                return
            except RuntimeError as exc:
                self._send(409, json.dumps({"hata": str(exc)}, ensure_ascii=False))
                return
            except OSError:
                self._send(500, '{"hata":"Sistem ayarı kaydedilemedi."}')
                return
            self._send(200, json.dumps(result, ensure_ascii=False))
            return
        if parsed.path == "/api/storage-clean":
            if not self._setup_yetkili():
                return
            req = self._json_oku()
            if "__hata__" in req:
                self._send(400, json.dumps({"hata": req["__hata__"]}))
                return
            if req.get("onay") is not True:
                self._send(400, '{"hata":"Temizleme onayı gerekli."}')
                return
            with _isler_kilit:
                active_jobs = any(
                    job.get("durum") in ("kuyrukta", "calisiyor")
                    or (hasattr(job.get("olay"), "is_set")
                        and not job["olay"].is_set())
                    for job in _isler.values())
                queued_jobs = bool(_kuyruk or _ceviri_kuyrugu)
            if active_jobs or queued_jobs:
                self._send(409, '{"hata":"Önce devam eden üretim ve sohbet işlerini bitirin."}')
                return
            if _app_update_state.get("status") in ("preparing", "downloading", "applying"):
                self._send(409, '{"hata":"Uygulama güncellemesi sürerken depolama temizlenemez."}')
                return
            try:
                result = pod_setup.clear_rebuildable_storage()
            except (OSError, RuntimeError) as exc:
                self._send(409, json.dumps({"hata": str(exc)}, ensure_ascii=False))
                return
            self._send(200, json.dumps(result, ensure_ascii=False))
            return
        self._send(404, '{"hata":"bulunamadi"}')

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
        parsed = urllib.parse.urlparse(self.path)
        if RUNPOD_MODE and getattr(self.server, "server_port", 8189) == SETUP_PORT:
            self._setup_get(parsed)
            return
        if parsed.path == "/api/sunucu-hazir":
            kapaniyor = _kapatma_istegi.is_set()
            hazir = not kapaniyor and (not RUNPOD_MODE or pod_setup.is_ready())
            self._send(503 if kapaniyor else 200,
                       json.dumps({"pid": os.getpid(), "hazir": hazir}))
            return
        if (RUNPOD_MODE and not pod_setup.is_ready()
                and parsed.path not in ("/", "/index.html", "/static/app.css",
                                        "/static/app.js")):
            self._send(503, '{"hata":"Sistem hazırlanıyor."}')
            return
        if _kapatma_istegi.is_set() and parsed.path not in (
                "/", "/index.html", "/static/app.css", "/static/app.js"):
            self._send(503, '{"hata":"WebUI güvenli biçimde kapanıyor"}')
            return
        if parsed.path in ("/", "/index.html"):
            with open(os.path.join(BASE_DIR, "index.html"), "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif parsed.path.startswith("/static/"):
            ad = parsed.path[len("/static/"):]
            if ad not in ("app.css", "app.js"):
                self._send(404, '{"hata":"bulunamadi"}')
            else:
                try:
                    with open(os.path.join(BASE_DIR, "static", ad), "rb") as f:
                        veri = f.read()
                except OSError:
                    self._send(404, '{"hata":"bulunamadi"}')
                else:
                    ctype = ("text/css; charset=utf-8" if ad.endswith(".css")
                             else "application/javascript; charset=utf-8")
                    self._send(200, veri, ctype)
        elif parsed.path == "/api/pod-kurulum" and RUNPOD_MODE:
            if self._admin_gerekli() is not None:
                self._send(200, json.dumps(pod_setup.status(), ensure_ascii=False))
        elif parsed.path == "/api/durum":
            info = {"comfy": False, "modeller": {}, "llm": {},
                    "uretim_suruyor": False, "kuyruk_bekleyen": 0}
            try:
                comfy_get_json("/system_stats")
                info["comfy"] = True
            except Exception as e:
                info["hata"] = str(e)[:200]
            # model dosya kontrolu (ComfyUI models klasorune gore)
            models_dir = os.path.join(COMFY_KOK, "models")
            for model_key, files in (
                    ("modeller", (("diffusion", "diffusion_models", DIFFUSION_FILE),
                                   ("text_encoder", "text_encoders", TEXT_ENCODER_FILE),
                                   ("vae", "vae", VAE_FILE))),
                    ("z_image_modeller", (("diffusion", "diffusion_models", Z_IMAGE_DIFFUSION_FILE),
                                           ("text_encoder", "text_encoders", Z_IMAGE_TEXT_ENCODER_FILE),
                                           ("vae", "vae", Z_IMAGE_VAE_FILE))),
                    ("anima_modeller", (("diffusion", "diffusion_models", ANIMA_DIFFUSION_FILE),
                                        ("text_encoder", "text_encoders", ANIMA_TEXT_ENCODER_FILE),
                                        ("vae", "vae", ANIMA_VAE_FILE))),
                    ("moody_anima_modeller", (
                        ("diffusion", "diffusion_models", MOODY_ANIMA_DIFFUSION_FILE),
                        ("text_encoder", "text_encoders", ANIMA_TEXT_ENCODER_FILE),
                        ("vae", "vae", ANIMA_VAE_FILE)))):
                info[model_key] = {}
                for key, sub, fn in files:
                    info[model_key][key] = {
                        "dosya": fn,
                        "var": os.path.isfile(os.path.join(models_dir, sub, fn))}
            try:
                info["llm"] = llm.durum(detayli=True)
                info["llm_models"] = llm.model_profilleri()
            except Exception:
                info["llm"] = {"canli": False}
                info["llm_models"] = []
            if RUNPOD_MODE:
                try:
                    info["model_instances"] = pod_setup.model_options()
                except Exception:
                    info["model_instances"] = []
            try:
                info["uretim_suruyor"] = _aktif.get("prompt_id") is not None
                info["kuyruk_bekleyen"] = len(_kuyruk)
                info["vram_bos_mb"] = _vram_bos_mb()
            except Exception:
                pass
            self._send(200, json.dumps(info))
        elif parsed.path == "/api/kurulum-durumu":
            if RUNPOD_MODE and not pod_setup.is_ready():
                self._send(503, json.dumps({"hata": "Sistem hazırlanıyor."}))
            else:
                self._send(200, json.dumps({"kurulu": _auth_store.kurulu_mu(),
                                           "runpod": RUNPOD_MODE}))
        elif parsed.path == "/api/ben":
            kullanici = self._oturum_kullanici()
            if kullanici is None:
                self._send(401, '{"hata":"giris gerekli"}')
            else:
                self._send(200, json.dumps({"kullanici": kullanici,
                                            "rol": _auth_store.rol(kullanici)}))
        elif parsed.path == "/api/loras" and RUNPOD_MODE:
            if self._auth_gerekli() is None:
                return
            query = urllib.parse.parse_qs(parsed.query or "")
            model = (query.get("model") or [None])[0]
            self._send(200, json.dumps({"loras": pod_setup.lora_status(model)},
                                       ensure_ascii=False))
        elif parsed.path == "/api/kasa/durum":
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            self._send(200, json.dumps(_kasa_durum_bilgi(kullanici), ensure_ascii=False))
        elif parsed.path == "/api/kullanicilar":
            if self._admin_gerekli() is None:
                return
            liste = []
            for k in _auth_store.liste():
                try:
                    mb, kayit = _kullanim_hesapla(k["kullanici"])
                except Exception:
                    mb, kayit = 0, 0
                k["kullanim_mb"] = mb
                k["kayit_sayisi"] = kayit
                liste.append(k)
            self._send(200, json.dumps({"kullanicilar": liste}, ensure_ascii=False))
        elif parsed.path == "/api/ilerleme":
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            if self._kasa_gerekli(kullanici) is None:
                return
            pid = _aktif.get("prompt_id")
            if pid is not None and _aktif.get("sahip") != kullanici:
                self._send(200, json.dumps({"aktif": False, "ilerleme": None,
                                            "gecen": 0}))
                return
            ilerleme = _aktif.get("ilerleme")
            if isinstance(ilerleme, dict) and "oran" not in ilerleme:
                ilerleme = dict(ilerleme)
                ilerleme["oran"] = _oran_hesapla(ilerleme)
            self._send(200, json.dumps({
                "aktif": pid is not None,
                "ilerleme": ilerleme,
                "gecen": round(time.time() - _aktif["baslangic"], 1)
                         if _aktif.get("baslangic") else 0}))
        elif parsed.path == "/api/sistem":
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            if self._kasa_gerekli(kullanici) is None:
                return
            try:
                bos = _vram_bos_mb()
            except Exception:
                bos = None
            try:
                ram_bilgi = _ram_bilgi()
            except Exception:
                ram_bilgi = {"toplam_mb": None, "bos_mb": None,
                             "yuzde": None}
            try:
                comfy_bilgi = comfy_get_json("/system_stats")
                cihazlar = comfy_bilgi.get("devices") or []
                comfy_vram = ([{"ad": c.get("name"),
                                "bos_mb": round((c.get("vram_free") or 0)
                                               / (1024 * 1024)),
                                "toplam_mb": round((c.get("vram_total") or 0)
                                                  / (1024 * 1024))}
                               for c in cihazlar])
                comfy_acik = True
            except Exception:
                comfy_vram = []
                comfy_acik = False
            try:
                llm_bilgi = llm.durum(detayli=True)
            except Exception:
                llm_bilgi = {"canli": False}
            aktif_llm = None
            if _aktif_llm.get("sahip") == kullanici and _aktif_llm.get("is_id"):
                aktif_llm = {
                    "is_id": _aktif_llm["is_id"],
                    "tur": _aktif_llm.get("tur"),
                    "durum": _aktif_llm.get("durum"),
                    "gecen_sn": round(time.time() - _aktif_llm["baslangic"], 1),
                }
            self._send(200, json.dumps(
                {"vram_bos_mb": bos, "ram": ram_bilgi,
                  "comfy_acik": comfy_acik, "comfy_vram": comfy_vram,
                  "llm": llm_bilgi,
                  "llm_is": aktif_llm,
                  "kuyruk_bekleyen": len(_kuyruk),
                  "aktif_is_var": _aktif.get("prompt_id") is not None},
                ensure_ascii=False))
            return
        elif parsed.path == "/api/log":
            if self._admin_gerekli() is None:
                return
            qs = urllib.parse.parse_qs(parsed.query or "")
            hangi = (qs.get("kaynak") or ["llama"])[0]
            try:
                satir = max(10, min(500, int((qs.get("satir") or ["200"])[0])))
            except Exception:
                satir = 200
            try:
                satirlar = _log_tail(hangi, satir)
            except ValueError as exc:
                self._send(400, json.dumps({"hata": str(exc)}))
                return
            self._send(200, json.dumps({"kaynak": hangi,
                                        "satirlar": satirlar},
                                       ensure_ascii=False))
            return
        elif parsed.path == "/api/prompt-gecmisi":
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            if self._kasa_gerekli(kullanici) is None:
                return
            qs = urllib.parse.parse_qs(parsed.query or "")
            try:
                limit = max(1, min(200, int((qs.get("limit") or ["50"])[0])))
            except Exception:
                limit = 50
            arama = (qs.get("q") or [""])[0]
            mod = (qs.get("mod") or [""])[0]
            self._send(200, json.dumps(
                {"kayitlar": prompt_gecmisi_oku(kullanici, limit, arama, mod)},
                ensure_ascii=False))
            return
        elif parsed.path == "/api/kutuphane":
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            if self._kasa_gerekli(kullanici) is None:
                return
            self._send(200, json.dumps({"fotograflar": kutuphane_listesi(kullanici)},
                                       ensure_ascii=False))
        elif parsed.path.startswith("/kutuphane/"):
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            if self._kasa_gerekli(kullanici) is None:
                return
            kutuphane_dir = _kullanici_kutuphane(kullanici)
            ad = urllib.parse.unquote(parsed.path[len("/kutuphane/"):])
            qs = urllib.parse.parse_qs(parsed.query or "")
            thumb = (qs.get("thumb") or [""])[0] in ("1", "evet", "true")
            ad = _guvenli_dosya_adi(ad)
            if not ad:
                self._send(400, '{"hata":"gecersiz ad"}')
                return
            yol = os.path.abspath(os.path.join(kutuphane_dir, ad))
            if thumb:
                taban = ad[:-4] if ad.lower().endswith(".png") else ad
                t_yol = os.path.abspath(os.path.join(kutuphane_dir, taban + "_thumb.jpg"))
                if os.path.isfile(t_yol):
                    yol = t_yol
            if not yol.startswith(os.path.abspath(kutuphane_dir) + os.sep) \
                    or not os.path.isfile(yol):
                self._send(404, '{"hata":"bulunamadi"}')
                return
            ctype = mimetypes.guess_type(yol)[0] or "application/octet-stream"
            with open(yol, "rb") as f:
                self._send(200, f.read(), ctype)
        elif parsed.path == "/api/arsiv":
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            if self._kasa_gerekli(kullanici) is None:
                return
            qs = urllib.parse.parse_qs(parsed.query or "")
            try:
                sayfa = int((qs.get("sayfa") or ["1"])[0])
            except Exception:
                sayfa = 1
            try:
                adet = int((qs.get("adet") or ["24"])[0])
            except Exception:
                adet = 24
            arama = (qs.get("q") or [""])[0]
            try:
                kok = _kullanici_koku(kullanici)
                self._send(200, json.dumps(
                    arsiv.arsiv_listesi(kok, sayfa, adet, arama),
                    ensure_ascii=False))
            except ValueError:
                self._send(409, '{"hata":"kasa kilitli"}')
        elif parsed.path.startswith("/api/arsiv/gorsel/") \
                or parsed.path.startswith("/api/arsiv/thumb/"):
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            if self._kasa_gerekli(kullanici) is None:
                return
            thumb = parsed.path.startswith("/api/arsiv/thumb/")
            on = len("/api/arsiv/thumb/") if thumb else len("/api/arsiv/gorsel/")
            ozet = urllib.parse.unquote(parsed.path[on:])
            if not re.fullmatch(r"[a-f0-9]{64}", ozet):
                self._send(400, '{"hata":"gecersiz hash"}')
                return
            try:
                kok = _kullanici_koku(kullanici)
            except ValueError:
                self._send(409, '{"hata":"kasa kilitli"}')
                return
            if thumb:
                yol = arsiv.arsiv_thumb(kok, ozet)
                if not yol or not os.path.isfile(yol):
                    self._send(404, '{"hata":"bulunamadi"}')
                    return
                with open(yol, "rb") as f:
                    self._send(200, f.read(), "image/jpeg")
                return
            veri = arsiv.arsiv_verisi(kok, ozet)
            if not veri:
                self._send(404, '{"hata":"bulunamadi"}')
                return
            self._send(200, veri[1], veri[0])
        elif parsed.path == "/api/promptlar":
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            if self._kasa_gerekli(kullanici) is None:
                return
            self._send(200, json.dumps({"promptlar": promptlari_oku(kullanici)},
                                       ensure_ascii=False))
        elif parsed.path == "/api/gecmis":
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            if self._kasa_gerekli(kullanici) is None:
                return
            qs = urllib.parse.parse_qs(parsed.query or "")
            try:
                sayfa = int((qs.get("sayfa") or ["1"])[0])
            except Exception:
                sayfa = 1
            try:
                adet = int((qs.get("adet") or ["20"])[0])
            except Exception:
                adet = 20
            arama = (qs.get("q") or [""])[0]
            favoriler = (qs.get("favoriler") or [""])[0] in ("1", "true", "evet")
            albume_gore = (qs.get("albume_gore") or [""])[0] in (
                "1", "true", "evet")
            album_id = (qs.get("album_id") or [None])[0]
            self._send(200, json.dumps(
                gecmis_listesi(kullanici, sayfa, adet, arama, favoriler,
                               albume_gore, album_id),
                ensure_ascii=False))
        elif parsed.path.startswith("/gecmis/"):
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            if self._kasa_gerekli(kullanici) is None:
                return
            gecmis_dir = _kullanici_gecmis(kullanici)
            ad = _guvenli_dosya_adi(urllib.parse.unquote(parsed.path[len("/gecmis/"):]))
            if not ad:
                self._send(400, '{"hata":"gecersiz ad"}')
                return
            yol = os.path.abspath(os.path.join(gecmis_dir, ad))
            if not yol.startswith(os.path.abspath(gecmis_dir) + os.sep) \
                    or not os.path.isfile(yol):
                self._send(404, '{"hata":"bulunamadi"}')
                return
            ctype = mimetypes.guess_type(yol)[0] or "application/octet-stream"
            with open(yol, "rb") as f:
                self._send(200, f.read(), ctype)
        elif parsed.path == "/api/ayarlar":
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            if self._kasa_gerekli(kullanici) is None:
                return
            ayar = _ayar_oku(kullanici)
            ayar.setdefault("gecmis_limiti", 0)
            ayar.setdefault("llm_ctx", 8000)
            ayar.setdefault("llm_prompt_destek_yaraticilik", 0.7)
            ayar.setdefault("llm_prompt_destek_uzunluk", "orta")
            ayar.setdefault("uretim_varsayilan_steps", 9)
            ayar.setdefault("uretim_varsayilan_seed", -1)
            ayar.setdefault("uretim_varsayilan_max_side", 1536)
            ayar.setdefault("uretim_prompt_destek", False)
            ayar.setdefault("uretim_prompt_ceviri", False)
            llm_varsayilanlar = {
                "sistem": llm.VARSAYILAN_SISTEM_PROMPTU,
                "sohbet": llm.VARSAYILAN_SOHBET_SISTEM_PROMPTU,
                "mod": dict(llm.VARSAYILAN_MOD_TALIMATI),
            }
            self._send(200, json.dumps(
                {"ayarlar": ayar, "llm_varsayilanlar": llm_varsayilanlar},
                ensure_ascii=False))
        elif parsed.path == "/api/isler":
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            if self._kasa_gerekli(kullanici) is None:
                return
            with _isler_kilit:
                isler = sorted((j for j in _isler.values()
                                if j.get("sahip") == kullanici),
                               key=lambda j: j.get("olustu", 0))
            self._send(200, json.dumps({"isler": [_is_ozet(j) for j in isler]},
                                       ensure_ascii=False))
        elif parsed.path.startswith("/api/is/"):
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            if self._kasa_gerekli(kullanici) is None:
                return
            is_id = urllib.parse.unquote(parsed.path[len("/api/is/"):])
            job = _isler.get(is_id)
            if job is None or job.get("sahip") != kullanici:
                self._send(404, '{"hata":"bulunamadi"}')
                return
            self._send(200, json.dumps(_is_detay(job), ensure_ascii=False))
        else:
            self._send(404, '{"hata":"bulunamadi"}')

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if RUNPOD_MODE and getattr(self.server, "server_port", 8189) == SETUP_PORT:
            self._setup_post(parsed)
            return
        if parsed.path == "/api/sunucu-kapat":
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

    def _do_POST(self, parsed):
        # Tum POST'lar CSRF perdesinden gecer (Origin/Referer varsa Host ile ayni olmali).
        if not self._origin_kontrol():
            return
        if parsed.path == "/api/pod-kurulum" and RUNPOD_MODE:
            if self._admin_gerekli() is None:
                return
            req = self._json_oku()
            try:
                if req.get("islem") == "iptal":
                    pod_setup.cancel(req.get("grup"))
                elif req.get("islem") == "duraklat":
                    pod_setup.pause(req.get("grup"))
                elif req.get("islem") == "devam":
                    pod_setup.start(req.get("grup"))
                elif req.get("islem") == "kaldir":
                    with _isler_kilit:
                        if _kuyruk_calismasi or _ceviri_calismasi or _kuyruk or _ceviri_kuyrugu:
                            raise RuntimeError("Modeli kaldırmak için işlerin bitmesini bekleyin.")
                        pod_setup.remove(req.get("grup"))
                elif req.get("islem") == "sira":
                    pod_setup.move(req.get("grup"), req.get("yon"))
                else:
                    pod_setup.start(req.get("grup"))
            except ValueError as hata:
                self._send(400, json.dumps({"hata": str(hata)}))
            except (OSError, RuntimeError) as hata:
                self._send(409, json.dumps({"hata": str(hata)}))
            else:
                self._send(202, json.dumps(pod_setup.status(), ensure_ascii=False))
            return
        # Herkese acik auth girisleri (kurulum/giris). Diger POST'lar asagida guard'li.
        if parsed.path == "/api/kurulum":
            if _auth_store.kurulu_mu():
                self._send(403, '{"hata":"sistem zaten kurulu"}')
                return
            req = self._json_oku()
            if "__hata__" in req:
                self._send(400, json.dumps({"hata": req["__hata__"]}))
                return
            try:
                bilgi = _auth_store.ilk_admin((req.get("kullanici") or "").strip(),
                                              req.get("sifre") or "")
            except auth.AuthHatasi as e:
                kod = 400 if e.kod in ("gecersiz-ad", "zayif-sifre") else 403
                self._send(kod, json.dumps({"hata": str(e)}))
                return
            tasinan = _ilk_migrasyon(bilgi["kullanici"])
            toplam, silinen = _zararli_prompt_temizle(bilgi["kullanici"])
            self._send(200, json.dumps({"kullanici": bilgi["kullanici"],
                                        "rol": bilgi["rol"],
                                        "tasinan": tasinan,
                                        "prompt_toplam": toplam,
                                        "prompt_temizlenen": silinen},
                                       ensure_ascii=False))
            return
        if parsed.path == "/api/giris":
            req = self._json_oku()
            if "__hata__" in req:
                self._send(400, json.dumps({"hata": req["__hata__"]}))
                return
            try:
                token, rol = _auth_store.giris(
                    (req.get("kullanici") or "").strip(), req.get("sifre") or "",
                    self.client_address[0])
            except auth.AuthHatasi:
                self._send(401, json.dumps({"hata": "kullanici adi veya sifre hatali"}))
                return
            self._send(200, json.dumps({"kullanici": (req.get("kullanici") or "").strip(),
                                        "rol": rol}),
                       ek_baslik=self._oturum_cerezi(token))
            return
        if parsed.path == "/api/cikis":
            kullanici = self._oturum_kullanici()
            if kullanici is not None:
                # Cikista once isler durur, kasa arka planda muhurlenir.
                try:
                    _kasa_kapat_arka(kullanici)
                except Exception as e:
                    self._send(500, json.dumps({"hata": "kasa kapanışı başlatılamadı: %s" % e},
                                               ensure_ascii=False))
                    return
            _auth_store.cikis(self._cerez_token())
            self._send(200, '{"cikis":true}',
                       ek_baslik=self._cerez_sil() + [
                           ("Clear-Site-Data", '"cache", "cookies", "storage"')])
            return
        if parsed.path == "/api/sifre":
            if not self._origin_kontrol():
                return
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            req = self._json_oku()
            if "__hata__" in req:
                self._send(400, json.dumps({"hata": req["__hata__"]}))
                return
            try:
                _auth_store.sifre_degistir(kullanici, req.get("eski"), req.get("yeni"))
            except auth.AuthHatasi as e:
                kod = 400 if e.kod in ("zayif-sifre", "hatali") else 401
                self._send(kod, json.dumps({"hata": str(e)}))
                return
            self._send(200, '{"degisti":true}')
            return
        if parsed.path == "/api/kullanici/ekle":
            if not self._origin_kontrol():
                return
            if self._admin_gerekli() is None:
                return
            req = self._json_oku()
            if "__hata__" in req:
                self._send(400, json.dumps({"hata": req["__hata__"]}))
                return
            try:
                bilgi = _auth_store.kullanici_ekle(
                    (req.get("kullanici") or "").strip(), req.get("sifre") or "",
                    req.get("rol", "uye"), req.get("kota_mb", 0),
                    req.get("kota_kayit", 0))
            except auth.AuthHatasi as e:
                self._send(400, json.dumps({"hata": str(e)}))
                return
            _kullanici_klasorleri(bilgi["kullanici"])
            self._send(200, json.dumps(bilgi, ensure_ascii=False))
            return
        if parsed.path == "/api/kullanici/sil":
            if not self._origin_kontrol():
                return
            silen = self._admin_gerekli()
            if silen is None:
                return
            req = self._json_oku()
            if "__hata__" in req:
                self._send(400, json.dumps({"hata": req["__hata__"]}))
                return
            hedef = (req.get("kullanici") or "").strip()
            try:
                _auth_store.kullanici_sil(hedef, silen)
            except auth.AuthHatasi as e:
                kod = 404 if e.kod == "yok" else 400
                self._send(kod, json.dumps({"hata": str(e)}))
                return
            try:  # kasa + calisma silinir
                kasa.profil_sil(hedef)
            except Exception:
                pass
            try:  # acilmamis ham veri kaldiysa o da silinir
                shutil.rmtree(_ham_kullanici_koku(hedef), ignore_errors=True)
            except Exception:
                pass
            if RUNPOD_MODE:
                pod_setup.checkpoint()
            self._send(200, '{"silindi":true}')
            return
        if parsed.path == "/api/kullanici/kota":
            if not self._origin_kontrol():
                return
            if self._admin_gerekli() is None:
                return
            req = self._json_oku()
            if "__hata__" in req:
                self._send(400, json.dumps({"hata": req["__hata__"]}))
                return
            try:
                _auth_store.kota_koy((req.get("kullanici") or "").strip(),
                                     req.get("kota_mb", 0), req.get("kota_kayit", 0))
            except auth.AuthHatasi as e:
                kod = 404 if e.kod == "yok" else 400
                self._send(kod, json.dumps({"hata": str(e)}))
                return
            self._send(200, '{"kota":true}')
            return
        # --- Asagidaki tum veri/is POST'lari oturum ister (Origin yukarida bakildi) ---
        if parsed.path == "/api/sistem/bosalti":
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            if self._kasa_gerekli(kullanici) is None:
                return
            global _bellek_bosaltma_aktif
            with _isler_kilit:
                if (_bellek_bosaltma_aktif or _kuyruk_calismasi or _kuyruk
                        or _aktif.get("prompt_id") is not None
                        or _aktif_llm.get("is_id") is not None):
                    self._send(409, json.dumps(
                        {"hata": "Bellek, WebUI işi veya kuyruğu çalışırken boşaltılamaz."},
                        ensure_ascii=False))
                    return
                _bellek_bosaltma_aktif = True
            try:
                # Once llama-server kapansin (VRAM'i o da tutuyor olabilir).
                llm_kapandi = False
                try:
                    if llm.durum().get("canli"):
                        llm_kapandi = _llm_kapat_ve_bekle()
                    else:
                        llm_kapandi = True
                except Exception:
                    llm_kapandi = True
                ok, neden = _comfy_bosalti_ve_dogrula(zorla=True)
                if not ok:
                    self._send(503, json.dumps(
                        {"hata": neden, "vram_bos_mb": _vram_bos_mb(),
                         "gerekli_vram_mb": LLM_VRAM_GEREK_MB,
                         "llm_kapandi": llm_kapandi},
                        ensure_ascii=False))
                    return
                self._send(200, json.dumps(
                    {"bosaltildi": True, "vram_bos_mb": _vram_bos_mb(),
                     "gerekli_vram_mb": LLM_VRAM_GEREK_MB,
                     "llm_kapandi": llm_kapandi},
                    ensure_ascii=False))
            finally:
                with _isler_kilit:
                    _bellek_bosaltma_aktif = False
            return
        if parsed.path == "/api/durdur":
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            if self._kasa_gerekli(kullanici) is None:
                return
            req = self._json_oku()
            with _isler_kilit:
                job = _isler.get(req.get("is_id")) if req.get("is_id") else None
                if job is not None and job.get("sahip") != kullanici:
                    job = None
                if job is None:
                    active_ids = {_aktif_llm.get("is_id")}
                    active_prompt = _aktif.get("prompt_id")
                    if active_prompt is not None:
                        active_ids.update(j["is_id"] for j in _isler.values()
                                          if j.get("prompt_id") == active_prompt)
                    job = next((j for j in reversed(list(_isler.values()))
                                if j.get("sahip") == kullanici
                                and j.get("durum") == "calisiyor"
                                and j.get("is_id") in active_ids), None)
            durduruldu = job is not None and _isi_iptal_et(job)
            self._send(200, json.dumps({"durduruldu": durduruldu,
                                        "kuyruk": job is not None}))
            return
        if parsed.path == "/api/arsiv/sil":
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            if self._kasa_gerekli(kullanici) is None:
                return
            req = self._json_oku()
            if "__hata__" in req:
                self._send(400, json.dumps({"hata": req["__hata__"]}))
                return
            ozet = str(req.get("hash") or "")
            if not re.fullmatch(r"[a-f0-9]{64}", ozet):
                self._send(400, '{"hata":"gecersiz hash"}')
                return
            try:
                kok = _kullanici_koku(kullanici)
            except ValueError:
                self._send(409, '{"hata":"kasa kilitli"}')
                return
            silindi = arsiv.arsiv_sil(kok, ozet)
            self._send(200, json.dumps({"silindi": silindi}))
            return
        if parsed.path == "/api/arsiv/sil-coklu":
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            if self._kasa_gerekli(kullanici) is None:
                return
            req = self._json_oku()
            if "__hata__" in req:
                self._send(400, json.dumps({"hata": req["__hata__"]}))
                return
            hashes = req.get("hashes")
            if not isinstance(hashes, list) or len(hashes) > 100:
                self._send(400, '{"hata":"En fazla 100 fotoğraf seçilebilir."}')
                return
            try:
                kok = _kullanici_koku(kullanici)
                silinen = arsiv.arsiv_sil_coklu(kok, hashes)
            except ValueError as exc:
                self._send(400, json.dumps({"hata": str(exc)}, ensure_ascii=False))
                return
            except (OSError, RuntimeError):
                self._send(409, '{"hata":"Arşiv kopyaları silinemedi."}')
                return
            self._send(200, json.dumps({"silinen": len(silinen),
                                        "silinen_hashler": silinen}))
            return
        if parsed.path == "/api/arsiv/ice-aktar":
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            if self._kasa_gerekli(kullanici) is None:
                return
            if self._kasa_kapaniyor_mu(kullanici):
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
            except Exception:
                length = 0
            if length > 200 * 1024 * 1024:
                self._send(413, '{"hata":"istek gövdesi çok büyük (200 MB üstü)"}')
                return
            req = self._json_oku()
            if "__hata__" in req:
                self._send(400, json.dumps({"hata": req["__hata__"]}))
                return
            gorseller = req.get("gorseller")
            if not isinstance(gorseller, list) or not gorseller:
                self._send(400, '{"hata":"gorseller listesi gerekli"}')
                return
            if len(gorseller) > 200:
                self._send(400, '{"hata":"tek seferde en fazla 200 görsel"}')
                return
            try:
                kok = _kullanici_koku(kullanici)
            except ValueError:
                self._send(409, '{"hata":"kasa kilitli"}')
                return
            sonuc = arsiv.arsiv_ice_aktar(kok, gorseller)
            self._send(200, json.dumps(sonuc, ensure_ascii=False))
            return
        if parsed.path == "/api/kutuphane/sil":
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            if self._kasa_gerekli(kullanici) is None:
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                req = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            except Exception as e:
                self._send(400, json.dumps({"hata": "istek okunamadi: %s" % e}))
                return
            ok = kutuphaneden_sil(kullanici, req.get("ad"))
            self._send(200 if ok else 404, json.dumps({"silindi": ok}))
            return
        if parsed.path == "/api/promptlar":
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            if self._kasa_gerekli(kullanici) is None:
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                req = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            except Exception as e:
                self._send(400, json.dumps({"hata": "istek okunamadi: %s" % e}))
                return
            ad = (req.get("ad") or "").strip()
            if not ad:
                self._send(400, json.dumps({"hata": "ad gerekli"}))
                return
            kayit = {"ad": ad, "prompt": req.get("prompt", ""),
                     "negative": req.get("negative", ""),
                     "model": req.get("model") if req.get("model") in
                     (MODEL_FLUX2, MODEL_Z_IMAGE, MODEL_ANIMA,
                      MODEL_MOODY_ANIMA) else MODEL_FLUX2,
                     "workflow": req.get("workflow", "tek"),
                     "steps": req.get("steps", 4), "cfg": req.get("cfg", 1.0),
                     "sampler": req.get("sampler", "euler"),
                     "width": req.get("width", 1024), "height": req.get("height", 1024),
                     "max_side": req.get("max_side", 1536)}
            if _istem_zararli_mi(kayit):
                self._send(400, json.dumps({"hata": "bu icerik kaydedilemez"}))
                return
            self._send(200, json.dumps({"kaydedildi": True,
                                        "promptlar": prompt_kaydet(kullanici, kayit)},
                                       ensure_ascii=False))
            return
        if parsed.path == "/api/promptlar/sil":
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            if self._kasa_gerekli(kullanici) is None:
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                req = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            except Exception as e:
                self._send(400, json.dumps({"hata": "istek okunamadi: %s" % e}))
                return
            ok = prompt_sil(kullanici, (req.get("ad") or "").strip())
            self._send(200 if ok else 404, json.dumps({"silindi": ok}))
            return
        if parsed.path == "/api/ayarlar":
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            if self._kasa_gerekli(kullanici) is None:
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                req = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            except Exception as e:
                self._send(400, json.dumps({"hata": "istek okunamadi: %s" % e}))
                return
            if not isinstance(req, dict):
                self._send(400, '{"hata":"ayarlar sözlük olmalı"}')
                return
            try:
                with _AYARLAR_KILIT:
                    ayar = _ayar_oku(kullanici)
                    _ayar_degisikliklerini_uygula(ayar, req)
                    _ayar_yaz(kullanici, ayar)
            except ValueError as e:
                self._send(400, json.dumps({"hata": str(e)}, ensure_ascii=False))
                return
            except OSError:
                self._send(500, '{"hata":"ayarlar kaydedilemedi"}')
                return
            if "gecmis_limiti" in req:
                _gecmis_buda(kullanici)
            llm_ctx_uygulama = None
            if "llm_ctx" in req:
                # Bağlamı yalnız LLM işçisi değiştirir; yeni iş ayarları diskten okur.
                llm_ctx_uygulama = {"istenen": ayar["llm_ctx"], "uygulandi": False,
                                    "model_yeniden_baslatilacak": False,
                                    "calisan_ctx": None,
                                    "not": "Bir sonraki LLM işinde uygulanır"}
            self._send(200, json.dumps(
                {"ayarlar": ayar, "llm_ctx_uygulama": llm_ctx_uygulama},
                ensure_ascii=False))
            return
        if parsed.path == "/api/kutuphane/temizle":
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            if self._kasa_gerekli(kullanici) is None:
                return
            self._send(200, json.dumps({"temizlendi": kutuphane_temizle(kullanici)}))
            return
        if parsed.path == "/api/gecmis/sil":
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            if self._kasa_gerekli(kullanici) is None:
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                req = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            except Exception as e:
                self._send(400, json.dumps({"hata": "istek okunamadi: %s" % e}))
                return
            ok = gecmis_kaydi_sil(kullanici, req.get("kayit"))
            self._send(200 if ok else 404, json.dumps({"silindi": ok}))
            return
        if parsed.path == "/api/gecmis/yildiz":
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            if self._kasa_gerekli(kullanici) is None:
                return
            req = self._json_oku()
            if "__hata__" in req:
                self._send(400, json.dumps({"hata": req["__hata__"]}))
                return
            if not isinstance(req.get("yildiz"), bool):
                self._send(400, '{"hata":"yildiz boolean olmali"}')
                return
            ok = gecmis_kaydi_yildiz(kullanici, req.get("kayit"),
                                     req.get("yildiz"), req.get("dosya"))
            self._send(200 if ok else 404, json.dumps({"yildiz": bool(ok)}))
            return
        if parsed.path == "/api/gecmis/temizle":
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            if self._kasa_gerekli(kullanici) is None:
                return
            self._send(200, json.dumps({"temizlendi": gecmis_temizle(kullanici)}))
            return
        if parsed.path == "/api/sohbet/kaydet":
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            if self._kasa_gerekli(kullanici) is None:
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                req = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            except Exception as e:
                self._send(400, json.dumps({"hata": "istek okunamadi: %s" % e}))
                return
            mesajlar = req.get("mesajlar")
            if not isinstance(mesajlar, list) or not mesajlar:
                self._send(400, json.dumps({"hata": "mesajlar listesi gerekli"}))
                return
            try:
                yanit = str(req.get("yanit") or "")
                olcum = req.get("sonuc") or {}
                if not isinstance(olcum, dict):
                    olcum = {}
                save_req = {"mesajlar": mesajlar, "sohbet_id": req.get("id"),
                            "baslik": req.get("baslik")}
                _sohbet_kaydet(kullanici, mesajlar, yanit, olcum, req.get("baslik"), req.get("id"))
                self._send(200, json.dumps({"kaydedildi": True}))
            except Exception as e:
                self._send(500, json.dumps({"hata": str(e)[:1000]}))
            return
        if parsed.path == "/api/sohbetler/temizle":
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            if self._kasa_gerekli(kullanici) is None:
                return
            ok = _sohbet_temizle(kullanici)
            self._send(200 if ok else 404, json.dumps({"temizlendi": ok}))
            return
        if parsed.path == "/api/sohbet/sil":
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            if self._kasa_gerekli(kullanici) is None:
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                req = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            except Exception as e:
                self._send(400, json.dumps({"hata": "istek okunamadi: %s" % e}))
                return
            ok = _sohbet_sil(kullanici, req.get("id"))
            self._send(200 if ok else 404, json.dumps({"silindi": ok}))
            return
        if parsed.path == "/api/sohbetler":
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            if self._kasa_gerekli(kullanici) is None:
                return
            qs = urllib.parse.parse_qs(parsed.query or "")
            is_id = (qs.get("id") or [None])[0]
            liste = _sohbet_oku(kullanici)
            if is_id:
                kayit = next((k for k in liste if k.get("id") == is_id), None)
                if kayit is None:
                    self._send(404, '{"hata":"bulunamadi"}')
                else:
                    self._send(200, json.dumps(kayit, ensure_ascii=False))
            else:
                self._send(200, json.dumps({"sohbetler": liste}, ensure_ascii=False))
            return
        if parsed.path == "/api/kuyruk":
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            if self._kasa_gerekli(kullanici) is None:
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                req = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            except Exception as e:
                self._send(400, json.dumps({"hata": "istek okunamadi: %s" % e}))
                return
            if "prompt_destek" in req and not isinstance(req.get("prompt_destek"), bool):
                self._send(400, '{"hata":"prompt_destek boolean olmali"}')
                return
            hata = _is_dogrula(req)
            if hata:
                self._send(400, json.dumps(hata))
                return
            kota_hata = _kota_kontrol(kullanici, yeni_bayt=0, yeni_kayit=1)
            if kota_hata:
                self._send(400, json.dumps({"hata": kota_hata}))
                return
            if self._kasa_kapaniyor_mu(kullanici):
                return
            job, sira = _kuyruga_ekle(req, kullanici)
            if job is None:
                self._send(409, '{"hata":"bellek bosaltilirken yeni is kuyruga eklenemez"}')
                return
            self._send(200, json.dumps({"is_id": job["is_id"], "sira": sira,
                                        "durum": "kuyrukta"}))
            return
        if parsed.path == "/api/kuyruk/temizle":
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            if self._kasa_gerekli(kullanici) is None:
                return
            with _isler_kilit:
                bekleyen = [i for i in _kuyruk if _isler[i]["sahip"] == kullanici]
                for is_id in bekleyen:
                    _bekleyen_isi_iptal_et(_isler[is_id])
            self._send(200, json.dumps({"temizlendi": len(bekleyen)}))
            return
        if parsed.path == "/api/is/iptal":
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            if self._kasa_gerekli(kullanici) is None:
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                req = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            except Exception as e:
                self._send(400, json.dumps({"hata": "istek okunamadi: %s" % e}))
                return
            job = _isler.get(req.get("is_id"))
            if job is None or job.get("sahip") != kullanici:
                self._send(404, '{"hata":"bulunamadi"}')
                return
            iptal = _isi_iptal_et(job)
            self._send(200, json.dumps({"iptal": iptal, "is_id": job["is_id"],
                                        "durum": job["durum"]}))
            return
        if parsed.path == "/api/kasa/ac":
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            req = self._json_oku()
            parola = req.get("parola") or ""
            try:
                req["parola"] = ""
            except Exception:
                pass
            try:
                kasa.ac(kullanici, parola)
                hata = None
            except kasa.KasaHatasi as e:
                hata = str(e)
            except Exception:
                hata = "kasa acilamadi"
            if hata is not None:
                self._send(403, json.dumps({"hata": hata}, ensure_ascii=False))
                return
            self._send(200, json.dumps(_kasa_durum_bilgi(kullanici), ensure_ascii=False))
            return
        if parsed.path == "/api/kasa/kapat":
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            try:
                _kasa_kapat_arka(kullanici)
            except Exception as e:
                self._send(500, json.dumps({"hata": "kasa kapanışı başlatılamadı: %s" % e},
                                           ensure_ascii=False))
                return
            self._send(200, json.dumps(_kasa_durum_bilgi(kullanici), ensure_ascii=False))
            return
        if parsed.path == "/api/kasa/nabiz":
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            try:
                kasa.dokun(kullanici)
            except Exception:
                pass
            self._send(200, json.dumps(_kasa_durum_bilgi(kullanici), ensure_ascii=False))
            return
        if parsed.path == "/api/kasa/olustur":
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            req = self._json_oku()
            if "__hata__" in req:
                self._send(400, json.dumps({"hata": req["__hata__"]}))
                return
            parola = req.get("parola") or ""
            parola2 = req.get("parola2") or ""
            anladim = bool(req.get("riski_anladim"))
            try:
                req["parola"] = ""
                req["parola2"] = ""
            except Exception:
                pass
            try:
                uyari = kasa.guc_uyarilari(parola)
            except Exception:
                uyari = []
            if uyari and not anladim:
                self._send(400, json.dumps(
                    {"hata": "parola zayif: " + "; ".join(uyari),
                     "uyarilar": uyari}, ensure_ascii=False))
                return
            try:
                ham = _ham_kullanici_koku(kullanici)
                kaynak = ham if os.path.isdir(ham) and os.listdir(ham) else None
                kasa.olustur(kullanici, parola, parola2, kaynak)
                kasa.ac(kullanici, parola)
                hata = None
            except kasa.KasaHatasi as e:
                hata = str(e)
            except Exception:
                hata = "kasa olusturulamadi"
            if hata is not None:
                self._send(403, json.dumps({"hata": hata}, ensure_ascii=False))
                return
            self._send(200, json.dumps(_kasa_durum_bilgi(kullanici), ensure_ascii=False))
            return
        if parsed.path == "/api/kasa/kurtar":
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            req = self._json_oku()
            parola = req.get("parola") or ""
            try:
                req["parola"] = ""
            except Exception:
                pass
            try:
                kasa.kurtar(kullanici, parola)
                kasa.ac(kullanici, parola)
                hata = None
            except kasa.KasaHatasi as e:
                hata = str(e)
            except Exception:
                hata = "kurtarma basarisiz"
            if hata is not None:
                self._send(403, json.dumps({"hata": hata}, ensure_ascii=False))
                return
            self._send(200, json.dumps(_kasa_durum_bilgi(kullanici), ensure_ascii=False))
            return
        if parsed.path == "/api/kasa/sure":
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            if self._kasa_gerekli(kullanici) is None:
                return
            req = self._json_oku()
            try:
                dakika = max(1, min(24 * 60, int(req.get("dakika", 15))))
            except Exception:
                self._send(400, json.dumps({"hata": "dakika sayi olmali"}))
                return
            kasa.bekleme_koy(kullanici, dakika)
            self._send(200, json.dumps({"dakika": dakika}))
            return
        if parsed.path == "/api/kasa/parola":
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            if self._kasa_gerekli(kullanici) is None:
                return
            req = self._json_oku()
            if "__hata__" in req:
                self._send(400, json.dumps({"hata": req["__hata__"]}, ensure_ascii=False))
                return
            mevcut = req.get("mevcut") or ""
            yeni = req.get("yeni") or ""
            tekrar = req.get("tekrar") or ""
            req["mevcut"] = req["yeni"] = req["tekrar"] = ""
            kapanis = False
            try:
                if not kasa.kapanis_baslat(kullanici):
                    self._send(409, '{"hata":"kasa şu anda başka bir işlemde"}')
                    return
                kapanis = True
                # Bu isteğin kendisini bekleme listesinden çıkar; başka
                # kaydetme istekleri bitmeden şifreli arşivi değiştirme.
                with _http_yazma_kosulu:
                    istekler = _kasa_yazma_istekleri.get(kullanici)
                    if istekler is not None:
                        istekler.discard(self)
                        if not istekler:
                            _kasa_yazma_istekleri.pop(kullanici, None)
                    _http_yazma_kosulu.notify_all()
                    yazmalar_bitti = _http_yazma_kosulu.wait_for(
                        lambda: not _kasa_yazma_istekleri.get(kullanici), timeout=30)
                if not yazmalar_bitti:
                    self._send(409, '{"hata":"başka bir kayıt işlemi sürüyor; tamamlanınca tekrar dene"}')
                    return
                with _isler_kilit:
                    calisan_is_var = any(
                        islem.get("sahip") == kullanici and not islem["olay"].is_set()
                        for islem in _isler.values())
                if calisan_is_var:
                    self._send(409, '{"hata":"önce devam eden üretim veya sohbet işlerinin bitmesini bekle"}')
                    return
                try:
                    kasa.parola_degistir(kullanici, mevcut, yeni, tekrar)
                except kasa.KasaHatasi as exc:
                    mesaj = str(exc)
                    durum = 400 if mesaj.startswith("yeni parola") else 403
                    self._send(durum, json.dumps({"hata": mesaj}, ensure_ascii=False))
                    return
                except Exception:
                    self._send(500, '{"hata":"parola değiştirilemedi; mevcut parola korunuyor"}')
                    return
                self._send(200, '{"degisti":true}')
            finally:
                if kapanis:
                    kasa.kapanis_iptal(kullanici)
            return
        if parsed.path == "/api/sunucu-kapat":
            # Yalniz bu makineden (baslatici) istenir. Giris istemez:
            # makinede zaten yetkili olan biri icin kapatma ek risk degildir.
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                self._send(403, '{"hata":"yalniz bu makineden"}')
                return
            req = self._json_oku()
            muhurlensin = req.get("mod") != "yeniden_baslat"
            with _kapatma_istek_kilit:
                if _kapatma_istegi.is_set():
                    self._send(409, '{"hata":"WebUI zaten kapanıyor"}')
                    return
                _kapatma_istegi.set()
            try:
                self._send(200, '{"kapaniyor":true}')
            finally:
                threading.Thread(target=_sunucu_kapat_arka,
                                 args=(muhurlensin,), daemon=True).start()
            return
        if parsed.path == "/api/prompt-gecmisi/sil":
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            if self._kasa_gerekli(kullanici) is None:
                return
            req = self._json_oku()
            if "__hata__" in req:
                self._send(400, json.dumps({"hata": req["__hata__"]}))
                return
            n = prompt_gecmisi_sil(kullanici, str(req.get("id") or ""))
            self._send(200, json.dumps({"silinen": n}))
            return
        if parsed.path == "/api/prompt-gecmisi/yildiz":
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            if self._kasa_gerekli(kullanici) is None:
                return
            req = self._json_oku()
            if "__hata__" in req:
                self._send(400, json.dumps({"hata": req["__hata__"]}))
                return
            ok = prompt_gecmisi_yildiz(kullanici, str(req.get("id") or ""),
                                       bool(req.get("yildiz")))
            self._send(200 if ok else 404, json.dumps({"yildiz": ok}))
            return
        if parsed.path == "/api/prompt-gecmisi/temizle":
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            if self._kasa_gerekli(kullanici) is None:
                return
            self._send(200, json.dumps(
                {"temizlendi": prompt_gecmisi_temizle(kullanici)}))
            return
        if parsed.path == "/api/llm/hazirla":
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            if self._kasa_gerekli(kullanici) is None:
                return
            if self._kasa_kapaniyor_mu(kullanici):
                return
            req = self._json_oku()
            if "__hata__" in req:
                self._send(400, json.dumps({"hata": req["__hata__"]}))
                return
            model = req.get("llm_model")
            if model is not None and (not isinstance(model, str) or model not in {
                    item["id"] for item in llm.model_profilleri()}):
                self._send(400, '{"hata":"LLM model seçimi geçersiz"}')
                return
            job, sira = _kuyruga_ekle(
                ({"llm_model": model} if model else {}), kullanici,
                tur="llm_preload")
            if job is None:
                self._send(409, '{"hata":"bellek bosaltilirken yeni is kuyruga eklenemez"}')
                return
            self._send(200, json.dumps({"queued": True, "is_id": job["is_id"],
                                        "sira": sira}))
            return
        if parsed.path == "/api/prompt-uret":
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            if self._kasa_gerekli(kullanici) is None:
                return
            if self._kasa_kapaniyor_mu(kullanici):
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
            except Exception:
                length = 0
            if length > 20 * 1024 * 1024:
                self._send(413, '{"hata":"istek gövdesi çok büyük (20 MB üstü)"}')
                return
            req = self._json_oku()
            if "__hata__" in req:
                self._send(400, json.dumps({"hata": req["__hata__"]}))
                return
            if req.get("llm_model") is not None and req.get("llm_model") not in {
                    item["id"] for item in llm.model_profilleri()}:
                self._send(400, '{"hata":"Seçilen LLM modeli bulunamadı."}')
                return
            profile = req.get("model_prompt_profile", "auto")
            if profile not in ("auto", "general", "z_image", "flux3", "krea2", "illustrious", "minimaxh3"):
                self._send(400, '{"hata":"gecersiz prompt profili"}')
                return
            if "bbox_mode" in req and not isinstance(req.get("bbox_mode"), bool):
                self._send(400, '{"hata":"bbox_mode boolean olmali"}')
                return
            if req.get("bbox_mode") and profile != "flux3":
                self._send(400, '{"hata":"bbox modu Flux 3 prompt profili gerektirir"}')
                return
            if (profile == "minimaxh3" and req.get("mod") != "video") or (
                    req.get("mod") == "video" and profile not in
                    ("auto", "general", "minimaxh3")):
                self._send(400, '{"hata":"video promptu için MiniMax H3 veya Genel profilini seç"}')
                return
            job, sira = _kuyruga_ekle(req, kullanici, tur="llm_prompt")
            if job is None:
                self._send(409, '{"hata":"bellek bosaltilirken yeni is kuyruga eklenemez"}')
                return
            self._send(200, json.dumps({"queued": True, "is_id": job["is_id"],
                                        "sira": sira}))
            return
        if parsed.path == "/api/sohbet":
            kullanici = self._auth_gerekli()
            if kullanici is None:
                return
            if self._kasa_gerekli(kullanici) is None:
                return
            if self._kasa_kapaniyor_mu(kullanici):
                return
            req = self._json_oku()
            if "__hata__" in req:
                self._send(400, json.dumps({"hata": req["__hata__"]}))
                return
            if req.get("llm_model") is not None and req.get("llm_model") not in {
                    item["id"] for item in llm.model_profilleri()}:
                self._send(400, '{"hata":"Seçilen LLM modeli bulunamadı."}')
                return
            ham = req.get("mesajlar") or []
            if not isinstance(ham, list) or not ham:
                self._send(400, json.dumps({"hata": "mesajlar listesi gerekli"}))
                return
            if "ek_talimat" in req and not isinstance(req.get("ek_talimat"), str):
                self._send(400, '{"hata":"ek_talimat metin olmali"}')
                return
            job, sira = _kuyruga_ekle(req, kullanici, tur="llm_chat")
            if job is None:
                self._send(409, '{"hata":"bellek bosaltilirken yeni is kuyruga eklenemez"}')
                return
            self._send(200, json.dumps({"queued": True, "is_id": job["is_id"],
                                        "sira": sira}))
            return
        if parsed.path != "/api/uret":
            self._send(404, '{"hata":"bulunamadi"}')
            return
        self._send(410, '{"hata":"/api/uret kaldirildi; /api/kuyruk kullan"}')
        return


_httpd = None
_duzgun_kapat_kilit = threading.Lock()
_duzgun_kapatildi = False
_kapatma_istegi = threading.Event()
_kapatma_istek_kilit = threading.Lock()


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


if __name__ == "__main__":
    if RUNPOD_MODE:
        os.umask(0o077)
        pod_setup.validate_mount()
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
