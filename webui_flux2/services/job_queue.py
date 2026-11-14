"""Queue admission and job state operations for the WebUI API.

The module owns no process-wide queue state. Callers provide the application
runtime namespace, including locks, queues, model policy and worker callbacks.
Worker implementations remain outside this service.
"""

from __future__ import annotations

import threading
import time
import uuid


def _is_dogrula(runtime, req):
    """Kuyruga girmeden once hizli ret. Hata dict'i doner veya None."""
    if "prompt_ceviri" in req and (not isinstance(req.get("prompt_ceviri"), bool)):
        return {"hata": "prompt_ceviri boolean olmali"}
    if req.get("prompt_ceviri") and req.get("prompt_destek"):
        return {
            "hata": "Prompt çevirisi ve Prompt Stüdyosu desteği aynı anda kullanılamaz"
        }
    if req.get("llm_model") is not None:
        allowed_llm = {item["id"] for item in runtime.llm.model_profilleri()}
        if req.get("llm_model") not in allowed_llm:
            return {
                "hata": "Seçilen LLM modeli bulunamadı. Ayarlardan geçerli bir model seç."
            }
    try:
        n_ref = (1 if req.get("image_base64") else 0) + (
            1 if req.get("image2_base64") else 0
        )
    except Exception:
        n_ref = 0
    if runtime.is_tipini_belirle(req, n_ref) == runtime.IS_TIPI_VIDEO:
        if req.get("prompt_ceviri"):
            return {
                "hata": "Prompt çevirisi yalnızca görsel üretiminde kullanılabilir",
                "is_tipi": runtime.IS_TIPI_VIDEO,
            }
        video_model = req.get("video_model_instance") or (
            "wan22" if runtime.RUNPOD_MODE else "video"
        )
        if video_model not in ("video", "wan22") or (
            video_model == "wan22" and (not runtime.RUNPOD_MODE)
        ):
            return {
                "hata": "Seçilen video modeli tanınmıyor.",
                "is_tipi": runtime.IS_TIPI_VIDEO,
            }
        if video_model == "wan22" and n_ref:
            return {
                "hata": "Wan 2.2 şu an metinden video üretir. Referans fotoğrafı kaldırın veya MiniMax H3 seçin.",
                "is_tipi": runtime.IS_TIPI_VIDEO,
            }
        if video_model == "video" and n_ref == 0:
            return {
                "hata": "video-uretim icin en az 1 referans fotograf gerekli",
                "is_tipi": runtime.IS_TIPI_VIDEO,
            }
        if video_model == "wan22" and (not runtime.pod_setup.workflow_ready("wan22")):
            return {
                "hata": "Wan 2.2 hazır değil. Kurulum ekranında Wan 2.2 grubunu indirin.",
                "is_tipi": runtime.IS_TIPI_VIDEO,
            }
        return None
    model = req.get("model", runtime.MODEL_FLUX2)
    if (
        model in (runtime.MODEL_ANIMA, runtime.MODEL_MOODY_ANIMA)
        and runtime.RUNPOD_MODE
    ):
        return {
            "hata": "Anima kapatıldı. Görsel üretim ayarlarından Illustrious veya başka bir modeli seç."
        }
    if model not in (
        runtime.MODEL_FLUX2,
        runtime.MODEL_Z_IMAGE,
        runtime.MODEL_ANIMA,
        runtime.MODEL_MOODY_ANIMA,
        runtime.MODEL_FLUX3,
        runtime.MODEL_KREA2,
        runtime.MODEL_QWEN_IMAGE_EDIT,
        runtime.MODEL_QWEN_IMAGE_EDIT_TURBO,
        runtime.MODEL_ILLUSTRIOUS,
    ):
        return {"hata": "Görsel modeli tanınmadı."}
    if model == runtime.MODEL_ILLUSTRIOUS and n_ref:
        return {
            "hata": "Illustrious şu anda yalnızca sıfırdan görsel üretir. Referansla başka bir görsel modeli seç."
        }
    if model == runtime.MODEL_KREA2:
        try:
            edit_strength = float(req.get("edit_strength", 0.65))
        except (TypeError, ValueError, OverflowError):
            return {"hata": "Krea 2 düzenleme gücü sayı olmalı."}
        if not 0.05 <= edit_strength <= 1.0:
            return {"hata": "Krea 2 düzenleme gücü 0.05 ile 1 arasında olmalı."}
    if model in (
        runtime.MODEL_QWEN_IMAGE_EDIT,
        runtime.MODEL_QWEN_IMAGE_EDIT_TURBO,
    ) and (not n_ref):
        return {
            "hata": "Qwen Image 2.1 düzenleme modeli için en az bir referans görsel ekle."
        }
    if req.get("flux3_bbox_mode") and model != runtime.MODEL_FLUX3:
        return {"hata": "Kutu yerleşimi yalnızca Flux 3 Klein ile kullanılabilir."}
    runpod_workflow = {
        runtime.MODEL_FLUX2: "flux2",
        runtime.MODEL_Z_IMAGE: "z_image",
        runtime.MODEL_FLUX3: "flux3",
        runtime.MODEL_KREA2: "krea2",
        runtime.MODEL_QWEN_IMAGE_EDIT: "qwen_image_edit",
        runtime.MODEL_QWEN_IMAGE_EDIT_TURBO: "qwen_image_edit_turbo",
        runtime.MODEL_ILLUSTRIOUS: "illustrious",
    }.get(model)
    if runtime.RUNPOD_MODE and runpod_workflow:
        instance_id = req.get("model_instance") or runpod_workflow
        instance = runtime.pod_setup.model_instance(instance_id)
        if instance is None or instance.get("family") != runpod_workflow:
            return {"hata": "Seçilen model kaydı bu üretim türüyle eşleşmiyor."}
        if not runtime.pod_setup.workflow_enabled(instance_id):
            return {"hata": "Bu görsel modeli şu anda geçici olarak devre dışı."}
        if not runtime.pod_setup.workflow_ready(instance_id):
            return {
                "hata": "Bu görsel modeli henüz hazır değil. 8192 portundaki Kurulum panelinden modeli indir."
            }
    return None


