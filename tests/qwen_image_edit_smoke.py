"""Catalog and API-graph checks for Qwen Image Edit 2511 modes."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "docker" / "models.json"
catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
normal = catalog["groups"]["qwen_image_edit"]
turbo = catalog["groups"]["qwen_image_edit_turbo"]
assert normal["engine"] == "comfy"
assert turbo["engine"] == "comfy"
assert turbo["depends_on"] == ["qwen_image_edit"]
assert catalog["workflows"]["qwen_image_edit"]["steps"] == 40
assert catalog["workflows"]["qwen_image_edit_turbo"]["steps"] == 4

diffusion = normal["files"][0]
encoder = normal["files"][1]
assert diffusion["name"] == "qwen_image_edit_2511_int8_convrot.safetensors"
assert encoder["name"] == "qwen_2.5_vl_7b_huihui_abliterated_int8_convrot.safetensors"
assert "abliterated" in encoder["repo"].lower()
assert turbo["files"][0]["name"].startswith("Qwen-Image-Edit-2511-Lightning-4steps")

os.environ["WEBUI_RUNPOD"] = "1"
os.environ["POD_MODEL_CATALOG"] = str(CATALOG_PATH)
sys.path.insert(0, str(ROOT / "webui_flux2"))
import image_graphs  # noqa: E402

for is_turbo in (False, True):
    graph, seed = image_graphs.build_qwen_image_edit_prompt(
        {"prompt": "Add a red scarf", "negative": "blurry", "seed": 17,
         "width": 768, "height": 768, "steps": 32, "cfg": 3.5,
         "model_files": {"diffusion": diffusion["name"],
                         "text_encoder": encoder["name"],
                         "vae": normal["files"][2]["name"],
                         "lora": turbo["files"][0]["name"]}},
        "synthetic.png", "synthetic-reference.png", turbo=is_turbo)
    assert seed == 17
    assert graph["1"]["class_type"] == "UNETLoader"
    assert graph["2"]["class_type"] == "ModelSamplingAuraFlow"
    assert graph["3"]["class_type"] == "CFGNorm"
    assert graph["4"]["inputs"]["type"] == "qwen_image"
    assert graph["7"]["class_type"] == "TextEncodeQwenImageEditPlus"
    assert graph["7"]["inputs"]["image2"] == ["32", 0]
    assert graph["8"]["inputs"]["prompt"] == "blurry"
    assert graph["10"]["inputs"]["steps"] == (4 if is_turbo else 32)
    assert graph["10"]["inputs"]["cfg"] == (1.0 if is_turbo else 3.5)
    assert graph["10"]["inputs"]["latent_image"] == ["9", 0]
    assert ("13" in graph) is is_turbo

try:
    image_graphs.build_qwen_image_edit_prompt({"prompt": "Edit", "seed": 17})
except ValueError as error:
    assert "referans görsel" in str(error)
else:
    raise AssertionError("Qwen Image Edit should require a source image")

print("Qwen Image Edit 2511 catalog and normal/turbo graphs passed")
