from __future__ import annotations

import datetime as dt
import hashlib
import os
import sys
import threading
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

WEBUI_ROOT = Path(__file__).resolve().parents[1] / "webui_flux2"
if str(WEBUI_ROOT) not in sys.path:
    sys.path.insert(0, str(WEBUI_ROOT))

import prompt_store  # noqa: E402
from services import user_content  # noqa: E402


class _SyntheticVault:
    def __init__(self, root: Path):
        self.CALISMA_DIR = str(root / "vault-work")
        Path(self.CALISMA_DIR).mkdir(parents=True, exist_ok=True)

    def calisma_koku(self, username):
        if username != "synthetic":
            raise ValueError("vault is locked")
        path = os.path.join(self.CALISMA_DIR, username)
        Path(path).mkdir(parents=True, exist_ok=True)
        return path


def _runtime(tmp_path):
    vault = _SyntheticVault(tmp_path)

    def user_root(username):
        if username != "synthetic":
            raise ValueError("invalid user")
        return vault.calisma_koku(username)

    def safe_name(value):
        if not isinstance(value, str):
            return None
        value = value.split("/")[-1].split("\\")[-1]
        if (
            not value
            or value in (".", "..")
            or not all(char.isalnum() or char in "._-" for char in value)
        ):
            return None
        return value

    def ensure_dir(path):
        Path(path).mkdir(parents=True, exist_ok=True)

    root = str(tmp_path)
    return SimpleNamespace(
        DATA_ROOT=root,
        kasa=vault,
        prompt_store=prompt_store,
        RUNPOD_MODE=False,
        MODEL_FLUX2="synthetic-image-model",
        _sohbet_kilit=threading.Lock(),
        _GECMIS_KILIT=threading.RLock(),
        _kullanici_koku=user_root,
        _ham_kullanici_koku=lambda username: os.path.join(root, "raw", username),
        _kullanici_kutuphane=lambda username: os.path.join(
            user_root(username), "kutuphane"
        ),
        _kullanici_gecmis=lambda username: os.path.join(user_root(username), "gecmis"),
        _kullanici_promptlar=lambda username: os.path.join(
            user_root(username), "promptlar.json"
        ),
        _klasor_elin=ensure_dir,
        _guvenli_dosya_adi=safe_name,
        _simdiki_zaman_damgasi=lambda: dt.datetime(2026, 11, 14, 12, 30, 45).strftime(
            "%Y%m%d_%H%M%S"
        ),
        _exif_duzelt=lambda image: image,
        _gecmis_limiti=lambda _username: 100,
    )


def _png_bytes(color="red"):
    image = Image.new("RGB", (8, 6), color=color)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_library_round_trip_dedup_and_safe_delete(tmp_path):
    runtime = _runtime(tmp_path)
    prepared = _png_bytes()

    filename = user_content.kutuphaneye_kaydet(
        runtime, "synthetic", prepared, prepared, 8, 6
    )
    duplicate = user_content.kutuphaneye_kaydet(
        runtime, "synthetic", prepared, prepared, 8, 6
    )

    assert filename.endswith(".png")
    assert duplicate == filename
    listed = user_content.kutuphane_listesi(runtime, "synthetic")
    assert len(listed) == 1
    assert listed[0]["ad"] == filename
    assert listed[0]["boyut"] == [8, 6]
    assert listed[0]["orijinal"]["boyut"] == [8, 6]
    assert (
        user_content.kutuphaneden_sil(runtime, "synthetic", "../../secret.png") is False
    )
    assert user_content.kutuphaneden_sil(runtime, "synthetic", filename) is True
    assert user_content.kutuphane_listesi(runtime, "synthetic") == []


