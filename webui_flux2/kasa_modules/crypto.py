"""Kasa crypto responsibilities.

The public kasa module owns shared state and remains the compatibility facade.
"""

import kasa as _ctx

def _gpg_ev():
    """GPG ev dizini (araci durumu burada; gizli anahtarlik kullanilmaz)."""
    try:
        _ctx._os.makedirs(_ctx.GPG_EV, exist_ok=True)
    except Exception:
        pass
    return _ctx.GPG_EV


def _gpg_bul():
    """GPG yolu: once klasor ici gomulu, sonra bilinen kurulum, sonra PATH.
    Klasor tasinmasina duyarsiz; hicbiri yoksa bilinen yolu doner
    (hata asagida anlasilir olur)."""
    for aday in (_ctx.GPG_GOMULU, r"C:\Program Files\GnuPG\bin\gpg.exe",
                 _ctx._shutil.which("gpg") or ""):
        if aday and _ctx._os.path.isfile(aday):
            return aday
    if _ctx._os.name != "nt":
        return _ctx._shutil.which("gpg") or "gpg"
    return r"C:\Program Files\GnuPG\bin\gpg.exe"


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


def _parola_hazirla(parola):
    """Parolayi bytearray'a cevirir. Bozuksa KasaHatasi."""
    if not isinstance(parola, str) or not parola:
        raise _ctx.KasaHatasi(_ctx.GENEL_HATA)
    if "\n" in parola or "\r" in parola:
        raise _ctx.KasaHatasi("parolada satir sonu karakteri olamaz")
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


def _gpg_komutu():
    return [_ctx.GPG, "--homedir", _ctx._gpg_ev(), "--batch", "--pinentry-mode", "loopback",
            "--passphrase-fd", "0", "--no-symkey-cache"]


def _tar_aktar(kasa, parola, tar_argumanlari):
    """Şifreyi çöz ve tar'a aktar. İki sürecin de başarılı olması gerekir."""
    with _ctx._sp.Popen(_ctx._gpg_komutu() + ["-d", kasa], stdin=_ctx._sp.PIPE, stdout=_ctx._sp.PIPE,
                   stderr=_ctx._sp.DEVNULL,
                   creationflags=_ctx._NO_WINDOW) as gpg:
        try:
            gpg.stdin.write(parola + b"\n")
            gpg.stdin.close()
            tar = _ctx._sp.run(tar_argumanlari, stdin=gpg.stdout, stdout=_ctx._sp.DEVNULL,
                          stderr=_ctx._sp.DEVNULL,
                          creationflags=_ctx._NO_WINDOW)
        finally:
            gpg.stdout.close()
        return tar.returncode == 0 and gpg.wait() == 0


def _deneme_cozum(vault, parola):
    """Kasayı diske çıkarmadan parolayı ve arşivin tamamını doğrular."""
    try:
        return _ctx._tar_aktar(vault, parola, ["tar", "-tf", "-"])
    except OSError:
        return False


def _arsiv_sifrele(kaynak, cikti, parola):
    # GPG stdin'in ilk satırından parolayı okur. Sonraki baytlar tar akışıdır.
    # Görsel ve video kasalarında tekrar sıkıştırma süreyi uzatır.
    komut = _ctx._gpg_komutu() + ["--cipher-algo", "AES256", "--compress-algo", "none",
                             "--symmetric", "-o", cikti]
    with _ctx._sp.Popen(komut, stdin=_ctx._sp.PIPE, stdout=_ctx._sp.DEVNULL,
                   stderr=_ctx._sp.DEVNULL,
                   creationflags=_ctx._NO_WINDOW) as gpg:
        try:
            gpg.stdin.write(parola + b"\n")
            gpg.stdin.flush()
            tar = _ctx._sp.run(["tar", "-cf", "-", "-C", kaynak, "."], stdout=gpg.stdin,
                          stderr=_ctx._sp.DEVNULL,
                          creationflags=_ctx._NO_WINDOW)
        finally:
            gpg.stdin.close()
        return tar.returncode == 0 and gpg.wait() == 0


def _arsiv_yeniden_sifrele(kaynak, cikti, eski_parola, yeni_parola):
    """Mevcut şifreli tar arşivini diske açmadan başka parolayla şifrele."""
    cozen = None
    sifreleyen = None
    try:
        cozen = _ctx._sp.Popen(_ctx._gpg_komutu() + ["-d", kaynak], stdin=_ctx._sp.PIPE,
                          stdout=_ctx._sp.PIPE, stderr=_ctx._sp.DEVNULL,
                          creationflags=_ctx._NO_WINDOW)
        sifreleyen = _ctx._sp.Popen(
            _ctx._gpg_komutu() + ["--cipher-algo", "AES256", "--compress-algo", "none",
                             "--yes", "--symmetric", "-o", cikti],
            stdin=_ctx._sp.PIPE, stdout=_ctx._sp.DEVNULL, stderr=_ctx._sp.DEVNULL,
            creationflags=_ctx._NO_WINDOW)
        cozen.stdin.write(eski_parola + b"\n")
        cozen.stdin.close()
        sifreleyen.stdin.write(yeni_parola + b"\n")
        sifreleyen.stdin.flush()
        while True:
            parca = cozen.stdout.read(1024 * 1024)
            if not parca:
                break
            sifreleyen.stdin.write(parca)
        cozen.stdout.close()
        sifreleyen.stdin.close()
        cozen_sonuc = cozen.wait()
        sifreleyen_sonuc = sifreleyen.wait()
        return cozen_sonuc == 0 and sifreleyen_sonuc == 0
    except (BrokenPipeError, OSError):
        for process in (cozen, sifreleyen):
            if process is not None and process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    pass
        for process in (cozen, sifreleyen):
            if process is not None:
                try:
                    process.wait()
                except OSError:
                    pass
        return False
    finally:
        for process in (cozen, sifreleyen):
            if process is None:
                continue
            for pipe in (process.stdin, process.stdout):
                if pipe is not None and not pipe.closed:
                    try:
                        pipe.close()
                    except OSError:
                        pass


def _gecici_sifreli_yol(kullanici, tur):
    fd, yol = _ctx._tempfile.mkstemp(prefix=".%s-%s-" % (kullanici, tur),
                                suffix=".gpg", dir=_ctx.KASALAR_DIR)
    _ctx._os.close(fd)
    _ctx._os.remove(yol)
    return yol
