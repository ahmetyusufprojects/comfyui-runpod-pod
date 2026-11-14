"""HTTP transport for the local llama-server OpenAI-compatible API."""

from __future__ import annotations

import base64
import io
import json
import socket
import urllib.error
import urllib.request

from PIL import Image, ImageOps


def request_json(runtime, payload, cancel=None):
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        runtime.ADRES + "/v1/chat/completions", data=data,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=runtime.ISTEK_SURE) as response:
            sock = getattr(getattr(getattr(response, "fp", None), "raw", None),
                           "_sock", None)
            if sock is not None:
                sock.settimeout(0.5)
            chunks = []
            while True:
                runtime._iptal_kontrolu(cancel)
                try:
                    chunk = (response.read1(65536) if hasattr(response, "read1")
                             else response.read(65536))
                except socket.timeout:
                    continue
                if not chunk:
                    break
                chunks.append(chunk)
            result = json.loads(b"".join(chunks).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8"))
            message = (detail.get("error") or {}).get("message") or str(exc)
        except Exception:
            message = str(exc)
        raise runtime.LLMHatasi("llama-server hatası: " + str(message)[:200],
                                "model") from exc
    except urllib.error.URLError as exc:
        raise runtime.LLMHatasi("llama-server erişilemiyor: %s" % exc.reason,
                                "surec") from exc
    try:
        choice = result["choices"][0]
        message = choice.get("message") or {}
        content = message.get("content") or ""
        reasoning = message.get("reasoning_content") or ""
    except (KeyError, IndexError, TypeError):
        raise runtime.LLMHatasi("yanıt beklenen biçimde değil", "model") from None
    timings = result.get("timings") or {}
    usage = result.get("usage") or {}
    token = timings.get("predicted_n") or usage.get("completion_tokens")
    if token is None:
        token = max(1, round(len(content) / 4))
    return content, {
        "token": int(token),
        "tok_s": timings.get("predicted_per_second"),
        "prompt_token": timings.get("prompt_n") or usage.get("prompt_tokens"),
        "finish": choice.get("finish_reason"),
        "model": result.get("model"),
        "reasoning_content": reasoning,
    }


def parse_stream_line(line):
    if not isinstance(line, str) or not line.startswith("data:"):
        return None
    value = line[5:].strip()
    if value == "[DONE]":
        return "__DONE__"
    try:
        return json.loads(value)
    except Exception:
        return None


def request_stream(runtime, payload, text_callback=None, reasoning_callback=None,
                   retry=0, cancel=None):
    stream_payload = dict(payload)
    stream_payload["stream"] = True
    data = json.dumps(stream_payload).encode("utf-8")
    request = urllib.request.Request(
        runtime.ADRES + "/v1/chat/completions", data=data,
        headers={"Content-Type": "application/json",
                 "Accept": "text/event-stream", "Connection": "close"})
    try:
        with urllib.request.urlopen(request, timeout=runtime.ISTEK_SURE) as response:
            sock = getattr(getattr(getattr(response, "fp", None), "raw", None),
                           "_sock", None)
            if sock is not None:
                sock.settimeout(0.5)
            content = ""
            reasoning = ""
            final = {}
            while True:
                runtime._iptal_kontrolu(cancel)
                try:
                    line = response.readline()
                except socket.timeout:
                    continue
                if not line:
                    break
                chunk = parse_stream_line(line.decode("utf-8", "replace").strip())
                if chunk is None or chunk == "__DONE__" or not isinstance(chunk, dict):
                    continue
                choices = chunk.get("choices") or []
                if not isinstance(choices, list) or not choices:
                    continue
                choice = choices[0]
                if not isinstance(choice, dict):
                    continue
                delta = choice.get("delta")
                if not isinstance(delta, dict):
                    delta = {}
                text = delta.get("content") or ""
                if text:
                    content += text
                    if text_callback:
                        text_callback(text)
                reason = delta.get("reasoning_content") or ""
                if reason:
                    reasoning += reason
                    if reasoning_callback:
                        reasoning_callback(reason)
                if choice.get("finish_reason"):
                    final["finish"] = choice.get("finish_reason")
                timings = chunk.get("timings") or {}
                usage = chunk.get("usage") or {}
                if timings or usage:
                    final["timings"] = timings
                    final["usage"] = usage
                if chunk.get("model"):
                    final["model"] = chunk.get("model")
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8"))
            message = (detail.get("error") or {}).get("message") or str(exc)
        except Exception:
            message = str(exc)
        raise runtime.LLMHatasi("llama-server hatası: " + str(message)[:200],
                                "model") from exc
    except (urllib.error.URLError, ConnectionResetError, ConnectionAbortedError,
            BrokenPipeError, OSError) as exc:
        if cancel is not None and cancel.is_set():
            raise runtime.LLMHatasi("İş durduruldu", "iptal") from exc
        if retry < 2:
            import time
            time.sleep(0.5)
            return request_stream(runtime, payload, text_callback=text_callback,
                                  reasoning_callback=reasoning_callback,
                                  retry=retry + 1, cancel=cancel)
        raise runtime.LLMHatasi("llama-server bağlantısı sıfırlandı: %s"
                                % str(exc)[:200], "surec") from exc
    timings = final.get("timings") or {}
    usage = final.get("usage") or {}
    token = timings.get("predicted_n") or usage.get("completion_tokens")
    if token is None:
        token = max(1, round(len(content) / 4))
    return content, {
        "token": int(token),
        "tok_s": timings.get("predicted_per_second"),
        "prompt_token": timings.get("prompt_n") or usage.get("prompt_tokens"),
        "finish": final.get("finish"),
        "model": final.get("model"),
        "reasoning_content": reasoning,
    }


def image_data_url(runtime, image):
    if not isinstance(image, dict):
        raise runtime.LLMHatasi("görsel girdisi bozuk", "istek")
    if image.get("url") or image.get("path") or image.get("href"):
        raise runtime.LLMHatasi("görsel yalnızca base64 kabul edilir", "istek")
    if image.get("mime") not in ("image/jpeg", "image/png", "image/webp"):
        raise runtime.LLMHatasi("desteklenmeyen görsel türü", "istek")
    data = image.get("veri")
    if not isinstance(data, str) or len(data) > 30 * 1024 * 1024:
        raise runtime.LLMHatasi("görsel verisi yok veya çok büyük", "istek")
    try:
        raw = base64.b64decode(data, validate=True)
    except Exception:
        raise runtime.LLMHatasi("görsel base64 bozuk", "istek") from None
    if len(raw) > runtime.GORSEL_HAM_SINIR:
        raise runtime.LLMHatasi("görsel çok büyük", "istek")
    try:
        picture = Image.open(io.BytesIO(raw))
        picture = ImageOps.exif_transpose(picture).convert("RGB")
        if max(picture.size) > runtime.GORSEL_UZUN_KENAR:
            scale = runtime.GORSEL_UZUN_KENAR / max(picture.size)
            picture = picture.resize(
                (max(1, round(picture.size[0] * scale)),
                 max(1, round(picture.size[1] * scale))), Image.LANCZOS)
        output = io.BytesIO()
        picture.save(output, format="JPEG", quality=85)
    except Exception:
        raise runtime.LLMHatasi("görsel okunamadı", "istek") from None
    return "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")
