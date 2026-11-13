"""ComfyUI API-format graph builders for the local image models."""
import json
import os
import random


def _runpod_workflows():
    if os.environ.get("WEBUI_RUNPOD") != "1":
        return {}
    try:
        with open(os.environ["POD_MODEL_CATALOG"], "r", encoding="utf-8") as stream:
            return json.load(stream).get("workflows", {})
    except (OSError, ValueError, KeyError):
        return {}


RUNPOD_WORKFLOWS = _runpod_workflows()


def _model_file(workflow, key, fallback, env=None):
    configured = RUNPOD_WORKFLOWS.get(workflow, {}).get(key)
    return (os.environ.get(env) if env else None) or configured or fallback


DIFFUSION_FILE = _model_file(
    "flux2", "diffusion_file", "flux2-klein-turbo-fp8.safetensors", "WEBUI_FLUX_UNET")
TEXT_ENCODER_FILE = _model_file(
    "flux2", "text_encoder_file", "qwen_3_8b_fp8mixed.safetensors", "WEBUI_FLUX_ENCODER")
VAE_FILE = _model_file("flux2", "vae_file", "flux2-vae.safetensors")
Z_IMAGE_DIFFUSION_FILE = _model_file(
    "z_image", "diffusion_file", "z-image-turbo-fp8.safetensors", "WEBUI_ZIMAGE_UNET")
Z_IMAGE_TEXT_ENCODER_FILE = _model_file(
    "z_image", "text_encoder_file", "qwen_3_4b.safetensors")
Z_IMAGE_VAE_FILE = _model_file("z_image", "vae_file", "ae.safetensors")
ANIMA_DIFFUSION_FILE = _model_file(
    "anima", "diffusion_file", "anima-turbo-v1.1.safetensors")
MOODY_ANIMA_DIFFUSION_FILE = _model_file(
    "moody_anima", "diffusion_file", "anima-moody-mix-v10.safetensors")
ANIMA_TEXT_ENCODER_FILE = _model_file(
    "anima", "text_encoder_file", "qwen_3_06b_base.safetensors")
ANIMA_VAE_FILE = _model_file("anima", "vae_file", "qwen_image_vae.safetensors")
FLUX3_DIFFUSION_FILE = _model_file(
    "flux3", "diffusion_file", "flux3-image-klein-int8-convrot.safetensors")
FLUX3_TEXT_ENCODER_FILE = _model_file(
    "flux3", "text_encoder_file",
    "Huihui-Qwen3-VL-4B-Instruct-abliterated-int8_convrot.safetensors")
FLUX3_VAE_FILE = _model_file("flux3", "vae_file", "flux2-vae.safetensors")
KREA2_DIFFUSION_FILE = _model_file(
    "krea2", "diffusion_file", "Moody-Krea-Mix-v7SE_00001__clean_fp8.safetensors")
KREA2_TEXT_ENCODER_FILE = _model_file(
    "krea2", "text_encoder_file", "qwen3vl_4b_fp8_scaled.safetensors")
KREA2_VAE_FILE = _model_file("krea2", "vae_file", "qwen_image_vae.safetensors")
QWEN_IMAGE_EDIT_DIFFUSION_FILE = _model_file(
    "qwen_image_edit", "diffusion_file",
    "qwen_image_edit_2511_int8_convrot.safetensors")
QWEN_IMAGE_EDIT_TEXT_ENCODER_FILE = _model_file(
    "qwen_image_edit", "text_encoder_file",
    "qwen_2.5_vl_7b_huihui_abliterated_int8_convrot.safetensors")
QWEN_IMAGE_EDIT_VAE_FILE = _model_file(
    "qwen_image_edit", "vae_file", "qwen_image_vae.safetensors")
QWEN_IMAGE_EDIT_TURBO_LORA_FILE = _model_file(
    "qwen_image_edit_turbo", "lora_file",
    "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors")
ILLUSTRIOUS_CHECKPOINT_FILE = _model_file(
    "illustrious", "checkpoint_file",
    "WAI-NSFW-illustrious-SDXL-V14.0-V-Prediction.safetensors")