def _is_budala_kilitli(runtime):
    bitmis = [j for j in runtime._isler.values() if j["olay"].is_set()]
    bitmis.sort(key=lambda j: j.get("bitis", 0))
    for j in bitmis[: max(0, len(bitmis) - runtime._SAKLANAN_IS_SAYISI)]:
        runtime._isler.pop(j["is_id"], None)


def _kuyruga_ekle(runtime, req, sahip, tur="goruntu"):
    job = {
        "is_id": uuid.uuid4().hex[:12],
        "durum": "kuyrukta",
        "istek": req,
        "tur": tur,
        "sahip": sahip,
        "olustu": time.time(),
        "baslangic": 0,
        "bitis": 0,
        "prompt_id": None,
        "ilerleme": {"kuyrukta": True, "oran": 0.0},
        "sonuc": None,
        "hata": None,
        "iptal": False,
        "olay": threading.Event(),
    }
    with runtime._isler_kilit:
        if runtime._bellek_bosaltma_aktif or runtime._kapatma_istegi.is_set():
            return (None, 0)
        kasa_durumu = runtime.kasa.durum(sahip)
        if not kasa_durumu["acik"] or kasa_durumu["kapaniyor"]:
            return (None, 0)
        runtime._isler[job["is_id"]] = job
        runtime._kuyruk.append(job["is_id"])
        _is_budala_kilitli(runtime)
        sira = len(runtime._kuyruk)
        if tur == "goruntu" and req.get("prompt_ceviri"):
            job["ceviri_hazir"] = threading.Event()
            job["ceviri_durum"] = "bekliyor"
            runtime._ceviri_kuyrugu.append(job)
            if not runtime._ceviri_calismasi:
                runtime._ceviri_calismasi = True
                threading.Thread(target=runtime._ceviri_worker, daemon=True).start()
        if not runtime._kuyruk_calismasi:
            runtime._kuyruk_calismasi = True
            threading.Thread(target=runtime._kuyruk_worker, daemon=True).start()
    return (job, sira)


def _is_canli_ilerleme(runtime, job):
    if (
        job.get("durum") == "calisiyor"
        and job.get("prompt_id")
        and (runtime._aktif.get("prompt_id") == job.get("prompt_id"))
    ):
        il = runtime._aktif.get("ilerleme") or {}
        if isinstance(il, dict) and "oran" not in il:
            il = dict(il)
            il["oran"] = runtime._oran_hesapla(il)
        return il
    return job.get("ilerleme")


