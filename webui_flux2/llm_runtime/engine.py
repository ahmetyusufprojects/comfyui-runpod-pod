"""Prompt Stüdyosu: llama-server süreç yönetimi ve OpenAI uyumlu istemci.

Yalnızca 127.0.0.1:8191 ile konuşur; GPU katmanlarını boş VRAM'e göre ayarlar.
Prompt ve görsel içeriği hiçbir dosyaya yazılmaz."""

import atexit
import ctypes
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

from . import metrics, prompting, transport
from .prompting import (SISTEM_PROMPTU, MOD_TALIMATI, MODEL_PROMPT_DOSYALARI,
                        MODEL_PROMPT_PROFILLERI, SEMA, DUAL_UZUNLUK_TOKEN,
                        DUAL_UZUNLUK_MAX, VIDEO_UZUNLUK_TOKEN,
                        VIDEO_UZUNLUK_MAX, JSON_DENEME, YUKLEME_BEKLEME,
                        ISTEK_SURE, BOSTA_KAPANIS, METIN_SINIR,
                        GORSEL_HAM_SINIR, GORSEL_UZUN_KENAR,
                        SOHBET_SISTEM_PROMPTU, VARSAYILAN_SISTEM_PROMPTU,
                        VARSAYILAN_MOD_TALIMATI,
                        VARSAYILAN_SOHBET_SISTEM_PROMPTU)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
RUNPOD_MODE = os.environ.get("WEBUI_RUNPOD") == "1"
LLAMA_SERVER = os.environ.get("LLAMA_SERVER_PATH", os.path.join(
    PROJECT_ROOT, "llama.cpp", "llama-server.exe"))
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_linux_process = None
_active_request_cancel = None
_active_request_cancel_lock = threading.Lock()


def iptal_istegi():
    """Ask the active generation to stop without unloading its model."""
    with _active_request_cancel_lock:
        event = _active_request_cancel
    if event is None:
        return False
    event.set()
    return True


def _iptal_kontrolu(iptal):
    if iptal is not None and iptal.is_set():
        raise LLMHatasi("İş durduruldu", "iptal")


def _runpod_llama_settings():
    if not RUNPOD_MODE:
        return {}
    try:
        with open(os.environ["POD_MODEL_CATALOG"], "r", encoding="utf-8") as stream:
            workflow = json.load(stream).get("workflows", {}).get("llama", {})
        model_root = os.path.join(os.environ["COMFYUI_ROOT"], "models", "llm")
        return {
            "model": os.path.join(model_root, workflow["model_file"]),
            "mmproj": (os.path.join(model_root, workflow["mmproj_file"])
                       if workflow.get("mmproj_file") else None),
            "gpu_device": workflow.get("gpu_device", "CUDA0"),
            "gpu_layers": workflow.get("gpu_layers", "all"),
        }
    except (OSError, ValueError, KeyError, TypeError):
        return {}


_RUNPOD_LLAMA = _runpod_llama_settings()


def _load_runpod_profiles():
    if not RUNPOD_MODE:
        return {}
    try:
        with open(os.environ["POD_MODEL_CATALOG"], "r", encoding="utf-8") as stream:
            catalog = json.load(stream)
        setup = sys.modules.get("pod_setup")
        if setup is not None and getattr(setup, "_ready", False):
            catalog["groups"] = setup.CATALOG
            catalog["workflows"] = setup.WORKFLOWS
        root = os.path.join(os.environ["COMFYUI_ROOT"], "models")
        profiles = {}
        for name, workflow in (catalog.get("workflows") or {}).items():
            workflow = workflow or {}
            group = (catalog.get("groups") or {}).get(workflow.get("group")) or {}
            if group.get("engine") != "llama" or not group.get("enabled", True):
                continue
            files = group.get("files") or []
            by_name = {item.get("name"): item for item in files}
            model = by_name.get(workflow.get("model_file"), {})
            mmproj_file = workflow.get("mmproj_file")
            mmproj = by_name.get(mmproj_file, {}) if mmproj_file else {}
            mtp = by_name.get(workflow.get("mtp_file"), {})
            if not workflow.get("model_file"):
                continue
            profiles[name] = {
                "model": os.path.join(root, model.get("dir", "llm"), workflow["model_file"]),
                "mmproj": (os.path.join(root, mmproj.get("dir", "llm"), mmproj_file)
                           if mmproj_file else None),
                "model_identity": " ".join(str(value or "") for value in (
                    name, group.get("label"), workflow.get("model_file"),
                    model.get("repo"), model.get("path"), model.get("url"))),
                "model_size": model.get("size"), "model_sha256": model.get("sha256"),
                "mmproj_size": mmproj.get("size"), "mmproj_sha256": mmproj.get("sha256"),
                "mtp": os.path.join(root, mtp.get("dir", "llm"), workflow["mtp_file"])
                        if workflow.get("mtp_file") else None,
                "mtp_embedded": bool(workflow.get("mtp_embedded") or re.search(
                    r"mtp", " ".join(str(value or "") for value in (
                        workflow.get("model_file"), model.get("path"),
                        model.get("url"))), re.IGNORECASE)),
                "mtp_size": mtp.get("size"), "mtp_sha256": mtp.get("sha256"),
                "gpu_device": workflow.get("gpu_device", "CUDA0"),
                "gpu_layers": workflow.get("gpu_layers", "all"),
                "context": int(workflow.get("context", 8192)),
                "reasoning": workflow.get("reasoning"),
                "label": group.get("label", name),
            }
        return profiles
    except (OSError, ValueError, KeyError, TypeError):
        return {}


_RUNPOD_PROFILES = _load_runpod_profiles()

def _refresh_runpod_profiles():
    global _RUNPOD_PROFILES
    if RUNPOD_MODE:
        profiles = _load_runpod_profiles()
        if profiles:
            _RUNPOD_PROFILES = profiles

def _llm_dbg(msg):
    try:
        with open(os.path.join(BASE_DIR, "webui_error.log"), "a", encoding="utf-8") as f:
            f.write(f"[LLMDBG {time.time():.0f}] {msg}\n")
    except Exception:
        pass
MODEL_PATH = os.environ.get("LLM_MODEL_PATH", _RUNPOD_LLAMA.get("model") or os.path.join(PROJECT_ROOT, "Gemma-4E4B-3",
                          "Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf"))
_DEFAULT_MMPROJ_PATH = (_RUNPOD_LLAMA.get("mmproj") or "") if RUNPOD_MODE else os.path.join(
    PROJECT_ROOT, "Gemma-4E4B-3",
    "mmproj-Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-f16.gguf")
MMPROJ_PATH = os.environ.get("LLM_MMPROJ_PATH", _DEFAULT_MMPROJ_PATH)
# Q8_0 ayristirmasi: f16 mmproj'in yarisi boyutunda. llama-quantize'un
# a.conv1d f32-tenensorlerinde boyut asserasyonu catirdigindan bu tensorler
# f32 birakildi. Yarim boyut -> yuklemede yaklasik 3 sn kazanc.
MMPROJ_Q8_PATH = os.environ.get("LLM_MMPROJ_Q8_PATH", os.path.join(PROJECT_ROOT, "Gemma-4E4B-3",
                              "mmproj-Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q8_0.gguf"))