def attach_loras(prompt, loras):
    """Insert compatible ComfyUI LoRA loaders into a stock model graph."""
    if not isinstance(loras, list) or not loras:
        return prompt
    model_node = None
    for node_id, node in prompt.items():
        inputs = node.get("inputs") or {}
        kind = node.get("class_type", "")
        if "model" not in inputs:
            continue
        if kind.startswith("ModelSampling"):
            model_node = (node_id, inputs)
            break
    if model_node is None:
        for preferred in ("CFGGuider", "KSampler", "SamplerCustomAdvanced"):
            model_node = next(((node_id, node.get("inputs") or {})
                               for node_id, node in prompt.items()
                               if node.get("class_type") == preferred
                               and "model" in (node.get("inputs") or {})), None)
            if model_node:
                break
    clip_ref = None
    for node in prompt.values():
        inputs = node.get("inputs") or {}
        if "clip" in inputs and isinstance(inputs["clip"], list):
            clip_ref = list(inputs["clip"])
            break
    if not model_node or clip_ref is None:
        raise ValueError("Bu görsel iş akışı LoRA bağlantısını desteklemiyor.")
    try:
        model_ref = list(model_node[1]["model"])
    except (KeyError, TypeError):
        raise ValueError("Görsel model bağlantısı LoRA için uygun değil.")
    used_ids = {int(key) for key in prompt if str(key).isdigit()}
    next_id = max(used_ids or {0}) + 1
    original_nodes = list(prompt.items())
    for index, entry in enumerate(loras):
        if not isinstance(entry, dict):
            continue
        filename = entry.get("name")
        if not isinstance(filename, str) or not filename or "/" in filename or "\\" in filename:
            continue
        try:
            weight = max(0.0, min(2.0, float(entry.get("weight", 1.0))))
        except (TypeError, ValueError, OverflowError):
            weight = 1.0
        node_id = str(next_id + index)
        prompt[node_id] = {"class_type": "LoraLoader", "inputs": {
            "model": model_ref, "clip": clip_ref,
            "lora_name": filename, "strength_model": weight,
            "strength_clip": weight}}
        model_ref = [node_id, 0]
        clip_ref = [node_id, 1]
    if not prompt:
        return prompt
    model_node[1]["model"] = model_ref
    for _, node in original_nodes:
        inputs = node.get("inputs") or {}
        if "clip" in inputs and inputs["clip"] == list(original_nodes_clip_ref(original_nodes)):
            inputs["clip"] = clip_ref
    return prompt


def original_nodes_clip_ref(nodes):
    for _, node in nodes:
        clip = (node.get("inputs") or {}).get("clip")
        if isinstance(clip, list):
            return clip
    return []


