"""Minimal stdlib client for the local ComfyUI HTTP API."""
import http.client
import json
import os
import uuid

COMFY = os.environ.get("COMFYUI_URL", "127.0.0.1:8188").replace("http://", "").rstrip("/")


def comfy_request(method, path, body=None, headers=None):
    conn = http.client.HTTPConnection(COMFY, timeout=60)
    conn.request(method, path, body=body, headers=headers or {})
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, data


def comfy_get_json(path):
    status, data = comfy_request("GET", path)
    if status != 200:
        raise RuntimeError("ComfyUI GET %s -> %s: %s" % (path, status, data[:300]))
    return json.loads(data.decode("utf-8"))


def comfy_post_json(path, obj):
    body = json.dumps(obj).encode("utf-8")
    status, data = comfy_request("POST", path, body=body,
                                 headers={"Content-Type": "application/json"})
    if status != 200:
        raise RuntimeError("ComfyUI POST %s -> %s: %s" % (path, status, data[:500]))
    return json.loads(data.decode("utf-8"))


def upload_image_to_comfy(png_bytes, filename):
    boundary = "----webui" + uuid.uuid4().hex
    fields = {"overwrite": "true", "type": "input"}
    parts = []
    for k, v in fields.items():
        parts.append("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                     % (boundary, k, v))
    head = "".join(parts).encode("utf-8")
    file_head = ("--%s\r\nContent-Disposition: form-data; name=\"image\"; "
                 "filename=\"%s\"\r\nContent-Type: image/png\r\n\r\n"
                 % (boundary, filename)).encode("utf-8")
    tail = ("\r\n--%s--\r\n" % boundary).encode("utf-8")
    body = head + file_head + png_bytes + tail
    status, data = comfy_request("POST", "/upload/image", body=body,
                                 headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    if status != 200:
        raise RuntimeError("upload/image -> %s: %s" % (status, data[:500]))
    return json.loads(data.decode("utf-8"))
