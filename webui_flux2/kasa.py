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
_profil_kilitleri = {}


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


def _gpg_komutu():
    return [GPG, "--homedir", _gpg_ev(), "--batch", "--pinentry-mode", "loopback",
            "--passphrase-fd", "0", "--no-symkey-cache"]


def _tar_aktar(kasa, parola, tar_argumanlari):
    """Şifreyi çöz ve tar'a aktar. İki sürecin de başarılı olması gerekir."""
    with _sp.Popen(_gpg_komutu() + ["-d", kasa], stdin=_sp.PIPE, stdout=_sp.PIPE,
                   stderr=_sp.DEVNULL,
                   creationflags=_NO_WINDOW) as gpg:
        try:
            gpg.stdin.write(parola + b"\n")
            gpg.stdin.close()
            tar = _sp.run(tar_argumanlari, stdin=gpg.stdout, stdout=_sp.DEVNULL,
                          stderr=_sp.DEVNULL,
                          creationflags=_NO_WINDOW)
        finally:
            gpg.stdout.close()
        return tar.returncode == 0 and gpg.wait() == 0


def _deneme_cozum(vault, parola):
    """Kasayı diske çıkarmadan parolayı ve arşivin tamamını doğrular."""
    try:
        return _tar_aktar(vault, parola, ["tar", "-tf", "-"])
    except OSError:
        return False


def _arsiv_sifrele(kaynak, cikti, parola):
    # GPG stdin'in ilk satırından parolayı okur. Sonraki baytlar tar akışıdır.
    # Görsel ve video kasalarında tekrar sıkıştırma süreyi uzatır.
    komut = _gpg_komutu() + ["--cipher-algo", "AES256", "--compress-algo", "none",
                             "--symmetric", "-o", cikti]
    with _sp.Popen(komut, stdin=_sp.PIPE, stdout=_sp.DEVNULL,
                   stderr=_sp.DEVNULL,
                   creationflags=_NO_WINDOW) as gpg:
        try:
            gpg.stdin.write(parola + b"\n")
            gpg.stdin.flush()
            tar = _sp.run(["tar", "-cf", "-", "-C", kaynak, "."], stdout=gpg.stdin,
                          stderr=_sp.DEVNULL,
                          creationflags=_NO_WINDOW)
        finally:
            gpg.stdin.close()
        return tar.returncode == 0 and gpg.wait() == 0


def yedek_uret(kullanici):
    """Create a password-encrypted snapshot of the currently open user vault."""
    _kullanici_dogrula(kullanici)
    with _kilit:
        kayit = _acik.get(kullanici)
        if kayit is None or kayit["kapaniyor"]:
            raise KasaHatasi("yedek için kasanın açık olması gerekir")
        parola = bytearray(kayit["parola"])
    fd, hedef = _tempfile.mkstemp(prefix="webui-yedek-", suffix=".wui.gpg")
    _os.close(fd)
    _os.remove(hedef)  # GPG needs a non-existent output path (no overwrite prompt).
    try:
        if not _arsiv_sifrele(_calisma(kullanici), hedef, parola):
            raise KasaHatasi("şifreli yedek oluşturulamadı")
        if not _deneme_cozum(hedef, parola):
            raise KasaHatasi("şifreli yedek doğrulanamadı")
        return hedef
    except Exception:
        try:
            _os.remove(hedef)
        except OSError:
            pass
        raise
    finally:
        _sifirla(parola)


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