def build_prompt(p, image_name=None, image_name2=None):
    """API-format prompt. Referanslar zincirlenir (Moody mantigi):
    foto1 -> Ref -> foto2 -> Ref -> guider. Referans yoksa passthrough."""
    seed = p["seed"] if p["seed"] >= 0 else random.randint(0, 2 ** 31 - 1)
    files = p.get("model_files") or {}
    pr = {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": files.get("diffusion", DIFFUSION_FILE), "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": files.get("text_encoder", TEXT_ENCODER_FILE), "type": "flux2", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": files.get("vae", VAE_FILE)}},
        "4": {"class_type": "CLIPTextEncode",
              "inputs": {"text": p["prompt"], "clip": ["2", 0]}},
        "5": {"class_type": "CLIPTextEncode",
              "inputs": {"text": p["negative"], "clip": ["2", 0]}},
        "6": {"class_type": "EmptyFlux2LatentImage",
              "inputs": {"width": p["width"], "height": p["height"], "batch_size": 1}},
        "11": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": p["sampler"]}},
        "12": {"class_type": "Flux2Scheduler",
               "inputs": {"steps": p["steps"], "width": p["width"], "height": p["height"]}},
        "13": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "14": {"class_type": "CFGGuider",
               "inputs": {"model": ["1", 0], "positive": ["9", 0],
                          "negative": ["10", 0], "cfg": p["cfg"]}},
        "15": {"class_type": "SamplerCustomAdvanced",
               "inputs": {"noise": ["13", 0], "guider": ["14", 0],
                          "sampler": ["11", 0], "sigmas": ["12", 0],
                          "latent_image": ["6", 0]}},
        "16": {"class_type": "VAEDecode",
               "inputs": {"samples": ["15", 0], "vae": ["3", 0]}},
        "17": {"class_type": "SaveImage",
               "inputs": {"images": ["16", 0], "filename_prefix": "webui-flux2"}},
    }
    refs = [r for r in (image_name, image_name2) if r]
    pos_src, neg_src = ["4", 0], ["5", 0]
    nid = 20
    for r in refs:
        pr[str(nid)] = {"class_type": "LoadImage", "inputs": {"image": r}}
        lid = nid
        nid += 1
        pr[str(nid)] = {"class_type": "VAEEncode",
                        "inputs": {"pixels": [str(lid), 0], "vae": ["3", 0]}}
        vid = nid
        nid += 1
        pr[str(nid)] = {"class_type": "ReferenceLatent",
                        "inputs": {"conditioning": pos_src, "latent": [str(vid), 0]}}
        pos_src = [str(nid), 0]
        nid += 1
        pr[str(nid)] = {"class_type": "ReferenceLatent",
                        "inputs": {"conditioning": neg_src, "latent": [str(vid), 0]}}
        neg_src = [str(nid), 0]
        nid += 1
    pr["14"]["inputs"]["positive"] = pos_src
    pr["14"]["inputs"]["negative"] = neg_src
    return attach_loras(pr, p.get("loras")), seed


