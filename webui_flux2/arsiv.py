"""Arşiv: kullanıcının TÜM fotoğraflarını sha256 ile tekilleştirerek listeler.

Kaynaklar: kütuphane/, gecmis/, arsiv/ (içe aktarılanlar) ve sohbetler.json
içindeki görseller. Aynı içerik (sha256) tek kayıt sayılır; ikinci kez
saklanmaz, yalnızca kaynak listesine eklenir.

Dosya bütünlüğü için sha256 kullanılır; önbellek yol+mtime+boyut ile
tazelendiğinden tekrarlı taramalarda yeniden hash hesaplanmaz.
"""

import base64
import hashlib
import io
import json
import os
import threading

from PIL import Image

_KILIT = threading.RLock()
CACHE_AD = "arsiv_cache.json"
ARSIV_DIR = "arsiv"
THUMB_DIR = "arsiv_thumb"
SOHBET_DOSYA = "sohbetler.json"
GECERLI_UZANTILAR = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")
MIME_UZANTI = {"image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg",
               "image/webp": ".webp", "image/bmp": ".bmp", "image/gif": ".gif"}
# Sunum önceliği: içe aktarılan > kütüphane > geçmiş > sohbet.
TUR_ONCELIGI = {"ice-aktarim": 0, "kutuphane": 1, "gecmis": 2, "sohbet": 3}


def _klasor_elin(d):
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass


def _cache_yol(kok):
    return os.path.join(kok, CACHE_AD)


def _cache_oku(kok):
    try:
        with open(_cache_yol(kok), "r", encoding="utf-8") as f:
            veri = json.load(f)
        if isinstance(veri, dict):
            veri.setdefault("dosyalar", {})
            veri.setdefault("sohbet", {})
            return veri
    except Exception:
        pass
    return {"dosyalar": {}, "sohbet": {}}