def _jsondan_medya_cikar(value, media_hint=False):
    if isinstance(value, dict):
        kind = str(value.get("type") or "").lower()
        mime = str(value.get("mime_type") or value.get("mime") or "").lower()
        media_block = (media_hint or any(part in kind for part in ("image", "video", "audio"))
                       or mime.startswith(("image/", "video/", "audio/")))
        result = {}
        changed = False
        for key, item in value.items():
            name = str(key).lower()
            if "base64" in name or (media_block and name in ("data", "bytes", "payload")):
                changed = True
                continue
            hint = name in ("image", "image_url", "video", "audio", "media", "inline_data")
            cleaned, item_changed = _jsondan_medya_cikar(item, hint)
            result[key] = cleaned
            changed = changed or item_changed
        return result, changed
    if isinstance(value, list):
        result = []
        changed = False
        for item in value:
            cleaned, item_changed = _jsondan_medya_cikar(item, media_hint)
            result.append(cleaned)
            changed = changed or item_changed
        return result, changed
    if isinstance(value, str):
        cleaned, count = _MEDYA_DATA_URI.subn("[medya yedekten çıkarıldı]", value)
        return cleaned, bool(count)
    return value, False


def _json_medya_temizle(path):
    try:
        with open(path, "r", encoding="utf-8") as stream:
            value = _json.load(stream)
        cleaned, changed = _jsondan_medya_cikar(value)
        if changed:
            temp = path + ".media-free-tmp"
            try:
                with open(temp, "w", encoding="utf-8") as stream:
                    _json.dump(cleaned, stream, ensure_ascii=False, indent=2)
                _os.replace(temp, path)
            except OSError:
                try:
                    _os.remove(temp)
                except OSError:
                    pass
                return None
        return changed
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def _medyayi_temizle(kok):
    silinen = 0
    for dizin, _, dosyalar in _os.walk(kok):
        for ad in dosyalar:
            yol = _os.path.join(dizin, ad)
            extension = _os.path.splitext(ad)[1].lower()
            if extension == ".json":
                if _json_medya_temizle(yol) is None:
                    raise KasaHatasi(
                        "Medyasız yedek için bir JSON kaydı güvenle ayıklanamadı.")
            if extension in _MEDYA_UZANTILARI:
                try:
                    _os.remove(yol)
                    silinen += 1
                except OSError as exc:
                    raise KasaHatasi(
                        "Bir medya dosyası yedekten güvenle çıkarılamadı.") from exc
    return silinen


@_contextlib.contextmanager
def sistem_yedegi_medya_haric_kasalar(data_root):
    """Build filtered encrypted vault snapshots from every currently open user."""
    temp = _tempfile.mkdtemp(prefix="webui-media-free-vaults-")
    passwords = {}
    try:
        vault_users = set()
        previous_users = set()
        try:
            for name in _os.listdir(KASALAR_DIR):
                if not name.endswith(".tar.gpg"):
                    continue
                previous = name.endswith(".onceki.tar.gpg")
                username = name[:-15] if previous else name[:-8]
                if not _auth.kullanici_adi_gecerli_mi(username):
                    continue
                (previous_users if previous else vault_users).add(username)
        except OSError:
            pass
        if previous_users - vault_users:
            raise KasaHatasi(
                "Medyasız yedek için kurtarma kasası bulunan her kullanıcının ana kasası açık olmalı.")

        with _kilit:
            for username in vault_users:
                record = _acik.get(username)
                if record is None or record.get("kapaniyor"):
                    raise KasaHatasi(
                        "Medyasız yedek için mevcut tüm kullanıcı kasaları açık olmalı. "
                        "Kullanıcılar kasalarını açtıktan sonra yeniden deneyin.")
                passwords[username] = bytearray(record["parola"])

        snapshots = {}
        for username in sorted(vault_users):
            source = _calisma(username)
            if not _os.path.isdir(source):
                raise KasaHatasi("Medyasız yedek için açık kullanıcı verisi bulunamadı.")
            filtered = _os.path.join(temp, username)
            _shutil.copytree(source, filtered, symlinks=False)
            _medyayi_temizle(filtered)
            destination = _os.path.join(temp, username + ".tar.gpg")
            if not _arsiv_sifrele(filtered, destination, passwords[username]):
                raise KasaHatasi("Medyasız kullanıcı kasası şifrelenemedi.")
            snapshots[username] = destination
        yield snapshots
    finally:
        for password in passwords.values():
            _sifirla(password)
        _shutil.rmtree(temp, ignore_errors=True)


