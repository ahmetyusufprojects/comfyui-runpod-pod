"""Prompt history persistence. Callers resolve the vault path."""
import hashlib
import json


def kayit_kimlik(kayit):
    kimlik = kayit.get("id")
    if kimlik:
        return str(kimlik)
    metin = str(kayit.get("tarih") or "") + str(kayit.get("positive") or "")
    return "e" + hashlib.sha1(metin.encode("utf-8")).hexdigest()[:10]


def oku(yol, limit=50, arama="", mod=""):
    try:
        with open(yol, "r", encoding="utf-8") as f:
            veri = json.load(f)
    except Exception:
        return []
    if not isinstance(veri, list):
        return []
    if mod:
        veri = [k for k in veri if k.get("mod") == mod]
    arama = (arama or "").strip().lower()
    if arama:
        veri = [k for k in veri if arama in " ".join(
            str(k.get(alan) or "") for alan in
            ("metin", "positive", "positive_tr", "caption_tr", "negative")
        ).lower() or arama in " ".join(
            str(t) for t in (k.get("tags") or [])).lower()]
    sonuc = []
    for k in veri[-max(1, min(200, int(limit))):][::-1]:
        k = dict(k)
        k["id"] = kayit_kimlik(k)
        sonuc.append(k)
    return sonuc


def sil(yol, kimlik):
    try:
        with open(yol, "r", encoding="utf-8") as f:
            veri = json.load(f)
        if not isinstance(veri, list):
            return 0
        yeni = [k for k in veri if kayit_kimlik(k) != str(kimlik)]
        with open(yol, "w", encoding="utf-8") as f:
            json.dump(yeni, f, ensure_ascii=False, indent=2)
        return len(veri) - len(yeni)
    except Exception:
        return 0


def yildizla(yol, kimlik, deger):
    """Kaydin yildiz bayragini degistirir. Donus: degistiyse True."""
    try:
        with open(yol, "r", encoding="utf-8") as f:
            veri = json.load(f)
        if not isinstance(veri, list):
            return False
        degisti = False
        for k in veri:
            if kayit_kimlik(k) == str(kimlik):
                k["yildiz"] = bool(deger)
                degisti = True
        if degisti:
            with open(yol, "w", encoding="utf-8") as f:
                json.dump(veri, f, ensure_ascii=False, indent=2)
        return degisti
    except Exception:
        return False


def temizle(yol):
    try:
        with open(yol, "w", encoding="utf-8") as f:
            json.dump([], f)
        return True
    except Exception:
        return False


def yaz(yol, kayit):
    try:
        with open(yol, "r", encoding="utf-8") as f:
            veri = json.load(f)
            if not isinstance(veri, list):
                veri = []
    except Exception:
        veri = []
    veri.append(kayit)
    veri = veri[-200:]
    with open(yol, "w", encoding="utf-8") as f:
        json.dump(veri, f, ensure_ascii=False, indent=2)
