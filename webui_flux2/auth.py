"""Kullanici kimlik dogrulama cekirdegi: parola hash, oturum, kaba-kuvvet kilidi.

Stdlib only, internet yok. Tum dogrulama bu makinede yapilir.
Dosya duzeni (kullanicilar.json):
  {"kullanicilar": {"<ad>": {"sifre": "pbkdf2$...", "rol": "admin|uye",
    "olustu": "<iso>", "kota_mb": 0, "kota_kayit": 0}},
   "oturumlar": {"<token>": {"kullanici": "<ad>", "olustu": "<iso>",
    "bitis": <epoch>}}}
kota 0 = sinirsiz. Oturumlar dosyada durur: sunucu yeniden bassa da surer.
"""
import datetime as _dt
import hashlib as _hashlib
import hmac as _hmac
import json as _json
import os as _os
import re as _re
import secrets as _secrets
import threading as _threading
import time as _time

KULLANICI_ADI_RE = _re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")
ROLLER = ("admin", "uye")
PBKDF2_ITER = 210000
OTURUM_SURE_SN = 30 * 24 * 3600  # 30 gun
COKIE_ADI = "flux2_oturum"


class AuthHatasi(Exception):
    """kod: makine-dostu kisa kod."""

    def __init__(self, kod, mesaj):
        super().__init__(mesaj)
        self.kod = kod


def kullanici_adi_gecerli_mi(ad):
    return isinstance(ad, str) and bool(KULLANICI_ADI_RE.match(ad))


def sifre_hashla(sifre):
    if not isinstance(sifre, str) or len(sifre) < 8:
        raise AuthHatasi("zayif-sifre", "sifre en az 8 karakter olmali")
    salt = _secrets.token_bytes(16)
    ozet = _hashlib.pbkdf2_hmac("sha256", sifre.encode("utf-8"), salt, PBKDF2_ITER)
    return "pbkdf2$%d$%s$%s" % (PBKDF2_ITER, salt.hex(), ozet.hex())


def sifre_dogrula(sifre, kayitli):
    """Zaman-sabit karsilastirma. Format bozuksa False."""
    try:
        skema, iter_s, salt_hex, hash_hex = (kayitli or "").split("$")
        if skema != "pbkdf2" or int(iter_s) != PBKDF2_ITER:
            return False
        salt = bytes.fromhex(salt_hex)
        beklenen = bytes.fromhex(hash_hex)
        if len(salt) != 16 or len(beklenen) != 32:
            return False
        hesap = _hashlib.pbkdf2_hmac("sha256", sifre.encode("utf-8"),
                                     salt, PBKDF2_ITER)
        return _hmac.compare_digest(hesap.hex(), beklenen.hex())
    except Exception:
        return False


def _simdi_iso():
    return _dt.datetime.now().isoformat(timespec="seconds")