def sistem_yedegi_uret(data_root, system_settings_path, parola, *,
                       medya_haric_vaultlar=None):
    """Create one encrypted archive containing every account record and global settings."""
    hazir = _parola_hazirla(parola)
    kok = _os.path.abspath(data_root)
    gecici_kok = _tempfile.mkdtemp(prefix="webui-system-backup-")
    payload = _os.path.join(gecici_kok, "payload")
    data_copy = _os.path.join(payload, "data")
    settings_copy = _os.path.join(payload, "global-settings")
    _os.makedirs(data_copy, exist_ok=True)
    _os.makedirs(settings_copy, exist_ok=True)
    fd, hedef = _tempfile.mkstemp(prefix="webui-system-", suffix=".wuis.gpg")
    _os.close(fd)
    _os.remove(hedef)
    try:
        if _os.path.isdir(kok):
            for entry in _os.scandir(kok):
                if entry.name in (".webui.lock", ".flex-restore.tar", ".flex-save.tar"):
                    continue
                destination = _os.path.join(data_copy, entry.name)
                if entry.is_dir(follow_symlinks=False):
                    _shutil.copytree(entry.path, destination, symlinks=False)
                elif entry.is_file(follow_symlinks=False):
                    _shutil.copy2(entry.path, destination)
        if medya_haric_vaultlar is not None:
            # Unencrypted leftovers are also filtered. Opaque user vaults are
            # replaced below with snapshots encrypted under each user's password.
            _medyayi_temizle(data_copy)
            vault_dir = _os.path.join(data_copy, "kasalar")
            _os.makedirs(vault_dir, exist_ok=True)
            for name in _os.listdir(vault_dir):
                if name.endswith(".tar.gpg"):
                    try:
                        _os.remove(_os.path.join(vault_dir, name))
                    except OSError:
                        pass
            for username, snapshot in medya_haric_vaultlar.items():
                _shutil.copy2(snapshot, _os.path.join(vault_dir, username + ".tar.gpg"))
                previous = _os.path.join(vault_dir, username + ".onceki.tar.gpg")
                if _os.path.isfile(_onceki_dosya(username)):
                    _shutil.copy2(snapshot, previous)
                else:
                    try:
                        _os.remove(previous)
                    except FileNotFoundError:
                        pass
        # Old browser sessions must never be transferred to another install.
        account_file = _os.path.join(data_copy, "kullanicilar.json")
        if _os.path.isfile(account_file):
            with open(account_file, "r", encoding="utf-8") as stream:
                accounts = _json.load(stream)
            if not isinstance(accounts, dict) or not isinstance(accounts.get("kullanicilar"), dict):
                raise KasaHatasi("kullanıcı kayıtları yedek için okunamadı")
            accounts["oturumlar"] = {}
            with open(account_file, "w", encoding="utf-8") as stream:
                _json.dump(accounts, stream, ensure_ascii=False, indent=2)
        settings_path = _os.path.abspath(str(system_settings_path or ""))
        settings_parent = _os.path.dirname(settings_path)
        if settings_parent and _os.path.isdir(settings_parent):
            import re as _re
            pattern = _re.compile(r"system-settings(?:-\d{19}-[a-f0-9]{16}-[a-f0-9]{64})?\.json$")
            for entry in _os.scandir(settings_parent):
                if pattern.fullmatch(entry.name) and entry.is_file(follow_symlinks=False):
                    _shutil.copy2(entry.path, _os.path.join(settings_copy, entry.name))
        settings_main = _os.path.join(settings_copy, "system-settings.json")
        import re as _re
        version_pattern = _re.compile(
            r"system-settings-(\d{19})-[a-f0-9]{16}-([a-f0-9]{64})\.json$")
        versions = []
        for name in _os.listdir(settings_copy):
            match = version_pattern.fullmatch(name)
            if match:
                versions.append((match.group(1), match.group(2), name))
        for _, digest, name in sorted(versions, reverse=True):
            candidate = _os.path.join(settings_copy, name)
            try:
                with open(candidate, "rb") as stream:
                    content = stream.read()
                value = _json.loads(content.decode("utf-8"))
                if (_hashlib.sha256(content).hexdigest() == digest
                        and isinstance(value, dict) and value.get("schema") == 1):
                    with open(settings_main, "wb") as stream:
                        stream.write(content)
                    break
            except (OSError, ValueError, UnicodeDecodeError):
                continue
        if not _os.path.isfile(settings_main):
            raise KasaHatasi("genel kurulum ayarları yedekte bulunamadı")
        if not _arsiv_sifrele(payload, hedef, hazir) or not _deneme_cozum(hedef, hazir):
            raise KasaHatasi("şifreli sistem yedeği oluşturulamadı veya doğrulanamadı")
        return hedef
    except Exception:
        try:
            _os.remove(hedef)
        except OSError:
            pass
        raise
    finally:
        _sifirla(hazir)
        _shutil.rmtree(gecici_kok, ignore_errors=True)


