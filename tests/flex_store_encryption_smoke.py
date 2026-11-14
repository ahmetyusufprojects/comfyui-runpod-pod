"""Exercise Flex snapshot encryption using isolated synthetic files only."""

import json
import hashlib
import io
import sys
import tarfile
import tempfile
from pathlib import Path

_webui_path = Path("/opt/webui_flux2")
if not _webui_path.is_dir():
    _webui_path = Path(__file__).resolve().parents[1] / "webui_flux2"
sys.path.insert(0, str(_webui_path))
from flex_store import FlexStore


def check_encrypted_round_trip():
    with tempfile.TemporaryDirectory(prefix="flex-encryption-check-") as temporary:
        base = Path(temporary)
        root = base / "container-data"
        snapshots = base / "mounted-volume" / "synthetic-prefix"
        gpg_home = base / "gpg-home"
        root.mkdir()
        gpg_home.mkdir(mode=0o700)
        (root / "kasalar").mkdir()
        marker = "synthetic-account-secret-marker"
        account = {"kullanicilar": {"synthetic": marker}, "oturumlar": {"fake": "token"}}
        settings = {"schema": 1, "synthetic-setting": marker}
        (root / "kullanicilar.json").write_text(json.dumps(account), encoding="utf-8")
        (root / "system-settings.json").write_text(json.dumps(settings), encoding="utf-8")
        vault = b"synthetic encrypted vault bytes"
        (root / "kasalar" / "synthetic.tar.gpg").write_bytes(vault)

        store = FlexStore(root, snapshots, "synthetic-passphrase-only-for-test", gpg_home=gpg_home)
        store.save()
        objects = store._objects()
        assert len(objects) == 1 and objects[0].name.endswith(".tar.gpg")
        ciphertext = objects[0].read_bytes()
        assert marker.encode() not in ciphertext
        assert list(snapshots.iterdir()) == objects

        (root / "kullanicilar.json").unlink()
        (root / "system-settings.json").unlink()
        (root / "kasalar" / "synthetic.tar.gpg").unlink()
        store.restore()

        restored_account = json.loads((root / "kullanicilar.json").read_text(encoding="utf-8"))
        restored_settings = json.loads((root / "system-settings.json").read_text(encoding="utf-8"))
        assert restored_account["kullanicilar"]["synthetic"] == marker
        assert restored_account["oturumlar"] == {}
        assert restored_settings["synthetic-setting"] == marker
        assert (root / "kasalar" / "synthetic.tar.gpg").read_bytes() == vault
        assert not any(root.glob(".flex-restore*"))
        assert not any(root.glob(".flex-save*"))

        wrong_key = FlexStore(root, snapshots, "wrong-synthetic-key", gpg_home=gpg_home)
        (root / "kullanicilar.json").unlink()
        try:
            wrong_key.restore()
        except RuntimeError as error:
            assert "şifreli" in str(error).lower()
        else:
            raise AssertionError("A different key must not restore the encrypted snapshot")
        assert not (root / "kullanicilar.json").exists()


def check_known_legacy_files_are_encrypted_before_removal():
    with tempfile.TemporaryDirectory(prefix="flex-legacy-check-") as temporary:
        base = Path(temporary)
        root = base / "container-data"
        snapshots = base / "mounted-volume" / "legacy-prefix"
        gpg_home = base / "gpg-home"
        snapshots.mkdir(parents=True)
        gpg_home.mkdir(mode=0o700)
        account_marker = "legacy-synthetic-account"
        vault_marker = b"legacy synthetic encrypted vault"
        account_payload = json.dumps({
            "kullanicilar": {"synthetic": account_marker},
            "oturumlar": {"old-token": "must-be-dropped"},
        }, ensure_ascii=False).encode("utf-8")
        tar_bytes = io.BytesIO()
        with tarfile.open(fileobj=tar_bytes, mode="w") as archive:
            for name, content in (
                ("kullanicilar.json", account_payload),
                ("kasalar/synthetic.tar.gpg", vault_marker),
            ):
                member = tarfile.TarInfo(name)
                member.mode = 0o600
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
        legacy_bytes = tar_bytes.getvalue()
        legacy_digest = hashlib.sha256(legacy_bytes).hexdigest()
        legacy_tar = snapshots / f"1794657000000000000-{legacy_digest}.tar"
        legacy_tar.write_bytes(legacy_bytes)

        old_settings = json.dumps({"schema": 1, "auto_download": False}).encode()
        latest_settings = json.dumps({
            "schema": 1, "auto_download": True,
            "synthetic-setting-marker": account_marker,
        }).encode()
        (snapshots / "system-settings.json").write_bytes(old_settings)
        # Simulate an interrupted earlier copy: migration must keep the source,
        # replace the incomplete ciphertext, and verify the replacement first.
        (snapshots / "system-settings.json.gpg").write_bytes(b"partial-ciphertext")
        version_digest = hashlib.sha256(latest_settings).hexdigest()
        versioned_settings = snapshots / (
            "system-settings-1794657000000000001-0123456789abcdef-"
            f"{version_digest}.json")
        versioned_settings.write_bytes(latest_settings)

        store = FlexStore(root, snapshots, "synthetic-legacy-passphrase", gpg_home=gpg_home)
        store.migrate_legacy()
        assert root.is_dir()
        assert not legacy_tar.exists()
        assert not (snapshots / "system-settings.json").exists()
        assert not versioned_settings.exists()
        assert store._objects()
        assert not list(snapshots.glob("system-settings*.json"))

        encrypted_sources = list(snapshots.iterdir())
        assert all(path.name.endswith(".gpg") for path in encrypted_sources)
        assert all(account_marker.encode() not in path.read_bytes()
                   for path in encrypted_sources)

        store.restore()
        restored = json.loads((root / "kullanicilar.json").read_text(encoding="utf-8"))
        assert restored["kullanicilar"]["synthetic"] == account_marker
        assert restored["oturumlar"] == {}
        settings = json.loads((root / "system-settings.json").read_text(encoding="utf-8"))
        assert settings["auto_download"] is True
        assert settings["synthetic-setting-marker"] == account_marker
        assert (root / "kasalar" / "synthetic.tar.gpg").read_bytes() == vault_marker


def check_settings_restore_does_not_replace_account():
    with tempfile.TemporaryDirectory(prefix="flex-settings-check-") as temporary:
        base = Path(temporary)
        root = base / "container-data"
        snapshots = base / "mounted-volume" / "settings-prefix"
        gpg_home = base / "gpg-home"
        root.mkdir()
        (root / "kasalar").mkdir()
        account = {"kullanicilar": {"synthetic": "current-account"}, "oturumlar": {}}
        settings = {"schema": 1, "synthetic-setting": "restored-value"}
        (root / "kullanicilar.json").write_text(json.dumps(account), encoding="utf-8")
        (root / "system-settings.json").write_text(json.dumps(settings), encoding="utf-8")
        store = FlexStore(root, snapshots, "synthetic-settings-passphrase", gpg_home=gpg_home)
        store.save()
        (root / "system-settings.json").unlink()

        assert store.restore_settings() is True
        restored_account = json.loads((root / "kullanicilar.json").read_text(encoding="utf-8"))
        assert restored_account == account
        restored_settings = json.loads((root / "system-settings.json").read_text(encoding="utf-8"))
        assert restored_settings == settings


if __name__ == "__main__":
    check_encrypted_round_trip()
    check_known_legacy_files_are_encrypted_before_removal()
    check_settings_restore_does_not_replace_account()
    print("Flex snapshots are encrypted, authenticated, and restored only with the configured key")
