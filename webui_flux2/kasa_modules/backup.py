"""Kasa backup responsibilities.

The public kasa module owns shared state and remains the compatibility facade.
"""

import kasa as _ctx

def yedek_uret(kullanici):
    """Create a password-encrypted snapshot of the currently open user vault."""
    _ctx._kullanici_dogrula(kullanici)
    with _ctx._kilit:
        kayit = _ctx._acik.get(kullanici)
        if kayit is None or kayit["kapaniyor"]:
            raise _ctx.KasaHatasi("yedek için kasanın açık olması gerekir")
        parola = bytearray(kayit["parola"])
    fd, hedef = _ctx._tempfile.mkstemp(prefix="webui-yedek-", suffix=".wui.gpg")
    _ctx._os.close(fd)
    _ctx._os.remove(hedef)  # GPG needs a non-existent output path (no overwrite prompt).
    try:
        if not _ctx._arsiv_sifrele(_ctx._calisma(kullanici), hedef, parola):
            raise _ctx.KasaHatasi("şifreli yedek oluşturulamadı")
        if not _ctx._deneme_cozum(hedef, parola):
            raise _ctx.KasaHatasi("şifreli yedek doğrulanamadı")
        return hedef
    except Exception:
        try:
            _ctx._os.remove(hedef)
        except OSError:
            pass
        raise
    finally:
        _ctx._sifirla(parola)


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
            cleaned, item_changed = _ctx._jsondan_medya_cikar(item, hint)
            result[key] = cleaned
            changed = changed or item_changed
        return result, changed
    if isinstance(value, list):
        result = []
        changed = False
        for item in value:
            cleaned, item_changed = _ctx._jsondan_medya_cikar(item, media_hint)
            result.append(cleaned)
            changed = changed or item_changed
        return result, changed
    if isinstance(value, str):
        cleaned, count = _ctx._MEDYA_DATA_URI.subn("[medya yedekten çıkarıldı]", value)
        return cleaned, bool(count)
    return value, False


def _json_medya_temizle(path):
    try:
        with open(path, "r", encoding="utf-8") as stream:
            value = _ctx._json.load(stream)
        cleaned, changed = _ctx._jsondan_medya_cikar(value)
        if changed:
            temp = path + ".media-free-tmp"
            try:
                with open(temp, "w", encoding="utf-8") as stream:
                    _ctx._json.dump(cleaned, stream, ensure_ascii=False, indent=2)
                _ctx._os.replace(temp, path)
            except OSError:
                try:
                    _ctx._os.remove(temp)
                except OSError:
                    pass
                return None
        return changed
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def _medyayi_temizle(kok):
    silinen = 0
    for dizin, _, dosyalar in _ctx._os.walk(kok):
        for ad in dosyalar:
            yol = _ctx._os.path.join(dizin, ad)
            extension = _ctx._os.path.splitext(ad)[1].lower()
            if extension == ".json":
                if _ctx._json_medya_temizle(yol) is None:
                    raise _ctx.KasaHatasi(
                        "Medyasız yedek için bir JSON kaydı güvenle ayıklanamadı.")
            if extension in _ctx._MEDYA_UZANTILARI:
                try:
                    _ctx._os.remove(yol)
                    silinen += 1
                except OSError as exc:
                    raise _ctx.KasaHatasi(
                        "Bir medya dosyası yedekten güvenle çıkarılamadı.") from exc
    return silinen