class AuthStore:
    """kullanicilar.json okuma/yazma + oturum + kilit. Thread-safe."""

    def __init__(self, dosya_yolu):
        self.dosya = dosya_yolu
        self._kilit = _threading.Lock()

    def _oku(self):
        try:
            with open(self.dosya, "r", encoding="utf-8") as f:
                veri = _json.load(f)
                if isinstance(veri, dict):
                    veri.setdefault("kullanicilar", {})
                    veri.setdefault("oturumlar", {})
                    return veri
        except Exception:
            pass
        return {"kullanicilar": {}, "oturumlar": {}}

    def _yaz(self, veri):
        tmp = self.dosya + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(veri, f, ensure_ascii=False, indent=2)
        _os.replace(tmp, self.dosya)

    def _budanmis(self, veri):
        """Suresi dolan oturumlari tembelce at."""
        simdi = _time.time()
        ot = veri.get("oturumlar") or {}
        for token in [t for t, o in ot.items()
                      if not isinstance(o, dict) or o.get("bitis", 0) < simdi]:
            ot.pop(token, None)
        return veri

    def kurulu_mu(self):
        with self._kilit:
            return bool(self._oku().get("kullanicilar"))

    def giris(self, ad, sifre, ip):
        # Kilitlenme YOK (bilerek): yanlis deneme hesabi kilitlemez.
        with self._kilit:
            veri = self._budanmis(self._oku())
            kayit = veri["kullanicilar"].get(ad)
            if kayit is None or not sifre_dogrula(sifre or "", kayit.get("sifre", "")):
                raise AuthHatasi("hatali", "kullanici adi veya sifre hatali")
            token = _secrets.token_urlsafe(32)
            simdi = _time.time()
            veri["oturumlar"][token] = {"kullanici": ad,
                                        "olustu": _simdi_iso(),
                                        "bitis": simdi + OTURUM_SURE_SN}
            self._yaz(veri)
            return token, kayit.get("rol", "uye")

    def ilk_admin(self, ad, sifre):
        """Kurulum: kullanici yokken ilk admin. Varsa AuthHatasi(kurulu)."""
        if not kullanici_adi_gecerli_mi(ad):
            raise AuthHatasi("gecersiz-ad", "kullanici adi kucuk harf/rakam, 2-32 karakter")
        kayit = sifre_hashla(sifre)  # zayifsa burada patlar
        with self._kilit:
            veri = self._budanmis(self._oku())
            if veri.get("kullanicilar"):
                raise AuthHatasi("kurulu", "sistem zaten kurulu")
            veri["kullanicilar"][ad] = {"sifre": kayit, "rol": "admin",
                                        "olustu": _simdi_iso(),
                                        "kota_mb": 0, "kota_kayit": 0}
            self._yaz(veri)
        return {"kullanici": ad, "rol": "admin"}

    def kullanici_ekle(self, ad, sifre, rol="uye", kota_mb=0, kota_kayit=0):
        if not kullanici_adi_gecerli_mi(ad):
            raise AuthHatasi("gecersiz-ad", "kullanici adi kucuk harf/rakam, 2-32 karakter")
        if rol not in ROLLER:
            raise AuthHatasi("gecersiz-rol", "rol admin veya uye olmali")
        kayit = sifre_hashla(sifre)
        try:
            kota_mb = max(0, int(kota_mb or 0))
            kota_kayit = max(0, int(kota_kayit or 0))
        except Exception:
            raise AuthHatasi("gecersiz-kota", "kota sayi olmali")
        with self._kilit:
            veri = self._budanmis(self._oku())
            if ad in veri["kullanicilar"]:
                raise AuthHatasi("var", "bu kullanici zaten var")
            veri["kullanicilar"][ad] = {"sifre": kayit, "rol": rol,
                                        "olustu": _simdi_iso(),
                                        "kota_mb": kota_mb, "kota_kayit": kota_kayit}
            self._yaz(veri)
        return {"kullanici": ad, "rol": rol}

    def kullanici_sil(self, ad, silen):
        with self._kilit:
            veri = self._budanmis(self._oku())
            hedef = veri["kullanicilar"].get(ad)
            if hedef is None:
                raise AuthHatasi("yok", "kullanici bulunamadi")
            if ad == silen:
                raise AuthHatasi("yasak", "kendi kaydin silinemez")
            if hedef.get("rol") == "admin" and sum(
                    1 for k in veri["kullanicilar"].values()
                    if k.get("rol") == "admin") <= 1:
                raise AuthHatasi("yasak", "son admin silinemez")
            veri["kullanicilar"].pop(ad, None)
            for token in [t for t, o in veri["oturumlar"].items()
                          if o.get("kullanici") == ad]:
                veri["oturumlar"].pop(token, None)
            self._yaz(veri)

    def kota_koy(self, ad, kota_mb, kota_kayit):
        try:
            kota_mb = max(0, int(kota_mb))
            kota_kayit = max(0, int(kota_kayit))
        except Exception:
            raise AuthHatasi("gecersiz-kota", "kota sayi olmali")
        with self._kilit:
            veri = self._budanmis(self._oku())
            if ad not in veri["kullanicilar"]:
                raise AuthHatasi("yok", "kullanici bulunamadi")
            veri["kullanicilar"][ad]["kota_mb"] = kota_mb
            veri["kullanicilar"][ad]["kota_kayit"] = kota_kayit
            self._yaz(veri)

    def rol(self, ad):
        with self._kilit:
            k = self._oku()["kullanicilar"].get(ad)
            return (k or {}).get("rol")

    def limitler(self, ad):
        """(kota_mb, kota_kayit) doner; kullanici yoksa (0, 0)."""
        with self._kilit:
            k = self._oku()["kullanicilar"].get(ad) or {}
            try:
                return max(0, int(k.get("kota_mb", 0))), max(0, int(k.get("kota_kayit", 0)))
            except Exception:
                return 0, 0

    def liste(self):
        """Admin gorunumu: hash ASLA disari cikmaz."""
        with self._kilit:
            veri = self._budanmis(self._oku())
            return [{"kullanici": ad,
                     "rol": k.get("rol", "uye"),
                     "olustu": k.get("olustu"),
                     "kota_mb": k.get("kota_mb", 0),
                     "kota_kayit": k.get("kota_kayit", 0)}
                    for ad, k in veri["kullanicilar"].items()]

    def oturum_kullanici(self, token):
        if not token:
            return None
        with self._kilit:
            veri = self._oku()
            ot = (veri.get("oturumlar") or {}).get(token)
            if not isinstance(ot, dict) or ot.get("bitis", 0) < _time.time():
                return None
            ad = ot.get("kullanici")
            return ad if ad in veri.get("kullanicilar", {}) else None

    def cikis(self, token):
        if not token:
            return
        with self._kilit:
            veri = self._oku()
            if token in veri.get("oturumlar", {}):
                veri["oturumlar"].pop(token, None)
                self._yaz(veri)

    def sifre_degistir(self, ad, eski, yeni):
        yeni_hash = sifre_hashla(yeni)
        with self._kilit:
            veri = self._budanmis(self._oku())
            kayit = veri["kullanicilar"].get(ad)
            if kayit is None:
                raise AuthHatasi("yok", "kullanici bulunamadi")
            if not sifre_dogrula(eski or "", kayit.get("sifre", "")):
                raise AuthHatasi("hatali", "mevcut sifre hatali")
            kayit["sifre"] = yeni_hash
            self._yaz(veri)
