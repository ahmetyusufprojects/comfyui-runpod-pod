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
import shutil as _shutil
import subprocess as _sp
import threading as _th
import time as _time

import auth as _auth

BASE_DIR = _os.path.dirname(_os.path.abspath(__file__))


KOK = _os.path.dirname(BASE_DIR)  # ComfyUI_windows_portable
GPG_GOMULU = _os.environ.get(
    "GPG_PATH", _os.path.join(KOK, "gnupg", "bin", "gpg.exe"))
GPG_EV = _os.environ.get(
    "GPG_HOME", _os.path.join(KOK, "gnupg", "ev"))


def _gpg_ev():
    """GPG ev dizini (araci durumu burada; gizli anahtarlik kullanilmaz)."""
    try:
        _os.makedirs(GPG_EV, exist_ok=True)
    except Exception:
        pass
    return GPG_EV


def _gpg_bul():
    """GPG yolu: once klasor ici gomulu, sonra bilinen kurulum, sonra PATH.
    Klasor tasinmasina duyarsiz; hicbiri yoksa bilinen yolu doner
    (hata asagida anlasilir olur)."""
    for aday in (GPG_GOMULU, r"C:\Program Files\GnuPG\bin\gpg.exe",
                 _shutil.which("gpg") or ""):
        if aday and _os.path.isfile(aday):
            return aday
    if _os.name != "nt":
        return _shutil.which("gpg") or "gpg"
    return r"C:\Program Files\GnuPG\bin\gpg.exe"


GPG = _gpg_bul()
DATA_ROOT = _os.path.abspath(_os.environ.get("WEBUI_DATA_ROOT", BASE_DIR))
KASALAR_DIR = _os.path.join(DATA_ROOT, "kasalar")
CALISMA_DIR = _os.path.join(DATA_ROOT, "calisma")
BEKLEME_VARSAYILAN_DK = 15
GENEL_HATA = "parola hatali veya kasa bozuk"

_acik = {}  # kullanici -> {"parola": bytearray, "son": epoch, "bekleme": sn, "kapaniyor": bool}
_muhur_suren = set()  # su an muhurlemesi suren kullanicilar (cift muhur engeli)
_kilit = _th.Lock()
_muhur_olaylari = {}


class KasaHatasi(Exception):
    pass


def _sifirla(b):
    try:
        for i in range(len(b)):
            b[i] = 0
    except Exception:
        pass


def gizle(yol):
    """Dosya/klasoru gizli+sistem yapar (Gezgin gostermez, yolla acilir).
    Olmazsa sessiz gecer."""
    try:
        import ctypes as _ct
        _ct.windll.kernel32.SetFileAttributesW(str(yol), 2 | 4)
    except Exception:
        pass


def _sure_oku(kullanici):
    """Kullanicinin sectigi bekleme suresi (dk). Dosyada saklanir, yoksa 15."""
    try:
        with open(_os.path.join(KASALAR_DIR, kullanici + ".sure"), "r",
                  encoding="utf-8") as f:
            return max(1, min(24 * 60, int((f.read() or "").strip() or 0)
                              or BEKLEME_VARSAYILAN_DK))
    except Exception:
        return BEKLEME_VARSAYILAN_DK


def _sure_yaz(kullanici, dakika):
    try:
        _os.makedirs(KASALAR_DIR, exist_ok=True)
        gizle(KASALAR_DIR)
        with open(_os.path.join(KASALAR_DIR, kullanici + ".sure"), "w",
                  encoding="utf-8") as f:
            f.write(str(dakika))
        gizle(_os.path.join(KASALAR_DIR, kullanici + ".sure"))
    except Exception:
        pass


def _kullanici_dogrula(kullanici):
    if not _auth.kullanici_adi_gecerli_mi(kullanici):
        raise KasaHatasi("gecersiz kullanici")


def _kasa_dosya(kullanici):
    return _os.path.join(KASALAR_DIR, kullanici + ".tar.gpg")


def _onceki_dosya(kullanici):
    return _os.path.join(KASALAR_DIR, kullanici + ".onceki.tar.gpg")


def _calisma(kullanici):
    return _os.path.join(CALISMA_DIR, kullanici)