@_ctx._contextlib.contextmanager
def sistem_yedegi_medya_haric_kasalar(data_root):
    """Build filtered encrypted vault snapshots from every currently open user."""
    temp = _ctx._tempfile.mkdtemp(prefix="webui-media-free-vaults-")
    passwords = {}
    try:
        vault_users = set()
        previous_users = set()
        try:
            for name in _ctx._os.listdir(_ctx.KASALAR_DIR):
                if not name.endswith(".tar.gpg"):
                    continue
                previous = name.endswith(".onceki.tar.gpg")
                username = name[:-15] if previous else name[:-8]
                if not _ctx._auth.kullanici_adi_gecerli_mi(username):
                    continue
                (previous_users if previous else vault_users).add(username)
        except OSError:
            pass
        if previous_users - vault_users:
            raise _ctx.KasaHatasi(
                "Medyasız yedek için kurtarma kasası bulunan her kullanıcının ana kasası açık olmalı.")

        with _ctx._kilit:
            for username in vault_users:
                record = _ctx._acik.get(username)
                if record is None or record.get("kapaniyor"):
                    raise _ctx.KasaHatasi(
                        "Medyasız yedek için mevcut tüm kullanıcı kasaları açık olmalı. "
                        "Kullanıcılar kasalarını açtıktan sonra yeniden deneyin.")
                passwords[username] = bytearray(record["parola"])

        snapshots = {}
        for username in sorted(vault_users):
            source = _ctx._calisma(username)
            if not _ctx._os.path.isdir(source):
                raise _ctx.KasaHatasi("Medyasız yedek için açık kullanıcı verisi bulunamadı.")
            filtered = _ctx._os.path.join(temp, username)
            _ctx._shutil.copytree(source, filtered, symlinks=False)
            _ctx._medyayi_temizle(filtered)
            destination = _ctx._os.path.join(temp, username + ".tar.gpg")
            if not _ctx._arsiv_sifrele(filtered, destination, passwords[username]):
                raise _ctx.KasaHatasi("Medyasız kullanıcı kasası şifrelenemedi.")
            snapshots[username] = destination
        yield snapshots
    finally:
        for password in passwords.values():
            _ctx._sifirla(password)
        _ctx._shutil.rmtree(temp, ignore_errors=True)


