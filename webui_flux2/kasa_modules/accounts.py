"""Kasa accounts responsibilities.

The public kasa module owns shared state and remains the compatibility facade.
"""

import kasa as _ctx

def _sure_oku(kullanici):
    """Kullanicinin sectigi bekleme suresi (dk). Dosyada saklanir, yoksa 15."""
    try:
        with open(_ctx._os.path.join(_ctx.KASALAR_DIR, kullanici + ".sure"), "r",
                  encoding="utf-8") as f:
            return max(1, min(24 * 60, int((f.read() or "").strip() or 0)
                              or _ctx.BEKLEME_VARSAYILAN_DK))
    except Exception:
        return _ctx.BEKLEME_VARSAYILAN_DK


def _sure_yaz(kullanici, dakika):
    try:
        _ctx._os.makedirs(_ctx.KASALAR_DIR, exist_ok=True)
        _ctx.gizle(_ctx.KASALAR_DIR)
        with open(_ctx._os.path.join(_ctx.KASALAR_DIR, kullanici + ".sure"), "w",
                  encoding="utf-8") as f:
            f.write(str(dakika))
        _ctx.gizle(_ctx._os.path.join(_ctx.KASALAR_DIR, kullanici + ".sure"))
    except Exception:
        pass


def _kullanici_dogrula(kullanici):
    if not _ctx._auth.kullanici_adi_gecerli_mi(kullanici):
        raise _ctx.KasaHatasi("gecersiz kullanici")


def _kasa_dosya(kullanici):
    return _ctx._os.path.join(_ctx.KASALAR_DIR, kullanici + ".tar.gpg")


def _onceki_dosya(kullanici):
    return _ctx._os.path.join(_ctx.KASALAR_DIR, kullanici + ".onceki.tar.gpg")


def _calisma(kullanici):
    return _ctx._os.path.join(_ctx.CALISMA_DIR, kullanici)


def _profil_gecisi(islem):
    """Bir profilin disk işlemlerini sırala, diğer profillerin işini bekletme."""
    @_ctx._wraps(islem)
    def sar(kullanici, *args, **kwargs):
        _ctx._kullanici_dogrula(kullanici)
        with _ctx._kilit:
            kilit = _ctx._profil_kilitleri.get(kullanici)
            if kilit is None:
                kilit = _ctx._profil_kilitleri[kullanici] = _ctx._th.RLock()
        with kilit:
            return islem(kullanici, *args, **kwargs)
    return sar