def _parola_hazirla(parola):
    """Parolayi bytearray'a cevirir. Bozuksa KasaHatasi."""
    if not isinstance(parola, str) or not parola:
        raise KasaHatasi(GENEL_HATA)
    if "\n" in parola or "\r" in parola:
        raise KasaHatasi("parolada satir sonu karakteri olamaz")
    return bytearray(parola.encode("utf-8"))


def guc_uyarilari(parola):
    """Zayif parolada uyari listesi doner. Bos liste = guclu."""
    uyari = []
    try:
        if len(parola) < 12:
            uyari.append("parola 12 karakterden kisa")
        cesit = sum([any(c.islower() for c in parola),
                     any(c.isupper() for c in parola),
                     any(c.isdigit() for c in parola),
                     any(not c.isalnum() for c in parola)])
        if cesit < 3:
            uyari.append("harf/rakam/isaret karisik kullan")
    except Exception:
        uyari.append("parola degerlendirilemedi")
    return uyari


def _gpg_simetrik(ara_tar, cikti, parola):
    """ara_tar dosyasini sifreler. Parola boruyla verilir."""
    p = _sp.Popen([GPG, "--homedir", _gpg_ev(), "--batch", "--pinentry-mode", "loopback",
                   "--passphrase-fd", "0", "--cipher-algo", "AES256",
                   "--symmetric", "-o", cikti, ara_tar],
                  stdin=_sp.PIPE, stdout=_sp.DEVNULL, stderr=_sp.PIPE)
    _th.Thread(target=lambda: (p.stdin.write(parola), p.stdin.close()),
               daemon=True).start()
    _, err = p.communicate()
    return p.returncode


def _gpg_coz_boruya(vault, parola):
    """Cozulmus arsivi boru olarak doner (tar'a aktarilir)."""
    p = _sp.Popen([GPG, "--homedir", _gpg_ev(), "--batch", "--pinentry-mode", "loopback",
                   "--passphrase-fd", "0", "-d", vault],
                  stdin=_sp.PIPE, stdout=_sp.PIPE, stderr=_sp.PIPE)
    _th.Thread(target=lambda: (p.stdin.write(parola), p.stdin.close()),
               daemon=True).start()
    return p


def _deneme_cozum(vault, parola):
    """Kasayi acmadan parola dogru mu + arsiv saglam mi bakar. True/False."""
    try:
        p = _gpg_coz_boruya(vault, parola)
        t = _sp.run(["tar", "-t"], stdin=p.stdout, capture_output=True)
        p.stdout.close()
        p.wait(timeout=60)
        return p.returncode == 0 and t.returncode == 0
    except Exception:
        return False


def _muhurle(kullanici, parola, kaynak_agac):
    """kaynak_agac icindekileri sifreleyip kasa dosyasina kaldirir.
    Once deneme-cozumle yeni dosya dogrulanir, sonra eski kasa yedege alinir.
    Yari dosya birakmaz: hata olursa eski kasa yerinde durur."""
    _os.makedirs(KASALAR_DIR, exist_ok=True)
    gizle(KASALAR_DIR)
    ara = _os.path.join(KASALAR_DIR, kullanici + ".ara.tar")
    yeni = _os.path.join(KASALAR_DIR, kullanici + ".yeni.tar.gpg")
    for f in (ara, yeni):
        try:
            if _os.path.isfile(f):
                _os.remove(f)
        except Exception:
            pass
    try:
        r = _sp.run(["tar", "-cf", ara, "-C", kaynak_agac, "."],
                    capture_output=True)
        if r.returncode != 0:
            raise KasaHatasi("arsiv yapilamadi")
        kod = _gpg_simetrik(ara, yeni, parola)
        if kod != 0 or not _os.path.isfile(yeni):
            raise KasaHatasi(GENEL_HATA)
        if not _deneme_cozum(yeni, parola):
            raise KasaHatasi("dogrulama basarisiz, eski kasa yerinde duruyor")
        kasa = _kasa_dosya(kullanici)
        onceki = _onceki_dosya(kullanici)
        if _os.path.isfile(kasa):
            try:
                if _os.path.isfile(onceki):
                    _os.remove(onceki)
            except Exception:
                pass
            _os.replace(kasa, onceki)
        _os.replace(yeni, kasa)
        gizle(kasa)
    finally:
        for f in (ara, yeni):
            try:
                if _os.path.isfile(f):
                    _os.remove(f)
            except Exception:
                pass


