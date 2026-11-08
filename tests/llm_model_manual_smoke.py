"""Verify RunPod exposes configured LLM profiles for explicit selection."""
import copy
import json
import os
import sys
import tempfile

sys.path.insert(0, "/opt/webui_flux2")

import pod_setup
import llm


def main():
    profiles = llm.model_profilleri()
    assert profiles and all(item.get("id") for item in profiles), profiles
    assert {item["id"] for item in profiles} == {"llm_26b"}, profiles
    assert not any(item in pod_setup.WORKFLOWS for item in ("llama", "llm_12b"))

    # A custom text-only model can omit both sidecar components. Its profile
    # remains selectable and the model loader disables image input cleanly.
    catalog_path = os.environ["POD_MODEL_CATALOG"]
    catalog = json.loads(open(catalog_path, encoding="utf-8").read())
    catalog = copy.deepcopy(catalog)
    catalog["groups"]["fixture_text_only"] = {
        "label": "Fixture · metin",
        "engine": "llama",
        "files": [{"name": "fixture-text.gguf", "dir": "llm", "size": 100}],
    }
    catalog["workflows"]["fixture_text_only"] = {
        "group": "fixture_text_only",
        "model_file": "fixture-text.gguf",
        "gpu_device": "CUDA0",
        "gpu_layers": "all",
        "context": 2048,
    }
    old_ready = pod_setup._ready
    old_profiles = llm._RUNPOD_PROFILES
    old_model_path, old_mmproj_path = llm.MODEL_PATH, llm.MMPROJ_PATH
    old_active_profile = llm._ACTIVE_PROFILE
    old_multimodal = llm.MULTIMODAL_ENABLED
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8",
                                         delete=False) as stream:
            json.dump(catalog, stream)
            fixture_catalog = stream.name
        os.environ["POD_MODEL_CATALOG"] = fixture_catalog
        pod_setup._ready = False
        fixture_profiles = llm._load_runpod_profiles()
        assert "fixture_text_only" in fixture_profiles, fixture_profiles
        assert fixture_profiles["fixture_text_only"]["mmproj"] is None
        assert fixture_profiles["fixture_text_only"]["mtp"] is None
        llm._RUNPOD_PROFILES = fixture_profiles
        llm.model_ayarla("fixture_text_only")
        assert llm.MMPROJ_PATH == ""
        llm.ayarlarini_uygula({"llm_multimodal": True})
        assert llm.MULTIMODAL_ENABLED is False
    finally:
        os.environ["POD_MODEL_CATALOG"] = catalog_path
        pod_setup._ready = old_ready
        llm._RUNPOD_PROFILES = old_profiles
        llm.MODEL_PATH, llm.MMPROJ_PATH = old_model_path, old_mmproj_path
        llm._ACTIVE_PROFILE = old_active_profile
        llm.MULTIMODAL_ENABLED = old_multimodal
        try:
            os.unlink(fixture_catalog)
        except (NameError, OSError):
            pass

    print("LLM manual model selection smoke passed")


if __name__ == "__main__":
    main()