@_profil_gecisi
def parola_degistir(kullanici, mevcut_parola, yeni_parola, tekrar):
    """Açık kasayı ve onun geri dönüş kopyasını yeni parolayla yeniden mühürle.

    Sunucu, işlem başlamadan önce kasayı geçici olarak kilitlemeli ve açık
    üretim/yazma işlerinin bitmesini sağlamalıdır. Açık çalışma klasörü
    yerinde kalır; yarım kalan işlemde eski şifreli dosyalar geri yüklenir.
    """
    _ctx._kullanici_dogrula(kullanici)
    if not isinstance(yeni_parola, str) or yeni_parola != tekrar:
        raise _ctx.KasaHatasi("yeni parolalar eşleşmiyor")
    if yeni_parola == mevcut_parola:
        raise _ctx.KasaHatasi("yeni parola mevcut paroladan farklı olmalı")
    uyari = _ctx.guc_uyarilari(yeni_parola)
    if uyari:
        raise _ctx.KasaHatasi("yeni parola zayıf: " + "; ".join(uyari))

    eski = _ctx._parola_hazirla(mevcut_parola)
    yeni = _ctx._parola_hazirla(yeni_parola)
    saklanan = None
    asama_kasa = asama_onceki = None
    tutulan_kasa = tutulan_onceki = None
    kasa_yer_degisti = onceki_yer_degisti = False
    yeni_kasa_yerlesik = yeni_onceki_yerlesik = False
    tamamlandi = False
    try:
        with _ctx._kilit:
            kayit = _ctx._acik.get(kullanici)
            if kayit is None:
                raise _ctx.KasaHatasi("kasa kilitli")
            if not kayit["kapaniyor"]:
                raise _ctx.KasaHatasi("parola değişikliği için kasa geçici olarak kilitlenmeli")
            saklanan = bytearray(kayit["parola"])
        if not _ctx._hmac.compare_digest(bytes(eski), bytes(saklanan)):
            raise _ctx.KasaHatasi(_ctx.GENEL_HATA)

        kasa = _ctx._kasa_dosya(kullanici)
        onceki = _ctx._onceki_dosya(kullanici)
        calisma = _ctx._calisma(kullanici)
        if not _ctx._os.path.isfile(kasa) or not _ctx._deneme_cozum(kasa, eski):
            raise _ctx.KasaHatasi(_ctx.GENEL_HATA)
        if not (_ctx._os.path.isdir(calisma) and _ctx._os.listdir(calisma)):
            raise _ctx.KasaHatasi("kasa çalışma alanı bulunamadı")

        _ctx._os.makedirs(_ctx.KASALAR_DIR, exist_ok=True)
        _ctx.gizle(_ctx.KASALAR_DIR)
        asama_kasa = _ctx._gecici_sifreli_yol(kullanici, "yeni-kasa")
        asama_onceki = _ctx._gecici_sifreli_yol(kullanici, "yeni-onceki")
        tutulan_kasa = _ctx._gecici_sifreli_yol(kullanici, "eski-kasa")
        tutulan_onceki = _ctx._gecici_sifreli_yol(kullanici, "eski-onceki")

        if not _ctx._arsiv_sifrele(calisma, asama_kasa, yeni):
            raise _ctx.KasaHatasi("yeni kasa şifrelenemedi; eski parola korunuyor")
        if not _ctx._deneme_cozum(asama_kasa, yeni):
            raise _ctx.KasaHatasi("yeni kasa doğrulanamadı; eski parola korunuyor")
        # Önceki kasayı da yeni parolayla koru. Akış bellekte taşınır; diskte
        # şifresiz ara arşiv veya klasör oluşturulmaz.
        if not _ctx._arsiv_yeniden_sifrele(kasa, asama_onceki, eski, yeni):
            raise _ctx.KasaHatasi("kasa yedek kopyası yeniden şifrelenemedi")
        if not _ctx._deneme_cozum(asama_onceki, yeni):
            raise _ctx.KasaHatasi("kasa yedek kopyası doğrulanamadı")

        try:
            _ctx._os.replace(kasa, tutulan_kasa)
            kasa_yer_degisti = True
            if _ctx._os.path.isfile(onceki):
                _ctx._os.replace(onceki, tutulan_onceki)
                onceki_yer_degisti = True
            _ctx._os.replace(asama_kasa, kasa)
            yeni_kasa_yerlesik = True
            _ctx._os.replace(asama_onceki, onceki)
            yeni_onceki_yerlesik = True
            if _ctx.kalici_kopya is not None:
                _ctx.kalici_kopya()
            with _ctx._kilit:
                guncel = _ctx._acik.get(kullanici)
                if guncel is not kayit or not guncel["kapaniyor"]:
                    raise _ctx.KasaHatasi("kasa durumu değişti; parola güncellenemedi")
                _ctx._sifirla(guncel["parola"])
                guncel["parola"] = bytearray(yeni)
                guncel["son"] = _ctx._time.time()
            tamamlandi = True
        except Exception:
            # Yeni kasa ve Flex kopyası doğrulanmadan eski dosyaları bırakma.
            if yeni_onceki_yerlesik:
                try:
                    _ctx._os.remove(onceki)
                except OSError:
                    pass
            if yeni_kasa_yerlesik:
                try:
                    _ctx._os.remove(kasa)
                except OSError:
                    pass
            if kasa_yer_degisti and _ctx._os.path.exists(tutulan_kasa):
                _ctx._os.replace(tutulan_kasa, kasa)
            if onceki_yer_degisti and _ctx._os.path.exists(tutulan_onceki):
                _ctx._os.replace(tutulan_onceki, onceki)
            raise

        return {"degisti": True}
    finally:
        _ctx._sifirla(eski)
        _ctx._sifirla(yeni)
        if saklanan is not None:
            _ctx._sifirla(saklanan)
        # Ara dosyalar yalnızca şifreli arşivlerdir. Tutulan eski arşivleri
        # silmek, başarılı değişiklikten sonra eski parolanın işe yaramasını
        # önler. Bunlar Flex'e aktarılmaz; yalnızca adlandırılmış kasa
        # dosyaları kalıcı yedek listesine girer.
        yollar = [asama_kasa, asama_onceki]
        if tamamlandi:
            yollar.extend((tutulan_kasa, tutulan_onceki))
        for yol in yollar:
            if yol:
                try:
                    _ctx._os.remove(yol)
                except OSError:
                    pass


