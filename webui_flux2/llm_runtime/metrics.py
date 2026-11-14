"""Generation activity and llama-server slot telemetry."""

from __future__ import annotations

import json
import time
import urllib.request


def start_measurement(runtime):
    runtime._son_olcum = {}
    runtime._uretim.update({"aktif": True, "baslama": time.monotonic(),
                            "ilk_token": None, "ilk_zaman": None})


def finish_measurement(runtime, measurement):
    runtime._uretim["aktif"] = False
    runtime._son_olcum = dict(measurement)
    runtime._son_olcum["aktif"] = False


def slot_data(runtime):
    now = time.monotonic()
    if now - runtime._slots_son["zaman"] < 0.4:
        return runtime._slots_son["veri"]
    try:
        with urllib.request.urlopen(runtime.ADRES + "/slots", timeout=1.5) as response:
            value = json.loads(response.read().decode("utf-8"))
    except Exception:
        value = None
    runtime._slots_son.update({"zaman": now, "veri": value})
    return value


def live_measurement(runtime):
    if not runtime._uretim["aktif"]:
        return dict(runtime._son_olcum)
    now = time.monotonic()
    result = {"aktif": True,
              "gecen_sn": round(now - runtime._uretim["baslama"], 1)}
    value = slot_data(runtime)
    slots = value.get("slots") if isinstance(value, dict) else value
    if not isinstance(slots, list):
        slots = []
    slot = next((item for item in slots if item.get("is_processing")), None)
    if slot is None and len(slots) == 1:
        slot = slots[0]
    if isinstance(slot, dict):
        next_token = slot.get("next_token")
        if isinstance(next_token, list) and next_token:
            next_token = next_token[0]
        if not isinstance(next_token, dict):
            next_token = {}
        decoded = next_token.get("n_decoded")
        prompt_total = slot.get("n_prompt_tokens")
        prompt_done = slot.get("n_prompt_tokens_processed")
        if isinstance(decoded, int):
            result["token"] = decoded
        if isinstance(prompt_done, int):
            result["prompt_token"] = prompt_done
        elif isinstance(prompt_total, int) and isinstance(decoded, int):
            result["prompt_token"] = max(0, prompt_total - decoded)
        if isinstance(decoded, int) and decoded > 0:
            if runtime._uretim["ilk_token"] is None:
                runtime._uretim["ilk_token"] = decoded
                runtime._uretim["ilk_zaman"] = now
            elapsed = now - (runtime._uretim["ilk_zaman"] or now)
            difference = decoded - (runtime._uretim["ilk_token"] or decoded)
            if elapsed > 0.2 and difference > 0:
                result["tok_s"] = round(difference / elapsed, 1)
            result["faz"] = "uretim"
        elif isinstance(prompt_done, int) and prompt_done:
            result["faz"] = "prompt"
        else:
            result["faz"] = "hazirlik"
    return result
