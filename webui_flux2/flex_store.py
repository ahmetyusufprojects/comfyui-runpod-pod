"""Store encrypted, versioned snapshots on RunPod Flex object storage.

The archive is assembled only on the container disk. The network volume receives
only an authenticated GPG-encrypted ``.tar.gpg`` object. The encryption key is
provided by the deployment as ``POD_FLEX_ENCRYPTION_KEY`` and is never written
to disk or included in a subprocess command line.
"""

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import threading
import time
from pathlib import Path

_OBJECT = re.compile(r"\d{19}-[a-f0-9]{64}\.tar\.gpg")
_LEGACY_OBJECT = re.compile(r"\d{19}-[a-f0-9]{64}\.tar")
_LEGACY_SETTINGS = re.compile(
    r"system-settings(?:-\d{19}-[a-f0-9]{16}-[a-f0-9]{64})?\.json")
_VAULT = re.compile(r"[a-z0-9][a-z0-9_-]{1,31}(?:\.onceki)?\.tar\.gpg|[a-z0-9][a-z0-9_-]{1,31}\.sure")
_ACCOUNTS = "kullanicilar.json"
_SETTINGS = "system-settings.json"
_GPG_TIMEOUT_SECONDS = 60 * 30


def _digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class FlexStore:
    def __init__(self, root, snapshots, encryption_key=None, gpg_path=None, gpg_home=None):
        self.root = Path(root)
        self.snapshots = Path(snapshots)
        self._encryption_key = (encryption_key if encryption_key is not None else
                                os.environ.get("POD_FLEX_ENCRYPTION_KEY", ""))
        if not isinstance(self._encryption_key, str) or not self._encryption_key.strip():
            raise RuntimeError("Flex yedeği için POD_FLEX_ENCRYPTION_KEY secret'ı gerekli.")
        if "\n" in self._encryption_key or "\r" in self._encryption_key:
            raise RuntimeError("POD_FLEX_ENCRYPTION_KEY tek satır olmalı.")
        self.gpg_path = gpg_path or os.environ.get("GPG_PATH") or shutil.which("gpg")
        if not self.gpg_path:
            raise RuntimeError("Şifreli Flex yedeği için GPG kurulu olmalı.")
        self.gpg_home = gpg_home or os.environ.get("GPG_HOME")
        self.lock = threading.RLock()

    def _objects(self):
        try:
            return sorted((p for p in self.snapshots.iterdir()
                           if _OBJECT.fullmatch(p.name) and p.is_file()
                           and not p.is_symlink()), reverse=True)
        except FileNotFoundError:
            return []

    def _legacy_files(self):
        try:
            return sorted((p for p in self.snapshots.iterdir()
                           if p.is_file() and not p.is_symlink()
                           and (_LEGACY_OBJECT.fullmatch(p.name)
                                or _LEGACY_SETTINGS.fullmatch(p.name))))
        except FileNotFoundError:
            return []

    def _vaults(self):
        return sorted(p for p in (self.root / "kasalar").glob("*")
                      if _VAULT.fullmatch(p.name) and p.is_file() and not p.is_symlink())

    def _run_gpg(self, args, output_path):
        command = [self.gpg_path, "--batch", "--yes", "--no-tty",
                   "--pinentry-mode", "loopback", "--passphrase-fd", "0"]
        if self.gpg_home:
            Path(self.gpg_home).mkdir(mode=0o700, parents=True, exist_ok=True)
            command.extend(("--homedir", str(self.gpg_home)))
        command.extend(args)
        try:
            result = subprocess.run(
                command, input=self._encryption_key.encode("utf-8") + b"\n",
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                timeout=_GPG_TIMEOUT_SECONDS, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            output_path.unlink(missing_ok=True)
            raise RuntimeError("Flex yedeği GPG ile işlenemedi.") from exc
        if result.returncode != 0 or not output_path.is_file():
            output_path.unlink(missing_ok=True)
            raise RuntimeError("Flex yedeği GPG ile doğrulanamadı.")

    def _encrypt(self, source, destination):
        self._run_gpg(["--symmetric", "--cipher-algo", "AES256",
                       "--compress-algo", "none", "--output", str(destination),
                       str(source)], destination)

    def _decrypt(self, source, destination):
        self._run_gpg(["--decrypt", "--output", str(destination), str(source)],
                      destination)

    @staticmethod
    def _archive_digest(name):
        return name.removesuffix(".tar.gpg").rsplit("-", 1)[-1]

    def _snapshot_matches(self, path, digest, encrypted_tmp, archive_tmp):
        try:
            shutil.copyfile(path, encrypted_tmp)
            self._decrypt(encrypted_tmp, archive_tmp)
            return _digest(archive_tmp) == digest
        except (OSError, RuntimeError):
            return False
        finally:
            encrypted_tmp.unlink(missing_ok=True)
            archive_tmp.unlink(missing_ok=True)

    def _encrypt_and_verify(self, source, destination):
        encrypted = self.root / ".flex-migration.gpg"
        decrypted = self.root / ".flex-migration.tmp"
        source_digest = _digest(source)
        destination_created = False
        try:
            if destination.is_symlink():
                raise RuntimeError("Flex geçiş hedefi sembolik bağlantı olamaz.")
            if destination.is_file():
                try:
                    self._decrypt(destination, decrypted)
                    if _digest(decrypted) == source_digest:
                        return True
                except RuntimeError:
                    pass
                # The validated plaintext source is still present. Replacing a
                # partial/stale encrypted copy makes an interrupted migration
                # retryable without deleting the only complete copy.
                destination.unlink()
            self._encrypt(source, encrypted)
            self._decrypt(encrypted, decrypted)
            if _digest(decrypted) != source_digest:
                raise RuntimeError("Şifrelenmiş Flex dosyası doğrulanamadı.")
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            shutil.copyfile(encrypted, destination)
            destination_created = True
            self._decrypt(destination, decrypted)
            if _digest(decrypted) != source_digest:
                raise RuntimeError("Volume üzerindeki Flex dosyası doğrulanamadı.")
            return True
        except Exception:
            if destination_created:
                # The plaintext source remains in place, so an incomplete
                # migration can be safely retried on the next startup.
                destination.unlink(missing_ok=True)
            raise
        finally:
            encrypted.unlink(missing_ok=True)
            decrypted.unlink(missing_ok=True)

    def migrate_legacy(self):
        """Encrypt known plaintext snapshots/settings before using the volume.

        Each source is removed only after an encrypted copy has been decrypted
        and its SHA-256 verified. Unrecognized files are left untouched.
        """
        with self.lock:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.snapshots.mkdir(mode=0o700, parents=True, exist_ok=True)
            for source in self._legacy_files():
                digest = _digest(source)
                if _LEGACY_OBJECT.fullmatch(source.name):
                    expected = source.name.removesuffix(".tar").rsplit("-", 1)[-1]
                    if digest != expected:
                        raise RuntimeError(
                            "Eski Flex snapshot özeti uyuşmuyor; düz kaynak korunarak başlangıç durduruldu.")
                    destination = (self.snapshots /
                                   f"{source.name.removesuffix('.tar')}.tar.gpg")
                else:
                    match = re.fullmatch(
                        r"system-settings-\d{19}-[a-f0-9]{16}-([a-f0-9]{64})\.json",
                        source.name)
                    if match and digest != match.group(1):
                        raise RuntimeError(
                            "Eski Flex ayar özeti uyuşmuyor; düz kaynak korunarak başlangıç durduruldu.")
                    destination = self.snapshots / (source.name + ".gpg")
                self._encrypt_and_verify(source, destination)
                source.unlink()

    def _restore_encrypted_legacy_settings(self):
        try:
            files = [p for p in self.snapshots.iterdir()
                     if p.is_file() and not p.is_symlink()
                     and p.name.endswith(".json.gpg")
                     and _LEGACY_SETTINGS.fullmatch(p.name[:-4])]
        except FileNotFoundError:
            return False
        if not files:
            return False

        def sort_key(path):
            match = re.fullmatch(
                r"system-settings-(\d{19})-[a-f0-9]{16}-[a-f0-9]{64}\.json\.gpg",
                path.name)
            return (match.group(1) if match else "0", path.name)

        encrypted = self.root / ".flex-settings-restore.gpg"
        candidate = self.root / ".flex-settings-restore.json"
        try:
            for path in sorted(files, key=sort_key, reverse=True):
                try:
                    shutil.copyfile(path, encrypted)
                    self._decrypt(encrypted, candidate)
                    payload = candidate.read_bytes()
                    if _LEGACY_SETTINGS.fullmatch(path.name[:-4]):
                        match = re.fullmatch(
                            r"system-settings-(\d{19})-([a-f0-9]{16})-([a-f0-9]{64})\.json\.gpg",
                            path.name)
                        if match and hashlib.sha256(payload).hexdigest() != match.group(3):
                            continue
                    value = json.loads(payload.decode("utf-8"))
                    if not isinstance(value, dict) or value.get("schema") != 1:
                        continue
                    if self._write_settings(payload):
                        return True
                except (OSError, RuntimeError, ValueError, UnicodeDecodeError):
                    continue
            raise RuntimeError(
                "Şifreli eski genel ayarlar açılamadı; kayıtlar korunarak başlangıç durduruldu.")
        finally:
            encrypted.unlink(missing_ok=True)
            candidate.unlink(missing_ok=True)
            (self.root / ".flex-settings-staged.json").unlink(missing_ok=True)

    def _write_settings(self, payload):
        value = json.loads(payload.decode("utf-8"))
        if not isinstance(value, dict) or value.get("schema") != 1:
            return False
        temporary = self.root / ".flex-settings-staged.json"
        destination = self.root / _SETTINGS
        try:
            temporary.write_bytes(payload)
            os.chmod(temporary, 0o600)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return True

    def restore_settings(self):
        """Restore only universal settings without replacing a local account."""
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        objects = self._objects()
        encrypted = self.root / ".flex-settings-snapshot.gpg"
        candidate = self.root / ".flex-settings-snapshot.tar"
        valid_snapshot = False
        try:
            for obj in objects:
                try:
                    shutil.copyfile(obj, encrypted)
                    self._decrypt(encrypted, candidate)
                    if _digest(candidate) != self._archive_digest(obj.name):
                        continue
                    valid_snapshot = True
                    with tarfile.open(candidate, "r") as archive:
                        try:
                            member = archive.getmember(_SETTINGS)
                        except KeyError:
                            continue
                        if not member.isfile():
                            continue
                        stream = archive.extractfile(member)
                        if stream is None:
                            continue
                        with stream:
                            if self._write_settings(stream.read()):
                                return True
                except (OSError, RuntimeError, tarfile.TarError, ValueError):
                    continue
            if objects and not valid_snapshot:
                raise RuntimeError(
                    "Şifreli Flex ayar kopyaları açılamadı; kayıtlar korunarak başlangıç durduruldu.")
            return self._restore_encrypted_legacy_settings()
        finally:
            encrypted.unlink(missing_ok=True)
            candidate.unlink(missing_ok=True)

    def restore(self):
        self.snapshots.mkdir(parents=True, exist_ok=True)
        objects = self._objects()
        if not objects:
            self._restore_encrypted_legacy_settings()
            return
        encrypted = self.root / ".flex-restore.tar.gpg"
        candidate = self.root / ".flex-restore.tar"
        staging = self.root / ".flex-restore-staging"
        try:
            valid = None
            restored_names = set()
            for obj in objects:
                try:
                    shutil.copyfile(obj, encrypted)
                    self._decrypt(encrypted, candidate)
                except (OSError, RuntimeError):
                    continue
                if _digest(candidate) == self._archive_digest(obj.name):
                    valid = candidate
                    break
                candidate.unlink(missing_ok=True)
            if valid is None:
                raise RuntimeError("Flex’te doğrulanabilen şifreli kasa kopyası bulunamadı.")

            staging.mkdir(mode=0o700, exist_ok=True)
            staged = []
            seen = set()
            with tarfile.open(valid, "r") as archive:
                for item in archive:
                    name = item.name
                    allowed_vault = (name.startswith("kasalar/")
                                     and _VAULT.fullmatch(name[8:]))
                    if (name in seen or not item.isfile()
                            or (name not in (_ACCOUNTS, _SETTINGS) and not allowed_vault)):
                        raise RuntimeError("Beklenmeyen Flex arşiv girdisi.")
                    seen.add(name)
                    output = staging / name
                    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    with archive.extractfile(item) as src, output.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
                    os.chmod(output, 0o600)
                    if name == _ACCOUNTS:
                        value = json.loads(output.read_text(encoding="utf-8"))
                        if not isinstance(value, dict):
                            raise RuntimeError("Flex hesap kaydı geçersiz.")
                        value["oturumlar"] = {}
                        output.write_text(json.dumps(value, ensure_ascii=False),
                                          encoding="utf-8")
                    staged.append((output, self.root / name))
                    restored_names.add(name)
            if _ACCOUNTS not in restored_names:
                raise RuntimeError("Flex arşivinde kullanıcı kaydı bulunamadı.")
            for source, destination in staged:
                destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                os.replace(source, destination)
            if _SETTINGS not in restored_names:
                self._restore_encrypted_legacy_settings()
        finally:
            encrypted.unlink(missing_ok=True)
            candidate.unlink(missing_ok=True)
            shutil.rmtree(staging, ignore_errors=True)

    def save(self):
        with self.lock:
            account_file = self.root / _ACCOUNTS
            if not account_file.is_file():
                return
            self.snapshots.mkdir(mode=0o700, parents=True, exist_ok=True)
            accounts = json.loads(account_file.read_text(encoding="utf-8"))
            accounts["oturumlar"] = {}
            payload = json.dumps(accounts, ensure_ascii=False).encode("utf-8")
            temporary = self.root / ".flex-save.tar"
            encrypted = self.root / ".flex-save.tar.gpg"
            verified = self.root / ".flex-save-verify.tar"
            try:
                with tarfile.open(temporary, "w") as archive:
                    member = tarfile.TarInfo(_ACCOUNTS)
                    member.mode = 0o600
                    member.size = len(payload)
                    archive.addfile(member, io.BytesIO(payload))
                    settings = self.root / _SETTINGS
                    if settings.is_file():
                        archive.add(settings, arcname=_SETTINGS, recursive=False)
                    for path in self._vaults():
                        archive.add(path, arcname=f"kasalar/{path.name}", recursive=False)
                digest = _digest(temporary)
                existing = self._objects()
                if existing:
                    previous_digest = self._archive_digest(existing[0].name)
                    if not self._snapshot_matches(existing[0], previous_digest,
                                                  encrypted, verified):
                        raise RuntimeError(
                            "Flex yedeği açılamadı; şifreleme anahtarını kontrol edin.")
                    if previous_digest == digest:
                        return
                self._encrypt(temporary, encrypted)
                self._decrypt(encrypted, verified)
                if _digest(verified) != digest:
                    raise RuntimeError("Flex kasa kopyası GPG doğrulamasından geçmedi.")
                destination = self.snapshots / f"{time.time_ns():019d}-{digest}.tar.gpg"
                check = self.root / ".flex-save-readback.tar.gpg"
                check_tar = self.root / ".flex-save-readback.tar"
                try:
                    shutil.copyfile(encrypted, destination)
                    shutil.copyfile(destination, check)
                    self._decrypt(check, check_tar)
                    if _digest(check_tar) != digest:
                        raise RuntimeError("Flex kasa kopyası doğrulanamadı.")
                except Exception:
                    destination.unlink(missing_ok=True)
                    raise
                finally:
                    check.unlink(missing_ok=True)
                    check_tar.unlink(missing_ok=True)
                for old in self._objects()[2:]:
                    try:
                        old.unlink()
                    except OSError:
                        pass
            finally:
                temporary.unlink(missing_ok=True)
                encrypted.unlink(missing_ok=True)
                verified.unlink(missing_ok=True)
