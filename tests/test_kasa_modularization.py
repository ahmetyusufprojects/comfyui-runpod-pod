"""Keep the public kasa module and shared live state stable during refactors."""

import sys
from pathlib import Path


WEBUI_ROOT = Path(__file__).resolve().parents[1] / "webui_flux2"
if str(WEBUI_ROOT) not in sys.path:
    sys.path.insert(0, str(WEBUI_ROOT))

import kasa
from kasa_modules import accounts, backup, crypto, vault


def test_facade_preserves_module_identity_and_public_operations():
    assert sys.modules["kasa"] is kasa
    assert all(module._ctx is kasa for module in (accounts, backup, crypto, vault))

    for name in (
        "KasaHatasi", "ac", "aciklar", "olustur", "profil_sil", "parola_degistir",
        "kapat", "kurtar", "yedek_uret", "yedek_ice_aktar", "sistem_yedegi_uret",
        "sistem_yedegi_ice_aktar", "sistem_yedegi_medya_haric_kasalar",
    ):
        assert hasattr(kasa, name), name

    assert kasa.ac.__module__ == "kasa_modules.vault"
    assert kasa.olustur.__module__ == "kasa_modules.accounts"
    assert kasa.sistem_yedegi_uret.__module__ == "kasa_modules.backup"
    assert kasa.yedek_ice_aktar.__wrapped__ is kasa._yedek_ice_aktar_impl


def test_mutable_vault_state_is_owned_by_facade(monkeypatch):
    sentinel = {"synthetic_user": {"kapaniyor": False}}
    monkeypatch.setattr(kasa, "_acik", sentinel)

    assert accounts._ctx._acik is sentinel
    assert vault._ctx._acik is sentinel
    assert backup._ctx._acik is sentinel
