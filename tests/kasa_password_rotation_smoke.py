"""Run an encrypted vault password change using only synthetic files."""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path("/opt/webui_flux2")
sys.path.insert(0, str(ROOT))


def main():
    gpg = shutil.which("gpg")
    if not gpg:
        raise SystemExit("gpg is required for the vault rotation smoke test")
    with tempfile.TemporaryDirectory(prefix="vault-rotation-smoke-") as temporary:
        root = Path(temporary)
        os.environ["WEBUI_DATA_ROOT"] = str(root / "data")
        os.environ["GPG_HOME"] = str(root / "gpg-home")
        os.environ["GPG_PATH"] = gpg

        import kasa

        user = "rotation_smoke"
        old = "OldVaultPass!2026"
        new = "NewVaultPass!2026"
        source = root / "synthetic-source"
        source.mkdir()
        (source / "note.txt").write_text("synthetic vault payload", encoding="utf-8")

        kasa.olustur(user, old, old, str(source))
        kasa.ac(user, old)
        work = Path(kasa.calisma_koku(user))
        (work / "updated.txt").write_text("latest synthetic payload", encoding="utf-8")
        kasa.kapat(user)
        kasa.ac(user, old)

        assert kasa.kapanis_baslat(user)
        kasa.parola_degistir(user, old, new, new)
        assert kasa.durum(user)["acik"] and kasa.durum(user)["kapaniyor"]
        assert (work / "note.txt").read_text(encoding="utf-8") == "synthetic vault payload"
        assert (work / "updated.txt").read_text(encoding="utf-8") == "latest synthetic payload"

        current = Path(kasa._kasa_dosya(user))
        previous = Path(kasa._onceki_dosya(user))
        assert kasa._deneme_cozum(str(current), bytearray(new.encode("utf-8")))
        assert not kasa._deneme_cozum(str(current), bytearray(old.encode("utf-8")))
        assert previous.is_file()
        assert kasa._deneme_cozum(str(previous), bytearray(new.encode("utf-8")))
        assert not kasa._deneme_cozum(str(previous), bytearray(old.encode("utf-8")))

        kasa.kapanis_iptal(user)
        kasa.kapat(user)
        try:
            kasa.ac(user, old)
        except kasa.KasaHatasi:
            pass
        else:
            raise AssertionError("old password still opened the rotated vault")
        kasa.ac(user, new)

        # A bad current password must leave the open vault and its key intact.
        assert kasa.kapanis_baslat(user)
        try:
            kasa.parola_degistir(user, old, "AnotherVaultPass!2026",
                                 "AnotherVaultPass!2026")
        except kasa.KasaHatasi:
            pass
        else:
            raise AssertionError("rotation accepted an incorrect current password")
        kasa.kapanis_iptal(user)
        assert kasa.durum(user)["acik"]
        assert kasa._deneme_cozum(str(current), bytearray(new.encode("utf-8")))
        kasa.kapat(user)

    print("vault password rotation smoke passed")


if __name__ == "__main__":
    main()