def sistem_yedegi_uret(data_root, system_settings_path, parola, *,
                       medya_haric_vaultlar=None):
    """Create one encrypted archive containing every account record and global settings."""
    hazir = _ctx._parola_hazirla(parola)
    kok = _ctx._os.path.abspath(data_root)
    gecici_kok = _ctx._tempfile.mkdtemp(prefix="webui-system-backup-")
    payload = _ctx._os.path.join(gecici_kok, "payload")
    data_copy = _ctx._os.path.join(payload, "data")
    settings_copy = _ctx._os.path.join(payload, "global-settings")
    _ctx._os.makedirs(data_copy, exist_ok=True)
    _ctx._os.makedirs(settings_copy, exist_ok=True)
    fd, hedef = _ctx._tempfile.mkstemp(prefix="webui-system-", suffix=".wuis.gpg")
    _ctx._os.close(fd)
    _ctx._os.remove(hedef)
    try:
        if _ctx._os.path.isdir(kok):
            for entry in _ctx._os.scandir(kok):
                if entry.name in (".webui.lock", ".flex-restore.tar", ".flex-save.tar"):
                    continue
                destination = _ctx._os.path.join(data_copy, entry.name)
                if entry.is_dir(follow_symlinks=False):
                    _ctx._shutil.copytree(entry.path, destination, symlinks=False)
                elif entry.is_file(follow_symlinks=False):
                    _ctx._shutil.copy2(entry.path, destination)
        if medya_haric_vaultlar is not None:
            # Unencrypted leftovers are also filtered. Opaque user vaults are
            # replaced below with snapshots encrypted under each user's password.
            _ctx._medyayi_temizle(data_copy)
            vault_dir = _ctx._os.path.join(data_copy, "kasalar")
            _ctx._os.makedirs(vault_dir, exist_ok=True)
            for name in _ctx._os.listdir(vault_dir):
                if name.endswith(".tar.gpg"):
                    try:
                        _ctx._os.remove(_ctx._os.path.join(vault_dir, name))
                    except OSError:
                        pass
            for username, snapshot in medya_haric_vaultlar.items():
                _ctx._shutil.copy2(snapshot, _ctx._os.path.join(vault_dir, username + ".tar.gpg"))
                previous = _ctx._os.path.join(vault_dir, username + ".onceki.tar.gpg")
                if _ctx._os.path.isfile(_ctx._onceki_dosya(username)):
                    _ctx._shutil.copy2(snapshot, previous)
                else:
                    try:
                        _ctx._os.remove(previous)
                    except FileNotFoundError:
                        pass
        # Old browser sessions must never be transferred to another install.
        account_file = _ctx._os.path.join(data_copy, "kullanicilar.json")
        if _ctx._os.path.isfile(account_file):
            with open(account_file, "r", encoding="utf-8") as stream:
                accounts = _ctx._json.load(stream)
            if not isinstance(accounts, dict) or not isinstance(accounts.get("kullanicilar"), dict):
                raise _ctx.KasaHatasi("kullanıcı kayıtları yedek için okunamadı")
            accounts["oturumlar"] = {}
            with open(account_file, "w", encoding="utf-8") as stream:
                _ctx._json.dump(accounts, stream, ensure_ascii=False, indent=2)
        settings_path = _ctx._os.path.abspath(str(system_settings_path or ""))
        settings_parent = _ctx._os.path.dirname(settings_path)
        if settings_parent and _ctx._os.path.isdir(settings_parent):
            import re as _re
            pattern = _re.compile(r"system-settings(?:-\d{19}-[a-f0-9]{16}-[a-f0-9]{64})?\.json$")
            for entry in _ctx._os.scandir(settings_parent):
                if pattern.fullmatch(entry.name) and entry.is_file(follow_symlinks=False):
                    _ctx._shutil.copy2(entry.path, _ctx._os.path.join(settings_copy, entry.name))
        settings_main = _ctx._os.path.join(settings_copy, "system-settings.json")
        import re as _re
        version_pattern = _re.compile(
            r"system-settings-(\d{19})-[a-f0-9]{16}-([a-f0-9]{64})\.json$")
        versions = []
        for name in _ctx._os.listdir(settings_copy):
            match = version_pattern.fullmatch(name)
            if match:
                versions.append((match.group(1), match.group(2), name))
        for _, digest, name in sorted(versions, reverse=True):
            candidate = _ctx._os.path.join(settings_copy, name)
            try:
                with open(candidate, "rb") as stream:
                    content = stream.read()
                value = _ctx._json.loads(content.decode("utf-8"))
                if (_ctx._hashlib.sha256(content).hexdigest() == digest
                        and isinstance(value, dict) and value.get("schema") == 1):
                    with open(settings_main, "wb") as stream:
                        stream.write(content)
                    break
            except (OSError, ValueError, UnicodeDecodeError):
                continue
        if not _ctx._os.path.isfile(settings_main):
            raise _ctx.KasaHatasi("genel kurulum ayarları yedekte bulunamadı")
        if not _ctx._arsiv_sifrele(payload, hedef, hazir) or not _ctx._deneme_cozum(hedef, hazir):
            raise _ctx.KasaHatasi("şifreli sistem yedeği oluşturulamadı veya doğrulanamadı")
        return hedef
    except Exception:
        try:
            _ctx._os.remove(hedef)
        except OSError:
            pass
        raise
    finally:
        _ctx._sifirla(hazir)
        _ctx._shutil.rmtree(gecici_kok, ignore_errors=True)


