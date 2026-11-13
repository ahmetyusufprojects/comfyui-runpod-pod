"""Small API workflows for independent image/video enhancement."""

import os
import uuid


IMAGE_PRESETS = {
    "skin": {"group": "enhance_skin", "file": "1xSkinContrast-High-SuperUltraCompact.pth",
             "model_scale": 1.0, "label": "Ten dokusu"},
    "photo": {"group": "enhance_photo", "file": "4x-ClearRealityV1.safetensors",
              "model_scale": 4.0, "label": "Doğal fotoğraf"},
    "realesrgan": {"group": "enhance_realesrgan", "file": "RealESRGAN_x4plus.pth",
                   "model_scale": 4.0, "label": "Genel · Real-ESRGAN x4"},
}

VIDEO_PRESETS = {
    "seedvr2": {"group": "enhance_seedvr2",
                "file": "seedvr2_ema_3b_fp16.safetensors"},
    "seedvr2_fp8": {"group": "enhance_seedvr2_fp8",
                     "file": "seedvr2_ema_3b_fp8_e4m3fn.safetensors"},
}


def build_image_enhancement(image_name, preset, scale):
    """Upscale/texture enhancement graph, kept separate from generation graphs."""
    choice = IMAGE_PRESETS[preset]
    scale = max(1.0, min(4.0, float(scale)))
    output = {
        "1": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "2": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": choice["file"]}},
        "3": {"class_type": "ImageUpscaleWithModel", "inputs": {
            "upscale_model": ["2", 0], "image": ["1", 0]}},
        "5": {"class_type": "SaveImage", "inputs": {
            "images": ["4", 0], "filename_prefix": "webui-enhance/image-%s" % uuid.uuid4().hex[:10]}},
    }
    image_ref = ["3", 0]
    model_scale = choice["model_scale"]
    if preset == "skin" and scale > 1:
        output["4"] = {"class_type": "UpscaleModelLoader", "inputs": {
            "model_name": IMAGE_PRESETS["photo"]["file"]}}
        output["6"] = {"class_type": "ImageUpscaleWithModel", "inputs": {
            "upscale_model": ["4", 0], "image": image_ref}}
        image_ref = ["6", 0]
        model_scale = IMAGE_PRESETS["photo"]["model_scale"]
    if abs(scale / model_scale - 1.0) < 0.001:
        output["5"]["inputs"]["images"] = image_ref
    else:
        output["5"] = {"class_type": "ImageScaleBy", "inputs": {
            "image": image_ref, "upscale_method": "lanczos",
            "scale_by": scale / model_scale}}
    return output, choice


def build_video_enhancement(frame_names, preset, scale, fps_multiplier, source_fps=24.0):
    """Build one small frame batch; video/audio muxing happens after all batches."""
    choice = IMAGE_PRESETS[preset]
    scale = max(1.0, min(4.0, float(scale)))
    fps_multiplier = max(1, min(4, int(fps_multiplier)))
    graph = {
        "1000": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": choice["file"]}},
    }
    load_ids = []
    next_id = 1
    for frame_name in frame_names:
        load_id = str(next_id)
        graph[load_id] = {"class_type": "LoadImage", "inputs": {"image": frame_name}}
        load_ids.append(load_id)
        next_id += 1
    image_ref = [load_ids[0], 0]
    for load_id in load_ids[1:]:
        batch_id = str(next_id)
        graph[batch_id] = {"class_type": "ImageBatch", "inputs": {
            "image1": image_ref, "image2": [load_id, 0]}}
        image_ref = [batch_id, 0]
        next_id += 1
    if fps_multiplier > 1:
        model_id = str(next_id)
        graph[model_id] = {"class_type": "FrameInterpolationModelLoader", "inputs": {
            "model_name": "rife_v4.26.safetensors"}}
        interpolate_id = str(next_id + 1)
        graph[interpolate_id] = {"class_type": "FrameInterpolate", "inputs": {
            "interp_model": [model_id, 0], "images": image_ref,
            "multiplier": fps_multiplier}}
        image_ref = [interpolate_id, 0]
        next_id += 2
    image_scale = choice["model_scale"]
    if preset == "skin" and scale > 1:
        skin_upscale_id = str(next_id)
        graph[skin_upscale_id] = {"class_type": "ImageUpscaleWithModel", "inputs": {
            "upscale_model": ["1000", 0], "image": image_ref}}
        photo_loader_id = str(next_id + 1)
        graph[photo_loader_id] = {"class_type": "UpscaleModelLoader", "inputs": {
            "model_name": IMAGE_PRESETS["photo"]["file"]}}
        image_ref = [skin_upscale_id, 0]
        photo_upscale_id = str(next_id + 2)
        graph[photo_upscale_id] = {"class_type": "ImageUpscaleWithModel", "inputs": {
            "upscale_model": [photo_loader_id, 0], "image": image_ref}}
        image_ref = [photo_upscale_id, 0]
        image_scale = IMAGE_PRESETS["photo"]["model_scale"]
        next_id += 3
    else:
        upscale_id = str(next_id)
        graph[upscale_id] = {"class_type": "ImageUpscaleWithModel", "inputs": {
            "upscale_model": ["1000", 0], "image": image_ref}}
        image_ref = [upscale_id, 0]
        next_id += 1
    if abs(scale / image_scale - 1.0) >= 0.001:
        resize_id = str(next_id)
        graph[resize_id] = {"class_type": "ImageScaleBy", "inputs": {
            "image": image_ref, "upscale_method": "lanczos",
            "scale_by": scale / image_scale}}
        image_ref = [resize_id, 0]
        next_id += 1
    graph[str(next_id)] = {"class_type": "SaveImage", "inputs": {
        "images": image_ref, "filename_prefix": "webui-enhance/frame-%s" % uuid.uuid4().hex[:10]}}
    return graph, choice


