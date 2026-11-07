"""Local Turkish-to-English prompt translation with Helsinki-NLP OPUS-MT."""
import os
import re
import site
import sys
import threading
import time
from collections import OrderedDict

MODEL_NAME = "Helsinki-NLP/opus-mt-tr-en"
MODEL_REVISION = "19c65427cc2af5f191337d4899e0348c4af25902"
MODEL_DIR = os.environ.get("WEBUI_TRANSLATION_MODEL_DIR", os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "models", "opus_mt_tr_en"))
MODEL_WEIGHT = os.path.join(MODEL_DIR, "pytorch_model.bin")
MODEL_WEIGHT_SIZE = 306727185
MAX_SOURCE_CHARS = 24000
MAX_SOURCE_TOKENS = 8192
_BATCH_SIZE = 4
_CHUNK_TOKENS = 112
_MAX_CACHE_ENTRIES = 128
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_BOUNDARY = re.compile(r"(?<=[.!?;])\s+|\n+")
_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)
_CACHE = OrderedDict()
_CACHE_LOCK = threading.Lock()
_MODEL_LOCK = threading.Lock()
_INFERENCE_LOCK = threading.Lock()
_MODEL = None
_WARMUP_STARTED = False
_WARMUP_LOCK = threading.Lock()


def _model_al():
    global _MODEL
    with _MODEL_LOCK:
        if _MODEL is not None:
            return _MODEL
        if not os.path.isfile(MODEL_WEIGHT):
            raise RuntimeError(
                "Çeviri modeli kurulu değil; install_prompt_translation_model.py çalıştırılmalı")
        if os.path.getsize(MODEL_WEIGHT) != MODEL_WEIGHT_SIZE:
            raise RuntimeError("Çeviri modeli dosya boyutu doğrulanamadı")
        if os.environ.get("WEBUI_RUNPOD") == "1":
            python = f"python{sys.version_info.major}.{sys.version_info.minor}"
            packages = os.path.join(os.environ["POD_RUNTIME_ROOT"], "comfy-venv",
                                    "lib", python, "site-packages")
            if not os.path.isdir(packages):
                raise RuntimeError("Çeviri bağımlılıkları kurulu değil")
            site.addsitedir(packages)
        import torch
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        # Sınırlı CPU iş parçacığı GPU kuyruğu ve HTTP isteklerine yer bırakır.
        torch.set_num_threads(min(4, os.cpu_count() or 1))
        tokenizer = AutoTokenizer.from_pretrained(MODEL_DIR, local_files_only=True)
        model = AutoModelForSeq2SeqLM.from_pretrained(
            MODEL_DIR, local_files_only=True).eval()
        _MODEL = torch, tokenizer, model
        return _MODEL


def modeli_bosalt():
    global _MODEL
    with _MODEL_LOCK, _CACHE_LOCK:
        _MODEL = None
        _CACHE.clear()


def onisit():
    """HTTP başlangıcını bekletmeden yerel çeviri bağımlılıklarını hazırla."""
    global _WARMUP_STARTED

    def arka_planda():
        try:
            _model_al()
        except Exception as hata:
            # İlk gerçek istekte tekrar denenecek ve hata orada gösterilecek.
            print("Çeviri ön yükleme tamamlanamadı:", hata, flush=True)

    with _WARMUP_LOCK:
        if not _WARMUP_STARTED:
            threading.Thread(target=arka_planda, name="ceviri-onisit",
                             daemon=True).start()
            _WARMUP_STARTED = True


def _parcala(tokenizer, metin):
    """Cümle sınırını, çok uzun cümlede ise kelime sınırını tercih et."""
    bekleyen = []
    for cumle in _BOUNDARY.split(metin):
        tokenler = tokenizer.encode(cumle.strip(), add_special_tokens=False,
                                    verbose=False)
        if not tokenler:
            continue
        if len(bekleyen) + len(tokenler) > _CHUNK_TOKENS and bekleyen:
            yield bekleyen
            bekleyen = []
        while len(tokenler) > _CHUNK_TOKENS:
            tavan = _CHUNK_TOKENS
            adaylar = tokenizer.convert_ids_to_tokens(tokenler[tavan - 24:tavan + 1])
            for fark in range(len(adaylar) - 1, -1, -1):
                if adaylar[fark].startswith("▁"):
                    tavan = tavan - 24 + fark
                    break
            yield tokenler[:tavan]
            tokenler = tokenler[tavan:]
        bekleyen.extend(tokenler)
    if bekleyen:
        yield bekleyen


def _iptali_dogrula(iptal):
    if iptal is not None and iptal():
        raise RuntimeError("Çeviri durduruldu")