def _cache_yaz(kok, cache):
    try:
        _klasor_elin(kok)
        with open(_cache_yol(kok), "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    except Exception:
        pass


def _sha256_bayt(veri):
    return hashlib.sha256(veri).hexdigest()


def _sha256_dosya(yol):
    h = hashlib.sha256()
    with open(yol, "rb") as f:
        for blok in iter(lambda: f.read(1024 * 1024), b""):
            h.update(blok)
    return h.hexdigest()


def _fotograf_dosyalari(kok):
    """(yol, tur, ad, mtime, bayt) — kütüphane, geçmiş ve arşiv klasörlerinden."""
    sonuc = []
    for alt, tur in (("kutuphane", "kutuphane"), ("gecmis", "gecmis"),
                     (ARSIV_DIR, "ice-aktarim")):
        d = os.path.join(kok, alt)
        try:
            adlar = os.listdir(d)
        except Exception:
            continue
        for ad in adlar:
            kucuk = ad.lower()
            if not kucuk.endswith(GECERLI_UZANTILAR):
                continue
            if alt == "kutuphane" and ad.endswith(("_orig.png", "_thumb.jpg")):
                continue
            yol = os.path.join(d, ad)
            if not os.path.isfile(yol):
                continue
            try:
                st = os.stat(yol)
            except OSError:
                continue
            sonuc.append((yol, tur, ad, st.st_mtime, st.st_size))
    return sonuc


def _sohbet_gorselleri(kok):
    """Sohbetlerdeki görseller: [(anahtar, data_url, tarih)]."""
    yol = os.path.join(kok, SOHBET_DOSYA)
    try:
        with open(yol, "r", encoding="utf-8") as f:
            liste = json.load(f)
    except Exception:
        return []
    if not isinstance(liste, list):
        return []
    sonuc = []
    for sohbet in liste:
        if not isinstance(sohbet, dict):
            continue
        sid = str(sohbet.get("id") or "")
        tarih = sohbet.get("guncelleme") or sohbet.get("tarih")
        for i, m in enumerate(sohbet.get("mesajlar") or []):
            if not isinstance(m, dict):
                continue
            adaylar = []
            resim = m.get("resim")
            if isinstance(resim, str):
                adaylar.append(resim)
            icerik = m.get("content")
            if isinstance(icerik, list):
                for p in icerik:
                    if isinstance(p, dict) and p.get("type") == "image_url":
                        u = (p.get("image_url") or {}).get("url")
                        if isinstance(u, str):
                            adaylar.append(u)
            for u in adaylar:
                if u.startswith("data:"):
                    sonuc.append(("%s:%d" % (sid, i), u, tarih))
    return sonuc


def _data_url_veri(u):
    """data:url -> (mime, bayt) veya (None, None)."""
    try:
        bas, veri = u.split(",", 1)
        mime = (bas[5:].split(";")[0] or "image/png").lower()
        return mime, base64.b64decode(veri)
    except Exception:
        return None, None


def arsiv_indeksi(kok):
    """Tüm fotoğrafları hash'e göre gruplar.

    Dönüş: {"gruplar": {hash: kayit}, "toplam_foto": n, "toplam_benzersiz": n}
    """
    cache = _cache_oku(kok)
    gruplar = {}
    degisti = False

    def grup(ozet):
        return gruplar.setdefault(ozet, {
            "hash": ozet, "kaynaklar": [], "tarih": None, "bayt": None,
            "boyut": None, "kaynak_dosya": None, "kaynak_tur": None,
            "kaynak_anahtar": None, "ad": None})

    for yol, tur, ad, mtime, bayt in _fotograf_dosyalari(kok):
        kayit = cache["dosyalar"].get(yol)
        if not isinstance(kayit, dict) or kayit.get("mtime") != mtime \
                or kayit.get("bayt") != bayt or not kayit.get("boyut"):
            try:
                ozet = _sha256_dosya(yol)
            except Exception:
                continue
            boyut = None
            try:
                with Image.open(yol) as _im:
                    boyut = [_im.width, _im.height]
            except Exception:
                pass
            kayit = {"hash": ozet, "mtime": mtime, "bayt": bayt, "boyut": boyut}
            cache["dosyalar"][yol] = kayit
            degisti = True
        ozet = kayit.get("hash")
        if not ozet:
            continue
        g = grup(ozet)
        g["kaynaklar"].append({"tur": tur, "ad": ad})
        if g["bayt"] is None:
            g["bayt"] = bayt
        if g.get("boyut") is None and kayit.get("boyut"):
            g["boyut"] = kayit["boyut"]
        if g["kaynak_dosya"] is None or \
                TUR_ONCELIGI.get(tur, 9) < TUR_ONCELIGI.get(g["kaynak_tur"], 9):
            g["kaynak_dosya"] = yol
            g["kaynak_tur"] = tur
            g["ad"] = ad
        try:
            tarih = __import__("datetime").datetime.fromtimestamp(mtime).isoformat(timespec="seconds")
        except Exception:
            tarih = None
        if tarih and (g["tarih"] is None or tarih < g["tarih"]):
            g["tarih"] = tarih

    for anahtar, u, tarih in _sohbet_gorselleri(kok):
        kayit = cache["sohbet"].get(anahtar)
        if not isinstance(kayit, dict) or kayit.get("uzunluk") != len(u) \
                or not kayit.get("boyut"):
            mime, veri = _data_url_veri(u)
            if not veri:
                continue
            boyut = None
            try:
                with Image.open(io.BytesIO(veri)) as _im:
                    boyut = [_im.width, _im.height]
            except Exception:
                pass
            kayit = {"hash": _sha256_bayt(veri), "uzunluk": len(u), "mime": mime,
                     "boyut": boyut}
            cache["sohbet"][anahtar] = kayit
            degisti = True
        ozet = kayit.get("hash")
        if not ozet:
            continue
        g = grup(ozet)
        g["kaynaklar"].append({"tur": "sohbet", "ad": anahtar})
        if g.get("boyut") is None and kayit.get("boyut"):
            g["boyut"] = kayit["boyut"]
        if g["kaynak_dosya"] is None:
            g["kaynak_dosya"] = None
            g["kaynak_tur"] = "sohbet"
            g["kaynak_anahtar"] = anahtar
            g["ad"] = "sohbet-" + anahtar.replace(":", "-")
        if tarih and (g["tarih"] is None or tarih < g["tarih"]):
            g["tarih"] = tarih

    if degisti:
        _cache_yaz(kok, cache)

    for g in gruplar.values():
        g["kaynak_sayisi"] = len(g["kaynaklar"])
        g["tur"] = ("video" if (g.get("ad") or "").lower().endswith(".mp4")
                    else "goruntu")
    return {"gruplar": gruplar,
            "toplam_foto": sum(g["kaynak_sayisi"] for g in gruplar.values()),
            "toplam_benzersiz": len(gruplar)}


def arsiv_listesi(kok, sayfa=1, adet=24, arama=""):
    indeks = arsiv_indeksi(kok)
    q = str(arama or "").strip().casefold()
    liste = []
    for g in indeks["gruplar"].values():
        if q:
            alan = " ".join([str(g.get("ad") or ""),
                             " ".join(str(k.get("ad") or "") for k in g["kaynaklar"]),
                             str(g.get("tarih") or "")]).casefold()
            if q not in alan:
                continue
        liste.append(g)
    liste.sort(key=lambda g: (g.get("tarih") or ""), reverse=True)
    toplam = len(liste)
    sayfa = max(1, int(sayfa or 1))
    adet = max(1, min(200, int(adet or 24)))
    bas = (sayfa - 1) * adet
    dilim = liste[bas:bas + adet]
    kayitlar = []
    for g in dilim:
        kayitlar.append({
            "hash": g["hash"], "ad": g.get("ad"), "tarih": g.get("tarih"),
            "bayt": g.get("bayt"), "boyut": g.get("boyut"),
            "kaynak_sayisi": g.get("kaynak_sayisi"),
            "kaynaklar": g.get("kaynaklar"),
            "tur": g.get("tur"),
            "url": "/api/arsiv/gorsel/%s" % g["hash"],
            "thumb": "/api/arsiv/thumb/%s" % g["hash"],
        })
    return {"toplam": toplam, "sayfa": sayfa, "adet": adet,
            "benzersiz": indeks["toplam_benzersiz"],
            "toplam_foto": indeks["toplam_foto"], "kayitlar": kayitlar}


def _grup_bul(kok, ozet):
    indeks = arsiv_indeksi(kok)
    return indeks["gruplar"].get(ozet)


def arsiv_verisi(kok, ozet):
    """Hash'e ait fotoğraf baytlarını döndürür (mime, bayt) veya None."""
    g = _grup_bul(kok, ozet)
    if not g:
        return None
    if g.get("kaynak_dosya"):
        try:
            with open(g["kaynak_dosya"], "rb") as f:
                veri = f.read()
        except OSError:
            return None
        uzanti = os.path.splitext(g["kaynak_dosya"])[1].lower()
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "webp": "image/webp", "gif": "image/gif",
                "bmp": "image/bmp"}.get(uzanti.lstrip("."), "image/png")
        return mime, veri
    if g.get("kaynak_tur") == "sohbet" and g.get("kaynak_anahtar"):
        for anahtar, u, _t in _sohbet_gorselleri(kok):
            if anahtar == g["kaynak_anahtar"]:
                mime, veri = _data_url_veri(u)
                if veri:
                    return mime or "image/png", veri
    return None