def build_seedvr2_video_enhancement(frame_names, resolution=1080,
                                   fps_multiplier=1, seed=42,
                                   model_file="seedvr2_ema_3b_fp16.safetensors"):
    """Build a temporal video-restoration graph for one 4n+1 frame batch.

    The caller supplies five frames (padding the last batch when needed). SeedVR2
    analyzes the sequence together; optional RIFE interpolation runs afterward.
    """
    if len(frame_names) != 5:
        raise ValueError("SeedVR2 için beş karelik bir paket gerekir.")
    fps_multiplier = max(1, min(4, int(fps_multiplier)))
    graph = {
        "1000": {"class_type": "SeedVR2LoadDiTModel", "inputs": {
            "model": model_file, "device": "cuda:0",
            "blocks_to_swap": 0, "swap_io_components": False,
            "offload_device": "cpu", "cache_model": False,
            "attention_mode": "sdpa"}},
        "1001": {"class_type": "SeedVR2LoadVAEModel", "inputs": {
            "model": "ema_vae_fp16.safetensors", "device": "cuda:0",
            "encode_tiled": True, "encode_tile_size": 1024,
            "encode_tile_overlap": 128, "decode_tiled": True,
            "decode_tile_size": 768, "decode_tile_overlap": 128,
            "tile_debug": "false", "offload_device": "cpu",
            "cache_model": False}},
    }
    load_ids = []
    next_id = 1
    for frame_name in frame_names:
        load_id = str(next_id)
        graph[load_id] = {"class_type": "LoadImage", "inputs": {"image": frame_name}}
        load_ids.append(load_id)
        next_id += 1
    image_ref = [load_ids[0], 0]
    for load_id in load_ids[1:]:
        batch_id = str(next_id)
        graph[batch_id] = {"class_type": "ImageBatch", "inputs": {
            "image1": image_ref, "image2": [load_id, 0]}}
        image_ref = [batch_id, 0]
        next_id += 1
    upscale_id = str(next_id)
    graph[upscale_id] = {"class_type": "SeedVR2VideoUpscaler", "inputs": {
        "image": image_ref, "dit": ["1000", 0], "vae": ["1001", 0],
        "seed": int(seed) % (2 ** 32), "resolution": int(resolution),
        "max_resolution": 4096, "batch_size": 5,
        "uniform_batch_size": True, "color_correction": "lab",
        "temporal_overlap": 0, "prepend_frames": 0,
        "input_noise_scale": 0.0, "latent_noise_scale": 0.0,
        "offload_device": "cpu", "enable_debug": False}}
    image_ref = [upscale_id, 0]
    next_id += 1
    if fps_multiplier > 1:
        model_id = str(next_id)
        graph[model_id] = {"class_type": "FrameInterpolationModelLoader", "inputs": {
            "model_name": "rife_v4.26.safetensors"}}
        interpolate_id = str(next_id + 1)
        graph[interpolate_id] = {"class_type": "FrameInterpolate", "inputs": {
            "interp_model": [model_id, 0], "images": image_ref,
            "multiplier": fps_multiplier}}
        image_ref = [interpolate_id, 0]
        next_id += 2
    graph[str(next_id)] = {"class_type": "SaveImage", "inputs": {
        "images": image_ref,
        "filename_prefix": "webui-enhance/seedvr2-%s" % uuid.uuid4().hex[:10]}}
    return graph


def seedvr2_video_chunks(frame_count):
    """Return 5-frame windows with one-frame overlap, preserving every source frame once."""
    frame_count = max(0, int(frame_count))
    starts = list(range(0, frame_count, 4))
    if len(starts) > 1 and frame_count - starts[-1] < 2:
        starts.pop()
    return [(start, min(5, frame_count - start)) for start in starts]