def sistem_yedegi_ice_aktar(sifreli_dosya, parola, data_root, system_settings_path,
                            ilerleme=None):
    """Validate and restore a complete encrypted system backup with rollback."""
    hazir = _parola_hazirla(parola)
    hedef = _os.path.abspath(data_root)
    parent = _os.path.dirname(hedef)
    _os.makedirs(parent, exist_ok=True)
    stage = _tempfile.mkdtemp(prefix=".webui-system-import-", dir=parent)
    data_stage = _os.path.join(stage, "payload", "data")
    settings_stage = _os.path.join(stage, "payload", "global-settings")
    settings_path = _os.path.abspath(str(system_settings_path or ""))
    settings_parent = _os.path.dirname(settings_path)
    old_data = hedef + ".system-import-old"
    settings_backup = _os.path.join(stage, "settings-old")
    def bildir(durum, **detay):
        if ilerleme is not None:
            ilerleme({"durum": durum, **detay})
    try:
        if _os.path.exists(old_data):
            raise KasaHatasi("önceki geri yükleme artığı bulundu; veri korunarak durduruldu")
        bildir("parola_dogrulaniyor")
        if not _deneme_cozum(sifreli_dosya, hazir):
            raise KasaHatasi(GENEL_HATA)
        bildir("arsiv_aciliyor", dosya=0, bayt=0)
        extract_root = os_path = _os.path.join(stage, "payload")
        _os.makedirs(extract_root, exist_ok=True)
        with _sp.Popen(_gpg_komutu() + ["-d", sifreli_dosya], stdin=_sp.PIPE,
                       stdout=_sp.PIPE, stderr=_sp.DEVNULL,
                       creationflags=_NO_WINDOW) as gpg:
            gpg.stdin.write(hazir + b"\n")
            gpg.stdin.close()
            import tarfile as _tarfile
            with _tarfile.open(fileobj=gpg.stdout, mode="r|") as archive:
                total = 0
                copied_total = 0
                file_count = 0
                for member in archive:
                    name = member.name.replace("\\", "/")
                    parts = [part for part in name.split("/") if part not in ("", ".")]
                    if not parts and member.isdir():
                        continue
                    if (not parts or parts[0] not in ("data", "global-settings")
                            or any(part == ".." for part in parts)
                            or not (member.isdir() or member.isfile())
                            or member.issym() or member.islnk()):
                        raise KasaHatasi("yedek içinde güvenli olmayan dosya yolu var")
                    total += max(0, member.size)
                    file_count += 1
                    if total > 20 * 1024 * 1024 * 1024:
                        raise KasaHatasi("yedek açılmış boyut sınırını aşıyor")
                    destination = _os.path.abspath(_os.path.join(extract_root, *parts))
                    if not destination.startswith(extract_root + _os.sep):
                        raise KasaHatasi("yedek içinde güvenli olmayan dosya yolu var")
                    if member.isdir():
                        _os.makedirs(destination, exist_ok=True)
                    else:
                        _os.makedirs(_os.path.dirname(destination), exist_ok=True)
                        source = archive.extractfile(member)
                        if source is None:
                            raise KasaHatasi("yedek dosyası okunamadı")
                        with source, open(destination, "wb") as output:
                            while True:
                                chunk = source.read(1024 * 1024)
                                if not chunk:
                                    break
                                output.write(chunk)
                                copied_total += len(chunk)
                                bildir("arsiv_aciliyor", dosya=file_count,
                                       bayt=copied_total)
                    if member.isdir():
                        bildir("arsiv_aciliyor", dosya=file_count,
                               bayt=copied_total)
            gpg.stdout.close()
            if gpg.wait() != 0:
                raise KasaHatasi(GENEL_HATA)
        bildir("kayitlar_dogrulaniyor", dosya=file_count, bayt=copied_total)
        account_file = _os.path.join(data_stage, "kullanicilar.json")
        with open(account_file, "r", encoding="utf-8") as stream:
            accounts = _json.load(stream)
        if not isinstance(accounts, dict) or not isinstance(accounts.get("kullanicilar"), dict):
            raise KasaHatasi("yedekte geçerli kullanıcı kayıtları yok")
        accounts["oturumlar"] = {}
        with open(account_file, "w", encoding="utf-8") as stream:
            _json.dump(accounts, stream, ensure_ascii=False, indent=2)
        settings_main = _os.path.join(settings_stage, "system-settings.json")
        with open(settings_main, "r", encoding="utf-8") as stream:
            settings = _json.load(stream)
        if not isinstance(settings, dict) or settings.get("schema") != 1:
            raise KasaHatasi("yedekte geçerli genel ayarlar yok")

        bildir("sisteme_yerlestiriliyor", dosya=file_count, bayt=copied_total)
        # Save existing data and settings so any filesystem error rolls back.
        _os.makedirs(settings_backup, exist_ok=True)
        if _os.path.isdir(settings_parent):
            import re as _re
            pattern = _re.compile(r"system-settings(?:-\d{19}-[a-f0-9]{16}-[a-f0-9]{64})?\.json$")
            for entry in _os.scandir(settings_parent):
                if pattern.fullmatch(entry.name) and entry.is_file(follow_symlinks=False):
                    _shutil.copy2(entry.path, _os.path.join(settings_backup, entry.name))
        if _os.path.exists(hedef):
            _os.replace(hedef, old_data)
        try:
            _os.replace(data_stage, hedef)
            _os.makedirs(settings_parent, exist_ok=True)
            import re as _re
            pattern = _re.compile(r"system-settings(?:-\d{19}-[a-f0-9]{16}-[a-f0-9]{64})?\.json$")
            for entry in _os.scandir(settings_parent):
                if pattern.fullmatch(entry.name) and entry.is_file(follow_symlinks=False):
                    _os.remove(entry.path)
            for entry in _os.scandir(settings_stage):
                _shutil.copy2(entry.path, _os.path.join(settings_parent, entry.name))
        except Exception:
            if _os.path.isdir(hedef):
                _shutil.rmtree(hedef, ignore_errors=True)
            if _os.path.exists(old_data):
                _os.replace(old_data, hedef)
            for entry in _os.scandir(settings_parent):
                if __import__("re").fullmatch(r"system-settings(?:-\d{19}-[a-f0-9]{16}-[a-f0-9]{64})?\.json", entry.name):
                    try:
                        _os.remove(entry.path)
                    except OSError:
                        pass
            for entry in _os.scandir(settings_backup):
                _shutil.copy2(entry.path, _os.path.join(settings_parent, entry.name))
            raise
        _shutil.rmtree(old_data, ignore_errors=True)
        bildir("tamamlandi", dosya=file_count, bayt=copied_total)
        return {"aktarildi": True, "kullanici_sayisi": len(accounts["kullanicilar"])}
    except KasaHatasi:
        raise
    except Exception as exc:
        raise KasaHatasi("sistem yedeği güvenle içe aktarılamadı: " + str(exc)[:180]) from exc
    finally:
        _sifirla(hazir)
        _shutil.rmtree(stage, ignore_errors=True)