def calisma_koku(kullanici):
    """Acik kasanin calisma klasoru. Kapaliysa ValueError."""
    _kullanici_dogrula(kullanici)
    with _kilit:
        if kullanici not in _acik:
            raise ValueError("kasa kilitli")
    return _calisma(kullanici)


def profil_sil(kullanici):
    """Kullanicinin kasasini ve acik calismasini siler (uyelik silinirken)."""
    _kullanici_dogrula(kullanici)
    try:
        kapat(kullanici)
    except Exception:
        with _kilit:
            kayit = _acik.pop(kullanici, None)
            if kayit is not None:
                _sifirla(kayit["parola"])
    try:
        _shutil.rmtree(_calisma(kullanici), ignore_errors=True)
    except Exception:
        pass
    for yol in (_kasa_dosya(kullanici), _onceki_dosya(kullanici),
                _os.path.join(KASALAR_DIR, kullanici + ".sure")):
        try:
            if _os.path.isfile(yol):
                _os.remove(yol)
        except Exception:
            pass


def olustur(kullanici, parola1, parola2, kaynak_agac=None):
    """Ilk kasa. kaynak_agac verilirse icindekiler kasaya konur (tek adimda).
    Muhur dogrulandiktan sonra kaynagin ici temizlenir (sifresiz kopya kalmaz).
    Guvenlik icin yalniz proje ve Temp altindaki kaynaklar temizlenir."""
    _kullanici_dogrula(kullanici)
    if (parola1 or "") != (parola2 or ""):
        raise KasaHatasi("parolalar uymuyor")
    parola = _parola_hazirla(parola1)
    try:
        if _os.path.isfile(_kasa_dosya(kullanici)):
            raise KasaHatasi("bu profilin kasasi zaten var")
        if kaynak_agac is None:
            _os.makedirs(_calisma(kullanici), exist_ok=True)
            kaynak_agac = _calisma(kullanici)
        _muhurle(kullanici, bytes(parola), kaynak_agac)
        _kaynak_temizle(kaynak_agac)
        return {"olustu": True, "uyarilar": guc_uyarilari(parola1 or "")}
    finally:
        _sifirla(parola)


def _kaynak_temizle(kaynak_agac):
    kok = _os.path.abspath(kaynak_agac)
    guvenli = kok.startswith(_os.path.abspath(BASE_DIR) + _os.sep) \
        or "Temp" in kok
    if not guvenli:
        return
    try:
        for ad in _os.listdir(kok):
            yol = _os.path.join(kok, ad)
            try:
                if _os.path.isfile(yol):
                    _os.remove(yol)
                elif _os.path.isdir(yol):
                    _shutil.rmtree(yol, ignore_errors=True)
            except Exception:
                pass
    except Exception:
        pass


def ac(kullanici, parola):
    """Kasayi calisma/ altina acar. Parola bellege alinir (kapanista silinir)."""
    _kullanici_dogrula(kullanici)
    hazir = _parola_hazirla(parola)
    try:
        with _kilit:
            if kullanici in _acik:
                # Ac dugmesine basmak sureyi sifirlar. Kapanis basladiysa
                # mudahale edilmez, bitince yeniden acilir.
                if _acik[kullanici]["kapaniyor"]:
                    raise KasaHatasi("kasa kapaniyor, bitince yeniden ac")
                _acik[kullanici]["son"] = _time.time()
                return {"acildi": True, "zaten": True}
            kasa = _kasa_dosya(kullanici)
            if not _os.path.isfile(kasa):
                raise KasaHatasi("bu profilin kasasi yok, once olustur")
            hedef = _calisma(kullanici)
            if _os.path.isdir(hedef) and _os.listdir(hedef):
                raise KasaHatasi("yarim kalmis calisma var, once kurtar")
            _os.makedirs(hedef, exist_ok=True)
            gizle(CALISMA_DIR)
            gizle(hedef)
            g = _gpg_coz_boruya(kasa, bytes(hazir))
            t = _sp.run(["tar", "-x", "-C", hedef], stdin=g.stdout)
            g.stdout.close()
            try:
                g.wait(timeout=120)
            except Exception:
                g.kill()
                raise KasaHatasi(GENEL_HATA)
            if g.returncode != 0 or t.returncode != 0:
                try:
                    _shutil.rmtree(hedef, ignore_errors=True)
                except Exception:
                    pass
                raise KasaHatasi(GENEL_HATA)
            bekleme = _sure_oku(kullanici) * 60
            _acik[kullanici] = {"parola": bytearray(bytes(hazir)),
                                "son": _time.time(),
                                "bekleme": bekleme, "kapaniyor": False}
            return {"acildi": True}
    finally:
        _sifirla(hazir)


