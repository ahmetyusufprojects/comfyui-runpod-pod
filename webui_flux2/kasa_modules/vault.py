"""Kasa vault responsibilities.

The public kasa module owns shared state and remains the compatibility facade.
"""

import kasa as _ctx

def _muhurle(kullanici, parola, kaynak_agac):
    """Arşivi şifreler, doğrular ve ancak sonra mevcut kasanın yerine koyar."""
    _ctx._os.makedirs(_ctx.KASALAR_DIR, exist_ok=True)
    _ctx.gizle(_ctx.KASALAR_DIR)
    yeni = _ctx._os.path.join(_ctx.KASALAR_DIR, kullanici + ".yeni.tar.gpg")
    # Önceki sürümden kalmış şifresiz ara arşivi de temizle.
    ara = _ctx._os.path.join(_ctx.KASALAR_DIR, kullanici + ".ara.tar")
    for yol in (yeni, ara):
        try:
            _ctx._os.remove(yol)
        except FileNotFoundError:
            pass
    try:
        if not _ctx._arsiv_sifrele(kaynak_agac, yeni, parola):
            raise _ctx.KasaHatasi("arsiv yapilamadi")
        if not _ctx._deneme_cozum(yeni, parola):
            raise _ctx.KasaHatasi("dogrulama basarisiz, eski kasa yerinde duruyor")
        kasa = _ctx._kasa_dosya(kullanici)
        onceki = _ctx._onceki_dosya(kullanici)
        eski_var = _ctx._os.path.isfile(kasa)
        if eski_var:
            _ctx._os.replace(kasa, onceki)
        try:
            _ctx._os.replace(yeni, kasa)
        except OSError:
            if eski_var:
                _ctx._os.replace(onceki, kasa)
            raise
        _ctx.gizle(kasa)
        if _ctx.kalici_kopya is not None:
            _ctx.kalici_kopya()
    finally:
        try:
            _ctx._os.remove(yeni)
        except OSError:
            pass


@_ctx._profil_gecisi
def ac(kullanici, parola):
    """Kasayi calisma/ altina acar. Parola bellege alinir (kapanista silinir)."""
    _ctx._kullanici_dogrula(kullanici)
    hazir = _ctx._parola_hazirla(parola)
    try:
        with _ctx._kilit:
            if kullanici in _ctx._acik:
                # Ac dugmesine basmak sureyi sifirlar. Kapanis basladiysa
                # mudahale edilmez, bitince yeniden acilir.
                if _ctx._acik[kullanici]["kapaniyor"]:
                    raise _ctx.KasaHatasi("kasa kapaniyor, bitince yeniden ac")
                _ctx._acik[kullanici]["son"] = _ctx._time.time()
                return {"acildi": True, "zaten": True}
        kasa = _ctx._kasa_dosya(kullanici)
        if not _ctx._os.path.isfile(kasa):
            raise _ctx.KasaHatasi("bu profilin kasasi yok, once olustur")
        hedef = _ctx._calisma(kullanici)
        if _ctx._os.path.isdir(hedef) and _ctx._os.listdir(hedef):
            raise _ctx.KasaHatasi("yarim kalmis calisma var, once kurtar")
        _ctx._os.makedirs(hedef, exist_ok=True)
        _ctx.gizle(_ctx.CALISMA_DIR)
        _ctx.gizle(hedef)
        try:
            if not _ctx._tar_aktar(kasa, hazir, ["tar", "-xf", "-", "-C", hedef]):
                raise _ctx.KasaHatasi(_ctx.GENEL_HATA)
        except (_ctx.KasaHatasi, OSError) as hata:
            _ctx._shutil.rmtree(hedef, ignore_errors=True)
            raise _ctx.KasaHatasi(_ctx.GENEL_HATA) from hata
        bekleme = _ctx._sure_oku(kullanici) * 60
        with _ctx._kilit:
            _ctx._acik[kullanici] = {"parola": bytearray(hazir), "son": _ctx._time.time(),
                                "bekleme": bekleme, "kapaniyor": False}
        return {"acildi": True}
    finally:
        _ctx._sifirla(hazir)


def aciklar():
    """Su an acik kasalarin kullanici listesi."""
    with _ctx._kilit:
        return [k for k in _ctx._acik if not k.startswith("_")]


def hepsini_kapat():
    """Acik tum kasalari muhurler. Donus: {kullanici: True/False}."""
    sonuc = {}
    for k in _ctx.aciklar():
        try:
            if not _ctx.durum(k).get("acik"):
                sonuc[k] = True
                continue
            sonuc[k] = bool(_ctx.kapat(k).get("kapandi"))
        except Exception:
            sonuc[k] = False
    return sonuc


def kapanis_baslat(kullanici):
    """Kapaniyor bayragini koyar. Kasa acik degilse False doner."""
    _ctx._kullanici_dogrula(kullanici)
    with _ctx._kilit:
        k = _ctx._acik.get(kullanici)
        if k is None or k["kapaniyor"]:
            return False
        k["kapaniyor"] = True
        return True