def sistem_yedegi_ice_aktar(sifreli_dosya, parola, data_root, system_settings_path,
                            ilerleme=None):
    """Validate and restore a complete encrypted system backup with rollback."""
    hazir = _ctx._parola_hazirla(parola)
    hedef = _ctx._os.path.abspath(data_root)
    parent = _ctx._os.path.dirname(hedef)
    _ctx._os.makedirs(parent, exist_ok=True)
    stage = _ctx._tempfile.mkdtemp(prefix=".webui-system-import-", dir=parent)
    data_stage = _ctx._os.path.join(stage, "payload", "data")
    settings_stage = _ctx._os.path.join(stage, "payload", "global-settings")
    settings_path = _ctx._os.path.abspath(str(system_settings_path or ""))
    settings_parent = _ctx._os.path.dirname(settings_path)
    old_data = hedef + ".system-import-old"
    settings_backup = _ctx._os.path.join(stage, "settings-old")
    def bildir(durum, **detay):
        if ilerleme is not None:
            ilerleme({"durum": durum, **detay})
    try:
        if _ctx._os.path.exists(old_data):
            raise _ctx.KasaHatasi("önceki geri yükleme artığı bulundu; veri korunarak durduruldu")
        bildir("parola_dogrulaniyor")
        if not _ctx._deneme_cozum(sifreli_dosya, hazir):
            raise _ctx.KasaHatasi(_ctx.GENEL_HATA)
        bildir("arsiv_aciliyor", dosya=0, bayt=0)
        extract_root = os_path = _ctx._os.path.join(stage, "payload")
        _ctx._os.makedirs(extract_root, exist_ok=True)
        with _ctx._sp.Popen(_ctx._gpg_komutu() + ["-d", sifreli_dosya], stdin=_ctx._sp.PIPE,
                       stdout=_ctx._sp.PIPE, stderr=_ctx._sp.DEVNULL,
                       creationflags=_ctx._NO_WINDOW) as gpg:
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
                        raise _ctx.KasaHatasi("yedek içinde güvenli olmayan dosya yolu var")
                    total += max(0, member.size)
                    file_count += 1
                    if total > 20 * 1024 * 1024 * 1024:
                        raise _ctx.KasaHatasi("yedek açılmış boyut sınırını aşıyor")
                    destination = _ctx._os.path.abspath(_ctx._os.path.join(extract_root, *parts))
                    if not destination.startswith(extract_root + _ctx._os.sep):
                        raise _ctx.KasaHatasi("yedek içinde güvenli olmayan dosya yolu var")
                    if member.isdir():
                        _ctx._os.makedirs(destination, exist_ok=True)
                    else:
                        _ctx._os.makedirs(_ctx._os.path.dirname(destination), exist_ok=True)
                        source = archive.extractfile(member)
                        if source is None:
                            raise _ctx.KasaHatasi("yedek dosyası okunamadı")
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
                raise _ctx.KasaHatasi(_ctx.GENEL_HATA)
        bildir("kayitlar_dogrulaniyor", dosya=file_count, bayt=copied_total)
        account_file = _ctx._os.path.join(data_stage, "kullanicilar.json")
        with open(account_file, "r", encoding="utf-8") as stream:
            accounts = _ctx._json.load(stream)
        if not isinstance(accounts, dict) or not isinstance(accounts.get("kullanicilar"), dict):
            raise _ctx.KasaHatasi("yedekte geçerli kullanıcı kayıtları yok")
        accounts["oturumlar"] = {}
        with open(account_file, "w", encoding="utf-8") as stream:
            _ctx._json.dump(accounts, stream, ensure_ascii=False, indent=2)
        settings_main = _ctx._os.path.join(settings_stage, "system-settings.json")
        with open(settings_main, "r", encoding="utf-8") as stream:
            settings = _ctx._json.load(stream)
        if not isinstance(settings, dict) or settings.get("schema") != 1:
            raise _ctx.KasaHatasi("yedekte geçerli genel ayarlar yok")

        bildir("sisteme_yerlestiriliyor", dosya=file_count, bayt=copied_total)
        # Save existing data and settings so any filesystem error rolls back.
        _ctx._os.makedirs(settings_backup, exist_ok=True)
        if _ctx._os.path.isdir(settings_parent):
            import re as _re
            pattern = _re.compile(r"system-settings(?:-\d{19}-[a-f0-9]{16}-[a-f0-9]{64})?\.json$")
            for entry in _ctx._os.scandir(settings_parent):
                if pattern.fullmatch(entry.name) and entry.is_file(follow_symlinks=False):
                    _ctx._shutil.copy2(entry.path, _ctx._os.path.join(settings_backup, entry.name))
        if _ctx._os.path.exists(hedef):
            _ctx._os.replace(hedef, old_data)
        try:
            _ctx._os.replace(data_stage, hedef)
            _ctx._os.makedirs(settings_parent, exist_ok=True)
            import re as _re
            pattern = _re.compile(r"system-settings(?:-\d{19}-[a-f0-9]{16}-[a-f0-9]{64})?\.json$")
            for entry in _ctx._os.scandir(settings_parent):
                if pattern.fullmatch(entry.name) and entry.is_file(follow_symlinks=False):
                    _ctx._os.remove(entry.path)
            for entry in _ctx._os.scandir(settings_stage):
                _ctx._shutil.copy2(entry.path, _ctx._os.path.join(settings_parent, entry.name))
        except Exception:
            if _ctx._os.path.isdir(hedef):
                _ctx._shutil.rmtree(hedef, ignore_errors=True)
            if _ctx._os.path.exists(old_data):
                _ctx._os.replace(old_data, hedef)
            for entry in _ctx._os.scandir(settings_parent):
                if __import__("re").fullmatch(r"system-settings(?:-\d{19}-[a-f0-9]{16}-[a-f0-9]{64})?\.json", entry.name):
                    try:
                        _ctx._os.remove(entry.path)
                    except OSError:
                        pass
            for entry in _ctx._os.scandir(settings_backup):
                _ctx._shutil.copy2(entry.path, _ctx._os.path.join(settings_parent, entry.name))
            raise
        _ctx._shutil.rmtree(old_data, ignore_errors=True)
        bildir("tamamlandi", dosya=file_count, bayt=copied_total)
        return {"aktarildi": True, "kullanici_sayisi": len(accounts["kullanicilar"])}
    except _ctx.KasaHatasi:
        raise
    except Exception as exc:
        raise _ctx.KasaHatasi("sistem yedeği güvenle içe aktarılamadı: " + str(exc)[:180]) from exc
    finally:
        _ctx._sifirla(hazir)
        _ctx._shutil.rmtree(stage, ignore_errors=True)


