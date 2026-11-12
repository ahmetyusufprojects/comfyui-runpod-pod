"""Safe checks for the passwordless installer, storage allowlist, and archive delete."""

import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, "/opt/webui_flux2")


def http_json(url, method="GET", payload=None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, method=method,
        headers={"Content-Type": "application/json"} if body is not None else {})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def check_installer_routes():
    base = "http://127.0.0.1:8192"
    status, data = http_json(base + "/api/setup-state")
    assert status == 200 and data.get("parolasiz") is True
    status, data = http_json(base + "/api/ben")
    assert status == 200 and data.get("parolasiz") is True
    status, data = http_json(base + "/api/storage-clean", "POST", {"onay": False})
    assert status == 400, (status, data)
    status, data = http_json("http://127.0.0.1:8189/api/ben")
    assert status == 401, (status, data)
    status, setup = http_json("http://127.0.0.1:8189/api/kurulum-durumu")
    assert status == 200 and setup.get("runpod") is True, (status, setup)
    assert setup.get("kurulu") is True, setup
    assert setup.get("storage_mode") == "ephemeral", setup
    print("passwordless installer is limited to port 8192; main WebUI still requires login")


def check_volume_optional_first_run():
    import pod_setup

    class EmptyAccounts:
        def __init__(self, path):
            self.dosya = str(path)

        def kurulu_mu(self):
            return False

        def ilk_admin(self, *_args):
            raise AssertionError("first admin must be created through the WebUI")

    keys = ("POD_VOLUME_MOUNT", "POD_ALLOW_EPHEMERAL_DATA", "POD_FLEX_PREFIX",
            "WEBUI_AUTO_DOWNLOAD")
    old_env = {key: os.environ.get(key) for key in keys}
    names = ("ROOT", "COMFY", "_data_lock", "_flex", "_ready",
             "_system_settings_path", "_storage_mode", "validate_mount")
    old_state = {name: getattr(pod_setup, name) for name in names}

    def close_lock():
        lock = getattr(pod_setup, "_data_lock", None)
        if lock is not None:
            try:
                lock.close()
            except OSError:
                pass
        pod_setup._data_lock = None

    try:
        os.environ["POD_ALLOW_EPHEMERAL_DATA"] = "1"
        os.environ["POD_FLEX_PREFIX"] = "gallery-webui"
        os.environ["WEBUI_AUTO_DOWNLOAD"] = "0"
        with tempfile.TemporaryDirectory(prefix="gallery-volume-check-") as temporary:
            base = Path(temporary)
            runtime = base / "runtime"
            user_data = runtime / "user-data"
            missing_volume = base / "volume-not-attached"
            pod_setup.ROOT = runtime
            pod_setup.COMFY = runtime / "ComfyUI"
            pod_setup._flex = None
            pod_setup._ready = False
            pod_setup._system_settings_path = None
            pod_setup._storage_mode = "unknown"
            pod_setup._data_lock = None
            os.environ["POD_VOLUME_MOUNT"] = str(missing_volume)

            no_volume_account = EmptyAccounts(user_data / "kullanicilar.json")
            pod_setup.prepare_data(str(user_data), no_volume_account)
            assert pod_setup.storage_status() == {
                "storage_mode": "ephemeral", "storage_persistent": False}
            assert pod_setup.is_ready()
            assert not Path(no_volume_account.dosya).exists()
            assert pod_setup._flex is None
            assert pod_setup._system_settings_path == user_data / "system-settings.json"
            assert pod_setup._system_settings_versions()
            close_lock()

            volume = base / "empty-volume"
            volume.mkdir()
            os.environ["POD_VOLUME_MOUNT"] = str(volume)
            pod_setup.validate_mount = lambda: volume  # simulate an attached empty mount
            pod_setup._ready = False
            pod_setup._system_settings_path = None
            pod_setup._storage_mode = "unknown"
            mounted_account = EmptyAccounts(user_data / "kullanicilar.json")
            pod_setup.prepare_data(str(user_data), mounted_account)
            assert pod_setup.storage_status() == {
                "storage_mode": "persistent", "storage_persistent": True}
            assert not Path(mounted_account.dosya).exists()
            assert pod_setup._flex is not None
            assert pod_setup._system_settings_path == volume / "gallery-webui" / "system-settings.json"
            assert pod_setup._system_settings_versions()
            close_lock()
    finally:
        close_lock()
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        for name, value in old_state.items():
            setattr(pod_setup, name, value)
    print("no-volume and empty-volume startup both remain ready for first-account creation")


