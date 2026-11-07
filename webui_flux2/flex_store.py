"""Store verified, versioned vault snapshots on Runpod Flex object storage."""

import hashlib
import io
import json
import re
import shutil
import tarfile
import threading
import time
from pathlib import Path

_OBJECT = re.compile(r"\d{19}-[a-f0-9]{64}\.tar")
_VAULT = re.compile(r"[a-z0-9][a-z0-9_-]{1,31}(?:\.onceki)?\.tar\.gpg|[a-z0-9][a-z0-9_-]{1,31}\.sure")
_ACCOUNTS = "kullanicilar.json"


def _digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class FlexStore:
    def __init__(self, root, snapshots):
        self.root = Path(root)
        self.snapshots = Path(snapshots)
        self.lock = threading.RLock()

    def _objects(self):
        return sorted((p for p in self.snapshots.iterdir() if _OBJECT.fullmatch(p.name)),
                      reverse=True)

    def _vaults(self):
        return sorted(p for p in (self.root / "kasalar").glob("*")
                      if _VAULT.fullmatch(p.name) and p.is_file() and not p.is_symlink())

    def restore(self):
        self.snapshots.mkdir(parents=True, exist_ok=True)
        objects = self._objects()
        if not objects:
            return
        candidate = self.root / ".flex-restore.tar"
        try:
            for obj in objects:
                shutil.copyfile(obj, candidate)
                if _digest(candidate) != obj.stem.split("-", 1)[1]:
                    continue
                with tarfile.open(candidate, "r") as archive:
                    for item in archive:
                        name = item.name
                        if (not item.isfile() or
                            (name != _ACCOUNTS and not (
                                name.startswith("kasalar/") and _VAULT.fullmatch(name[8:])))):
                            raise RuntimeError("Beklenmeyen Flex arşiv girdisi.")
                        output = self.root / name
                        output.parent.mkdir(parents=True, exist_ok=True)
                        with archive.extractfile(item) as src, output.open("wb") as dst:
                            shutil.copyfileobj(src, dst)
                return
            raise RuntimeError("Flex’te doğrulanabilen kasa kopyası bulunamadı.")
        finally:
            candidate.unlink(missing_ok=True)

    def save(self):
        with self.lock:
            account_file = self.root / _ACCOUNTS
            if not account_file.is_file():
                return
            accounts = json.loads(account_file.read_text(encoding="utf-8"))
            accounts["oturumlar"] = {}
            payload = json.dumps(accounts, ensure_ascii=False).encode("utf-8")
            temporary = self.root / ".flex-save.tar"
            try:
                with tarfile.open(temporary, "w") as archive:
                    member = tarfile.TarInfo(_ACCOUNTS)
                    member.mode = 0o600
                    member.size = len(payload)
                    archive.addfile(member, io.BytesIO(payload))
                    for path in self._vaults():
                        archive.add(path, arcname=f"kasalar/{path.name}", recursive=False)
                digest = _digest(temporary)
                existing = self._objects()
                if existing and existing[0].stem.endswith(digest):
                    return
                destination = self.snapshots / f"{time.time_ns():019d}-{digest}.tar"
                shutil.copyfile(temporary, destination)
                if _digest(destination) != digest:
                    raise RuntimeError("Flex kasa kopyası doğrulanamadı.")
                for old in self._objects()[2:]:
                    try:
                        old.unlink()
                    except OSError:
                        pass
            finally:
                temporary.unlink(missing_ok=True)