def build_z_image_prompt(p, image_name=None, image_name2=None):
    """Z-Image Turbo API workflow; optional references use its Omni encoder."""
    seed = p["seed"] if p["seed"] >= 0 else random.randint(0, 2 ** 31 - 1)
    files = p.get("model_files") or {}
    pr = {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": files.get("diffusion", Z_IMAGE_DIFFUSION_FILE), "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": files.get("text_encoder", Z_IMAGE_TEXT_ENCODER_FILE), "type": "lumina2", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": files.get("vae", Z_IMAGE_VAE_FILE)}},
        "5": {"class_type": "CLIPTextEncode",
              "inputs": {"text": p.get("negative", ""), "clip": ["2", 0]}},
        "6": {"class_type": "EmptySD3LatentImage",
              "inputs": {"width": p["width"], "height": p["height"], "batch_size": 1}},
        "7": {"class_type": "ModelSamplingAuraFlow",
              "inputs": {"model": ["1", 0], "shift": 3.0}},
        "8": {"class_type": "KSampler",
              "inputs": {"model": ["7", 0], "seed": seed, "steps": p["steps"],
                         "cfg": p["cfg"], "sampler_name": p["sampler"], "scheduler": "simple",
                         "positive": ["4", 0], "negative": ["5", 0],
                         "latent_image": ["6", 0], "denoise": 1.0}},
        "9": {"class_type": "VAEDecode",
              "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        "10": {"class_type": "SaveImage",
               "inputs": {"images": ["9", 0], "filename_prefix": "webui-z-image"}},
    }
    refs = [r for r in (image_name, image_name2) if r]
    if refs:
        # Keep the existing reference-edit conditioning path unchanged.
        pr["5"] = {"class_type": "ConditioningZeroOut",
                   "inputs": {"conditioning": ["4", 0]}}
        for i, image_name in enumerate(refs, start=20):
            pr[str(i)] = {"class_type": "LoadImage", "inputs": {"image": image_name}}
        encode_inputs = {"clip": ["2", 0], "prompt": p["prompt"],
                         "auto_resize_images": True, "vae": ["3", 0]}
        for i, node_id in enumerate(range(20, 20 + len(refs)), start=1):
            encode_inputs["image%d" % i] = [str(node_id), 0]
        pr["4"] = {"class_type": "TextEncodeZImageOmni", "inputs": encode_inputs}
    else:
        pr["4"] = {"class_type": "CLIPTextEncode",
                   "inputs": {"text": p["prompt"], "clip": ["2", 0]}}
        if os.environ.get("WEBUI_RUNPOD") == "1":
            # The provided Z-Image sample uses a partial first pass, latent
            # upscaling, then a second noisy pass. Use it for text-to-image
            # only; reference-image editing keeps its existing workflow.
            steps = max(1, min(50, int(p.get("steps", 9))))
            if steps >= 5:
                scale = min(1.7, 4096.0 / max(p["width"], p["height"]))
                first_end = max(1, min(steps - 1, round(steps * 7 / 9)))
                second_start = max(0, min(steps - 1, round(steps * 4 / 9)))
                sampler = p.get("sampler", "dpmpp_2m_sde")
                pr["8"] = {"class_type": "KSamplerAdvanced", "inputs": {
                    "model": ["7", 0], "positive": ["4", 0],
                    "negative": ["5", 0], "latent_image": ["6", 0],
                    "add_noise": "enable", "noise_seed": seed,
                    "steps": steps, "cfg": p["cfg"],
                    "sampler_name": sampler, "scheduler": "beta",
                    "start_at_step": 0, "end_at_step": first_end,
                    "return_with_leftover_noise": "enable"}}
                pr["9"] = {"class_type": "LatentUpscaleBy", "inputs": {
                    "samples": ["8", 0], "upscale_method": "bislerp",
                    "scale_by": scale}}
                pr["10"] = {"class_type": "KSamplerAdvanced", "inputs": {
                    "model": ["7", 0], "positive": ["4", 0],
                    "negative": ["5", 0], "latent_image": ["9", 0],
                    "add_noise": "enable", "noise_seed": seed,
                    "steps": steps, "cfg": p["cfg"],
                    "sampler_name": sampler, "scheduler": "sgm_uniform",
                    "start_at_step": second_start, "end_at_step": steps,
                    "return_with_leftover_noise": "disable"}}
                pr["11"] = {"class_type": "VAEDecode", "inputs": {
                    "samples": ["10", 0], "vae": ["3", 0]}}
                pr["12"] = {"class_type": "SaveImage", "inputs": {
                    "images": ["11", 0], "filename_prefix": "webui-z-image"}}
                actual_scale = scale
                p["output_width"] = int(round(p["width"] * actual_scale / 8) * 8)
                p["output_height"] = int(round(p["height"] * actual_scale / 8) * 8)
    return attach_loras(pr, p.get("loras")), seed


def build_anima_prompt(p, diffusion_file=ANIMA_DIFFUSION_FILE):
    """Minimal stock-node workflow for Anima text-to-image generation."""
    seed = p["seed"] if p["seed"] >= 0 else random.randint(0, 2 ** 31 - 1)
    pr = {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": diffusion_file, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": ANIMA_TEXT_ENCODER_FILE,
                         "type": "stable_diffusion", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": ANIMA_VAE_FILE}},
        "4": {"class_type": "CLIPTextEncode",
              "inputs": {"text": p["prompt"], "clip": ["2", 0]}},
        "5": {"class_type": "ConditioningZeroOut",
              "inputs": {"conditioning": ["4", 0]}},
        "6": {"class_type": "EmptySD3LatentImage",
              "inputs": {"width": p["width"], "height": p["height"], "batch_size": 1}},
        "7": {"class_type": "KSampler",
              "inputs": {"model": ["1", 0], "seed": seed, "steps": p["steps"],
                         "cfg": p["cfg"], "sampler_name": p["sampler"],
                         "scheduler": "simple", "positive": ["4", 0],
                         "negative": ["5", 0], "latent_image": ["6", 0],
                         "denoise": 1.0}},
        "8": {"class_type": "VAEDecode",
              "inputs": {"samples": ["7", 0], "vae": ["3", 0]}},
        "9": {"class_type": "SaveImage",
              "inputs": {"images": ["8", 0], "filename_prefix": "webui-anima"}},
    }
    return attach_loras(pr, p.get("loras")), seed


