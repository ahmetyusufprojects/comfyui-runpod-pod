from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

WEBUI_ROOT = Path(__file__).resolve().parents[1] / "webui_flux2"
if str(WEBUI_ROOT) not in sys.path:
    sys.path.insert(0, str(WEBUI_ROOT))

from services import generation  # noqa: E402


class _NeverCalled:
    def __getattr__(self, name):
        raise AssertionError(f"unexpected callback: {name}")


def _base_runtime():
    calls = []

    def cleanup():
        calls.append("clean-comfy-artifacts")

    def request(*args, **kwargs):
        calls.append(("comfy-request", args, kwargs))

    return SimpleNamespace(
        calls=calls,
        _is_dogrula=lambda _request: None,
        _medyayi_iyilestir=lambda _job: None,
        _video_uret=lambda _job, _request: None,
        kasa=SimpleNamespace(temizle_comfy_artiklari=cleanup),
        comfy_request=request,
        IS_TIPI_VIDEO="video",
        is_tipini_belirle=lambda _request, _refs: "image",
        video_parametreleri=lambda _request: {"suresn": 2, "fps": 24},
    )


def _job(request):
    return {
        "is_id": "synthetic-job",
        "istek": request,
        "durum": "kuyrukta",
        "baslangic": 0,
        "bitis": 0,
        "iptal": False,
        "sahip": "synthetic",
    }


def test_invalid_request_exits_without_comfy_or_model_calls():
    runtime = _base_runtime()
    runtime._is_dogrula = lambda _request: {"hata": "invalid synthetic request"}
    runtime.kasa = _NeverCalled()
    runtime.comfy_request = _NeverCalled()
    runtime._medyayi_iyilestir = _NeverCalled()
    runtime._video_uret = _NeverCalled()
    job = _job({"prompt": "synthetic"})

    generation._isi_calistir(runtime, job)

    assert job["durum"] == "hata"
    assert job["hata"] == "invalid synthetic request"
    assert isinstance(job["bitis"], float) and job["bitis"] > 0
    assert runtime.calls == []


def test_postprocess_dispatches_to_media_enhancement_callback():
    runtime = _base_runtime()
    runtime._medyayi_iyilestir = lambda job: runtime.calls.append(
        ("postprocess", job["istek"]["postprocess"])
    )
    job = _job({"postprocess": "image"})

    generation._isi_calistir(runtime, job)

    assert ("postprocess", "image") in runtime.calls
    assert runtime.calls[0] == ("postprocess", "image")
    assert runtime.calls[1] == "clean-comfy-artifacts"
    assert runtime.calls[2][0] == "comfy-request"
    assert runtime.calls[2][1][0:2] == ("POST", "/history")


def test_video_generation_dispatches_to_video_callback():
    runtime = _base_runtime()
    runtime.is_tipini_belirle = lambda _request, _refs: "video"
    runtime._video_uret = lambda job, request: runtime.calls.append(
        ("video-generation", job["is_id"], request["video"])
    )
    job = _job({"video": True})

    generation._isi_calistir(runtime, job)

    assert ("video-generation", "synthetic-job", True) in runtime.calls
    assert runtime.calls[-1][0] == "comfy-request"