MMPROJ_Q8_BEYIT = 555408320
MMPROJ_Q8_KOD = "DC40C0FA3C765D90E744825EC567B1698847F033A06EE04CE5F7B0085D349C0E"
MODEL_BEYIT = 5335285728
MMPROJ_BEYIT = 990288832
MODEL_KOD = "D0027DD3A9128D9323E9F282C8BF010A8526C46477584535991DC1A869B56E96"
MMPROJ_KOD = "DEBAD39AB9C1152AB67695A674FB35E8375B2320C57BFD5075835D3CCB16C7DB"
PORT = 8191
# Windows keeps its existing 38-layer setting. RunPod reads its GPU target and
# full-layer choice from the versioned model catalog.
GPU_KATMAN = os.environ.get("LLAMA_GPU_LAYERS",
                            str(_RUNPOD_LLAMA.get("gpu_layers", "all" if RUNPOD_MODE else 38)))
GPU_DEVICE = os.environ.get("LLAMA_GPU_DEVICE",
                            _RUNPOD_LLAMA.get("gpu_device", "CUDA0"))
CTX = 8000                 # varsayilan; kullanicinin llm_ctx ayari ile guncellenir
LLAMA_THREADS = 8
LLAMA_BATCH = 4096
LLAMA_UBATCH = 1024
TOP_P = 0.95
TOP_K = 40
REPEAT_PENALTY = 1.05
MTP_ENABLED = True
MULTIMODAL_ENABLED = True
FLASH_ATTENTION = "on"
KV_CACHE_TYPE_K = "f16"
KV_CACHE_TYPE_V = "f16"
MTP_DRAFT_TOKENS = 3
REASONING_MODE = "on"
REASONING_EFFORT = "default"
REASONING_BUDGET = 160
BAGLAM_ALT = 1024
BAGLAM_UST = 32768
LOG_PATH = os.path.join(BASE_DIR, "llm_server.log")
PID_PATH = os.path.join(BASE_DIR, "llm_server.pid")
KOD_OSURU = os.path.join(BASE_DIR, "llm_sha256.json")  # sha256 onbellek (path->boyut/mtime/kod)

ADRES = "http://127.0.0.1:%d" % PORT

from .prompting import (SISTEM_PROMPTU, MOD_TALIMATI, MODEL_PROMPT_DOSYALARI,
                        MODEL_PROMPT_PROFILLERI, SEMA, DUAL_UZUNLUK_TOKEN,
                        DUAL_UZUNLUK_MAX, VIDEO_UZUNLUK_TOKEN,
                        VIDEO_UZUNLUK_MAX, JSON_DENEME, YUKLEME_BEKLEME,
                        ISTEK_SURE, BOSTA_KAPANIS, METIN_SINIR,
                        GORSEL_HAM_SINIR, GORSEL_UZUN_KENAR,
                        SOHBET_SISTEM_PROMPTU, VARSAYILAN_SISTEM_PROMPTU,
                        VARSAYILAN_MOD_TALIMATI,
                        VARSAYILAN_SOHBET_SISTEM_PROMPTU)

class LLMHatasi(Exception):
    """kod: istek(400) | mesgul(409) | model(502) | surec(502) | sure(504)"""

    def __init__(self, mesaj, kod):
        super().__init__(mesaj)
        self.kod = kod


# ------------------------------------------------------------------ doğrulama

def _kod_tablo_oku():
    try:
        with open(KOD_OSURU, "r", encoding="ascii") as f:
            return json.load(f)
    except Exception:
        return {}


def _kod_tablo_yaz(tablo):
    try:
        with open(KOD_OSURU, "w", encoding="ascii") as f:
            json.dump(tablo, f)
    except Exception:
        pass


def _dosya_kod(yol, bek):
    bek_kod = bek[1].upper()
    try:
        boyut = os.path.getsize(yol)
    except OSError:
        raise LLMHatasi("model dosyasi yok: " + os.path.basename(yol), "surec")
    if boyut != bek[0]:
        raise LLMHatasi(os.path.basename(yol) + " dosya boyutu yanlis", "surec")
    tablo = _kod_tablo_oku()
    kayit = tablo.get(yol)
    if kayit and kayit.get("boyut") == boyut \
            and kayit.get("mtime") == os.path.getmtime(yol) \
            and str(kayit.get("kod", "")).upper() == bek_kod:
        return
    hesap = hashlib.sha256()
    with open(yol, "rb") as f:
        for blok in iter(lambda: f.read(8 * 1024 * 1024), b""):
            hesap.update(blok)
    kod = hesap.hexdigest().upper()
    if kod != bek_kod:
        raise LLMHatasi(os.path.basename(yol) + " sha256 doğrulanamadı", "surec")
    tablo[yol] = {"boyut": boyut, "mtime": os.path.getmtime(yol), "kod": kod}
    _kod_tablo_yaz(tablo)


def model_dogrula():
    global _ACTIVE_MTP_PATH, _ACTIVE_MTP_EMBEDDED
    _refresh_runpod_profiles()
    if RUNPOD_MODE and _ACTIVE_PROFILE in _RUNPOD_PROFILES:
        config = _RUNPOD_PROFILES[_ACTIVE_PROFILE]
        check_files = [("model", MODEL_PATH)]
        if MULTIMODAL_ENABLED:
            check_files.append(("mmproj", MMPROJ_PATH))
        for key, path in check_files:
            size, digest = config.get(key + "_size"), config.get(key + "_sha256")
            if size and digest:
                _dosya_kod(path, (size, digest))
            elif not os.path.isfile(path):
                raise LLMHatasi(os.path.basename(path) + " bulunamadı", "model")
        _ACTIVE_MTP_PATH = None
        _ACTIVE_MTP_EMBEDDED = bool(config.get("mtp_embedded"))
        mtp = config.get("mtp")
        if MTP_ENABLED and mtp and os.path.isfile(mtp):
            try:
                if config.get("mtp_size") and config.get("mtp_sha256"):
                    _dosya_kod(mtp, (config["mtp_size"], config["mtp_sha256"]))
                _ACTIVE_MTP_PATH = mtp
                _ACTIVE_MTP_EMBEDDED = False
            except LLMHatasi:
                # MTP only speeds decoding; an incompatible optional file cannot block the main model.
                _ACTIVE_MTP_PATH = None
        return
    yol, beylit, kod = _aktif_mmproj()
    _dosya_kod(yol, (beylit, kod))
    _dosya_kod(MODEL_PATH, (MODEL_BEYIT, MODEL_KOD))


def _aktif_mmproj():
    """Q8_0 ayristirmasi varsa onu, yoksa f16 dosyayi secer."""
    if not RUNPOD_MODE and os.path.exists(MMPROJ_Q8_PATH):
        return MMPROJ_Q8_PATH, MMPROJ_Q8_BEYIT, MMPROJ_Q8_KOD
    return MMPROJ_PATH, MMPROJ_BEYIT, MMPROJ_KOD


_ACTIVE_PROFILE = "llm_26b" if RUNPOD_MODE else "llama"
_ACTIVE_MTP_PATH = None
_ACTIVE_MTP_EMBEDDED = False
_calisan_profile = None
_calisan_signature = None


