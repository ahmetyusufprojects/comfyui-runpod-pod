"""Image/video generation coordination around the legacy job state.

All application state and subsystem calls arrive via the explicit runtime
namespace. Workflow workers and ComfyUI/LLM implementations remain adapters.
"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
import uuid


def _isi_calistir(runtime, job):
    """Kuyruk iscisi tek uretimi bastan sona yurutur (eski /api/uret govdesi).
    Istemci baglantiyi kesse bile devam eder; sonuc is kaydinda + gecmiste durur."""
    request_rejected = False
    req = job.get("istek") or {}
    job["durum"] = "calisiyor"
    if not req.get("prompt_destek") and (not job.get("baslangic")):
        job["baslangic"] = time.time()
    try:
        if req.get("postprocess") in ("image", "video"):
            runtime._medyayi_iyilestir(job)
            return
        hata = runtime._is_dogrula(req)
        if hata:
            job["durum"] = "hata"
            job["hata"] = hata.get("hata", "gecersiz istek")
            job["bitis"] = time.time()
            request_rejected = True
            return
        w = max(64, min(4096, int(req.get("width", 1024))))
        h = max(64, min(4096, int(req.get("height", 1024))))
        max_side = max(256, min(4096, int(req.get("max_side", 1536))))
        raw = None
        raw2 = None
        ham1 = None
        ham2 = None
        foto_kaynak = None
        foto_hedef = None
        foto_kaynak2 = None
        foto_hedef2 = None
        kucultuldu = False
        olcekle = req.get("max_olcek", True)
        n_ref_istek = (1 if req.get("image_base64") else 0) + (
            1 if req.get("image2_base64") else 0
        )
        is_tipi = runtime.is_tipini_belirle(req, n_ref_istek)
        vid = runtime.video_parametreleri(req)
        if is_tipi == runtime.IS_TIPI_VIDEO:
            runtime._video_uret(job, req)
            return
        if req.get("image_base64"):
            ham1 = base64.b64decode(req["image_base64"])
            raw, foto_kaynak, foto_hedef, k1 = runtime._fotograf_hazirla(
                ham1, max_side, olcekle
            )
            kucultuldu = kucultuldu or k1
        if req.get("image2_base64"):
            ham2 = base64.b64decode(req["image2_base64"])
            raw2, foto_kaynak2, foto_hedef2, k2 = runtime._fotograf_hazirla(
                ham2, max_side, olcekle
            )
            kucultuldu = kucultuldu or k2
        baz = foto_hedef or foto_hedef2
        if baz is not None:
            w, h = baz
        else:
            w, h = (max(16, w // 16 * 16), max(16, h // 16 * 16))
        model = req.get("model")
        if model in (runtime.MODEL_ANIMA, runtime.MODEL_MOODY_ANIMA) and n_ref_istek:
            model = runtime.MODEL_FLUX2
        elif runtime.RUNPOD_MODE and model in (
            runtime.MODEL_ANIMA,
            runtime.MODEL_MOODY_ANIMA,
        ):
            model = runtime.MODEL_ILLUSTRIOUS
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
            model = runtime.MODEL_FLUX2
        model_instance = req.get("model_instance") or model
        workflow = (
            runtime.pod_setup.workflow(model_instance) if runtime.RUNPOD_MODE else {}
        )
        default_sampler = workflow.get("sampler") or (
            "res_multistep" if model == runtime.MODEL_Z_IMAGE else "euler"
        )
        default_steps = workflow.get("steps") or (
            9
            if model == runtime.MODEL_Z_IMAGE
            else 8 if model == runtime.MODEL_ANIMA else 4
        )
        default_cfg = workflow.get("cfg", 1.0)
        prompt_girdi = str(req.get("prompt_girdi") or req.get("prompt") or "")
        prompt_servis = str(req.get("prompt") or "")
        ceviri = None
        if req.get("prompt_ceviri"):
            job["ilerleme"] = {
                "asama": "ceviri",
                "calisan": "Türkçe prompt İngilizceye çevriliyor",
                "oran": 0.04,
            }
            ceviri = job["ceviri_sonuc"]
            prompt_servis = ceviri["metin"]
            req["prompt_girdi"] = prompt_girdi
            req["prompt_en"] = prompt_servis
        p = {
            "prompt": prompt_servis,
            "negative": str(req.get("negative") or "").strip()[:2000],
            "width": w,
            "height": h,
            "seed": int(req.get("seed", -1)),
            "steps": max(1, min(50, int(req.get("steps", default_steps)))),
            "cfg": float(req.get("cfg", default_cfg)),
            "sampler": req.get("sampler", default_sampler),
            "edit_strength": float(req.get("edit_strength", 0.65)),
            "flux3_bbox_mode": bool(req.get("flux3_bbox_mode")),
        }
        if runtime.RUNPOD_MODE:
            workflow_key = {
                runtime.MODEL_FLUX2: "flux2",
                runtime.MODEL_Z_IMAGE: "z_image",
                runtime.MODEL_FLUX3: "flux3",
                runtime.MODEL_KREA2: "krea2",
                runtime.MODEL_QWEN_IMAGE_EDIT: "qwen_image_edit",
                runtime.MODEL_QWEN_IMAGE_EDIT_TURBO: "qwen_image_edit_turbo",
                runtime.MODEL_ILLUSTRIOUS: "illustrious",
            }[model]
            if model in (
                runtime.MODEL_FLUX2,
                runtime.MODEL_Z_IMAGE,
                runtime.MODEL_FLUX3,
                runtime.MODEL_KREA2,
                runtime.MODEL_QWEN_IMAGE_EDIT,
                runtime.MODEL_QWEN_IMAGE_EDIT_TURBO,
            ):
                p["model_files"] = {
                    "diffusion": workflow.get("diffusion_file"),
                    "text_encoder": workflow.get("text_encoder_file"),
                    "vae": workflow.get("vae_file"),
                }
            elif model == runtime.MODEL_ILLUSTRIOUS:
                p["model_files"] = {"checkpoint": workflow.get("checkpoint_file")}
            p["loras"] = runtime.pod_setup.ensure_loras(
                workflow_key, req.get("loras") if "loras" in req else None, job=job
            )
        uretim_baslangic = time.perf_counter()
        image_name = None
        image_name2 = None
        if raw is not None:
            fn = "webui_%s.png" % uuid.uuid4().hex[:8]
            up = runtime.upload_image_to_comfy(raw, fn)
            image_name = up.get("name", fn)
        if raw2 is not None:
            fn = "webui2_%s.png" % uuid.uuid4().hex[:8]
            up = runtime.upload_image_to_comfy(raw2, fn)
            image_name2 = up.get("name", fn)
        if model == runtime.MODEL_ANIMA:
            prompt, seed = runtime.build_anima_prompt(p)
        elif model == runtime.MODEL_MOODY_ANIMA:
            prompt, seed = runtime.build_anima_prompt(
                p, runtime.MOODY_ANIMA_DIFFUSION_FILE
            )
        elif model == runtime.MODEL_FLUX3:
            prompt, seed = runtime.build_flux3_prompt(p, image_name, image_name2)
        elif model == runtime.MODEL_KREA2:
            prompt, seed = runtime.build_krea2_prompt(p, image_name)
        elif model in (
            runtime.MODEL_QWEN_IMAGE_EDIT,
            runtime.MODEL_QWEN_IMAGE_EDIT_TURBO,
        ):
            prompt, seed = runtime.build_qwen_image_edit_prompt(
                p,
                image_name,
                image_name2,
                turbo=model == runtime.MODEL_QWEN_IMAGE_EDIT_TURBO,
            )
        elif model == runtime.MODEL_ILLUSTRIOUS:
            prompt, seed = runtime.build_illustrious_prompt(p)
        else:
            prompt_builder = (
                runtime.build_z_image_prompt
                if model == runtime.MODEL_Z_IMAGE
                else runtime.build_prompt
            )
            prompt, seed = prompt_builder(p, image_name, image_name2)
        cid = uuid.uuid4().hex
        isitma_yolu = runtime._imge_modeli_isitme_karari(
            model, bool(job.get("_llama_canliydi"))
        )
        if isitma_yolu:
            job["ilerleme"] = {
                "asama": "isitma",
                "calisan": "Model dosyası önbelleğe alınıyor",
                "oran": 0.02,
            }
            runtime._dosya_isit(isitma_yolu, job)
        if job["iptal"]:
            raise RuntimeError("İş durduruldu")
        res = runtime.comfy_post_json("/prompt", {"prompt": prompt, "client_id": cid})
        if "node_errors" in res and res["node_errors"]:
            job["durum"] = "hata"
            job["hata"] = "node hatasi: %s" % json.dumps(res["node_errors"])[:500]
            job["bitis"] = time.time()
            return
        job["prompt_id"] = res["prompt_id"]
        runtime._aktif["prompt_id"] = res["prompt_id"]
        runtime._aktif["sahip"] = job.get("sahip")
        runtime._aktif["baslangic"] = time.time()
        runtime._aktif["ilerleme"] = {"kuyrukta": True, "oran": 0.0}
        runtime._aktif["nodlar"] = {
            nid: v.get("class_type", nid) for nid, v in prompt.items()
        }
        threading.Thread(
            target=runtime._ws_ilerleme_dinle,
            args=(res["prompt_id"], cid, runtime._aktif["nodlar"]),
            daemon=True,
        ).start()
        try:
            if job["iptal"]:
                runtime._comfy_isini_kes(res["prompt_id"])
            entry = runtime.wait_result(res["prompt_id"], job=job)
        finally:
            if runtime._aktif.get("prompt_id") == res["prompt_id"]:
                runtime._aktif["prompt_id"] = None
                runtime._aktif["sahip"] = None
        imgs = []
        bloblar = []
        for node_id, out in (entry.get("outputs") or {}).items():
            for im in out.get("images", []) or []:
                blob = runtime.fetch_view_image(im)
                bloblar.append(blob)
                imgs.append(
                    "data:image/png;base64," + base64.b64encode(blob).decode("ascii")
                )
        generation_ms = round((time.perf_counter() - uretim_baslangic) * 1000)
        sure_ms = (
            round((time.time() - job["baslangic"]) * 1000)
            if job.get("baslangic")
            else generation_ms
        )
        prompt_islem = (
            "ceviri"
            if ceviri
            else "prompt_destek" if req.get("prompt_destek") else None
        )
        prompt_en = p["prompt"] if prompt_islem else None
        n_ref = (1 if image_name else 0) + (1 if image_name2 else 0)
        mod = (
            "saf-uretim"
            if n_ref == 0
            else "cift-referans" if n_ref == 2 else "duzenleme"
        )
        sahip = job.get("sahip")
        kota_hata = runtime._kota_kontrol(
            sahip,
            yeni_bayt=sum((len(b) for b in bloblar))
            + (len(raw) if raw else 0)
            + (len(raw2) if raw2 else 0),
            yeni_kayit=len(bloblar) + (1 if raw else 0) + (1 if raw2 else 0),
        )
        if kota_hata:
            job["durum"] = "hata"
            job["hata"] = kota_hata
            job["bitis"] = time.time()
            return
        runtime._is_sonucu_baslat(job)
        kut1 = kut2 = None
        try:
            if ham1 is not None and raw is not None and foto_hedef:
                kut1 = runtime.kutuphaneye_kaydet(
                    sahip, ham1, raw, foto_hedef[0], foto_hedef[1]
                )
        except Exception:
            kut1 = None
        try:
            if ham2 is not None and raw2 is not None and foto_hedef2:
                kut2 = runtime.kutuphaneye_kaydet(
                    sahip, ham2, raw2, foto_hedef2[0], foto_hedef2[1]
                )
        except Exception:
            kut2 = None
        gecmis_kayit = None
        gecmis_dosyalar = []
        try:
            meta = {
                "prompt": p["prompt"],
                "prompt_tr": prompt_girdi if prompt_islem else None,
                "prompt_en": prompt_en,
                "prompt_islem": prompt_islem,
                "prompt_ceviri_model": ceviri["model"] if ceviri else None,
                "prompt_ceviri_device": ceviri["cihaz"] if ceviri else None,
                "prompt_ceviri_ms": ceviri["sure_ms"] if ceviri else None,
                "generation_ms": generation_ms,
                "sure_ms": sure_ms,
                "negative": p["negative"],
                "width": w,
                "height": h,
                "seed": seed,
                "steps": p["steps"],
                "cfg": p["cfg"],
                "sampler": p["sampler"],
                "model": model,
                "workflow": req.get("workflow", "tek"),
                "max_side": max_side,
                "max_olcek": olcekle,
                "mod": mod,
                "is_tipi": is_tipi,
                "suresn": vid["suresn"],
                "fps": vid["fps"],
                "foto_hashes": [
                    hashlib.sha256(raw_image).hexdigest()
                    for raw_image in (ham1, ham2)
                    if raw_image is not None
                ],
                "foto_kutuphane": kut1,
                "foto_kutuphane2": kut2,
            }
            gecmis_kayit, gecmis_dosyalar = runtime.gecmise_kaydet(sahip, bloblar, meta)
        except Exception:
            gecmis_kayit, gecmis_dosyalar = (None, [])
        job["sonuc"] = {
            "gorseller": imgs,
            "seed": seed,
            "width": w,
            "height": h,
            "model": model,
            "prompt": p["prompt"],
            "negative": p["negative"],
            "prompt_girdi": prompt_girdi,
            "prompt_en": prompt_en,
            "prompt_islem": prompt_islem,
            "prompt_ceviri_model": ceviri["model"] if ceviri else None,
            "prompt_ceviri_device": ceviri["cihaz"] if ceviri else None,
            "prompt_ceviri_ms": ceviri["sure_ms"] if ceviri else None,
            "generation_ms": generation_ms,
            "sure_ms": sure_ms,
            "prompt_destek": bool(req.get("prompt_destek")),
            "foto_kaynak": foto_kaynak,
            "foto_hedef": foto_hedef,
            "foto_kaynak2": foto_kaynak2,
            "foto_hedef2": foto_hedef2,
            "foto_kucultuldu": kucultuldu,
            "foto_boy": foto_hedef,
            "foto_boy2": foto_hedef2,
            "mod": mod,
            "is_tipi": is_tipi,
            "suresn": vid["suresn"],
            "fps": vid["fps"],
            "kutuphane": kut1,
            "kutuphane2": kut2,
            "gecmis": gecmis_kayit,
            "gecmis_dosyalar": gecmis_dosyalar,
            "is_id": job["is_id"],
        }
        runtime._uretim_sonrasi_iyilestir(job, "image")
        job["ilerleme"] = {"bitti": True, "oran": 1.0}
        job["durum"] = "bitti"
        job["bitis"] = time.time()
    except Exception as e:
        if "durduruldu" in str(e):
            job["durum"] = "durduruldu"
        else:
            job["durum"] = "hata"
            job["hata"] = str(e)[:1000]
        job["bitis"] = time.time()
    finally:
        if not request_rejected:
            try:
                runtime.kasa.temizle_comfy_artiklari()
            except Exception:
                pass
            try:
                runtime.comfy_request(
                    "POST",
                    "/history",
                    body=b'{"clear": true}',
                    headers={"Content-Type": "application/json"},
                )
            except Exception:
                pass
