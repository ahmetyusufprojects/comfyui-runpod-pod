"""User-scoped library, prompt/chat history and generated media records.

All filesystem locations, vault access and application callbacks come from the
explicit runtime namespace supplied by the caller. This module never imports
``server`` and can be exercised with isolated synthetic storage.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import urllib.parse
import uuid


def kutuphaneye_kaydet(runtime, kullanici, orijinal_blob, hazir_bytes, w1, h1):
    """Yuklenen fotoyu SADECE bu kullanicinin kutuphane/ altina saklar
    (hedef + orijinal + thumb). Ayni icerik varsa yeniden kaydetmez.
    Donus: dosya adi (hedef) veya None. Cakismamasi icin uuid kuyrugu vardir."""
    try:
        kutuphane_dir = runtime._kullanici_kutuphane(kullanici)
        runtime._klasor_elin(kutuphane_dir)
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
        taban = "%s_%s" % (runtime._simdiki_zaman_damgasi(), uuid.uuid4().hex[:6])
        hedef_ad = taban + ".png"
        with open(os.path.join(kutuphane_dir, hedef_ad), "wb") as f:
            f.write(hazir_bytes)
        try:
            from PIL import Image as _PILImage
            import io as _bio

            with _PILImage.open(_bio.BytesIO(orijinal_blob)) as _im:
                runtime._exif_duzelt(_im).convert("RGB").save(
                    os.path.join(kutuphane_dir, taban + "_orig.png"), format="PNG"
                )
            with _PILImage.open(_bio.BytesIO(hazir_bytes)) as _im:
                _im.thumbnail((512, 512), _PILImage.LANCZOS)
                _im.convert("RGB").save(
                    os.path.join(kutuphane_dir, taban + "_thumb.jpg"),
                    format="JPEG",
                    quality=85,
                )
        except Exception:
            pass
        return hedef_ad
    except Exception:
        return None


def kutuphane_listesi(runtime, kullanici):
    kutuphane_dir = runtime._kullanici_kutuphane(kullanici)
    runtime._klasor_elin(kutuphane_dir)
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
            boyut, mod, bicim = (None, None, None)
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
            kayitlar.append(
                (
                    st.st_mtime,
                    {
                        "ad": ad,
                        "boyut": boyut,
                        "mod": mod,
                        "bicim": bicim,
                        "bayt": st.st_size,
                        "orijinal": orijinal,
                        "tarih": _dt.datetime.fromtimestamp(st.st_mtime).isoformat(
                            timespec="seconds"
                        ),
                        "thumb": "/kutuphane/%s?thumb=1" % urllib.parse.quote(ad),
                        "url": "/kutuphane/%s" % urllib.parse.quote(ad),
                    },
                )
            )
    except Exception:
        pass
    kayitlar.sort(key=lambda k: k[0], reverse=True)
    return [k[1] for k in kayitlar]


def kutuphaneden_sil(runtime, kullanici, ad):
    ad = runtime._guvenli_dosya_adi(ad)
    if not ad:
        return False
    kutuphane_dir = runtime._kullanici_kutuphane(kullanici)
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


def kutuphane_temizle(runtime, kullanici):
    """Bu kullanicinin kutuphanesindeki tum yonetilen dosyalari siler."""
    kutuphane_dir = runtime._kullanici_kutuphane(kullanici)
    runtime._klasor_elin(kutuphane_dir)
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


def promptlari_oku(runtime, kullanici):
    try:
        with open(runtime._kullanici_promptlar(kullanici), "r", encoding="utf-8") as f:
            veri = json.load(f)
            if isinstance(veri, list):
                return veri
    except Exception:
        pass
    return []


def prompt_kaydet(runtime, kullanici, kayit):
    liste = promptlari_oku(runtime, kullanici)
    ad = (kayit.get("ad") or "").strip()
    liste = [k for k in liste if (k.get("ad") or "") != ad]
    liste.append(kayit)
    runtime._klasor_elin(runtime._kullanici_koku(kullanici))
    with open(runtime._kullanici_promptlar(kullanici), "w", encoding="utf-8") as f:
        json.dump(liste, f, ensure_ascii=False, indent=2)
    return liste


def prompt_gecmisi_dosya(runtime, kullanici):
    try:
        kok = os.path.abspath(runtime.kasa.calisma_koku(kullanici))
    except Exception:
        try:
            kok = os.path.abspath(runtime._ham_kullanici_koku(kullanici))
        except Exception:
            kok = None
    if not kok:
        raise ValueError("kasa kilitli")
    return os.path.join(kok, "prompt_gecmisi.json")


def prompt_gecmisi_oku(runtime, kullanici, limit=50, arama="", mod=""):
    try:
        yol = prompt_gecmisi_dosya(runtime, kullanici)
    except Exception:
        return []
    return runtime.prompt_store.oku(yol, limit, arama, mod)


def prompt_gecmisi_sil(runtime, kullanici, kimlik):
    return runtime.prompt_store.sil(prompt_gecmisi_dosya(runtime, kullanici), kimlik)


def prompt_gecmisi_yildiz(runtime, kullanici, kimlik, deger):
    return runtime.prompt_store.yildizla(
        prompt_gecmisi_dosya(runtime, kullanici), kimlik, deger
    )


def prompt_gecmisi_temizle(runtime, kullanici):
    return runtime.prompt_store.temizle(prompt_gecmisi_dosya(runtime, kullanici))


def _sohbet_dosya(runtime, kullanici):
    try:
        kok = os.path.abspath(runtime.kasa.calisma_koku(kullanici))
    except Exception:
        raise ValueError("kasa kilitli")
    return os.path.join(kok, "sohbetler.json")


def _sohbet_oku(runtime, kullanici):
    try:
        with open(_sohbet_dosya(runtime, kullanici), "r", encoding="utf-8") as f:
            veri = json.load(f)
    except Exception:
        return []
    if not isinstance(veri, list):
        return []
    return sorted(
        veri, key=lambda k: k.get("guncelleme") or k.get("tarih") or "", reverse=True
    )


def _sohbet_yaz(runtime, kullanici, kayit):
    yol = _sohbet_dosya(runtime, kullanici)
    runtime._klasor_elin(runtime._kullanici_koku(kullanici))
    with runtime._sohbet_kilit:
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


def _sohbet_sil(runtime, kullanici, kimlik):
    with runtime._sohbet_kilit:
        try:
            with open(_sohbet_dosya(runtime, kullanici), "r", encoding="utf-8") as f:
                liste = json.load(f)
            if not isinstance(liste, list):
                return 0
        except Exception:
            return 0
        yeni = [k for k in liste if k.get("id") != kimlik]
        if len(yeni) == len(liste):
            return 0
        with open(_sohbet_dosya(runtime, kullanici), "w", encoding="utf-8") as f:
            json.dump(yeni, f, ensure_ascii=False, indent=2)
    return len(liste) - len(yeni)


def _sohbet_temizle(runtime, kullanici):
    with runtime._sohbet_kilit:
        try:
            with open(_sohbet_dosya(runtime, kullanici), "w", encoding="utf-8") as f:
                json.dump([], f, ensure_ascii=False, indent=2)
        except Exception:
            return False
    return True


def _sohbet_baslik(runtime, mesajlar):
    for m in mesajlar or []:
        if isinstance(m, dict) and str(m.get("role") or "") == "user":
            metin = str(m.get("content") or "").strip()
            if metin:
                return metin[:60]
    return "Sohbet"


def _sohbet_kaydet(
    runtime, kullanici, mesajlar, yanit, olcum, baslik=None, sohbet_id=None
):
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
        "baslik": baslik or _sohbet_baslik(runtime, mesajlar),
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
    _sohbet_yaz(runtime, kullanici, kayit)
    return sohbet_id


def prompt_gecmisi_yaz(runtime, kullanici, kayit):
    yol = prompt_gecmisi_dosya(runtime, kullanici)
    runtime._klasor_elin(runtime._kullanici_koku(kullanici))
    runtime.prompt_store.yaz(yol, kayit)


def prompt_sil(runtime, kullanici, ad):
    liste = promptlari_oku(runtime, kullanici)
    yeni = [k for k in liste if (k.get("ad") or "") != ad]
    runtime._klasor_elin(runtime._kullanici_koku(kullanici))
    with open(runtime._kullanici_promptlar(kullanici), "w", encoding="utf-8") as f:
        json.dump(yeni, f, ensure_ascii=False, indent=2)
    return len(yeni) != len(liste)


def _video_kapak_uret(runtime, blob):
    """Return a small JPEG thumbnail for a generated video on RunPod."""
    if not runtime.RUNPOD_MODE or not blob:
        return None
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                "pipe:0",
                "-ss",
                "0.05",
                "-frames:v",
                "1",
                "-vf",
                "scale=480:-2",
                "-f",
                "image2pipe",
                "-vcodec",
                "mjpeg",
                "pipe:1",
            ],
            input=blob,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
        return (
            result.stdout
            if result.returncode == 0 and result.stdout.startswith(b"\xff\xd8")
            else None
        )
    except (OSError, subprocess.SubprocessError):
        return None


def gecmise_kaydet(
    runtime, kullanici, blob_listesi, meta, video=False, poster_blob=None
):
    """Her uretimi BU kullanicinin gecmis/ altina yazar: goruntu PNG, video MP4.
    Ayni taban adli .json meta ile birlikte; budama uygulanir."""
    gecmis_dir = runtime._kullanici_gecmis(kullanici)
    runtime._klasor_elin(gecmis_dir)
    uzanti = ".mp4" if video else ".png"
    taban = "%s_seed%s_%s" % (
        runtime._simdiki_zaman_damgasi(),
        meta.get("seed", "x"),
        uuid.uuid4().hex[:6],
    )
    dosyalar = []
    if len(blob_listesi) == 1:
        dosyalar = [taban + uzanti]
    else:
        dosyalar = ["%s_%d%s" % (taban, i, uzanti) for i in range(len(blob_listesi))]
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
    meta.update(
        {
            "dosyalar": dosyalar,
            "poster_file": poster_file,
            "dosya_bayt": baytlar,
            "tarih": _dt.datetime.now().isoformat(timespec="seconds"),
        }
    )
    with runtime._GECMIS_KILIT:
        with open(
            os.path.join(gecmis_dir, taban + ".json"), "w", encoding="utf-8"
        ) as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    _gecmis_buda(runtime, kullanici)
    return (taban, dosyalar)


def _gecmis_buda(runtime, kullanici):
    lim = runtime._gecmis_limiti(kullanici)
    if lim <= 0:
        return
    gecmis_dir = runtime._kullanici_gecmis(kullanici)
    try:
        jsonlar = sorted((a for a in os.listdir(gecmis_dir) if a.endswith(".json")))
        while len(jsonlar) > lim:
            eski = jsonlar.pop(0)
            try:
                with open(os.path.join(gecmis_dir, eski), "r", encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception:
                meta = {}
            for aday in (
                [eski] + list(meta.get("dosyalar") or []) + [meta.get("poster_file")]
            ):
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


def gecmis_kaydi_sil(runtime, kullanici, kayit):
    """Tek gecmis kaydini (.json + png'leri) siler."""
    kayit = runtime._guvenli_dosya_adi(kayit)
    if not kayit or not kayit.endswith(".json"):
        return False
    gecmis_dir = runtime._kullanici_gecmis(kullanici)
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
    for aday in [kayit] + list(meta.get("dosyalar") or []) + [meta.get("poster_file")]:
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


def gecmis_temizle(runtime, kullanici):
    """Bu kullanicinin gecmisindeki tum kayitlari siler. Donus: silinen dosya adedi."""
    gecmis_dir = runtime._kullanici_gecmis(kullanici)
    runtime._klasor_elin(gecmis_dir)
    kok = os.path.abspath(gecmis_dir) + os.sep
    n = 0
    try:
        for ad in os.listdir(gecmis_dir):
            if not (
                ad.lower().endswith((".png", ".mp4", ".json"))
                or ad.lower().endswith("_poster.jpg")
            ):
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


def _gecmis_album_id(runtime, meta):
    if not isinstance(meta, dict) or not meta.get("prompt"):
        return None
    dosyalar = meta.get("dosyalar") or []
    if any(
        (
            str(dosya).lower().endswith((".mp4", ".webm", ".mov", ".m4v"))
            for dosya in dosyalar
        )
    ):
        return None
    refs = meta.get("foto_hashes")
    if not isinstance(refs, list) or not refs:
        refs = [meta.get("foto_kutuphane"), meta.get("foto_kutuphane2")]
    refs = [str(ref) for ref in refs if ref]
    anahtar = json.dumps(
        [str(meta.get("prompt")), str(meta.get("model", runtime.MODEL_FLUX2)), refs],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(anahtar.encode("utf-8")).hexdigest()


def _gecmis_dosya_favorili(runtime, meta, dosya):
    if str(dosya).lower().endswith((".mp4", ".webm", ".mov")):
        return False
    favoriler = meta.get("yildiz_dosyalar")
    if isinstance(favoriler, list):
        return dosya in favoriler
    return bool(meta.get("yildiz"))


def _gecmis_kayit_bilgisi(runtime, gecmis_dir, ad, meta, dosya=None):
    dosyalar = meta.get("dosyalar") or []
    ilk = dosya or (dosyalar[0] if dosyalar else ad[:-5] + ".png")
    try:
        dosya_bayt = os.path.getsize(os.path.join(gecmis_dir, ilk))
    except OSError:
        dosya_bayt = None
    poster_file = meta.get("poster_file")
    if (
        not isinstance(poster_file, str)
        or os.path.basename(poster_file) != poster_file
        or (not poster_file.lower().endswith("_poster.jpg"))
        or (not os.path.isfile(os.path.join(gecmis_dir, poster_file)))
    ):
        poster_file = None
    return {
        "kayit": ad,
        "dosya": ilk,
        "dosyalar": dosyalar,
        "tur": "video" if ilk.lower().endswith(".mp4") else "goruntu",
        "tarih": meta.get("tarih"),
        "seed": meta.get("seed"),
        "mod": meta.get("mod"),
        "is_tipi": meta.get("is_tipi"),
        "model": meta.get("model", runtime.MODEL_FLUX2),
        "workflow": meta.get("workflow"),
        "prompt": (meta.get("prompt") or "")[:300],
        "negative": (meta.get("negative") or "")[:300],
        "width": meta.get("width"),
        "height": meta.get("height"),
        "steps": meta.get("steps"),
        "cfg": meta.get("cfg"),
        "sampler": meta.get("sampler"),
        "dosya_bayt": dosya_bayt,
        "frames": meta.get("frames"),
        "suresn": meta.get("suresn"),
        "fps": meta.get("fps"),
        "yildiz": _gecmis_dosya_favorili(runtime, meta, ilk),
        "video_poster_url": (
            "/gecmis/%s" % urllib.parse.quote(poster_file) if poster_file else None
        ),
        "url": "/gecmis/%s" % urllib.parse.quote(ilk),
    }


def gecmis_listesi(
    runtime,
    kullanici,
    sayfa,
    adet,
    arama="",
    favoriler=False,
    albume_gore=False,
    album_id=None,
):
    gecmis_dir = runtime._kullanici_gecmis(kullanici)
    runtime._klasor_elin(gecmis_dir)
    try:
        jsonlar = sorted(
            (a for a in os.listdir(gecmis_dir) if a.endswith(".json")), reverse=True
        )
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
        if (
            q
            and q
            not in " ".join(
                (
                    str(meta.get(k) or "")
                    for k in (
                        "prompt",
                        "negative",
                        "model",
                        "mod",
                        "seed",
                        "width",
                        "height",
                        "tarih",
                    )
                )
            ).casefold()
        ):
            continue
        adaylar.append((ad, meta))
    album_konumlari = {}
    album_foto_sayilari = {}
    for ad, meta in tum_kayitlar:
        album_id_for_record = _gecmis_album_id(runtime, meta)
        if album_id_for_record is None:
            continue
        files = meta.get("dosyalar") or [ad[:-5] + ".png"]
        for file in files:
            album_konumlari[ad, file] = (
                album_id_for_record,
                album_foto_sayilari.get(album_id_for_record, 0),
            )
            album_foto_sayilari[album_id_for_record] = (
                album_foto_sayilari.get(album_id_for_record, 0) + 1
            )
    if album_id is not None:
        if not re.fullmatch("[a-f0-9]{64}", str(album_id)):
            return {"toplam": 0, "sayfa": 1, "adet": 100, "kayitlar": []}
        album_eslesmeleri = []
        for ad, meta in adaylar:
            if _gecmis_album_id(runtime, meta) == album_id:
                files = meta.get("dosyalar") or [ad[:-5] + ".png"]
                album_eslesmeleri.append((ad, meta, files))
        toplam = sum((len(files) for _, _, files in album_eslesmeleri))
        sayfa = max(1, sayfa)
        adet = max(1, min(100, adet))
        baslangic = (sayfa - 1) * adet
        bitis = baslangic + adet
        album_kayitlari = []
        album_indeksi = 0
        for ad, meta, files in album_eslesmeleri:
            for file in files:
                if baslangic <= album_indeksi < bitis:
                    item = _gecmis_kayit_bilgisi(runtime, gecmis_dir, ad, meta, file)
                    item["album_index"] = album_indeksi
                    item["album_id"] = album_id
                    album_kayitlari.append(item)
                album_indeksi += 1
                if album_indeksi >= bitis:
                    break
            if album_indeksi >= bitis:
                break
        return {
            "toplam": toplam,
            "sayfa": sayfa,
            "adet": adet,
            "kayitlar": album_kayitlari,
        }
    if favoriler:
        kayitlar = []
        for ad, meta in adaylar:
            files = meta.get("dosyalar") or [ad[:-5] + ".png"]
            for file in files:
                if not _gecmis_dosya_favorili(runtime, meta, file):
                    continue
                item = _gecmis_kayit_bilgisi(runtime, gecmis_dir, ad, meta, file)
                album_position = album_konumlari.get((ad, file))
                if (
                    album_position
                    and albume_gore
                    and (album_foto_sayilari.get(album_position[0], 0) >= 2)
                ):
                    item.update(
                        {
                            "album_member": True,
                            "album_id": album_position[0],
                            "album_index": album_position[1],
                            "album_count": album_foto_sayilari.get(
                                album_position[0], 0
                            ),
                        }
                    )
                kayitlar.append((ad, item))
        adaylar = kayitlar
        toplam = len(adaylar)
        sayfa = max(1, sayfa)
        adet = max(1, min(100, adet))
        dilim = adaylar[(sayfa - 1) * adet : sayfa * adet]
        return {
            "toplam": toplam,
            "sayfa": sayfa,
            "adet": adet,
            "kayitlar": [item for _, item in dilim],
        }
    if albume_gore:
        albumler = {}
        gorunum = []
        for ad, meta in adaylar:
            album_id = _gecmis_album_id(runtime, meta)
            if album_id is None:
                gorunum.append(("tek", [(ad, meta)]))
            else:
                if album_id not in albumler:
                    albumler[album_id] = []
                    gorunum.append((album_id, albumler[album_id]))
                albumler[album_id].append((ad, meta))
        adaylar = []
        for album_id, uyeler in gorunum:
            photo_count = sum((len(meta.get("dosyalar") or [1]) for _, meta in uyeler))
            if album_id == "tek" or photo_count < 2:
                adaylar.extend(uyeler)
                continue
            kapak = None
            for uye_ad, uye_meta in uyeler:
                dosyalar = uye_meta.get("dosyalar") or [uye_ad[:-5] + ".png"]
                kapak_dosyasi = next(
                    (
                        file
                        for file in dosyalar
                        if _gecmis_dosya_favorili(runtime, uye_meta, file)
                    ),
                    None,
                )
                if kapak_dosyasi:
                    kapak = (uye_ad, uye_meta, kapak_dosyasi)
                    break
                if kapak is None:
                    kapak = (uye_ad, uye_meta, dosyalar[0])
            oge = _gecmis_kayit_bilgisi(
                runtime, gecmis_dir, kapak[0], kapak[1], kapak[2]
            )
            oge.update(
                {"album": True, "album_id": album_id, "album_count": photo_count}
            )
            adaylar.append((kapak[0], oge))
    else:
        fotograf_kayitlari = []
        for ad, meta in adaylar:
            dosyalar = meta.get("dosyalar") or [ad[:-5] + ".png"]
            fotograf_kayitlari.extend(
                (
                    (ad, _gecmis_kayit_bilgisi(runtime, gecmis_dir, ad, meta, dosya))
                    for dosya in dosyalar
                )
            )
        adaylar = fotograf_kayitlari
    toplam = len(adaylar)
    sayfa = max(1, sayfa)
    adet = max(1, min(100, adet))
    dilim = adaylar[(sayfa - 1) * adet : sayfa * adet]
    kayitlar = []
    for ad, meta in dilim:
        if meta.get("album") or "dosya" in meta:
            kayitlar.append(meta)
        else:
            kayitlar.append(_gecmis_kayit_bilgisi(runtime, gecmis_dir, ad, meta))
    return {"toplam": toplam, "sayfa": sayfa, "adet": adet, "kayitlar": kayitlar}


def gecmis_kaydi_yildiz(runtime, kullanici, kayit, yildiz, dosya=None):
    kayit = runtime._guvenli_dosya_adi(kayit)
    if not kayit or not kayit.endswith(".json"):
        return False
    gecmis_dir = runtime._kullanici_gecmis(kullanici)
    kok = os.path.abspath(gecmis_dir) + os.sep
    yol = os.path.abspath(os.path.join(gecmis_dir, kayit))
    if not yol.startswith(kok) or not os.path.isfile(yol):
        return False
    try:
        with runtime._GECMIS_KILIT:
            with open(yol, "r", encoding="utf-8") as f:
                meta = json.load(f)
            if not isinstance(meta, dict):
                return False
            if dosya is None:
                meta["yildiz"] = bool(yildiz)
                meta.pop("yildiz_dosyalar", None)
            else:
                dosya = runtime._guvenli_dosya_adi(dosya)
                dosyalar = meta.get("dosyalar") or [kayit[:-5] + ".png"]
                if (
                    not dosya
                    or dosya not in dosyalar
                    or dosya.lower().endswith((".mp4", ".webm", ".mov"))
                ):
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