def build_flux3_prompt(p, image_name=None, image_name2=None):
    """FLUX 3 Klein distilled workflow with optional references and layout rows."""
    seed = p["seed"] if p["seed"] >= 0 else random.randint(0, 2 ** 31 - 1)
    prompt_text = str(p["prompt"] or "")
    refs = [ref for ref in (image_name, image_name2) if ref]
    pr = {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": FLUX3_DIFFUSION_FILE,
                         "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": FLUX3_TEXT_ENCODER_FILE,
                         "type": "flux3", "device": "default"}},
        "3": {"class_type": "VAELoader",
              "inputs": {"vae_name": FLUX3_VAE_FILE}},
        "4": {"class_type": "CLIPTextEncode",
              "inputs": {"text": prompt_text, "clip": ["2", 0]}},
        "5": {"class_type": "CLIPTextEncode",
              "inputs": {"text": p.get("negative", ""), "clip": ["2", 0]}},
        "6": {"class_type": "EmptyFlux2LatentImage",
              "inputs": {"width": p["width"], "height": p["height"],
                         "batch_size": 1}},
        "7": {"class_type": "KSampler",
              "inputs": {"model": ["1", 0], "positive": ["4", 0],
                         "negative": ["5", 0], "latent_image": ["6", 0],
                         "seed": seed, "steps": p.get("steps", 16),
                         "cfg": p.get("cfg", 1.0),
                         "sampler_name": p.get("sampler", "euler"),
                         "scheduler": p.get("scheduler", "simple"),
                         "denoise": 1.0}},
        "8": {"class_type": "VAEDecode",
              "inputs": {"samples": ["7", 0], "vae": ["3", 0]}},
        "9": {"class_type": "SaveImage",
              "inputs": {"images": ["8", 0], "filename_prefix": "webui-flux3"}},
    }
    if p.get("flux3_bbox_mode") and not refs:
        caption, elements = _flux3_layout_elements(prompt_text, p["width"], p["height"])
        if elements:
            pr["4"]["inputs"]["text"] = ["15", 0]
            pr["14"] = {"class_type": "CreateBoundingBoxes", "inputs": {
                "width": p["width"], "height": p["height"],
                "bboxes": json.dumps(elements, ensure_ascii=False),
                "editor_state": [], "last_incoming": []}}
            pr["15"] = {"class_type": "Flux3LayoutPrompt", "inputs": {
                "prompt": caption, "element": ["14", 2]}}
    if refs:
        # The official image-edit bbox table contains from/src_bbox/tgt_bbox.
        # Pass that full table through unchanged: ComfyUI's CreateBoundingBoxes
        # node intentionally reduces boxes to a generation-only layout table.
        ref_nodes = []
        for node_id, ref in enumerate(refs, start=20):
            pr[str(node_id)] = {"class_type": "LoadImage",
                                "inputs": {"image": ref}}
            ref_nodes.append(node_id)
        ref_loader_id = str(max(int(value) for value in pr) + 1)
        ref_inputs = {"positive": ["4", 0], "negative": ["5", 0],
                      "vae": ["3", 0], "resolution": "resize",
                      "resolution.megapixels": 1}
        for index, node_id in enumerate(ref_nodes):
            ref_inputs["images.image%d" % index] = [str(node_id), 0]
        pr[ref_loader_id] = {"class_type": "ReferenceMultiLatent", "inputs": ref_inputs}
        pr["7"]["inputs"].update({"positive": [ref_loader_id, 0],
                                  "negative": [ref_loader_id, 1],
                                  "latent_image": [ref_loader_id, 2]})
    return attach_loras(pr, p.get("loras")), seed


