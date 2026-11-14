"""A legacy system backup keeps current built-ins and restores compatible LoRAs."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/opt/webui_flux2")
import pod_setup

with tempfile.TemporaryDirectory(prefix="legacy-system-settings-") as temporary:
    pod_setup._system_settings_path = Path(temporary) / "system-settings.json"
    legacy = {
        "schema": 1,
        "auto_download": False,
        "default_download_order": ["zimage"],
        "model_instances": [{"id": "z_image", "name": "Legacy Z name"}],
        "link_overrides": {},
        "loras": [{
            "id": "0123456789ab",
            "name": "Legacy Z LoRA",
            "model": "z_image",
            "group": "lora_0123456789ab",
            "file": "legacy-z-style.safetensors",
            "weight": 0.85,
            "enabled": True,
            "default_selected": True,
            "auto_download": False,
            "link": ("https://huggingface.co/example/legacy-loras/resolve/"
                     + "a" * 40 + "/legacy-z-style.safetensors"),
        }],
    }
    pod_setup._system_settings_path.write_text(json.dumps(legacy), encoding="utf-8")
    pod_setup.refresh_after_system_import()

    options = pod_setup.model_options()
    by_id = {item["id"]: item for item in options}
    assert "flux3" in by_id, "a model added after the backup must remain available"
    assert by_id["z_image"]["name"] == "Legacy Z name"
    loras = pod_setup.lora_status("z_image")
    restored = next(item for item in loras if item["id"] == "0123456789ab")
    assert restored["name"] == "Legacy Z LoRA"
    assert "lora_0123456789ab" in pod_setup.CATALOG
    source = pod_setup.CATALOG["lora_0123456789ab"]["files"][0]
    assert source["repo"] == "example/legacy-loras"
    assert source["path"] == "legacy-z-style.safetensors"

print("legacy backups retain new built-in models and restore old LoRAs")