def model_ayarla(profile):
    """Select a RunPod LLM profile; the server restarts on its next queued job."""
    global MODEL_PATH, MMPROJ_PATH, MODEL_BEYIT, MMPROJ_BEYIT
    global MODEL_KOD, MMPROJ_KOD, GPU_DEVICE, GPU_KATMAN, CTX
    global _ACTIVE_PROFILE, _ACTIVE_MTP_PATH, _ACTIVE_MTP_EMBEDDED, MULTIMODAL_ENABLED
    _refresh_runpod_profiles()
    if not RUNPOD_MODE:
        if profile not in (None, "llama"):
            raise LLMHatasi("Bulut LLM seçimi yalnızca RunPod üzerinde kullanılabilir.", "istek")
        return _ACTIVE_PROFILE
    if profile not in _RUNPOD_PROFILES:
        raise LLMHatasi("Seçilen LLM modeli bu imajda tanımlı değil.", "istek")
    config = _RUNPOD_PROFILES[profile]
    MODEL_PATH, MMPROJ_PATH = config["model"], config.get("mmproj") or ""
    MODEL_BEYIT, MODEL_KOD = config.get("model_size"), config.get("model_sha256")
    MMPROJ_BEYIT, MMPROJ_KOD = config.get("mmproj_size"), config.get("mmproj_sha256")
    GPU_DEVICE, GPU_KATMAN = config["gpu_device"], str(config["gpu_layers"])
    CTX = int(config.get("context") or 8192)
    _ACTIVE_PROFILE = profile
    _ACTIVE_MTP_PATH = None
    _ACTIVE_MTP_EMBEDDED = bool(config.get("mtp_embedded"))
    if not MMPROJ_PATH:
        # A text-only profile must not inherit the previous model's vision path.
        MULTIMODAL_ENABLED = False
    return profile


def _profil_ayar_varsayilanlari(profile=None):
    config = _RUNPOD_PROFILES.get(profile or _ACTIVE_PROFILE) or {}
    identity = " ".join(str(value or "") for value in (
        profile, config.get("label"), config.get("model_identity"),
        os.path.basename(config.get("model") or "")))
    qwen = "qwen" in identity.casefold()
    mtp = bool(config.get("mtp") or config.get("mtp_embedded"))
    return {
        "gpu_layers": "auto" if qwen else config.get("gpu_layers", "auto"),
        "context": 8192 if qwen else int(config.get("context") or 8192),
        "threads": 8,
        "batch": 2048 if qwen else 4096,
        "ubatch": 512 if qwen else 1024,
        "flash_attn": "on",
        "cache_type_k": "q8_0" if qwen else "f16",
        "cache_type_v": "q8_0" if qwen else "f16",
        "top_p": 0.80 if qwen else 0.95,
        "top_k": 20 if qwen else 40,
        "repeat_penalty": 1.0 if qwen else 1.05,
        "temperature": 0.7,
        "use_mtp": mtp,
        "multimodal": bool(config.get("mmproj")),
        "reasoning": config.get("reasoning") or "on",
        "reasoning_effort": "low" if qwen else "default",
        "reasoning_budget": 160,
        "mtp_draft_tokens": 2 if qwen and mtp else 3,
    }


def ayarlarini_uygula(settings, profile=None):
    global GPU_KATMAN, LLAMA_THREADS, LLAMA_BATCH, LLAMA_UBATCH
    global TOP_P, TOP_K, REPEAT_PENALTY, MTP_ENABLED, MULTIMODAL_ENABLED
    global CTX, FLASH_ATTENTION, KV_CACHE_TYPE_K, KV_CACHE_TYPE_V
    global REASONING_MODE, REASONING_EFFORT, REASONING_BUDGET
    global MTP_DRAFT_TOKENS
    if not isinstance(settings, dict):
        return
    profile = profile or _ACTIVE_PROFILE
    defaults = _profil_ayar_varsayilanlari(profile)
    profiles = settings.get("llm_model_tuning")
    selected = profiles.get(profile) if isinstance(profiles, dict) else None
    if isinstance(selected, dict):
        tuning = dict(defaults)
        tuning.update(selected)
    else:
        # Keep old account settings effective until the user saves per-model values.
        tuning = dict(defaults)
        if not profiles and not ("qwen" in str(_RUNPOD_PROFILES.get(profile, {}).get(
                "model_identity", "")).casefold()):
            for field, legacy in (("gpu_layers", "llm_gpu_layers"),
                                  ("threads", "llm_threads"),
                                  ("batch", "llm_batch"),
                                  ("top_p", "llm_top_p"),
                                  ("top_k", "llm_top_k"),
                                  ("repeat_penalty", "llm_repeat_penalty"),
                                  ("temperature", "llm_temperature"),
                                  ("use_mtp", "llm_use_mtp"),
                                  ("multimodal", "llm_multimodal")):
                if legacy in settings:
                    tuning[field] = settings[legacy]
    layers = tuning.get("gpu_layers", "auto")
    if layers in ("all", "auto"):
        GPU_KATMAN = layers
    elif isinstance(layers, int) and not isinstance(layers, bool) and 0 <= layers <= 512:
        GPU_KATMAN = str(layers)
    else:
        GPU_KATMAN = str(defaults["gpu_layers"])
    CTX = max(BAGLAM_ALT, min(BAGLAM_UST, int(tuning.get("context", defaults["context"]))))
    LLAMA_THREADS = max(1, min(64, int(tuning.get("threads", defaults["threads"]))))
    LLAMA_BATCH = max(128, min(8192, int(tuning.get("batch", defaults["batch"]))))
    LLAMA_UBATCH = max(128, min(LLAMA_BATCH, int(tuning.get("ubatch", defaults["ubatch"]))))
    TOP_P = max(0.05, min(1.0, float(tuning.get("top_p", defaults["top_p"]))))
    TOP_K = max(0, min(1000, int(tuning.get("top_k", defaults["top_k"]))))
    REPEAT_PENALTY = max(0.8, min(2.0, float(tuning.get("repeat_penalty", defaults["repeat_penalty"]))))
    MTP_ENABLED = tuning.get("use_mtp", defaults["use_mtp"]) is not False
    wants_multimodal = tuning.get("multimodal", defaults["multimodal"]) is not False
    MULTIMODAL_ENABLED = wants_multimodal and (
        not RUNPOD_MODE or os.path.isfile(MMPROJ_PATH))
    FLASH_ATTENTION = tuning.get("flash_attn", "on")
    KV_CACHE_TYPE_K = tuning.get("cache_type_k", "f16")
    KV_CACHE_TYPE_V = tuning.get("cache_type_v", "f16")
    REASONING_MODE = tuning.get("reasoning", "on")
    REASONING_EFFORT = tuning.get("reasoning_effort", "default")
    REASONING_BUDGET = max(-1, min(32768, int(tuning.get("reasoning_budget", 160))))
    MTP_DRAFT_TOKENS = max(1, min(16, int(tuning.get("mtp_draft_tokens", 3))))
    if wants_multimodal and not MULTIMODAL_ENABLED:
        _llm_dbg("mmproj dosyası yok; ana Gemma modeli görsel girişi olmadan devam edecek")


def profil_vram_mb(profile=None, context=None):
    _refresh_runpod_profiles()
    config = _RUNPOD_PROFILES.get(profile or _ACTIVE_PROFILE) or {}
    def actual_size(key):
        path = config.get(key)
        return os.path.getsize(path) if path and os.path.isfile(path) else int(config.get(key + "_size") or 0)
    weights = actual_size("model")
    if MULTIMODAL_ENABLED:
        weights += actual_size("mmproj")
    mtp = config.get("mtp")
    if MTP_ENABLED and mtp and os.path.isfile(mtp):
        weights += actual_size("mtp")
    context = int(context or CTX)
    return int(weights / (1024 * 1024) + max(2500, context / 4))