def _flux3_layout_elements(prompt, width, height):
    """Convert the caption + BFL element-table output into stock ComfyUI nodes."""
    text = str(prompt or "").rstrip()
    decoder = json.JSONDecoder()
    for start in (index for index, char in enumerate(text) if char == "["):
        try:
            rows, end = decoder.raw_decode(text[start:])
        except (ValueError, TypeError):
            continue
        if text[start + end:].strip() or not isinstance(rows, list):
            continue
        caption = text[:start].rstrip()
        elements, references = [], []
        for row in rows[:100]:
            if not isinstance(row, dict):
                continue
            box = row.get("tgt_bbox") if "tgt_bbox" in row else row.get("bbox")
            if not isinstance(box, (list, tuple)) or len(box) != 4:
                continue
            try:
                bounds = [max(0, min(1000, round(float(value)))) for value in box]
            except (TypeError, ValueError, OverflowError):
                continue
            if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
                continue
            desc = str(row.get("desc") or "").strip()[:1600]
            if not desc:
                continue
            old_id = str(row.get("id") or "").strip()
            new_id = "obj_%d" % (len(elements) + 1)
            if old_id:
                references.append((old_id, new_id))
            elements.append({"type": "obj", "bbox": bounds, "desc": desc})
        if not elements:
            return text, []
        for old_id, new_id in references:
            caption = caption.replace("<" + old_id + ">", "<" + new_id + ">")
        return caption, elements
    return text, []


def build_krea2_prompt(p):
    """ComfyUI stock-node workflow matching the supplied Krea 2 example."""
    seed = p["seed"] if p["seed"] >= 0 else random.randint(0, 2 ** 31 - 1)
    files = p.get("model_files") or {}
    pr = {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": files.get("diffusion", KREA2_DIFFUSION_FILE),
                         "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": files.get("text_encoder", KREA2_TEXT_ENCODER_FILE),
                         "type": "krea2", "device": "default"}},
        "3": {"class_type": "VAELoader",
              "inputs": {"vae_name": files.get("vae", KREA2_VAE_FILE)}},
        "4": {"class_type": "CLIPTextEncode",
              "inputs": {"text": p["prompt"], "clip": ["2", 0]}},
        "5": {"class_type": "CLIPTextEncode",
              "inputs": {"text": p.get("negative", ""), "clip": ["2", 0]}},
        "6": {"class_type": "EmptyLatentImage",
              "inputs": {"width": p["width"], "height": p["height"],
                         "batch_size": 1}},
        "7": {"class_type": "ModelSamplingAuraFlow",
              "inputs": {"model": ["1", 0], "shift": 3.0}},
        "8": {"class_type": "KSampler",
              "inputs": {"model": ["7", 0], "seed": seed,
                         "steps": p.get("steps", 8), "cfg": p.get("cfg", 1.0),
                         "sampler_name": p.get("sampler", "euler_ancestral"),
                         "scheduler": p.get("scheduler", "beta"),
                         "positive": ["4", 0], "negative": ["5", 0],
                         "latent_image": ["6", 0], "denoise": 1.0}},
        "9": {"class_type": "VAEDecode",
              "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        "10": {"class_type": "SaveImage",
               "inputs": {"images": ["9", 0], "filename_prefix": "webui-krea2"}},
    }
    return attach_loras(pr, p.get("loras")), seed