def _yedek_ice_aktar_impl(kullanici, sifreli_dosya, parola):
    """Validate an encrypted vault archive and atomically install it for a user."""
    _kullanici_dogrula(kullanici)
    hazir = _parola_hazirla(parola)
    gecici = _kasa_dosya(kullanici) + ".import"
    eski = _kasa_dosya(kullanici) + ".import-old"
    try:
        with _kilit:
            if kullanici in _acik or kullanici in _muhur_suren:
                raise KasaHatasi("hedef kasayı içe aktarmadan önce kapat")
        if not _deneme_cozum(sifreli_dosya, hazir):
            raise KasaHatasi(GENEL_HATA)
        _os.makedirs(KASALAR_DIR, exist_ok=True)
        _os.replace(sifreli_dosya, gecici)
        if _os.path.isfile(_kasa_dosya(kullanici)):
            _os.replace(_kasa_dosya(kullanici), eski)
        try:
            _os.replace(gecici, _kasa_dosya(kullanici))
            gizle(_kasa_dosya(kullanici))
            if not _deneme_cozum(_kasa_dosya(kullanici), hazir):
                raise KasaHatasi("yedek hedefe yazıldıktan sonra doğrulanamadı")
        except Exception:
            try:
                _os.remove(_kasa_dosya(kullanici))
            except OSError:
                pass
            if _os.path.isfile(eski):
                _os.replace(eski, _kasa_dosya(kullanici))
            raise
        try:
            _os.remove(eski)
        except OSError:
            pass
        return {"aktarildi": True}
    finally:
        _sifirla(hazir)
        for path in (gecici, eski):
            try:
                _os.remove(path)
            except OSError:
                pass