def _is_ozet(runtime, job):
    try:
        with runtime._isler_kilit:
            sira = (
                runtime._kuyruk.index(job["is_id"]) + 1
                if job["durum"] == "kuyrukta"
                else 0
            )
    except ValueError:
        sira = 0
    istek = job.get("istek") or {}
    tur = job.get("tur", "goruntu")
    if tur == "llm_prompt":
        _tip = "llm-prompt"
        prompt = (istek.get("metin") or "")[:80]
    elif tur == "llm_chat":
        _tip = "llm-sohbet"
        prompt = "Sohbet"
    elif tur == "llm_preload":
        _tip = "llm-hazirla"
        prompt = "Model hazırlığı"
    else:
        try:
            _n_ref = (1 if istek.get("image_base64") else 0) + (
                1 if istek.get("image2_base64") else 0
            )
            _tip = runtime.is_tipini_belirle(istek, _n_ref)
        except Exception:
            _tip = None
        prompt = (istek.get("prompt") or "")[:80]
    return {
        "is_id": job["is_id"],
        "durum": job["durum"],
        "sira": sira,
        "ilerleme": _is_canli_ilerleme(runtime, job),
        "hata": job.get("hata"),
        "ceviri_durum": job.get("ceviri_durum"),
        "gecen": (
            round(time.time() - job["baslangic"], 1) if job.get("baslangic") else 0
        ),
        "prompt": prompt,
        "tur": tur,
        "model": istek.get("model", runtime.MODEL_FLUX2),
        "workflow": istek.get("workflow", "tek"),
        "is_tipi": _tip,
        "seed": (job.get("sonuc") or {}).get("seed"),
    }


def _is_detay(runtime, job):
    d = _is_ozet(runtime, job)
    d["sonuc"] = job.get("sonuc")
    istek = job.get("istek") or {}
    goruntu_prompt_destek = job.get("tur") == "goruntu" and bool(
        istek.get("prompt_destek")
    )
    if (
        job.get("tur") in ("llm_prompt", "llm_chat", "llm_preload")
        or goruntu_prompt_destek
    ) and job.get("durum") == "calisiyor":
        d["llm_canli"] = runtime.llm.canli_olcum()
    return d


def _is_istegi_temizle(runtime, job):
    req = job.get("istek")
    if isinstance(req, dict):
        for key in ("image_base64", "image2_base64", "metin", "gorseller", "mesajlar"):
            req.pop(key, None)


def _is_sonucu_baslat(runtime, job):
    with runtime._isler_kilit:
        if job["iptal"]:
            raise RuntimeError("İş durduruldu")
        job["sonuc_yaziliyor"] = True


def _bekleyen_isi_iptal_et(runtime, job):
    """Yalnız kuyruk kilidi altındayken çağrılır."""
    runtime._kuyruk.remove(job["is_id"])
    job["iptal"] = True
    job["durum"] = "durduruldu"
    job["bitis"] = time.time()
    if "ceviri_hazir" in job:
        if job in runtime._ceviri_kuyrugu:
            runtime._ceviri_kuyrugu.remove(job)
            job["ceviri_hazir"].set()
        job["ceviri_durum"] = "durduruldu"
        job.pop("ceviri_sonuc", None)
        job.pop("ceviri_hata", None)
    _is_istegi_temizle(runtime, job)
    job["olay"].set()


def _isi_iptal_et(runtime, job):
    with runtime._isler_kilit:
        if job["durum"] == "kuyrukta":
            _bekleyen_isi_iptal_et(runtime, job)
            return True
        if job["durum"] != "calisiyor" or job.get("sonuc_yaziliyor"):
            return False
        if job["iptal"]:
            return True
        job["iptal"] = True
        prompt_id = job["prompt_id"]
    if prompt_id:
        runtime._comfy_isini_kes(prompt_id)
    with runtime._llm_is_gecis_kilit:
        llm_is_active = runtime._aktif_llm.get("is_id") == job["is_id"]
        llm_faz = runtime._aktif_llm.get("durum")
    if llm_is_active:
        if not runtime.llm.iptal_istegi() and (
            job.get("tur") == "llm_preload"
            or llm_faz in ("Hazırlanıyor", "Model yükleniyor")
        ):
            runtime.llm.durdur()
    return True


def _kullanici_islerini_durdur(runtime, kullanici, bekle=True, tavan_sn=None):
    """Kullanıcının işlerini iptal eder ve istenirse son yazımları bekler."""
    with runtime._isler_kilit:
        isler = [
            j
            for j in runtime._isler.values()
            if j["sahip"] == kullanici and (not j["olay"].is_set())
        ]
    for job in reversed(isler):
        _isi_iptal_et(runtime, job)
    if not bekle:
        return True
    son = None if tavan_sn is None else time.monotonic() + tavan_sn
    for job in isler:
        kalan = None if son is None else max(0, son - time.monotonic())
        if not job["olay"].wait(kalan):
            return False
    return True