def aciklar():
    """Su an acik kasalarin kullanici listesi."""
    with _kilit:
        return [k for k in _acik if not k.startswith("_")]


def hepsini_kapat():
    """Acik tum kasalari muhurler. Donus: {kullanici: True/False}."""
    sonuc = {}
    for k in aciklar():
        try:
            if not durum(k).get("acik"):
                sonuc[k] = True
                continue
            sonuc[k] = bool(kapat(k).get("kapandi"))
        except Exception:
            sonuc[k] = False
    return sonuc


def kapanis_baslat(kullanici):
    """Kapaniyor bayragini koyar. Kasa acik degilse False doner."""
    _kullanici_dogrula(kullanici)
    with _kilit:
        k = _acik.get(kullanici)
        if k is None or k["kapaniyor"]:
            return False
        k["kapaniyor"] = True
        return True


def kapat(kullanici):
    """Acik agaci yeniden sifreler, calisma/'yi siler, parolayi bellekten siler."""
    _kullanici_dogrula(kullanici)
    son = _time.time() + 120
    bekledi = False
    while True:
        with _kilit:
            kayit = _acik.get(kullanici)
            if kayit is None:
                return {"kapandi": bekledi, "neden": "zaten kapali"}
            if kullanici not in _muhur_suren:
                _muhur_suren.add(kullanici)
                olay = _th.Event()
                _muhur_olaylari[kullanici] = olay
                parola = bytes(kayit["parola"])
                hedef = _calisma(kullanici)
                break
            olay = _muhur_olaylari.get(kullanici)
        if _time.time() >= son:
            # Diger muhur sureci hala sureyorsa bu kapatmayi daha fazla
            # bekletme; kasa acik kalir, kurtarma yoluyla yeniden muhurlenir.
            raise KasaHatasi("diger muhurlenme sureci tamamlanmadi")
        bekledi = True
        if olay is not None:
            kalan = max(0.05, min(1.0, son - _time.time()))
            olay.wait(timeout=kalan)
        else:
            _time.sleep(0.1)

    muhurlendi = False
    try:
        _muhurle(kullanici, parola, hedef)
        if _os.path.isdir(hedef):
            _shutil.rmtree(hedef)
        muhurlendi = True
        try:
            temizle_comfy_artiklari()
        except Exception:
            pass
        return {"kapandi": True}
    finally:
        with _kilit:
            kayit = _acik.get(kullanici)
            if muhurlendi:
                kayit = _acik.pop(kullanici, None)
                if kayit is not None:
                    _sifirla(kayit["parola"])
            elif kayit is not None:
                kayit["kapaniyor"] = False
            _muhur_suren.discard(kullanici)
            olay = _muhur_olaylari.pop(kullanici, None)
            if olay is not None:
                olay.set()
        _sifirla(bytearray(parola))


def kapanis_bekle(kullanici, tavan_sn=None):
    """Devam eden kasa kapatma işlemi tamamlanana veya geri alınana kadar bekle.
    tavan_sn verilirse en fazla o kadar saniye bekler (kapanma turunu asili
    bir arka is asmasin diye)."""
    _kullanici_dogrula(kullanici)
    # Arkadaki _arka is parcacigi kuyruk iscisinin donmesini 30'ar saniyelik
    # adimlarla bekliyor; buradaki adim ondan kisa olmali ki bayragin indigini
    # zamaninda gorebilelim (aksi halde kapanis turu asili kalir).
    son = None if tavan_sn is None else (_time.time() + tavan_sn)
    while True:
        with _kilit:
            kayit = _acik.get(kullanici)
            if kayit is None or not kayit["kapaniyor"]:
                return
            olay = _muhur_olaylari.get(kullanici)
        if son is not None and _time.time() >= son:
            return
        if olay is not None:
            kalan = None if son is None else max(0.05, min(10.0, son - _time.time()))
            olay.wait(timeout=10.0 if kalan is None else kalan)
        else:
            _time.sleep(0.1)


