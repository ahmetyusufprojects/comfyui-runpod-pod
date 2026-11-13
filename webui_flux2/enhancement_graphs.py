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