def _runtime_signature():
    profile = _ACTIVE_PROFILE if RUNPOD_MODE else "llama"
    config = _RUNPOD_PROFILES.get(profile) or {}
    file_state = []
    for path in (MODEL_PATH, MMPROJ_PATH, config.get("mtp")):
        if not path:
            file_state.append(None)
            continue
        try:
            stat = os.stat(path)
            file_state.append((path, stat.st_size, stat.st_mtime_ns))
        except OSError:
            file_state.append((path, None, None))
    return [profile, config.get("model_identity"), tuple(file_state),
            CTX, str(GPU_KATMAN), LLAMA_THREADS, LLAMA_BATCH, LLAMA_UBATCH, bool(MTP_ENABLED),
            bool(MULTIMODAL_ENABLED), FLASH_ATTENTION, KV_CACHE_TYPE_K,
            KV_CACHE_TYPE_V, REASONING_MODE, REASONING_EFFORT,
            REASONING_BUDGET, MTP_DRAFT_TOKENS,
            _ACTIVE_MTP_PATH, bool(_ACTIVE_MTP_EMBEDDED)]


def model_profilleri():
    _refresh_runpod_profiles()
    return [{"id": name, "ad": config.get("label", name),
             "tuning_family": ("qwen" if "qwen" in str(
                 config.get("model_identity", "")).casefold() else "generic"),
             "model_bayt": config.get("model_size"),
             "mmproj_bayt": config.get("mmproj_size"),
             "mtp_bayt": config.get("mtp_size"),
             "mtp_dosyasi": bool(config.get("mtp")),
             "mtp_dahili": bool(config.get("mtp_embedded")),
             "gorsel_dosyasi": bool(config.get("mmproj")),
             "ctx": config.get("context", 8192),
             "tuning_defaults": _profil_ayar_varsayilanlari(name),
             "hazir": os.path.isfile(config.get("model", "")),
             "gorsel_hazir": bool(config.get("mmproj")
                                  and os.path.isfile(config.get("mmproj", "")))}
            for name, config in _RUNPOD_PROFILES.items()]


# ------------------------------------------------------------------ süreç

def _pid_oku():
    try:
        with open(PID_PATH, "r", encoding="ascii") as f:
            ham = f.read().strip()
    except Exception:
        return None
    try:
        veri = json.loads(ham)
        if isinstance(veri, dict) and veri.get("pid") is not None:
            return int(veri["pid"])
    except Exception:
        pass
    try:
        return int(ham)
    except Exception:
        return None


def _pid_ctx_oku():
    """PID dosyasindaki baglam boyutu; eski bicimde None doner."""
    try:
        with open(PID_PATH, "r", encoding="ascii") as f:
            veri = json.loads(f.read())
        if isinstance(veri, dict) and isinstance(veri.get("ctx"), int):
            return veri["ctx"]
    except Exception:
        return None
    return None


def _pid_profile_oku():
    if not RUNPOD_MODE:
        return "llama"
    try:
        with open(PID_PATH, "r", encoding="ascii") as f:
            value = json.load(f)
        return value.get("profile") if isinstance(value, dict) else None
    except Exception:
        return None


def _pid_signature_oku():
    try:
        with open(PID_PATH, "r", encoding="ascii") as f:
            value = json.load(f)
        signature = value.get("signature") if isinstance(value, dict) else None
        return signature if isinstance(signature, list) else None
    except Exception:
        return None


def _surec_mem_byt(pid=None):
    """llama-server surecinin RAM kullanimi (bayt). Windows only."""
    if pid is None:
        pid = _pid_oku()
    if not _surec_yasiyor(pid):
        return None
    try:
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not h:
            return None

        class PM(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong),
                        ("PageFaultCount", ctypes.c_ulong),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]

        m = PM()
        m.cb = ctypes.sizeof(m)
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(
            h, ctypes.byref(m), m.cb)
        ctypes.windll.kernel32.CloseHandle(h)
        return int(m.WorkingSetSize) if ok else None
    except Exception:
        return None


def _surec_yasiyor(pid):
    if pid is None:
        return False
    if os.name != "nt":
        return (_linux_process is not None and _linux_process.pid == pid
                and _linux_process.poll() is None)
    h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if h:
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    return False


def _port_sahibi():
    if os.name != "nt":
        if _linux_process is not None and _linux_process.poll() is None and _saglik(1):
            return _linux_process.pid
        return None
    try:
        out = subprocess.check_output(["netstat", "-ano", "-p", "TCP"],
                                      creationflags=subprocess.CREATE_NO_WINDOW,
                                      timeout=30).decode("utf-8", "replace")
        for satir in out.splitlines():
            p = satir.split()
            if len(p) >= 5 and p[3] == "LISTENING" and \
                    p[1] in ("127.0.0.1:%d" % PORT, "0.0.0.0:%d" % PORT):
                return int(p[-1])
    except Exception:
        pass
    return None


def _saglik(saniye=3):
    try:
        with urllib.request.urlopen(ADRES + "/health", timeout=saniye) as r:
            return json.loads(r.read().decode("utf-8")).get("status")
    except urllib.error.HTTPError as e:
        try:
            hata = json.loads(e.read().decode("utf-8")).get("error") or {}
            mesaj = str(hata.get("message") or "").lower()
        except Exception:
            mesaj = ""
        if e.code == 503 and "load" in mesaj:
            return "loading"
        return "hata"
    except Exception:
        return None


def durum(detayli=False):
    pid = _pid_oku()
    canli = _surec_yasiyor(pid)
    d = {"pid": pid, "canli": canli,
         "saglik": _saglik() if canli else None,
         "model": _ACTIVE_PROFILE if RUNPOD_MODE else "llama",
         "multimodal": bool(MULTIMODAL_ENABLED),
         "mtp": bool(MTP_ENABLED and (_ACTIVE_MTP_PATH or _ACTIVE_MTP_EMBEDDED))}
    if detayli:
        komut_parcalar = [LLAMA_SERVER, "-m", MODEL_PATH]
        if MULTIMODAL_ENABLED:
            komut_parcalar += ["--mmproj", _aktif_mmproj()[0]]
        komut_parcalar += ["--host", "127.0.0.1", "--port", str(PORT),
                           "-c", str(CTX), "-ngl", str(GPU_KATMAN),
                           "-t", str(LLAMA_THREADS), "-b", str(LLAMA_BATCH),
                           "-ub", str(LLAMA_UBATCH), "-np", "1",
                           "-fa", str(FLASH_ATTENTION), "-ctk", KV_CACHE_TYPE_K,
                           "-ctv", KV_CACHE_TYPE_V, "--fit", "on"]
        if os.name != "nt":
            komut_parcalar += ["--device", GPU_DEVICE]
        if REASONING_MODE == "off":
            komut_parcalar += ["-rea", "off"]
        else:
            komut_parcalar += ["-rea", REASONING_MODE,
                               "--reasoning-effort", REASONING_EFFORT]
            if isinstance(REASONING_BUDGET, int):
                komut_parcalar += ["--reasoning-budget", str(REASONING_BUDGET)]
            komut_parcalar += ["--reasoning-format", "deepseek"]
        if MTP_ENABLED and _ACTIVE_MTP_PATH:
            komut_parcalar += ["--spec-type", "draft-mtp", "--spec-draft-model",
                               _ACTIVE_MTP_PATH, "--spec-draft-n-max",
                               str(MTP_DRAFT_TOKENS)]
        elif MTP_ENABLED and _ACTIVE_MTP_EMBEDDED:
            komut_parcalar += ["--spec-type", "draft-mtp", "--spec-draft-n-max",
                               str(MTP_DRAFT_TOKENS)]
        komut_parcalar += ["--no-webui", "--cache-reuse", "256", "-lm", "mmap",
                           "--no-warmup"]
        d.update({"port": PORT, "ngl": GPU_KATMAN, "ctx": CTX,
                  "gpu_device": GPU_DEVICE if os.name != "nt" else None,
                  "calisan_ctx": _calisan_ctx,
                  "calisan_model": _calisan_profile,
                  "flash_attn": FLASH_ATTENTION,
                  "cache_type_k": KV_CACHE_TYPE_K,
                  "cache_type_v": KV_CACHE_TYPE_V,
                  "reasoning": REASONING_MODE,
                  "reasoning_effort": REASONING_EFFORT,
                  "mtp_embedded": _ACTIVE_MTP_EMBEDDED,
                  "komut": " ".join(komut_parcalar),
                  "ram_byt": _surec_mem_byt(pid),
                  "son_kullanim_sn": round(time.monotonic()
                                           - _son_kullanim, 1),
                  "bosta_kapanis_sn": BOSTA_KAPANIS,
                  "yukleme_ms": _son_yukleme_ms,
                  "olcum": canli_olcum(),
                  "uretim_suruyor": _kilit.locked()})
    return d


