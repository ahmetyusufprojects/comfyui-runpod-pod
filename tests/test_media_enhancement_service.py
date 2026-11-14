"""Synthetic tests for the isolated media enhancement service."""

import inspect
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

WEBUI_ROOT = Path(__file__).resolve().parents[1] / "webui_flux2"
if str(WEBUI_ROOT) not in sys.path:
    sys.path.insert(0, str(WEBUI_ROOT))

from services import media_enhancement as media


FUNCTIONS = (
    media._dosya_isit,
    media._imge_modeli_isitme_karari,
    media._iyilestirme_kaydi,
    media._iyilestirme_istegi_dogrula,
    media._uretim_sonrasi_iyilestirme_istegi_dogrula,
    media._iyilestirme_modelini_hazirla,
    media._iyilestirme_ffprobe,
    media._iyilestirme_cikti_temizle,
    media._iyilestirme_comfy_isini_yurut,
    media._medyayi_iyilestir,
    media._uretim_sonrasi_iyilestir,
)


def _safe_name(value):
    if not isinstance(value, str) or Path(value).name != value or value in (".", ".."):
        return None
    return value


def _history_runtime(history_dir):
    return SimpleNamespace(
        os=os,
        json=json,
        _guvenli_dosya_adi=_safe_name,
        _kullanici_gecmis=lambda _username: str(history_dir),
        RUNPOD_MODE=True,
        IMAGE_PRESETS={"skin": {"group": "enhance_skin"}},
        VIDEO_PRESETS={"seedvr2": {"group": "enhance_seedvr2"}},
    )


def test_every_service_operation_takes_runtime_explicitly():
    assert all(next(iter(inspect.signature(function).parameters)) == "runtime"
               for function in FUNCTIONS)


def test_file_warming_reads_only_the_supplied_synthetic_file(tmp_path):
    source = tmp_path / "synthetic-weights.bin"
    source.write_bytes(b"synthetic model bytes")

    media._dosya_isit(SimpleNamespace(), str(source), {"iptal": False})

    assert source.read_bytes() == b"synthetic model bytes"


def test_image_encoder_warming_uses_runtime_and_skips_recent_interaction():
    ticks = iter((100.0, 150.0))
    runtime = SimpleNamespace(
        RUNPOD_MODE=True,
        MODEL_FLUX2="flux2", MODEL_Z_IMAGE="zimage", MODEL_FLUX3="flux3",
        MODEL_KREA2="krea2", MODEL_QWEN_IMAGE_EDIT="qwen-edit",
        MODEL_QWEN_IMAGE_EDIT_TURBO="qwen-edit-turbo",
        COMFY_KOK="/synthetic/comfy",
        os=os,
        time=SimpleNamespace(monotonic=lambda: next(ticks)),
        pod_setup=SimpleNamespace(workflow=lambda _key: {"text_encoder_file": "synthetic-clip.safetensors"}),
    )

    warmed = media._imge_modeli_isitme_karari(runtime, "flux3", llama_canliydi=True)
    skipped = media._imge_modeli_isitme_karari(runtime, "flux3", llama_canliydi=False)

    assert warmed.endswith(os.path.join("text_encoders", "synthetic-clip.safetensors"))
    assert skipped is None
    assert runtime._son_goruntu_etkilesim == 100.0


def test_history_lookup_and_validation_use_only_temporary_synthetic_records(tmp_path):
    history = tmp_path / "history"
    history.mkdir()
    (history / "record.json").write_text(
        json.dumps({"dosyalar": ["synthetic.png"]}), encoding="utf-8")
    (history / "synthetic.png").write_bytes(b"synthetic image bytes")
    runtime = _history_runtime(history)
    request = {"kayit": "record.json", "dosya": "synthetic.png",
               "postprocess": "image", "enhance_preset": "skin",
               "scale": 2, "fps_multiplier": 1}

    selected = media._iyilestirme_kaydi(runtime, "synthetic-user", request)
    assert selected["file"] == "synthetic.png" and not selected["video"]
    assert media._iyilestirme_istegi_dogrula(runtime, "synthetic-user", request) is None
    with pytest.raises(ValueError):
        media._iyilestirme_kaydi(
            runtime, "synthetic-user", {"kayit": "../record.json", "dosya": "synthetic.png"}
        )

    rejected = media._iyilestirme_istegi_dogrula(
        runtime, "synthetic-user", {**request, "postprocess": "video"})
    assert rejected and "eşleşmiyor" in rejected["hata"]


def test_post_generation_video_options_are_normalized_without_media_io():
    runtime = SimpleNamespace(
        RUNPOD_MODE=True,
        IS_TIPI_VIDEO="video",
        is_tipini_belirle=lambda _request, _refs: "video",
        IMAGE_PRESETS={"skin": {}},
        VIDEO_PRESETS={"seedvr2": {}},
    )
    request = {"enhance_after": {"enabled": True, "type": "video",
                                  "preset": "seedvr2", "scale": "2",
                                  "fps_multiplier": "2"}}

    assert media._uretim_sonrasi_iyilestirme_istegi_dogrula(runtime, request) is None
    assert request["enhance_after"] == {
        "enabled": True, "type": "video", "preset": "seedvr2",
        "scale": 2.0, "fps_multiplier": 2,
    }
