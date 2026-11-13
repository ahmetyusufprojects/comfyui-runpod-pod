"""Catalog and API-graph checks for the current open Qwen Image 2.1 edit modes."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "docker" / "models.json"
catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
normal = catalog["groups"]["qwen_image_edit"]
turbo = catalog["groups"]["qwen_image_edit_turbo"]
normal_workflow = catalog["workflows"]["qwen_image_edit"]
turbo_workflow = catalog["workflows"]["qwen_image_edit_turbo"]

assert normal["engine"] == turbo["engine"] == "comfy"
assert "depends_on" not in turbo
assert normal_workflow["steps"] == 25
assert turbo_workflow["steps"] == 8
assert turbo_workflow["cfg"] == 1.0
assert len(turbo_workflow["sigmas"].split(",")) == 9

normal_diffusion, encoder, vae = normal["files"]
turbo_diffusion = turbo["files"][0]
assert normal_diffusion["name"] == "qwen_image_2.1_int8_convrot.safetensors"
assert normal_diffusion["sha256"] == "cb74113cb03faecd79611b01fd7fd642f0aa60d6f0b95086abee214d75eaa57d"
assert turbo_diffusion["name"] == "qwen_image_2.1_turbo_int8_convrot.safetensors"
assert turbo_diffusion["sha256"] == "8144a0182964aa8403ad6dbc011db837304167d235161b9aabaa8532e967314c"
assert encoder["name"] == "qwen3vl_8b_int8_convrot_heretic.safetensors"
assert "heretic" in encoder["repo"].lower()
assert vae["name"] == "qwen_image_2.1_vae_bf16.safetensors"
assert vae["sha256"] == "bb21f7473051e1ac368515dd3f2e15cd44d7a11748ee8823e1ddca3e4876b7c9"
assert normal["files"][1:] == turbo["files"][1:]

os.environ["WEBUI_RUNPOD"] = "1"
os.environ["POD_MODEL_CATALOG"] = str(CATALOG_PATH)
sys.path.insert(0, str(ROOT / "webui_flux2"))
import image_graphs  # noqa: E402

for is_turbo in (False, True):
    group = turbo if is_turbo else normal
    diffusion = group["files"][0]
    graph, seed = image_graphs.build_qwen_image_edit_prompt(
        {"prompt": "Add a red scarf", "negative": "blurry", "seed": 17,
         "width": 768, "height": 768, "steps": 32, "cfg": 3.5,
         "model_files": {"diffusion": diffusion["name"],
                         "text_encoder": encoder["name"], "vae": vae["name"]}},
        "synthetic.png", "synthetic-reference.png", turbo=is_turbo)
    assert seed == 17
    assert graph["1"]["class_type"] == "UNETLoader"
    assert graph["1"]["inputs"]["unet_name"] == diffusion["name"]
    assert graph["2"]["class_type"] == "QwenImage21Cache"
    assert graph["3"]["inputs"]["type"] == "qwen_image"
    assert graph["5"]["class_type"] == "TextEncodeQwenImage21"
    assert graph["5"]["inputs"]["negative_prompt"] == "blurry"
    assert graph["5"]["inputs"]["image_1"] == ["31", 0]
    assert graph["5"]["inputs"]["image_2"] == ["32", 0]
    if is_turbo:
        assert graph["7"]["inputs"]["cfg"] == 1.0
        assert graph["9"]["inputs"]["sigmas"] == turbo_workflow["sigmas"]
        assert graph["10"]["class_type"] == "SamplerCustomAdvanced"
    else:
        assert graph["6"]["class_type"] == "KSampler"
        assert graph["6"]["inputs"]["steps"] == 32
        assert graph["6"]["inputs"]["cfg"] == 3.5

try:
    image_graphs.build_qwen_image_edit_prompt({"prompt": "Edit", "seed": 17})
except ValueError as error:
    assert "referans görsel" in str(error)
else:
    raise AssertionError("Qwen Image 2.1 edit should require a source image")

print("Qwen Image 2.1 normal/Turbo catalog and graphs passed")