def _yedek_ice_aktar_impl(kullanici, sifreli_dosya, parola):
    """Validate an encrypted vault archive and atomically install it for a user."""
    _ctx._kullanici_dogrula(kullanici)
    hazir = _ctx._parola_hazirla(parola)
    gecici = _ctx._kasa_dosya(kullanici) + ".import"
    eski = _ctx._kasa_dosya(kullanici) + ".import-old"
    try:
        with _ctx._kilit:
            if kullanici in _ctx._acik or kullanici in _ctx._muhur_suren:
                raise _ctx.KasaHatasi("hedef kasayı içe aktarmadan önce kapat")
        if not _ctx._deneme_cozum(sifreli_dosya, hazir):
            raise _ctx.KasaHatasi(_ctx.GENEL_HATA)
        _ctx._os.makedirs(_ctx.KASALAR_DIR, exist_ok=True)
        _ctx._os.replace(sifreli_dosya, gecici)
        if _ctx._os.path.isfile(_ctx._kasa_dosya(kullanici)):
            _ctx._os.replace(_ctx._kasa_dosya(kullanici), eski)
        try:
            _ctx._os.replace(gecici, _ctx._kasa_dosya(kullanici))
            _ctx.gizle(_ctx._kasa_dosya(kullanici))
            if not _ctx._deneme_cozum(_ctx._kasa_dosya(kullanici), hazir):
                raise _ctx.KasaHatasi("yedek hedefe yazıldıktan sonra doğrulanamadı")
        except Exception:
            try:
                _ctx._os.remove(_ctx._kasa_dosya(kullanici))
            except OSError:
                pass
            if _ctx._os.path.isfile(eski):
                _ctx._os.replace(eski, _ctx._kasa_dosya(kullanici))
            raise
        try:
            _ctx._os.remove(eski)
        except OSError:
            pass
        return {"aktarildi": True}
    finally:
        _ctx._sifirla(hazir)
        for path in (gecici, eski):
            try:
                _ctx._os.remove(path)
            except OSError:
                pass