def calisma_koku(kullanici):
    """Acik kasanin calisma klasoru. Kapaliysa ValueError."""
    _ctx._kullanici_dogrula(kullanici)
    with _ctx._kilit:
        if kullanici not in _ctx._acik:
            raise ValueError("kasa kilitli")
    return _ctx._calisma(kullanici)


@_profil_gecisi
def profil_sil(kullanici):
    """Kullanicinin kasasini ve acik calismasini siler (uyelik silinirken)."""
    _ctx._kullanici_dogrula(kullanici)
    try:
        _ctx.kapat(kullanici)
    except Exception:
        with _ctx._kilit:
            kayit = _ctx._acik.pop(kullanici, None)
            if kayit is not None:
                _ctx._sifirla(kayit["parola"])
    try:
        _ctx._shutil.rmtree(_ctx._calisma(kullanici), ignore_errors=True)
    except Exception:
        pass
    for yol in (_ctx._kasa_dosya(kullanici), _ctx._onceki_dosya(kullanici),
                _ctx._os.path.join(_ctx.KASALAR_DIR, kullanici + ".sure")):
        try:
            if _ctx._os.path.isfile(yol):
                _ctx._os.remove(yol)
        except Exception:
            pass


@_profil_gecisi
def olustur(kullanici, parola1, parola2, kaynak_agac=None):
    """Ilk kasa. kaynak_agac verilirse icindekiler kasaya konur (tek adimda).
    Muhur dogrulandiktan sonra kaynagin ici temizlenir (sifresiz kopya kalmaz).
    Guvenlik icin yalniz proje ve Temp altindaki kaynaklar temizlenir."""
    _ctx._kullanici_dogrula(kullanici)
    if (parola1 or "") != (parola2 or ""):
        raise _ctx.KasaHatasi("parolalar uymuyor")
    parola = _ctx._parola_hazirla(parola1)
    try:
        if _ctx._os.path.isfile(_ctx._kasa_dosya(kullanici)):
            raise _ctx.KasaHatasi("bu profilin kasasi zaten var")
        if kaynak_agac is None:
            _ctx._os.makedirs(_ctx._calisma(kullanici), exist_ok=True)
            kaynak_agac = _ctx._calisma(kullanici)
        _ctx._muhurle(kullanici, parola, kaynak_agac)
        _ctx._kaynak_temizle(kaynak_agac)
        return {"olustu": True, "uyarilar": _ctx.guc_uyarilari(parola1 or "")}
    finally:
        _ctx._sifirla(parola)


def _kaynak_temizle(kaynak_agac):
    kok = _ctx._os.path.abspath(kaynak_agac)
    guvenli = kok.startswith(_ctx._os.path.abspath(_ctx.BASE_DIR) + _ctx._os.sep) \
        or "Temp" in kok
    if not guvenli:
        return
    try:
        for ad in _ctx._os.listdir(kok):
            yol = _ctx._os.path.join(kok, ad)
            try:
                if _ctx._os.path.isfile(yol):
                    _ctx._os.remove(yol)
                elif _ctx._os.path.isdir(yol):
                    _ctx._shutil.rmtree(yol, ignore_errors=True)
            except Exception:
                pass
    except Exception:
        pass


@_profil_gecisi
def kurtar(kullanici, parola):
    """Yarim kalmis calisma/'yi (ornegin yeniden baslatma sonrasi) muhurler."""
    _ctx._kullanici_dogrula(kullanici)
    hazir = _ctx._parola_hazirla(parola)
    try:
        with _ctx._kilit:
            if kullanici in _ctx._acik:
                raise _ctx.KasaHatasi("kasa zaten acik")
        hedef = _ctx._calisma(kullanici)
        if not (_ctx._os.path.isdir(hedef) and _ctx._os.listdir(hedef)):
            raise _ctx.KasaHatasi("kurtarilacak calisma yok")
        kasa = _ctx._kasa_dosya(kullanici)
        if _ctx._os.path.isfile(kasa) and not _ctx._deneme_cozum(kasa, hazir):
            raise _ctx.KasaHatasi(_ctx.GENEL_HATA)
        _ctx._muhurle(kullanici, hazir, hedef)
        try:
            _ctx._shutil.rmtree(hedef, ignore_errors=True)
        except Exception:
            pass
        return {"kurtardi": True}
    finally:
        _ctx._sifirla(hazir)


def bekleme_koy(kullanici, dakika):
    sn = max(1, min(24 * 60, int(dakika))) * 60
    with _ctx._kilit:
        if kullanici in _ctx._acik:
            _ctx._acik[kullanici]["bekleme"] = sn
    _ctx._sure_yaz(kullanici, sn // 60)
    return sn // 60