@_ctx._profil_gecisi
def kapat(kullanici):
    """Acik agaci yeniden sifreler, calisma/'yi siler, parolayi bellekten siler."""
    _ctx._kullanici_dogrula(kullanici)
    with _ctx._kilit:
        kayit = _ctx._acik.get(kullanici)
        if kayit is None:
            return {"kapandi": False, "neden": "zaten kapali"}
        kayit["kapaniyor"] = True
        _ctx._muhur_suren.add(kullanici)
        _ctx._muhur_olaylari[kullanici] = _ctx._th.Event()
        parola = bytearray(kayit["parola"])
        hedef = _ctx._calisma(kullanici)

    muhurlendi = False
    try:
        _ctx._muhurle(kullanici, parola, hedef)
        if _ctx._os.path.isdir(hedef):
            _ctx._shutil.rmtree(hedef)
        muhurlendi = True
        return {"kapandi": True}
    finally:
        with _ctx._kilit:
            kayit = _ctx._acik.get(kullanici)
            if muhurlendi:
                kayit = _ctx._acik.pop(kullanici, None)
                if kayit is not None:
                    _ctx._sifirla(kayit["parola"])
            elif kayit is not None:
                kayit["kapaniyor"] = False
            _ctx._muhur_suren.discard(kullanici)
            olay = _ctx._muhur_olaylari.pop(kullanici, None)
            if olay is not None:
                olay.set()
        _ctx._sifirla(parola)


def kapanis_bekle(kullanici, tavan_sn=None):
    """Devam eden kasa kapatma işlemi tamamlanana veya geri alınana kadar bekle.
    tavan_sn verilirse en fazla o kadar saniye bekler (kapanma turunu asili
    bir arka is asmasin diye)."""
    _ctx._kullanici_dogrula(kullanici)
    # Arkadaki _arka is parcacigi kuyruk iscisinin donmesini 30'ar saniyelik
    # adimlarla bekliyor; buradaki adim ondan kisa olmali ki bayragin indigini
    # zamaninda gorebilelim (aksi halde kapanis turu asili kalir).
    son = None if tavan_sn is None else (_ctx._time.time() + tavan_sn)
    while True:
        with _ctx._kilit:
            kayit = _ctx._acik.get(kullanici)
            if kayit is None or not kayit["kapaniyor"]:
                return
            olay = _ctx._muhur_olaylari.get(kullanici)
        if son is not None and _ctx._time.time() >= son:
            return
        if olay is not None:
            kalan = None if son is None else max(0.05, min(10.0, son - _ctx._time.time()))
            olay.wait(timeout=10.0 if kalan is None else kalan)
        else:
            _ctx._time.sleep(0.1)


def kapanis_iptal(kullanici):
    """Is durdurulamadiysa kapaniyor bayragini indir.
    Arka muhur sureci hala calisiyorsa bayrak kalir; o is bittiginde
    kendiliginden iner (kapat()'in finally blogu _muhur_suren'i temizler)."""
    _ctx._kullanici_dogrula(kullanici)
    with _ctx._kilit:
        kayit = _ctx._acik.get(kullanici)
        if kayit is not None and kullanici not in _ctx._muhur_suren:
            kayit["kapaniyor"] = False


def dokun(kullanici):
    """Kullanim oldu, sure sayacini sifirla."""
    with _ctx._kilit:
        k = _ctx._acik.get(kullanici)
        if k is not None and not k["kapaniyor"]:
            k["son"] = _ctx._time.time()


def durum(kullanici):
    with _ctx._kilit:
        k = _ctx._acik.get(kullanici)
        if k is None:
            return {"acik": False, "kapaniyor": False, "kalan_sn": 0}
        kalan = max(0, int(k["bekleme"] - (_ctx._time.time() - k["son"])))
        return {"acik": True, "kapaniyor": k["kapaniyor"], "kalan_sn": kalan,
                "bekleme_dk": k["bekleme"] // 60}


def _bekci_turu(kapanis_islevi):
    with _ctx._kilit:
        asanlar = [(k, v) for k, v in _ctx._acik.items()
                   if not k.startswith("_") and not v["kapaniyor"]
                   and (_ctx._time.time() - v["son"]) >= v["bekleme"]]
        for k, v in asanlar:
            v["kapaniyor"] = True
    for k, _ in asanlar:
        try:
            kapanis_islevi(k)
        except Exception:
            # Kapanis basarisizsa bayragi indir, sonraki tur yine denesin.
            # Kasa suresi dolmusken acik kalmaya devam etmez.
            with _ctx._kilit:
                kayit = _ctx._acik.get(k)
                if kayit is not None:
                    kayit["kapaniyor"] = False


def bekci_baslat(kapanis_islevi, aralik_sn=5):
    """Suresi dolan kasalari bulan arka is. kapanis_islevi(kullanici) cagrilir."""
    def dongu():
        while True:
            try:
                _ctx._bekci_turu(kapanis_islevi)
            except Exception:
                pass
            _ctx._time.sleep(aralik_sn)
    t = _ctx._th.Thread(target=dongu, daemon=True)
    t.start()
    return t


def temizle_comfy_artiklari():
    """Uretimden kalan ozel dosyalari siler. Donus: silinen adedi."""
    comfy = _ctx._os.path.abspath(_ctx._os.path.join(_ctx.BASE_DIR, "..", "ComfyUI"))
    # Cikti oneki tek tek yazilmaz: yeni model eklendiginde temizlik unutulmasin.
    # Web arayuzunun diske yazdigi her dosya "webui" ile baslar.
    plan = [("input", ("webui_", "webui2_")),
            ("output", ("webui",)),
            ("output/video", ("MiniMax_H3",)),
            ("temp", ("webui",))]
    n = 0
    for alt, onekler in plan:
        d = _ctx._os.path.join(comfy, alt)
        try:
            for ad in _ctx._os.listdir(d):
                if not ad.startswith(onekler):
                    continue
                yol = _ctx._os.path.join(d, ad)
                try:
                    if _ctx._os.path.isfile(yol):
                        _ctx._os.remove(yol)
                        n += 1
                except Exception:
                    pass
        except Exception:
            pass
    return n