def check_storage_clean_allowlist():
    import pod_setup

    with tempfile.TemporaryDirectory(prefix="gallery-clean-check-") as temporary:
        base = Path(temporary)
        runtime = base / "runtime"
        user_data = runtime / "user-data"
        volume = base / "flex"
        model_storage = base / "models"
        user_data.mkdir(parents=True)
        volume.mkdir()
        model_storage.mkdir()
        (user_data / "synthetic-account.marker").write_text("keep", encoding="utf-8")
        settings = volume / "general-settings.json"
        settings.write_text('{"synthetic":true}', encoding="utf-8")
        (model_storage / "downloaded-model.marker").write_bytes(b"remove model")

        for name in ("ComfyUI", "comfy-venv", "bin", "llama", "hf-cache",
                     "cache", "uv-cache", "pip-cache"):
            folder = runtime / name
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "discard.marker").write_bytes(b"delete me")
        (runtime / "setup.log").write_text("temporary log", encoding="utf-8")

        old_env = {key: os.environ.get(key) for key in
                   ("WEBUI_DATA_ROOT", "POD_VOLUME_MOUNT", "HF_HOME",
                    "XDG_CACHE_HOME", "UV_CACHE_DIR", "POD_MODEL_STORAGE")}
        try:
            os.environ["WEBUI_DATA_ROOT"] = str(user_data)
            os.environ["POD_VOLUME_MOUNT"] = str(volume)
            os.environ["HF_HOME"] = str(runtime / "hf-cache")
            os.environ["XDG_CACHE_HOME"] = str(runtime / "cache")
            os.environ["UV_CACHE_DIR"] = str(runtime / "uv-cache")
            os.environ["POD_MODEL_STORAGE"] = str(model_storage)
            pod_setup.ROOT = runtime
            pod_setup.COMFY = runtime / "ComfyUI"
            pod_setup._comfy_process = None
            pod_setup._active = None
            pod_setup._queue.clear()

            import sys
            from types import SimpleNamespace
            sys.modules["llm"] = SimpleNamespace(durdur=lambda: None)
            sys.modules["prompt_translation"] = SimpleNamespace(modeli_bosalt=lambda: None)
            result = pod_setup.clear_rebuildable_storage()
        finally:
            for key, value in old_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        assert result["preserved_user_data"] is True
        assert result["preserved_flex_settings"] is True
        assert (user_data / "synthetic-account.marker").read_text(encoding="utf-8") == "keep"
        assert settings.read_text(encoding="utf-8") == '{"synthetic":true}'
        assert not model_storage.exists()
        assert not (runtime / "setup.log").exists()
        assert not any((runtime / name).exists() for name in
                       ("ComfyUI", "comfy-venv", "bin", "llama", "hf-cache",
                        "cache", "uv-cache", "pip-cache"))
    print("storage clean removes only reinstallable files and keeps account and general settings")


def check_archive_deletion():
    import arsiv
    from PIL import Image

    with tempfile.TemporaryDirectory(prefix="gallery-archive-check-") as temporary:
        root = Path(temporary)
        library = root / "kutuphane"
        library.mkdir()
        image_path = library / "kept.png"
        Image.new("RGB", (3, 2), (31, 73, 119)).save(image_path)
        image_bytes = image_path.read_bytes()
        image_path.unlink()
        imported = arsiv.arsiv_ice_aktar(str(root), [{"bayt": image_bytes,
                                                       "mime": "image/png"}])
        assert imported["eklenen"] == 1
        image_hash = imported["kayitlar"][0]["hash"]
        image_path.write_bytes(image_bytes)
        before = arsiv.arsiv_listesi(str(root))
        entry = next(item for item in before["kayitlar"] if item["hash"] == image_hash)
        assert entry["silinebilir"] is True

        deleted = arsiv.arsiv_sil_coklu(str(root), [image_hash])
        assert deleted == [image_hash]
        after = arsiv.arsiv_listesi(str(root))
        assert all(item["hash"] != image_hash for item in after["kayitlar"])
        assert image_path.is_file()

        restored = arsiv.arsiv_ice_aktar(str(root), [{"bayt": image_bytes,
                                                       "mime": "image/png"}])
        assert restored["eklenen"] == 1
        assert any(item["hash"] == image_hash
                   for item in arsiv.arsiv_listesi(str(root))["kayitlar"])

        try:
            arsiv.arsiv_sil_coklu(str(root), ["../outside"])
        except ValueError:
            pass
        else:
            raise AssertionError("invalid archive hashes must be rejected")
    print("archive multi-delete hides every selected image and preserves source originals")


if __name__ == "__main__":
    check_installer_routes()
    check_volume_optional_first_run()
    check_storage_clean_allowlist()
    check_archive_deletion()