def _pid_yaz(pid, ctx=None):
    try:
        with open(PID_PATH, "w", encoding="ascii") as f:
            if ctx is None and not RUNPOD_MODE:
                f.write(str(pid))
            else:
                value = {"pid": int(pid)}
                if ctx is not None:
                    value["ctx"] = int(ctx)
                if RUNPOD_MODE:
                    value["profile"] = _ACTIVE_PROFILE
                    value["signature"] = _runtime_signature()
                    if _ACTIVE_MTP_PATH:
                        value["mtp"] = True
                json.dump(value, f)
    except OSError:
        pass


def _hazir_bekle(pid, baslama, iptal=None):
    """Saglik 'ok' olana kadar 0.25 sn araliklarla bekle. Donus: yukleme ms."""
    while time.monotonic() - baslama < YUKLEME_BEKLEME:
        _iptal_kontrolu(iptal)
        if not _surec_yasiyor(pid):
            return None
        durum = _saglik()
        if durum == "ok":
            return int((time.monotonic() - baslama) * 1000)
        if durum == "hata":
            return None
        time.sleep(0.25)
    return None


def _gpu_backend_dogrula():
    """RunPod must see a CUDA device; never quietly fall back to CPU."""
    if os.name == "nt":
        return
    try:
        output = subprocess.check_output([LLAMA_SERVER, "--list-devices"],
                                         stderr=subprocess.STDOUT, text=True,
                                         timeout=30)
    except (OSError, subprocess.SubprocessError):
        raise LLMHatasi("llama-server GPU aygıtlarını listeleyemedi; CUDA kurulumu kontrol edilmeli.",
                        "surec") from None
    if not re.search(r"(?m)^\s*" + re.escape(GPU_DEVICE) + r":", output):
        raise LLMHatasi("llama-server " + GPU_DEVICE +
                        " GPU'sunu göremedi; CPU ile yavaş çalışması engellendi.", "surec")


def _linux_llama_command(pid):
    if os.name == "nt" or not pid:
        return []
    try:
        raw = open("/proc/%d/cmdline" % int(pid), "rb").read()
        return [item.decode("utf-8", "replace") for item in raw.split(b"\0") if item]
    except (OSError, ValueError):
        return []


def _gpu_sureci_mi(pid):
    """A healthy RunPod llama process must explicitly target CUDA and GPU layers."""
    if os.name == "nt":
        return True
    args = _linux_llama_command(pid)
    if not args or "llama-server" not in os.path.basename(args[0]):
        return False
    try:
        device = args[args.index("--device") + 1]
        layers = args[args.index("-ngl") + 1]
    except (ValueError, IndexError):
        return False
    if device != GPU_DEVICE:
        return False
    try:
        # auto/all let llama.cpp place as many layers on the GPU as fit.
        # Layer count zero is an explicit user choice (all layers stay on CPU).
        # The server still has to be launched with CUDA0 and the CUDA backend
        # itself is verified separately by _gpu_backend_dogrula().
        return layers.lower() in ("all", "auto") or int(layers) >= 0
    except (TypeError, ValueError):
        return False