def _arsiv_yeniden_sifrele(kaynak, cikti, eski_parola, yeni_parola):
    """Mevcut şifreli tar arşivini diske açmadan başka parolayla şifrele."""
    cozen = None
    sifreleyen = None
    try:
        cozen = _sp.Popen(_gpg_komutu() + ["-d", kaynak], stdin=_sp.PIPE,
                          stdout=_sp.PIPE, stderr=_sp.DEVNULL,
                          creationflags=_NO_WINDOW)
        sifreleyen = _sp.Popen(
            _gpg_komutu() + ["--cipher-algo", "AES256", "--compress-algo", "none",
                             "--yes", "--symmetric", "-o", cikti],
            stdin=_sp.PIPE, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
            creationflags=_NO_WINDOW)
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
    fd, yol = _tempfile.mkstemp(prefix=".%s-%s-" % (kullanici, tur),
                                suffix=".gpg", dir=KASALAR_DIR)
    _os.close(fd)
    _os.remove(yol)
    return yol


def _profil_gecisi(islem):
    """Bir profilin disk işlemlerini sırala, diğer profillerin işini bekletme."""
    @_wraps(islem)
    def sar(kullanici, *args, **kwargs):
        _kullanici_dogrula(kullanici)
        with _kilit:
            kilit = _profil_kilitleri.get(kullanici)
            if kilit is None:
                kilit = _profil_kilitleri[kullanici] = _th.RLock()
        with kilit:
            return islem(kullanici, *args, **kwargs)
    return sar


yedek_ice_aktar = _profil_gecisi(_yedek_ice_aktar_impl)