def arsiv_thumb(kok, ozet):
    """256px JPEG küçük resim yolu; yoksa üretir. Üretemezse None."""
    d = os.path.join(kok, THUMB_DIR)
    yol = os.path.join(d, ozet + ".jpg")
    if os.path.isfile(yol):
        return yol
    veri = arsiv_verisi(kok, ozet)
    if not veri:
        return None
    try:
        _klasor_elin(d)
        with Image.open(io.BytesIO(veri[1])) as im:
            im.thumbnail((512, 512), Image.LANCZOS)
            im.convert("RGB").save(yol, format="JPEG", quality=85)
        return yol
    except Exception:
        return None


def arsiv_ice_aktar(kullanici_kok, gorseller):
    """Görselleri içe aktarır. Aynı hash varsa yeniden saklamaz.

    gorseller: [{"mime": ..., "veri": base64}] veya [{"bayt": bytes}]
    Dönüş: {"eklenen": n, "tekrar": n, "hata": [...], "kayitlar": [...]}
    """
    indeks = arsiv_indeksi(kullanici_kok)
    mevcut = set(indeks["gruplar"].keys())
    d = os.path.join(kullanici_kok, ARSIV_DIR)
    _klasor_elin(d)
    eklenen = 0
    tekrar = 0
    hatalar = []
    kayitlar = []
    for g in gorseller or []:
        try:
            if isinstance(g, dict) and g.get("bayt") is not None:
                veri = g["bayt"]
                mime = str(g.get("mime") or "image/png").lower()
            else:
                mime, veri = _data_url_veri(
                    "data:%s;base64,%s" % (g.get("mime") or "image/png",
                                           g.get("veri") or ""))
            if not veri:
                hatalar.append("görsel çözümlenemedi")
                continue
            if len(veri) > 60 * 1024 * 1024:
                hatalar.append("görsel çok büyük (60 MB üstü)")
                continue
            # Geçerli bir görsel mi? (bozuk dosyayı arşive alma)
            try:
                with Image.open(io.BytesIO(veri)) as im:
                    im.verify()
            except Exception:
                hatalar.append("geçerli bir görsel değil")
                continue
            ozet = _sha256_bayt(veri)
            if ozet in mevcut:
                tekrar += 1
                kayitlar.append({"hash": ozet, "durum": "tekrar"})
                continue
            uzanti = MIME_UZANTI.get(mime) or os.path.splitext(
                str(g.get("ad") or ""))[1].lower() or ".png"
            if uzanti not in GECERLI_UZANTILAR:
                uzanti = ".png"
            yol = os.path.join(d, ozet + uzanti)
            if os.path.isfile(yol):
                tekrar += 1
                kayitlar.append({"hash": ozet, "durum": "tekrar"})
                mevcut.add(ozet)
                continue
            with open(yol, "wb") as f:
                f.write(veri)
            mevcut.add(ozet)
            eklenen += 1
            kayitlar.append({"hash": ozet, "durum": "eklendi"})
        except Exception as e:
            hatalar.append(str(e)[:200])
    return {"eklenen": eklenen, "tekrar": tekrar, "hata": hatalar,
            "kayitlar": kayitlar}


def arsiv_sil(kok, ozet):
    """Yalnızca içe aktarılan kopyayı siler; kütüphane/geçmiş kayıtlarına dokunmaz."""
    d = os.path.join(kok, ARSIV_DIR)
    silindi = False
    try:
        for ad in os.listdir(d):
            if not ad.startswith(ozet + "."):
                continue
            yol = os.path.abspath(os.path.join(d, ad))
            if not yol.startswith(os.path.abspath(d) + os.sep):
                continue
            if os.path.isfile(yol):
                os.remove(yol)
                silindi = True
    except Exception:
        pass
    try:
        t = os.path.join(kok, THUMB_DIR, ozet + ".jpg")
        if os.path.isfile(t):
            os.remove(t)
    except Exception:
        pass
    return silindi