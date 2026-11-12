"""Round-trip a complete synthetic user/settings backup through GPG."""
import json
import hashlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/opt/webui_flux2")


def main():
    gpg = shutil.which("gpg")
    if not gpg:
        raise SystemExit("gpg is required for the system backup smoke test")
    with tempfile.TemporaryDirectory(prefix="system-backup-smoke-") as temporary:
        root = Path(temporary)
        os.environ["GPG_HOME"] = str(root / "gpg-home")
        os.environ["GPG_PATH"] = gpg
        import kasa

        data = root / "data"
        (data / "kasalar").mkdir(parents=True)
        (data / "veriler" / "first_user").mkdir(parents=True)
        accounts = {"kullanicilar": {
            "first_user": {"rol": "admin", "sifre": "synthetic-hash"},
            "second_user": {"rol": "uye", "sifre": "synthetic-hash-2"}},
            "oturumlar": {"must-not-transfer": {"kullanici": "first_user"}}}
        (data / "kullanicilar.json").write_text(json.dumps(accounts), encoding="utf-8")
        (data / "kasalar" / "first_user.tar.gpg").write_bytes(b"synthetic encrypted vault")
        (data / "veriler" / "first_user" / "prefs.json").write_text('{"theme":"night"}', encoding="utf-8")
        volume = root / "volume" / "flex-small"
        volume.mkdir(parents=True)
        settings_path = volume / "system-settings.json"
        original_settings = {"schema": 1, "auto_download": False,
                             "default_download_order": ["z_image"]}
        settings_path.write_text('{"schema":1,"auto_download":true}', encoding="utf-8")
        version_payload = json.dumps(original_settings).encode("utf-8")
        digest = hashlib.sha256(version_payload).hexdigest()
        version = volume / ("system-settings-0000000000000000001-0123456789abcdef-"
                            + digest + ".json")
        version.write_bytes(version_payload)

        password = "SystemBackup!2026"
        archive = Path(kasa.sistem_yedegi_uret(data, settings_path, password))
        assert archive.is_file() and archive.stat().st_size > 0
        (data / "kullanicilar.json").write_text('{"kullanicilar":{},"oturumlar":{}}', encoding="utf-8")
        (data / "kasalar" / "first_user.tar.gpg").unlink()
        (data / "veriler" / "first_user" / "prefs.json").write_text('{"theme":"changed"}', encoding="utf-8")
        settings_path.write_text('{"schema":1,"auto_download":true}', encoding="utf-8")

        result = kasa.sistem_yedegi_ice_aktar(archive, password, data, settings_path)
        assert result["kullanici_sayisi"] == 2
        restored = json.loads((data / "kullanicilar.json").read_text(encoding="utf-8"))
        assert set(restored["kullanicilar"]) == {"first_user", "second_user"}
        assert restored["oturumlar"] == {}
        assert (data / "kasalar" / "first_user.tar.gpg").read_bytes() == b"synthetic encrypted vault"
        assert json.loads((data / "veriler" / "first_user" / "prefs.json").read_text()) == {"theme": "night"}
        assert json.loads(settings_path.read_text(encoding="utf-8")) == original_settings
        assert version.is_file()

        try:
            kasa.sistem_yedegi_ice_aktar(archive, "wrong-password", data, settings_path)
        except kasa.KasaHatasi:
            pass
        else:
            raise AssertionError("wrong system-backup password was accepted")
        print("full system backup encrypted round-trip restored all users and volume settings")


if __name__ == "__main__":
    main()