def _muhurle(kullanici, parola, kaynak_agac):
    """Arşivi şifreler, doğrular ve ancak sonra mevcut kasanın yerine koyar."""
    _os.makedirs(KASALAR_DIR, exist_ok=True)
    gizle(KASALAR_DIR)
    yeni = _os.path.join(KASALAR_DIR, kullanici + ".yeni.tar.gpg")
    # Önceki sürümden kalmış şifresiz ara arşivi de temizle.
    ara = _os.path.join(KASALAR_DIR, kullanici + ".ara.tar")
    for yol in (yeni, ara):
        try:
            _os.remove(yol)
        except FileNotFoundError:
            pass
    try:
        if not _arsiv_sifrele(kaynak_agac, yeni, parola):
            raise KasaHatasi("arsiv yapilamadi")
        if not _deneme_cozum(yeni, parola):
            raise KasaHatasi("dogrulama basarisiz, eski kasa yerinde duruyor")
        kasa = _kasa_dosya(kullanici)
        onceki = _onceki_dosya(kullanici)
        eski_var = _os.path.isfile(kasa)
        if eski_var:
            _os.replace(kasa, onceki)
        try:
            _os.replace(yeni, kasa)
        except OSError:
            if eski_var:
                _os.replace(onceki, kasa)
            raise
        gizle(kasa)
        if kalici_kopya is not None:
            kalici_kopya()
    finally:
        try:
            _os.remove(yeni)
        except OSError:
            pass


@_profil_gecisi
def parola_degistir(kullanici, mevcut_parola, yeni_parola, tekrar):
    """Açık kasayı ve onun geri dönüş kopyasını yeni parolayla yeniden mühürle.

    Sunucu, işlem başlamadan önce kasayı geçici olarak kilitlemeli ve açık
    üretim/yazma işlerinin bitmesini sağlamalıdır. Açık çalışma klasörü
    yerinde kalır; yarım kalan işlemde eski şifreli dosyalar geri yüklenir.
    """
    _kullanici_dogrula(kullanici)
    if not isinstance(yeni_parola, str) or yeni_parola != tekrar:
        raise KasaHatasi("yeni parolalar eşleşmiyor")
    if yeni_parola == mevcut_parola:
        raise KasaHatasi("yeni parola mevcut paroladan farklı olmalı")
    uyari = guc_uyarilari(yeni_parola)
    if uyari:
        raise KasaHatasi("yeni parola zayıf: " + "; ".join(uyari))

    eski = _parola_hazirla(mevcut_parola)
    yeni = _parola_hazirla(yeni_parola)
    saklanan = None
    asama_kasa = asama_onceki = None
    tutulan_kasa = tutulan_onceki = None
    kasa_yer_degisti = onceki_yer_degisti = False
    yeni_kasa_yerlesik = yeni_onceki_yerlesik = False
    tamamlandi = False
    try:
        with _kilit:
            kayit = _acik.get(kullanici)
            if kayit is None:
                raise KasaHatasi("kasa kilitli")
            if not kayit["kapaniyor"]:
                raise KasaHatasi("parola değişikliği için kasa geçici olarak kilitlenmeli")
            saklanan = bytearray(kayit["parola"])
        if not _hmac.compare_digest(bytes(eski), bytes(saklanan)):
            raise KasaHatasi(GENEL_HATA)

        kasa = _kasa_dosya(kullanici)
        onceki = _onceki_dosya(kullanici)
        calisma = _calisma(kullanici)
        if not _os.path.isfile(kasa) or not _deneme_cozum(kasa, eski):
            raise KasaHatasi(GENEL_HATA)
        if not (_os.path.isdir(calisma) and _os.listdir(calisma)):
            raise KasaHatasi("kasa çalışma alanı bulunamadı")

        _os.makedirs(KASALAR_DIR, exist_ok=True)
        gizle(KASALAR_DIR)
        asama_kasa = _gecici_sifreli_yol(kullanici, "yeni-kasa")
        asama_onceki = _gecici_sifreli_yol(kullanici, "yeni-onceki")
        tutulan_kasa = _gecici_sifreli_yol(kullanici, "eski-kasa")
        tutulan_onceki = _gecici_sifreli_yol(kullanici, "eski-onceki")

        if not _arsiv_sifrele(calisma, asama_kasa, yeni):
            raise KasaHatasi("yeni kasa şifrelenemedi; eski parola korunuyor")
        if not _deneme_cozum(asama_kasa, yeni):
            raise KasaHatasi("yeni kasa doğrulanamadı; eski parola korunuyor")
        # Önceki kasayı da yeni parolayla koru. Akış bellekte taşınır; diskte
        # şifresiz ara arşiv veya klasör oluşturulmaz.
        if not _arsiv_yeniden_sifrele(kasa, asama_onceki, eski, yeni):
            raise KasaHatasi("kasa yedek kopyası yeniden şifrelenemedi")
        if not _deneme_cozum(asama_onceki, yeni):
            raise KasaHatasi("kasa yedek kopyası doğrulanamadı")

        try:
            _os.replace(kasa, tutulan_kasa)
            kasa_yer_degisti = True
            if _os.path.isfile(onceki):
                _os.replace(onceki, tutulan_onceki)
                onceki_yer_degisti = True
            _os.replace(asama_kasa, kasa)
            yeni_kasa_yerlesik = True
            _os.replace(asama_onceki, onceki)
            yeni_onceki_yerlesik = True
            if kalici_kopya is not None:
                kalici_kopya()
            with _kilit:
                guncel = _acik.get(kullanici)
                if guncel is not kayit or not guncel["kapaniyor"]:
                    raise KasaHatasi("kasa durumu değişti; parola güncellenemedi")
                _sifirla(guncel["parola"])
                guncel["parola"] = bytearray(yeni)
                guncel["son"] = _time.time()
            tamamlandi = True
        except Exception:
            # Yeni kasa ve Flex kopyası doğrulanmadan eski dosyaları bırakma.
            if yeni_onceki_yerlesik:
                try:
                    _os.remove(onceki)
                except OSError:
                    pass
            if yeni_kasa_yerlesik:
                try:
                    _os.remove(kasa)
                except OSError:
                    pass
            if kasa_yer_degisti and _os.path.exists(tutulan_kasa):
                _os.replace(tutulan_kasa, kasa)
            if onceki_yer_degisti and _os.path.exists(tutulan_onceki):
                _os.replace(tutulan_onceki, onceki)
            raise

        return {"degisti": True}
    finally:
        _sifirla(eski)
        _sifirla(yeni)
        if saklanan is not None:
            _sifirla(saklanan)
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
                    _os.remove(yol)
                except OSError:
                    pass


