"""Profil kasasi: her kullanicinin ozel verisi tek sifreli dosyada durur.

Kasa kapaliysa veri ortada yoktur, aciksa calisma/ altinda durur.
Sifreleme GPG ile, arsivleme tar.exe ile yapilir. Baska program gerekmez.

Dosyalar:
  kasalar/<kullanici>.tar.gpg          kilitli kasa
  kasalar/<kullanici>.onceki.tar.gpg   bir onceki kasa (kurtarma icin)
  calisma/<kullanici>/                 kasa ACIKKEN buradadir, kapaliyken silinir

Parola kurallari:
  - Parola komut satirina, diske, gunluge DUSMEZ. GPG'ye boruyla verilir.
  - Parolada alt-satir (enter) karakteri yasak, bastan reddedilir.
  - Yanlis parola ile bozuk kasa ayni hatayi verir (ayirt edilmez).
  - Kilitlenme YOK: yanlis deneme hesabi kilitlemez.
"""

import os as _os
import hmac as _hmac
import hashlib as _hashlib
import json as _json
import re as _re
import shutil as _shutil
import subprocess as _sp
import tempfile as _tempfile
import threading as _th
import time as _time
import contextlib as _contextlib
from functools import wraps as _wraps

import auth as _auth

_NO_WINDOW = getattr(_sp, "CREATE_NO_WINDOW", 0)
kalici_kopya = None

BASE_DIR = _os.path.dirname(_os.path.abspath(__file__))

KOK = _os.path.dirname(BASE_DIR)  # ComfyUI_windows_portable
GPG_GOMULU = _os.environ.get(
    "GPG_PATH", _os.path.join(KOK, "gnupg", "bin", "gpg.exe"))
GPG_EV = _os.environ.get(
    "GPG_HOME", _os.path.join(KOK, "gnupg", "ev"))

DATA_ROOT = _os.path.abspath(_os.environ.get("WEBUI_DATA_ROOT", BASE_DIR))
KASALAR_DIR = _os.path.join(DATA_ROOT, "kasalar")
CALISMA_DIR = _os.path.join(DATA_ROOT, "calisma")
BEKLEME_VARSAYILAN_DK = 15
GENEL_HATA = "parola hatali veya kasa bozuk"

_acik = {}  # kullanici -> {"parola": bytearray, "son": epoch, "bekleme": sn, "kapaniyor": bool}
_muhur_suren = set()  # su an muhurlemesi suren kullanicilar (cift muhur engeli)
_kilit = _th.Lock()
_muhur_olaylari = {}
_profil_kilitleri = {}

class KasaHatasi(Exception):
    pass

_MEDYA_UZANTILARI = {
    ".png", ".jpg", ".jpeg", ".jfif", ".webp", ".gif", ".apng", ".bmp",
    ".tif", ".tiff", ".avif", ".heic", ".heif", ".jxl", ".ico", ".raw",
    ".dng", ".cr2", ".nef", ".arw", ".orf", ".rw2", ".mp4", ".webm",
    ".mov", ".mkv", ".avi", ".m4v", ".mpeg", ".mpg", ".3gp", ".3g2",
    ".wmv", ".m2v", ".m2ts", ".ts", ".vob", ".f4v", ".wav", ".mp3",
    ".m4a", ".aac", ".flac", ".ogg", ".opus", ".aiff", ".wma", ".amr",
    ".mid", ".midi",
}
_MEDYA_DATA_URI = _re.compile(
    r"data:(?:image|video|audio)/[^;,]+;base64,[A-Za-z0-9+/=_-]+", _re.IGNORECASE)

# Functional modules; kasa remains the shared-state compatibility facade.
from kasa_modules.crypto import (
    _arsiv_sifrele,
    _arsiv_yeniden_sifrele,
    _deneme_cozum,
    _gecici_sifreli_yol,
    _gpg_bul,
    _gpg_ev,
    _gpg_komutu,
    _parola_hazirla,
    _sifirla,
    _tar_aktar,
    gizle,
    guc_uyarilari,
)
from kasa_modules.accounts import (
    _calisma,
    _kasa_dosya,
    _kaynak_temizle,
    _kullanici_dogrula,
    _onceki_dosya,
    _profil_gecisi,
    _sure_oku,
    _sure_yaz,
    bekleme_koy,
    calisma_koku,
    kurtar,
    olustur,
    parola_degistir,
    profil_sil,
)
from kasa_modules.vault import (
    _bekci_turu,
    _muhurle,
    ac,
    aciklar,
    bekci_baslat,
    dokun,
    durum,
    hepsini_kapat,
    kapanis_baslat,
    kapanis_bekle,
    kapanis_iptal,
    kapat,
    temizle_comfy_artiklari,
)
from kasa_modules.backup import (
    _json_medya_temizle,
    _jsondan_medya_cikar,
    _medyayi_temizle,
    _yedek_ice_aktar_impl,
    sistem_yedegi_ice_aktar,
    sistem_yedegi_medya_haric_kasalar,
    sistem_yedegi_uret,
    yedek_uret,
)
yedek_ice_aktar = _profil_gecisi(_yedek_ice_aktar_impl)
GPG = _gpg_bul()
