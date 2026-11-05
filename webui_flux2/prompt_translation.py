"""Local Turkish-to-English prompt translation with Helsinki-NLP OPUS-MT."""
import gc
import os
import re
import threading
import time
from collections import OrderedDict

import torch
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer


MODEL_NAME = "Helsinki-NLP/opus-mt-tr-en"
MODEL_REVISION = "19c65427cc2af5f191337d4899e0348c4af25902"
MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "models", "opus_mt_tr_en")
MODEL_WEIGHT = os.path.join(MODEL_DIR, "pytorch_model.bin")
MODEL_WEIGHT_SIZE = 306727185
MAX_SOURCE_TOKENS = 256
MAX_CACHE_ENTRIES = 128
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_CACHE = OrderedDict()
_CACHE_LOCK = threading.Lock()


def cevir(metin, kullanici=""):
    if not isinstance(metin, str) or not metin.strip():
        raise ValueError("Çevrilecek Türkçe prompt boş olamaz")
    metin = metin.strip()
    cache_key = (str(kullanici), metin)
    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
        if cached is not None:
            _CACHE.move_to_end(cache_key)
            return dict(cached, sure_ms=0, onbellek=True)
    parts = re.split(r"(" + _NUMBER.pattern + r")", metin)
    numbers = _NUMBER.findall(metin)
    segments = {}
    for index, part in enumerate(parts):
        if not part or _NUMBER.fullmatch(part):
            continue
        prefix = part[:len(part) - len(part.lstrip())]
        suffix = part[len(part.rstrip()):]
        content = part.strip()
        if content and re.search(r"[^\W\d_]", content, re.UNICODE):
            segments[index] = (prefix, content, suffix)
    if not segments:
        sonuc = {"metin": metin, "sure_ms": 0, "model": MODEL_NAME,
                 "cihaz": "cpu"}
        with _CACHE_LOCK:
            _CACHE[cache_key] = dict(sonuc)
            _CACHE.move_to_end(cache_key)
            while len(_CACHE) > MAX_CACHE_ENTRIES:
                _CACHE.popitem(last=False)
        return dict(sonuc, onbellek=False)
    if not os.path.isfile(MODEL_WEIGHT):
        raise RuntimeError(
            "Çeviri modeli kurulu değil; install_prompt_translation_model.py çalıştırılmalı")
    if os.path.getsize(MODEL_WEIGHT) != MODEL_WEIGHT_SIZE:
        raise RuntimeError("Çeviri modeli dosya boyutu doğrulanamadı")

    baslangic = time.perf_counter()
    tokenizer = None
    model = None
    girdiler = None
    cikti = None
    ceviriler = []
    try:
        tokenizer = AutoTokenizer.from_pretrained(MODEL_DIR, local_files_only=True)
        kaynak = tokenizer(metin, return_tensors="pt", truncation=False)
        token_sayisi = int(kaynak["input_ids"].shape[1])
        del kaynak
        if token_sayisi > MAX_SOURCE_TOKENS:
            raise ValueError(
                "Çeviri için prompt en fazla %d alt-token olabilir (mevcut: %d)"
                % (MAX_SOURCE_TOKENS, token_sayisi))
        model = AutoModelForSeq2SeqLM.from_pretrained(
            MODEL_DIR, local_files_only=True).eval()
        girdiler = tokenizer([segment[1] for segment in segments.values()],
                             return_tensors="pt", padding=True,
                             truncation=False)
        cikti_tavan = min(256, max(64, token_sayisi * 2 + 16))
        with torch.inference_mode():
            cikti = model.generate(
                **girdiler, num_beams=4, max_length=cikti_tavan,
                early_stopping=True)
        ceviriler = [tokenizer.decode(row, skip_special_tokens=True).strip()
                     for row in cikti]
        ceviri_parcalari = iter(ceviriler)
        translated = []
        for index, part in enumerate(parts):
            if _NUMBER.fullmatch(part):
                translated.append(part)
            elif index in segments:
                prefix, _, suffix = segments[index]
                translated.append(prefix + next(ceviri_parcalari) + suffix)
            else:
                translated.append(part)
        ceviri = "".join(translated).strip()
        if _NUMBER.findall(ceviri) != numbers:
            raise RuntimeError("Çeviride sayısal değerler korunamadı")
        if not ceviri:
            raise RuntimeError("Çeviri modeli boş sonuç döndürdü")
    finally:
        del model, cikti, girdiler, tokenizer, ceviriler
        gc.collect()

    sure = time.perf_counter() - baslangic
    sonuc = {"metin": ceviri, "sure_ms": round(sure * 1000),
             "model": MODEL_NAME, "cihaz": "cpu"}
    with _CACHE_LOCK:
        _CACHE[cache_key] = dict(sonuc)
        _CACHE.move_to_end(cache_key)
        while len(_CACHE) > MAX_CACHE_ENTRIES:
            _CACHE.popitem(last=False)
    return dict(sonuc, onbellek=False)


def onbellek_temizle(kullanici):
    with _CACHE_LOCK:
        for key in [key for key in _CACHE if key[0] == str(kullanici)]:
            del _CACHE[key]