def calisma_koku(kullanici):
    """Acik kasanin calisma klasoru. Kapaliysa ValueError."""
    _kullanici_dogrula(kullanici)
    with _kilit:
        if kullanici not in _acik:
            raise ValueError("kasa kilitli")
    return _calisma(kullanici)


@_profil_gecisi
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


@_profil_gecisi
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
        _muhurle(kullanici, parola, kaynak_agac)
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


@_profil_gecisi
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
        try:
            if not _tar_aktar(kasa, hazir, ["tar", "-xf", "-", "-C", hedef]):
                raise KasaHatasi(GENEL_HATA)
        except (KasaHatasi, OSError) as hata:
            _shutil.rmtree(hedef, ignore_errors=True)
            raise KasaHatasi(GENEL_HATA) from hata
        bekleme = _sure_oku(kullanici) * 60
        with _kilit:
            _acik[kullanici] = {"parola": bytearray(hazir), "son": _time.time(),
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


@_profil_gecisi
def kapat(kullanici):
    """Acik agaci yeniden sifreler, calisma/'yi siler, parolayi bellekten siler."""
    _kullanici_dogrula(kullanici)
    with _kilit:
        kayit = _acik.get(kullanici)
        if kayit is None:
            return {"kapandi": False, "neden": "zaten kapali"}
        kayit["kapaniyor"] = True
        _muhur_suren.add(kullanici)
        _muhur_olaylari[kullanici] = _th.Event()
        parola = bytearray(kayit["parola"])
        hedef = _calisma(kullanici)

    muhurlendi = False
    try:
        _muhurle(kullanici, parola, hedef)
        if _os.path.isdir(hedef):
            _shutil.rmtree(hedef)
        muhurlendi = True
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
        _sifirla(parola)


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


@_profil_gecisi
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
        if _os.path.isfile(kasa) and not _deneme_cozum(kasa, hazir):
            raise KasaHatasi(GENEL_HATA)
        _muhurle(kullanici, hazir, hedef)
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
