"""ComfyUI API-format graph builders for the local image models."""
import os
import random

DIFFUSION_FILE = os.environ.get("WEBUI_FLUX_UNET", "flux2-klein-turbo-fp8.safetensors")
TEXT_ENCODER_FILE = os.environ.get("WEBUI_FLUX_ENCODER", "qwen_3_8b_fp8mixed.safetensors")
VAE_FILE = "flux2-vae.safetensors"
Z_IMAGE_DIFFUSION_FILE = os.environ.get("WEBUI_ZIMAGE_UNET", "z-image-turbo-fp8.safetensors")
Z_IMAGE_TEXT_ENCODER_FILE = "qwen_3_4b.safetensors"
Z_IMAGE_VAE_FILE = "ae.safetensors"
ANIMA_DIFFUSION_FILE = "anima-turbo-v1.1.safetensors"
MOODY_ANIMA_DIFFUSION_FILE = "anima-moody-mix-v10.safetensors"
ANIMA_TEXT_ENCODER_FILE = "qwen_3_06b_base.safetensors"
ANIMA_VAE_FILE = "qwen_image_vae.safetensors"


def build_prompt(p, image_name=None, image_name2=None):
    """API-format prompt. Referanslar zincirlenir (Moody mantigi):
    foto1 -> Ref -> foto2 -> Ref -> guider. Referans yoksa passthrough."""
    seed = p["seed"] if p["seed"] >= 0 else random.randint(0, 2 ** 31 - 1)
    pr = {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": DIFFUSION_FILE, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": TEXT_ENCODER_FILE, "type": "flux2", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE_FILE}},
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
    return pr, seed


def build_z_image_prompt(p, image_name=None, image_name2=None):
    """Z-Image Turbo API workflow; optional references use its Omni encoder."""
    seed = p["seed"] if p["seed"] >= 0 else random.randint(0, 2 ** 31 - 1)
    pr = {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": Z_IMAGE_DIFFUSION_FILE, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": Z_IMAGE_TEXT_ENCODER_FILE, "type": "lumina2", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": Z_IMAGE_VAE_FILE}},
        "5": {"class_type": "ConditioningZeroOut",
              "inputs": {"conditioning": ["4", 0]}},
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
    return pr, seed


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
    return pr, seed