def baslat(*, gpu_hazirla=None, iptal=None):
    """Sunucuyu hazirlar. Donus: bu cagrida gecen yukleme ms (0 = zaten hazir)."""
    global _calisan_ctx, _linux_process, _calisan_profile
    global _ACTIVE_MTP_PATH, _ACTIVE_MTP_EMBEDDED
    global _calisan_signature
    baslama = time.monotonic()
    _llm_dbg("baslat_basla")
    _gpu_backend_dogrula()
    saglik = _saglik()
    pid = _pid_oku()

    stored_profile = _pid_profile_oku()
    if (RUNPOD_MODE and saglik in ("ok", "loading")
            and stored_profile != _ACTIVE_PROFILE
            and (stored_profile is not None or _ACTIVE_PROFILE != "llama")):
        # An old profile belongs to this application's managed llama process.
        durdur()
        saglik = None
        pid = None

    if saglik == "ok":
        sahibi = _port_sahibi()
        if sahibi is None and _surec_yasiyor(pid):
            sahibi = pid
        if sahibi is None:
            raise LLMHatasi("llama-server süreci ve GPU kullanımı doğrulanamadı.", "surec")
        if sahibi is not None and not _gpu_sureci_mi(sahibi):
            if "llama-server" not in os.path.basename((_linux_llama_command(sahibi) or [""])[0]):
                raise LLMHatasi("8191 portunu llama-server dışı bir süreç kullanıyor.", "surec")
            _pid_yaz(sahibi, _pid_ctx_oku())
            durdur()
            saglik = "hata"
            _llm_dbg("GPU kullanmayan eski llama süreci GPU ayarlarıyla yenileniyor")
        else:
            if sahibi is not None:
                _pid_yaz(sahibi)
            signature = _runtime_signature()
            if _calisan_signature != signature:
                _llm_dbg("baslat_ayarlar_degisti_yeniden_yukleniyor")
                durdur()
                saglik = None
                pid = None
            else:
                _calisan_ctx = _pid_ctx_oku()
                _calisan_profile = _ACTIVE_PROFILE
                _calisan_signature = signature
                _llm_dbg(f"baslat_zaten_hazir ms={int((time.monotonic()-baslama)*1000)}")
                return 0

    if saglik == "loading":
        sahibi = _port_sahibi()
        if sahibi is None and _surec_yasiyor(pid):
            sahibi = pid
        if sahibi is not None:
            _calisan_ctx = _pid_ctx_oku()
            _calisan_profile = _ACTIVE_PROFILE
            _calisan_signature = _runtime_signature()
            _pid_yaz(sahibi, _calisan_ctx)
            kalan = _hazir_bekle(sahibi, baslama, iptal)
            if kalan is not None:
                _llm_dbg(f"baslat_bekle_bitis ms={int((time.monotonic()-baslama)*1000)}")
                return kalan
            raise LLMHatasi("llama-server %d saniyede hazır olmadı"
                            % YUKLEME_BEKLEME, "surec")
        durdur()

    model_dogrula()
    sahibi = _port_sahibi()
    if sahibi is not None:
        if sahibi != pid:
            raise LLMHatasi("port %d başka bir süreçte (%d); o süreç kapatılmalı"
                            % (PORT, sahibi), "surec")
        _calisan_ctx = _pid_ctx_oku()
        kalan = _hazir_bekle(sahibi, baslama, iptal)
        if kalan is not None:
            _llm_dbg(f"baslat_port_sahibi_bekle_bitis ms={int((time.monotonic()-baslama)*1000)}")
            return kalan
        durdur()

    try:
        os.remove(PID_PATH)
    except OSError:
        pass

    komut = [LLAMA_SERVER, "-m", MODEL_PATH]
    if MULTIMODAL_ENABLED:
        komut += ["--mmproj", _aktif_mmproj()[0]]
    komut += [
        "--host", "127.0.0.1", "--port", str(PORT),
        "-c", str(CTX), "-ngl", str(GPU_KATMAN), "-t", str(LLAMA_THREADS),
        "-b", str(LLAMA_BATCH), "-ub", str(LLAMA_UBATCH), "-np", "1",
        "-kvu", "-ctxcp", "32", "-fa", str(FLASH_ATTENTION),
        "-ctk", str(KV_CACHE_TYPE_K), "-ctv", str(KV_CACHE_TYPE_V),
        "--fit", "on", "-lm", "mmap",
        "--no-webui", "--cache-reuse", "256",
        # Warmup bos bir kosu calistirir ve ~18 sn surer; ilk istekte zaten
        # graph'lar olusur. Atlaninca model acilisi yaklasik ikiye katlanir.
        "--no-warmup",
    ]
    if os.name != "nt":
        komut += ["--device", GPU_DEVICE]
    if REASONING_MODE == "off":
        komut += ["-rea", "off"]
    else:
        komut += ["-rea", REASONING_MODE,
                  "--reasoning-effort", REASONING_EFFORT]
        if isinstance(REASONING_BUDGET, int):
            komut += ["--reasoning-budget", str(REASONING_BUDGET)]
        komut += ["--reasoning-format", "deepseek"]
    if str(GPU_KATMAN) == "0":
        komut += ["-nkvo", "--no-mmproj-offload"]
    if MTP_ENABLED and _ACTIVE_MTP_PATH and os.path.isfile(_ACTIVE_MTP_PATH):
        komut += ["--spec-type", "draft-mtp", "--spec-draft-model", _ACTIVE_MTP_PATH,
                  "--spec-draft-n-max", str(MTP_DRAFT_TOKENS)]
    elif MTP_ENABLED and _ACTIVE_MTP_EMBEDDED:
        # This GGUF contains its own MTP head; it does not need a draft sidecar.
        komut += ["--spec-type", "draft-mtp", "--spec-draft-n-max",
                  str(MTP_DRAFT_TOKENS)]
    _dosyalari_isit()
    if gpu_hazirla is not None:
        gpu_hazirla()
    try:
        log = open(LOG_PATH, "a", encoding="utf-8", errors="replace")
    except OSError as e:
        raise LLMHatasi("log dosyasi acilamadi: %s" % e, "surec")
    try:
        surec = subprocess.Popen(komut, stdout=log, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, shell=False,
                                 creationflags=_NO_WINDOW)
        if os.name != "nt":
            _linux_process = surec
    finally:
        log.close()
    _pid_yaz(surec.pid, CTX)
    _calisan_ctx = CTX
    _calisan_profile = _ACTIVE_PROFILE
    _calisan_signature = _runtime_signature()

    sure = _hazir_bekle(surec.pid, baslama, iptal)
    if iptal is not None and iptal.is_set():
        durdur()
        raise LLMHatasi("İş durduruldu", "iptal")
    if sure is None:
        if _ACTIVE_MTP_PATH or _ACTIVE_MTP_EMBEDDED:
            _llm_dbg("MTP ile model başlayamadı; MTP olmadan tekrar deneniyor")
            _ACTIVE_MTP_PATH = None
            _ACTIVE_MTP_EMBEDDED = False
            durdur()
            return baslat(gpu_hazirla=gpu_hazirla)
        if surec.poll() is not None:
            satirlar = _log_sonu(50)
            raise LLMHatasi("llama-server başlamadı:\n" + "\n".join(satirlar),
                            "surec")
        durdur()
        raise LLMHatasi("llama-server %d saniyede hazır olmadı"
                        % YUKLEME_BEKLEME, "surec")
    return sure


_ISITMA_OKUMA = 8 * 1024 * 1024  # 8 MB blok: NVMe hizinda sayfa onbelligini doldurur


def _dosyalari_isit():
    """Model dosyalarini sirali okuyarak Windows sayfa onbelligine koyar.

    llama.gguf'lar mmap ile yuklenir; soguk disk'te tensor faulleri 350-600
    MB/s'de akar (sirali NVMe okuma 2400 MB/s). Onbellek ortaktir: ComfyUI
    her imge isinde ~10 GB model okur ve llama'nin 5.8 GB'ini onbellekten
    atar; o yuzden "yakin zamanda kapandi" varmi guvenli degildir, her
    baslatmadan once her zaman isitilir (~2-3 sn)."""
    baslama = time.monotonic()
    for yol in (MODEL_PATH, _aktif_mmproj()[0]):
        try:
            with open(yol, "rb") as f:
                blok = bytearray(_ISITMA_OKUMA)
                mv = memoryview(blok)
                while f.readinto(mv):
                    pass
        except OSError:
            pass
    ms = int((time.monotonic() - baslama) * 1000)
    try:
        with open(os.path.join(os.path.dirname(LOG_PATH), "webui_error.log"), "a", encoding="utf-8") as ef:
            ef.write(f"[prewarm] {ms} ms\n")
    except Exception:
        pass
    return ms


def _log_sonu(n):
    try:
        with open(LOG_PATH, "rb") as f:
            f.seek(0, os.SEEK_END)
            boyut = f.tell()
            f.seek(max(0, boyut - 20000))
            parca = f.read().decode("utf-8", "replace")
            return [s.strip() for s in parca.splitlines() if s.strip()][-n:]
    except OSError:
        return []