def kapanis_iptal(kullanici):
    """Is durdurulamadiysa kapaniyor bayragini indir.
    Arka muhur sureci hala calisiyorsa bayrak kalir; o is bittiginde
    kendiliginden iner (kapat()'in finally blogu _muhur_suren'i temizler)."""
    _kullanici_dogrula(kullanici)
    with _kilit:
        kayit = _acik.get(kullanici)
        if kayit is not None and kullanici not in _muhur_suren:
            kayit["kapaniyor"] = False


def kurtar(kullanici, parola):
    """Yarim kalmis calisma/'yi (ornegin yeniden baslatma sonrasi) muhurler."""
    _kullanici_dogrula(kullanici)
    hazir = _parola_hazirla(parola)
    try:
        with _kilit:
            if kullanici in _acik:
                raise KasaHatasi("kasa zaten acik")
            hedef = _calisma(kullanici)
            if not (_os.path.isdir(hedef) and _os.listdir(hedef)):
                raise KasaHatasi("kurtarilacak calisma yok")
            kasa = _kasa_dosya(kullanici)
            if _os.path.isfile(kasa) and not _deneme_cozum(kasa, bytes(hazir)):
                raise KasaHatasi(GENEL_HATA)
            _muhurle(kullanici, bytes(hazir), hedef)
            try:
                _shutil.rmtree(hedef, ignore_errors=True)
            except Exception:
                pass
            return {"kurtardi": True}
    finally:
        _sifirla(hazir)


def dokun(kullanici):
    """Kullanim oldu, sure sayacini sifirla."""
    with _kilit:
        k = _acik.get(kullanici)
        if k is not None and not k["kapaniyor"]:
            k["son"] = _time.time()


def bekleme_koy(kullanici, dakika):
    sn = max(1, min(24 * 60, int(dakika))) * 60
    with _kilit:
        if kullanici in _acik:
            _acik[kullanici]["bekleme"] = sn
    _sure_yaz(kullanici, sn // 60)
    return sn // 60


def durum(kullanici):
    with _kilit:
        k = _acik.get(kullanici)
        if k is None:
            return {"acik": False, "kapaniyor": False, "kalan_sn": 0}
        kalan = max(0, int(k["bekleme"] - (_time.time() - k["son"])))
        return {"acik": True, "kapaniyor": k["kapaniyor"], "kalan_sn": kalan,
                "bekleme_dk": k["bekleme"] // 60}


def _bekci_turu(kapanis_islevi):
    with _kilit:
        asanlar = [(k, v) for k, v in _acik.items()
                   if not k.startswith("_") and not v["kapaniyor"]
                   and (_time.time() - v["son"]) >= v["bekleme"]]
        for k, v in asanlar:
            v["kapaniyor"] = True
    for k, _ in asanlar:
        try:
            kapanis_islevi(k)
        except Exception:
            # Kapanis basarisizsa bayragi indir, sonraki tur yine denesin.
            # Kasa suresi dolmusken acik kalmaya devam etmez.
            with _kilit:
                kayit = _acik.get(k)
                if kayit is not None:
                    kayit["kapaniyor"] = False


def bekci_baslat(kapanis_islevi, aralik_sn=5):
    """Suresi dolan kasalari bulan arka is. kapanis_islevi(kullanici) cagrilir."""
    def dongu():
        while True:
            try:
                _bekci_turu(kapanis_islevi)
            except Exception:
                pass
            _time.sleep(aralik_sn)
    t = _th.Thread(target=dongu, daemon=True)
    t.start()
    return t


def temizle_comfy_artiklari():
    """Uretimden kalan ozel dosyalari siler. Donus: silinen adedi."""
    comfy = _os.path.abspath(_os.path.join(BASE_DIR, "..", "ComfyUI"))
    # Cikti oneki tek tek yazilmaz: yeni model eklendiginde temizlik unutulmasin.
    # Web arayuzunun diske yazdigi her dosya "webui" ile baslar.
    plan = [("input", ("webui_", "webui2_")),
            ("output", ("webui",)),
            ("output/video", ("MiniMax_H3",)),
            ("temp", ("webui",))]
    n = 0
    for alt, onekler in plan:
        d = _os.path.join(comfy, alt)
        try:
            for ad in _os.listdir(d):
                if not ad.startswith(onekler):
                    continue
                yol = _os.path.join(d, ad)
                try:
                    if _os.path.isfile(yol):
                        _os.remove(yol)
                        n += 1
                except Exception:
                    pass
        except Exception:
            pass
    return n