def build_qwen_image_edit_prompt(p, image_name=None, image_name2=None,
                                 turbo=False):
    """Qwen Image Edit 2511 graph with optional 4-step Lightning LoRA."""
    seed = p["seed"] if p["seed"] >= 0 else random.randint(0, 2 ** 31 - 1)
    files = p.get("model_files") or {}
    diffusion = files.get("diffusion", QWEN_IMAGE_EDIT_DIFFUSION_FILE)
    encoder = files.get("text_encoder", QWEN_IMAGE_EDIT_TEXT_ENCODER_FILE)
    vae = files.get("vae", QWEN_IMAGE_EDIT_VAE_FILE)
    lora = files.get("lora", QWEN_IMAGE_EDIT_TURBO_LORA_FILE)
    refs = [ref for ref in (image_name, image_name2) if ref]
    if not refs:
        raise ValueError("Qwen Image Edit 2511 için en az bir referans görsel gerekli.")
    pr = {
        "1": {"class_type": "UNETLoader", "inputs": {
            "unet_name": diffusion, "weight_dtype": "default"}},
        "2": {"class_type": "ModelSamplingAuraFlow", "inputs": {
            "model": ["1", 0], "shift": 3.1}},
        "3": {"class_type": "CFGNorm", "inputs": {
            "model": ["2", 0], "strength": 1.0}},
        "4": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": encoder, "type": "qwen_image", "device": "default"}},
        "5": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
        "6": {"class_type": "FluxKontextImageScale", "inputs": {
            "image": ["31", 0]}},
        "7": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {
            "clip": ["4", 0], "prompt": str(p.get("prompt") or ""),
            "vae": ["5", 0], "image1": ["6", 0]}},
        "8": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {
            "clip": ["4", 0], "prompt": str(p.get("negative") or ""),
            "vae": ["5", 0], "image1": ["6", 0]}},
        "9": {"class_type": "VAEEncode", "inputs": {
            "pixels": ["6", 0], "vae": ["5", 0]}},
        "10": {"class_type": "KSampler", "inputs": {
            "model": ["3", 0], "positive": ["7", 0], "negative": ["8", 0],
            "latent_image": ["9", 0], "seed": seed,
            "steps": 4 if turbo else max(1, min(60, int(p.get("steps", 40)))),
            "cfg": 1.0 if turbo else float(p.get("cfg", 4.0)),
            "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "11": {"class_type": "VAEDecode", "inputs": {
            "samples": ["10", 0], "vae": ["5", 0]}},
        "12": {"class_type": "SaveImage", "inputs": {
            "images": ["11", 0], "filename_prefix": "webui-qwen-image-edit-2511"}},
        "31": {"class_type": "LoadImage", "inputs": {"image": refs[0]}},
    }
    if len(refs) > 1:
        pr["32"] = {"class_type": "LoadImage", "inputs": {"image": refs[1]}}
        pr["7"]["inputs"]["image2"] = ["32", 0]
        pr["8"]["inputs"]["image2"] = ["32", 0]
    if turbo:
        pr["13"] = {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": ["3", 0], "lora_name": lora, "strength_model": 1.0}}
        pr["10"]["inputs"]["model"] = ["13", 0]
    return pr, seed


def build_illustrious_prompt(p):
    """SDXL checkpoint workflow with the checkpoint's v-prediction sampling."""
    seed = p["seed"] if p["seed"] >= 0 else random.randint(0, 2 ** 31 - 1)
    files = p.get("model_files") or {}
    pr = {
        "1": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": files.get("checkpoint", ILLUSTRIOUS_CHECKPOINT_FILE)}},
        "2": {"class_type": "ModelSamplingDiscrete",
              "inputs": {"model": ["1", 0], "sampling": "v_prediction",
                         "zsnr": True}},
        "3": {"class_type": "CLIPTextEncode",
              "inputs": {"text": p["prompt"], "clip": ["1", 1]}},
        "4": {"class_type": "CLIPTextEncode",
              "inputs": {"text": p.get("negative", ""), "clip": ["1", 1]}},
        "5": {"class_type": "EmptyLatentImage",
              "inputs": {"width": p["width"], "height": p["height"],
                         "batch_size": 1}},
        "6": {"class_type": "KSampler",
              "inputs": {"model": ["2", 0], "seed": seed,
                         "steps": p.get("steps", 28), "cfg": p.get("cfg", 5.0),
                         "sampler_name": p.get("sampler", "euler_ancestral"),
                         "scheduler": p.get("scheduler", "normal"),
                         "positive": ["3", 0], "negative": ["4", 0],
                         "latent_image": ["5", 0], "denoise": 1.0}},
        "7": {"class_type": "VAEDecode",
              "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
        "8": {"class_type": "SaveImage",
              "inputs": {"images": ["7", 0], "filename_prefix": "webui-illustrious"}},
    }
    return attach_loras(pr, p.get("loras")), seed