def durdur():
    global _calisan_ctx, _calisan_profile, _calisan_signature
    pid = _pid_oku()
    if os.name != "nt" and _surec_yasiyor(pid):
        try:
            if _linux_process is not None and _linux_process.pid == pid:
                _linux_process.terminate()
                _linux_process.wait(timeout=5)
            elif _gpu_sureci_mi(pid) or "llama-server" in os.path.basename(
                    (_linux_llama_command(pid) or [""])[0]):
                os.kill(pid, 15)
                for _ in range(10):
                    if not _surec_yasiyor(pid):
                        break
                    time.sleep(0.5)
                if _surec_yasiyor(pid):
                    os.kill(pid, 9)
        except (ProcessLookupError, PermissionError, subprocess.TimeoutExpired):
            pass
    if pid is not None and _surec_yasiyor(pid):
        try:
            subprocess.Popen(["taskkill", "/PID", str(pid), "/T"],
                             creationflags=subprocess.CREATE_NO_WINDOW,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(10):
                time.sleep(0.5)
                if not _surec_yasiyor(pid):
                    break
            if _surec_yasiyor(pid):
                subprocess.Popen(["taskkill", "/F", "/PID", str(pid), "/T"],
                                 creationflags=subprocess.CREATE_NO_WINDOW,
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        except Exception:
            pass
    try:
        os.remove(PID_PATH)
    except OSError:
        pass
    _calisan_ctx = None
    _calisan_profile = None
    _calisan_signature = None


# ------------------------------------------------------------------ istek

def baglam_ayarla(deger):
    """Kullanici ayarindan gelen baglam boyutunu dogrular ve CTX'e yazar."""
    global CTX
    try:
        d = int(deger)
    except (TypeError, ValueError):
        raise LLMHatasi("bağlam boyutu sayı olmalı", "istek")
    if d < BAGLAM_ALT or d > BAGLAM_UST:
        raise LLMHatasi("bağlam boyutu %d-%d aralığında olmalı"
                        % (BAGLAM_ALT, BAGLAM_UST), "istek")
    CTX = d
    return CTX


def etkin_baglam():
    """Sunucu açıksa gerçekten çalışan bağlam, değilse bir sonraki
    başlatmada kullanılacak CTX değerini döner."""
    if _calisan_ctx and _surec_yasiyor(_pid_oku()):
        return _calisan_ctx
    return CTX


def baglam_degisti():
    """Sunucu canlıysa ve yapılandırılan CTX farklıysa True döner;
    yeni değer bir sonraki başlatmada uygulanır."""
    return bool(_calisan_ctx and (_calisan_ctx != CTX or
                (RUNPOD_MODE and (_calisan_profile != _ACTIVE_PROFILE or
                                  _calisan_signature != _runtime_signature())))
                and _surec_yasiyor(_pid_oku()))


def istek_suruyor():
    """Aktif LLM isteği var mı (kilitli üretim veya canlı ölçüm)."""
    return _kilit.locked() or _uretim["aktif"]


def _istek_gon(payload, iptal=None):
    return transport.request_json(sys.modules[__name__], payload, iptal)


def _aki_parca(satir):
    return transport.parse_stream_line(satir)


def _istek_gon_akis(payload, parca_geri=None, dusunme_geri=None, deneme=0,
                    iptal=None):
    return transport.request_stream(sys.modules[__name__], payload,
                                    text_callback=parca_geri,
                                    reasoning_callback=dusunme_geri,
                                    retry=deneme, cancel=iptal)


def _gorsel_url(g):
    return transport.image_data_url(sys.modules[__name__], g)

_kilit = threading.Lock()
_son_yukleme_ms = 0
_calisan_ctx = None       # canli sunucunun baslatildigi baglam boyutu
_son_olcum = {}
_uretim = {"aktif": False, "baslama": 0.0, "ilk_token": None,
           "ilk_zaman": None}
_slots_son = {"zaman": 0.0, "veri": None}


def _olcum_baslat():
    return metrics.start_measurement(sys.modules[__name__])


def _olcum_bitir(olcum):
    return metrics.finish_measurement(sys.modules[__name__], olcum)


def _slot_verisi():
    return metrics.slot_data(sys.modules[__name__])


def canli_olcum():
    return metrics.live_measurement(sys.modules[__name__])


def hazirla(*, gpu_hazirla=None):
    """Modeli onceden yukle. Donus: durum sozlugu."""
    global _son_yukleme_ms
    if not _kilit.acquire(blocking=False):
        raise LLMHatasi("bir üretim zaten sürüyor", "mesgul")
    try:
        sure = baslat(gpu_hazirla=gpu_hazirla)
        if sure:
            _son_yukleme_ms = sure
        _son_kullanim_guncelle()
        return {"canli": _saglik() == "ok", "yukleme_ms": sure,
                "ctx": etkin_baglam(), "ngl": GPU_KATMAN}
    finally:
        _kilit.release()


def uret(mod, metin, gorseller, dil, yaraticilik, uzunluk,
         sistem_ozel=None, mod_ozel=None, *, gpu_hazirla=None,
         model_prompt_profile=None, bbox_mode=False):
    global _active_request_cancel
    if gorseller and not MULTIMODAL_ENABLED:
        raise LLMHatasi("Görsel anlama LLM ayarlarından kapalı.", "istek")
    if not _kilit.acquire(blocking=False):
        raise LLMHatasi("bir üretim zaten sürüyor", "mesgul")
    cancel = threading.Event()
    with _active_request_cancel_lock:
        _active_request_cancel = cancel
    try:
        return _uret(mod, metin, gorseller, dil, yaraticilik, uzunluk,
                     sistem_ozel, mod_ozel, gpu_hazirla=gpu_hazirla,
                     model_prompt_profile=model_prompt_profile,
                     bbox_mode=bbox_mode, iptal=cancel)
    finally:
        with _active_request_cancel_lock:
            if _active_request_cancel is cancel:
                _active_request_cancel = None
        _kilit.release()


def sohbet_et(mesajlar, yaraticilik=0.7, max_tokens=None, parca_geri=None,
              dusunme_geri=None, stream=True, *, gpu_hazirla=None):
    """Serbest sohbet: sistem promptu + geçmiş + kullanıcı mesajı.
    mesajlar: [{"role": "user"/"assistant", "content": "..."}] listesi.
    max_tokens verilmezse modelin kendi karar vermesi için sınır gönderilmez.
    stream=False ise tek seferlik yanıt alınır (canlı parça aktarım kapalı).
    Dönüş: (yanıt_metni, ölçüm). Tek eşzamanlı kilit kullanır."""
    global _son_yukleme_ms, _active_request_cancel
    if not _kilit.acquire(blocking=False):
        raise LLMHatasi("bir üretim zaten sürüyor", "mesgul")
    cancel = threading.Event()
    with _active_request_cancel_lock:
        _active_request_cancel = cancel
    try:
        try:
            yaraticilik = max(0.2, min(1.2, float(yaraticilik)))
        except (TypeError, ValueError):
            yaraticilik = 0.7
        _son_yukleme_ms = baslat(gpu_hazirla=gpu_hazirla, iptal=cancel)
        _son_kullanim_guncelle()
        gecmis = [{"role": "system", "content": SOHBET_SISTEM_PROMPTU}]
        if not isinstance(mesajlar, list):
            mesajlar = []
        for m in mesajlar:
            try:
                if not isinstance(m, dict):
                    continue
                r = str(m.get("role") or "").strip()
                c = m.get("content")
                if c is None:
                    continue
                if isinstance(c, str):
                    c = c.strip()[:6000]
                    if not c:
                        continue
                    icerik = c
                elif isinstance(c, list):
                    parcalar = []
                    for p in c:
                        if not isinstance(p, dict):
                            continue
                        t = p.get("type")
                        if t == "text":
                            tx = str(p.get("text") or "").strip()
                            if tx:
                                parcalar.append({"type": "text", "text": tx})
                        elif t == "image_url":
                            iu = p.get("image_url") or {}
                            url = iu.get("url") or ""
                            if url:
                                parcalar.append({"type": "image_url", "image_url": {"url": url}})
                    if not parcalar:
                        continue
                    icerik = parcalar
                else:
                    continue
            except Exception:
                continue
            if r in ("user", "assistant"):
                gecmis.append({"role": r, "content": icerik})
        payload = {"messages": gecmis[-33:],
                   "temperature": yaraticilik,
                   "top_p": TOP_P,
                   "top_k": TOP_K,
                   "repeat_penalty": REPEAT_PENALTY,
                   "cache_prompt": True}
        if max_tokens is not None:
            try:
                max_tokens = max(64, int(max_tokens))
            except (TypeError, ValueError):
                max_tokens = None
            if max_tokens is not None:
                payload["max_tokens"] = max_tokens
        baslama = time.monotonic()
        _olcum_baslat()
        try:
            if stream:
                icerik, olcum = _istek_gon_akis(payload, parca_geri=parca_geri,
                                                dusunme_geri=dusunme_geri,
                                                iptal=cancel)
            else:
                icerik, olcum = _istek_gon(payload, iptal=cancel)
        except Exception:
            _olcum_bitir({})
            raise
        sure = int((time.monotonic() - baslama) * 1000)
        olcum.update({"sure_ms": sure, "yukleme_ms": _son_yukleme_ms,
                      "ctx": etkin_baglam()})
        if not olcum.get("tok_s") and olcum.get("token"):
            olcum["tok_s"] = round(olcum["token"] / max(0.1, sure / 1000), 1)
        _olcum_bitir(olcum)
        _son_kullanim_guncelle()
        return icerik, olcum
    finally:
        with _active_request_cancel_lock:
            if _active_request_cancel is cancel:
                _active_request_cancel = None
        _kilit.release()


def _son_kullanim_guncelle():
    global _son_kullanim
    _son_kullanim = time.monotonic()


def _model_prompt_talimati(profile, mod, bbox_mode=False):
    return prompting.model_prompt_instructions(sys.modules[__name__], profile,
                                               mod, bbox_mode)


def _uretim_sistem_mesaji(mod, dil, uzunluk, sistem_ozel=None,
                          mod_ozel=None, model_prompt_profile=None,
                          bbox_mode=False):
    return prompting.build_system_message(
        sys.modules[__name__], mod, dil, uzunluk, sistem_ozel=sistem_ozel,
        mod_ozel=mod_ozel, model_prompt_profile=model_prompt_profile,
        bbox_mode=bbox_mode)


def _uret(mod, metin, gorseller, dil, yaraticilik, uzunluk,
          sistem_ozel=None, mod_ozel=None, *, gpu_hazirla=None,
          model_prompt_profile=None, bbox_mode=False, iptal=None):
    global _son_kullanim
    if mod not in MOD_TALIMATI:
        raise LLMHatasi("geçersiz mod", "istek")
    if not isinstance(metin, str):
        raise LLMHatasi("metin dizge olmalı", "istek")
    metin = metin.strip()
    uyari = None
    if len(metin) > METIN_SINIR:
        uyari = "metin %d karaktere kırpıldı" % METIN_SINIR
        metin = metin[:METIN_SINIR]
    if not isinstance(gorseller, list) or len(gorseller) > 2:
        raise LLMHatasi("en fazla 2 görsel", "istek")
    if mod == "gorsel" and not gorseller:
        raise LLMHatasi("görsel modu için 1 görsel gerekli", "istek")
    if mod == "birlestir" and len(gorseller) < 2:
        raise LLMHatasi("birleştirme için 2 görsel gerekli", "istek")
    if mod in ("fikir", "ceviri", "video", "duzenle") and not metin:
        raise LLMHatasi("metin gerekli", "istek")
    try:
        yaraticilik = max(0.2, min(1.2, float(yaraticilik)))
    except (TypeError, ValueError):
        yaraticilik = 0.7
    if uzunluk not in DUAL_UZUNLUK_TOKEN:
        uzunluk = None
    if dil not in ("en", "tr"):
        dil = "en"
    if model_prompt_profile not in (None, "", "auto", "general",
                                     *MODEL_PROMPT_DOSYALARI):
        raise LLMHatasi("geçersiz prompt profili", "istek")

    siste = _uretim_sistem_mesaji(mod, dil, uzunluk, sistem_ozel, mod_ozel,
                                  model_prompt_profile, bbox_mode)

    icerik = []
    if metin:
        icerik.append({"type": "text", "text": metin})
    for g in gorseller:
        icerik.append({"type": "image_url", "image_url": {"url": _gorsel_url(g)}})

    global _son_yukleme_ms
    _son_yukleme_ms = baslat(gpu_hazirla=gpu_hazirla, iptal=iptal)
    _son_kullanim_guncelle()
    baslama = time.monotonic()
    mesajlar = [{"role": "system", "content": siste},
                {"role": "user", "content": icerik}]
    sema = {"type": "json_schema",
            "json_schema": {"name": "prompt_cikti", "schema": SEMA}}
    sonuc = None
    son_icerik = ""
    son_olcum = {}
    toplam_token = 0
    deneme_sayisi = 0
    girdi_tahmini = (len(siste) + len(metin)) // 3 + 320 * len(gorseller)
    etkin = etkin_baglam()
    tavan = max(320, etkin - girdi_tahmini - 128)
    token_limitleri = (VIDEO_UZUNLUK_TOKEN if mod == "video" else
                       DUAL_UZUNLUK_TOKEN)
    token_artis_limitleri = (VIDEO_UZUNLUK_MAX if mod == "video" else
                             DUAL_UZUNLUK_MAX)
    max_token = tavan if uzunluk is None else min(token_limitleri[uzunluk], tavan)
    for deneme in range(1, JSON_DENEME + 2):
        deneme_sayisi = deneme
        if deneme == 1:
            istek_mesajlar = mesajlar
            sicaklik = yaraticilik
        else:
            ek = []
            if son_icerik.strip():
                ek.append({"role": "assistant",
                           "content": son_icerik[:2000]})
            ek.append({"role": "user",
                       "content": "Onceki yanit gecerli JSON degildi veya "
                                  "sema eksikti. Yalnizca istenen JSON "
                                  "semasini dondur; markdown ve aciklama "
                                  "yazma."})
            istek_mesajlar = mesajlar + ek
            sicaklik = 0.1
        payload = {"messages": istek_mesajlar,
                   "temperature": sicaklik,
                   "top_p": TOP_P,
                   "top_k": TOP_K,
                   "repeat_penalty": REPEAT_PENALTY,
                   "max_tokens": max_token,
                   "cache_prompt": True,
                   "response_format": sema}
        _olcum_baslat()
        try:
            son_icerik, son_olcum = _istek_gon(payload, iptal=iptal)
        except Exception:
            _olcum_bitir({})
            raise
        toplam_token += int(son_olcum.get("token") or 0)
        ham = _json_plus(son_icerik)
        sonuc = _sema_uygun(ham) if ham else None
        if sonuc:
            break
        if (son_olcum.get("finish") == "length" and uzunluk is not None
                and max_token < tavan):
            max_token = min(tavan, token_artis_limitleri[uzunluk],
                            max_token + 400)
        time.sleep(0.15)
    if not sonuc:
        _olcum_bitir({})
        raise LLMHatasi("model %d denemede gecerli JSON uretemedi"
                        % deneme_sayisi, "model")
    sure = int((time.monotonic() - baslama) * 1000)
    olcum = {"token": toplam_token,
             "prompt_token": son_olcum.get("prompt_token"),
             "tok_s": son_olcum.get("tok_s"),
             "finish": son_olcum.get("finish"),
             "model": son_olcum.get("model"),
             "deneme": deneme_sayisi,
             "yukleme_ms": _son_yukleme_ms,
             "ctx": etkin}
    if not olcum.get("tok_s") and toplam_token:
        olcum["tok_s"] = round(toplam_token / max(0.1, sure / 1000), 1)
    cikti = _normalizasyon(sonuc, uyari, sure, olcum)
    _olcum_bitir(olcum)
    _son_kullanim_guncelle()
    return cikti


def _json_plus(text):
    return prompting.json_plus(text)


def _sema_uygun(sonuc):
    return prompting.schema_appropriate(sonuc)


def _normalizasyon(sonuc, uyari, sure_ms, olcum):
    return prompting.normalize_output(sonuc, uyari, sure_ms, olcum)

# ------------------------------------------------------------------ boşta bekçi

_son_kullanim = time.monotonic()
_bekci_durdu = threading.Event()


def _bekci_dongu():
    while not _bekci_durdu.wait(30):
        if time.monotonic() - _son_kullanim >= BOSTA_KAPANIS:
            if _kilit.acquire(blocking=False):
                try:
                    if time.monotonic() - _son_kullanim >= BOSTA_KAPANIS:
                        d = durum()
                        if d["canli"] and d["saglik"] is not None:
                            durdur()
                finally:
                    _kilit.release()


def _bekci_baslat():
    threading.Thread(target=_bekci_dongu, name="llm-bekci", daemon=True).start()


atexit.register(durdur)
_bekci_baslat()