def _ceviri_uret(torch, tokenizer, model, parcalar, iptal):
    cevaplar = {}
    # Yakın boydaki parçaları birlikte işleyerek dolgu ve decoder beklemesini azalt.
    sirali = sorted(enumerate(parcalar), key=lambda oge: len(oge[1]))
    def durdurma_olcutu(_ids, _scores, **_kwargs):
        return iptal()

    with torch.inference_mode():
        for bas in range(0, len(sirali), _BATCH_SIZE):
            _iptali_dogrula(iptal)
            grup = sirali[bas:bas + _BATCH_SIZE]
            girdiler = tokenizer.pad(
                {"input_ids": [tokenler + [tokenizer.eos_token_id]
                               for _, tokenler in grup]},
                return_tensors="pt", return_attention_mask=True)
            tavan = min(512, max(64, 2 * len(grup[-1][1]) + 24))
            olcutler = [durdurma_olcutu] if iptal is not None else None
            cikti = model.generate(
                **girdiler, num_beams=2 if len(parcalar) > 1 else 4,
                max_length=tavan, early_stopping=True, stopping_criteria=olcutler)
            _iptali_dogrula(iptal)
            # Sınıra ulaşan uzun bir çeviri varsa decoder'a daha fazla alan tanı.
            if cikti.shape[-1] == tavan and tavan < 512:
                cikti = model.generate(
                    **girdiler, num_beams=2, max_length=512, early_stopping=True,
                    stopping_criteria=olcutler)
                _iptali_dogrula(iptal)
                if cikti.shape[-1] == 512:
                    raise RuntimeError("Çeviri modeli bir metin parçasını tamamlayamadı")
            for (indeks, _), metin in zip(grup, tokenizer.batch_decode(
                    cikti, skip_special_tokens=True)):
                if not metin.strip():
                    raise RuntimeError("Çeviri modeli boş sonuç döndürdü")
                cevaplar[indeks] = metin.strip()
    return [cevaplar[i] for i in range(len(parcalar))]


def cevir(metin, kullanici="", iptal=None):
    if not isinstance(metin, str) or not metin.strip():
        raise ValueError("Çevrilecek Türkçe prompt boş olamaz")
    metin = metin.strip()
    if len(metin) > MAX_SOURCE_CHARS:
        raise ValueError("Çeviri metni en fazla %d karakter olabilir" % MAX_SOURCE_CHARS)
    _iptali_dogrula(iptal)
    cache_key = (str(kullanici), metin)
    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
        if cached is not None:
            _CACHE.move_to_end(cache_key)
            return dict(cached, sure_ms=0, onbellek=True)

    baslangic = time.perf_counter()
    parcalar = re.split(r"(" + _NUMBER.pattern + r")", metin)
    cevrilecekler = [(i, parca) for i, parca in enumerate(parcalar)
                    if _LETTER.search(parca)]
    if cevrilecekler:
        torch, tokenizer, model = _model_al()
        with _INFERENCE_LOCK:
            _iptali_dogrula(iptal)
            gruplar = [(i, parca, list(_parcala(tokenizer, parca)))
                       for i, parca in cevrilecekler]
            token_parcalari = [tokenler for _, _, grup in gruplar for tokenler in grup]
            if sum(map(len, token_parcalari)) > MAX_SOURCE_TOKENS:
                raise ValueError("Çeviri metni en fazla %d alt-token olabilir"
                                 % MAX_SOURCE_TOKENS)
            cevaplar = iter(_ceviri_uret(torch, tokenizer, model, token_parcalari, iptal))
            for i, parca, grup in gruplar:
                bas = parca[:len(parca) - len(parca.lstrip())]
                son = parca[len(parca.rstrip()):]
                parcalar[i] = bas + " ".join(next(cevaplar) for _ in grup) + son

    ceviri = "".join(parcalar).strip()
    if _NUMBER.findall(ceviri) != _NUMBER.findall(metin):
        raise RuntimeError("Çeviride sayısal değerler korunamadı")
    sonuc = {"metin": ceviri, "sure_ms": round((time.perf_counter() - baslangic) * 1000),
             "model": MODEL_NAME, "cihaz": "cpu"}
    with _CACHE_LOCK:
        _iptali_dogrula(iptal)
        _CACHE[cache_key] = dict(sonuc)
        _CACHE.move_to_end(cache_key)
        while len(_CACHE) > _MAX_CACHE_ENTRIES:
            _CACHE.popitem(last=False)
    return dict(sonuc, onbellek=False)


def onbellek_temizle(kullanici):
    with _CACHE_LOCK:
        for key in [key for key in _CACHE if key[0] == str(kullanici)]:
            del _CACHE[key]
