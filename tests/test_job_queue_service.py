from __future__ import annotations

import threading
from types import SimpleNamespace

from pathlib import Path
import sys

WEBUI_ROOT = Path(__file__).resolve().parents[1] / "webui_flux2"
if str(WEBUI_ROOT) not in sys.path:
    sys.path.insert(0, str(WEBUI_ROOT))

from services import job_queue  # noqa: E402


class _FakeVault:
    def __init__(self, *, opened=True, closing=False):
        self.opened = opened
        self.closing = closing

    def durum(self, _username):
        return {"acik": self.opened, "kapaniyor": self.closing}


class _FakeLLM:
    def model_profilleri(self):
        return [{"id": "llm-synthetic"}]

    def canli_olcum(self):
        return {"aktif": True, "tok_s": 12.5}

    def iptal_istegi(self):
        return True

    def durdur(self):
        raise AssertionError("a queued synthetic job must not stop llama")


class _FakePodSetup:
    def workflow_ready(self, _workflow):
        return True

    def model_instance(self, _instance_id):
        return {"family": "flux2"}

    def workflow_enabled(self, _instance_id):
        return True


def _runtime():
    return SimpleNamespace(
        llm=_FakeLLM(),
        pod_setup=_FakePodSetup(),
        kasa=_FakeVault(),
        RUNPOD_MODE=False,
        MODEL_FLUX2="flux2",
        MODEL_ANIMA="anima",
        MODEL_MOODY_ANIMA="moody-anima",
        MODEL_Z_IMAGE="z-image",
        MODEL_FLUX3="flux3",
        MODEL_KREA2="krea2",
        MODEL_QWEN_IMAGE_EDIT="qwen-edit",
        MODEL_QWEN_IMAGE_EDIT_TURBO="qwen-edit-turbo",
        MODEL_ILLUSTRIOUS="illustrious",
        IS_TIPI_VIDEO="video",
        is_tipini_belirle=lambda request, _refs: (
            "video" if request.get("video") else "goruntu"
        ),
        _isler={},
        _isler_kilit=threading.Lock(),
        _kuyruk=[],
        _ceviri_kuyrugu=[],
        _kuyruk_calismasi=True,
        _ceviri_calismasi=True,
        _bellek_bosaltma_aktif=False,
        _kapatma_istegi=threading.Event(),
        _SAKLANAN_IS_SAYISI=10,
        _aktif={"prompt_id": None, "ilerleme": None},
        _oran_hesapla=lambda value: 0.5,
        _llm_is_gecis_kilit=threading.Lock(),
        _aktif_llm={"is_id": None, "durum": None},
        _comfy_isini_kes=lambda _prompt_id: None,
        _ceviri_worker=lambda: None,
        _kuyruk_worker=lambda: None,
    )


def _new_job(runtime, owner, request=None, kind="goruntu"):
    job, order = job_queue._kuyruga_ekle(
        runtime, request or {"prompt": "synthetic prompt"}, owner, kind
    )
    assert job is not None
    assert order == len(runtime._kuyruk)
    return job


def test_request_validation_preserves_existing_rules():
    runtime = _runtime()

    assert (
        job_queue._is_dogrula(runtime, {"model": "z-image", "prompt": "synthetic"})
        is None
    )
    assert job_queue._is_dogrula(runtime, {"llm_model": "missing"}) == {
        "hata": "Seçilen LLM modeli bulunamadı. Ayarlardan geçerli bir model seç."
    }
    assert job_queue._is_dogrula(
        runtime, {"video": True, "video_model_instance": "video"}
    ) == {
        "hata": "video-uretim icin en az 1 referans fotograf gerekli",
        "is_tipi": "video",
    }
    assert job_queue._is_dogrula(
        runtime, {"prompt_ceviri": True, "prompt_destek": True}
    ) == {"hata": "Prompt çevirisi ve Prompt Stüdyosu desteği aynı anda kullanılamaz"}


def test_enqueue_keeps_order_owner_and_refuses_closed_vault():
    runtime = _runtime()
    first = _new_job(runtime, "synthetic-a")
    second = _new_job(runtime, "synthetic-b", {"prompt": "second"})

    assert runtime._kuyruk == [first["is_id"], second["is_id"]]
    assert first["sahip"] == "synthetic-a"
    assert second["sahip"] == "synthetic-b"
    assert first["durum"] == "kuyrukta"
    assert first["ilerleme"] == {"kuyrukta": True, "oran": 0.0}

    runtime.kasa = _FakeVault(opened=False)
    assert job_queue._kuyruga_ekle(runtime, {"prompt": "denied"}, "synthetic-a") == (
        None,
        0,
    )
    assert len(runtime._kuyruk) == 2


def test_job_summary_detail_and_live_progress_keep_response_shape():
    runtime = _runtime()
    job = {
        "is_id": "job-1",
        "durum": "calisiyor",
        "istek": {
            "prompt": "a detailed synthetic scene",
            "model": "z-image",
            "workflow": "portrait",
        },
        "tur": "llm_chat",
        "sahip": "synthetic",
        "olustu": 1.0,
        "baslangic": 10.0,
        "bitis": 0,
        "prompt_id": "prompt-1",
        "ilerleme": {"node": "sampler"},
        "sonuc": {"seed": 123},
        "hata": None,
        "iptal": False,
    }
    runtime._aktif = {"prompt_id": "prompt-1", "ilerleme": {"node": "sampler"}}
    runtime._oran_hesapla = lambda value: 0.42

    summary = job_queue._is_ozet(runtime, job)
    detail = job_queue._is_detay(runtime, job)

    assert summary == {
        "is_id": "job-1",
        "durum": "calisiyor",
        "sira": 0,
        "ilerleme": {"node": "sampler", "oran": 0.42},
        "hata": None,
        "ceviri_durum": None,
        "gecen": summary["gecen"],
        "prompt": "Sohbet",
        "tur": "llm_chat",
        "model": "z-image",
        "workflow": "portrait",
        "is_tipi": "llm-sohbet",
        "seed": 123,
    }
    assert isinstance(summary["gecen"], float)
    assert detail["sonuc"] == {"seed": 123}
    assert detail["llm_canli"] == {"aktif": True, "tok_s": 12.5}


def test_queued_cancel_removes_job_and_sensitive_request_fields():
    runtime = _runtime()
    request = {
        "prompt": "keep me",
        "image_base64": "synthetic-image",
        "metin": "private text",
        "mesajlar": [{"role": "user"}],
    }
    job = _new_job(runtime, "synthetic", request)
    job["ceviri_hazir"] = threading.Event()
    job["ceviri_durum"] = "bekliyor"
    runtime._ceviri_kuyrugu.append(job)

    assert job_queue._isi_iptal_et(runtime, job) is True
    assert runtime._kuyruk == []
    assert runtime._ceviri_kuyrugu == []
    assert job["durum"] == "durduruldu"
    assert job["olay"].is_set()
    assert job["ceviri_hazir"].is_set()
    assert job["istek"] == {"prompt": "keep me"}
    assert job["ceviri_durum"] == "durduruldu"