def test_saved_prompts_and_prompt_history_keep_json_shapes(tmp_path):
    runtime = _runtime(tmp_path)
    first = {"ad": "first", "positive": "warm light", "tags": ["warm"]}
    updated = {"ad": "first", "positive": "soft light", "tags": ["soft"]}

    assert user_content.prompt_kaydet(runtime, "synthetic", first) == [first]
    assert user_content.prompt_kaydet(runtime, "synthetic", updated) == [updated]
    user_content.prompt_gecmisi_yaz(
        runtime,
        "synthetic",
        {"id": "prompt-1", "positive": "soft light", "mod": "fikir"},
    )
    assert (
        user_content.prompt_gecmisi_oku(runtime, "synthetic", mod="fikir")[0]["id"]
        == "prompt-1"
    )
    assert user_content.prompt_gecmisi_yildiz(runtime, "synthetic", "prompt-1", True)
    assert user_content.prompt_gecmisi_oku(runtime, "synthetic")[0]["yildiz"] is True
    assert user_content.prompt_gecmisi_sil(runtime, "synthetic", "prompt-1") == 1
    assert user_content.prompt_sil(runtime, "synthetic", "first") is True
    assert user_content.promptlari_oku(runtime, "synthetic") == []


def test_chat_history_round_trip_delete_and_clear(tmp_path):
    runtime = _runtime(tmp_path)
    messages = [{"role": "user", "content": "Hello"}]
    saved_id = user_content._sohbet_kaydet(
        runtime,
        "synthetic",
        messages,
        "Hi",
        {"token": 2, "model": "test"},
        sohbet_id="chat-1",
    )

    assert saved_id == "chat-1"
    record = user_content._sohbet_oku(runtime, "synthetic")[0]
    assert record["mesajlar"] == messages + [{"role": "assistant", "content": "Hi"}]
    assert record["sonuc"]["token"] == 2
    assert user_content._sohbet_sil(runtime, "synthetic", "chat-1") == 1
    assert user_content._sohbet_temizle(runtime, "synthetic") is True
    assert user_content._sohbet_oku(runtime, "synthetic") == []


def test_generated_history_preserves_metadata_album_favorites_and_delete(tmp_path):
    runtime = _runtime(tmp_path)
    metadata = {
        "seed": 42,
        "prompt": "two-frame scene",
        "model": "z-image",
        "foto_hashes": [hashlib.sha256(b"ref").hexdigest()],
        "width": 512,
        "height": 768,
        "steps": 8,
        "cfg": 1.0,
    }
    first, first_files = user_content.gecmise_kaydet(
        runtime, "synthetic", [_png_bytes("red")], metadata
    )
    second, _second_files = user_content.gecmise_kaydet(
        runtime, "synthetic", [_png_bytes("blue")], metadata
    )

    single = user_content.gecmis_listesi(
        runtime, "synthetic", 1, 100, albume_gore=False
    )
    album_view = user_content.gecmis_listesi(
        runtime, "synthetic", 1, 100, albume_gore=True
    )
    assert single["toplam"] == 2
    album = next(item for item in album_view["kayitlar"] if item.get("album"))
    album_items = user_content.gecmis_listesi(
        runtime, "synthetic", 1, 100, album_id=album["album_id"]
    )
    assert album_items["toplam"] == 2
    assert all(
        item["album_id"] == album["album_id"] for item in album_items["kayitlar"]
    )
    assert album_items["kayitlar"][0]["width"] == 512

    assert (
        user_content.gecmis_kaydi_yildiz(
            runtime, "synthetic", first + ".json", True, first_files[0]
        )
        is True
    )
    favorites = user_content.gecmis_listesi(
        runtime, "synthetic", 1, 100, favoriler=True
    )
    assert favorites["toplam"] == 1
    assert favorites["kayitlar"][0]["yildiz"] is True
    assert user_content.gecmis_kaydi_sil(runtime, "synthetic", second + ".json") is True
    assert user_content.gecmis_temizle(runtime, "synthetic") >= 2
    assert user_content.gecmis_listesi(runtime, "synthetic", 1, 100)["toplam"] == 0
