"""ComfyUI media enhancement operations.

Pass the server module (or a compatible runtime object) explicitly. This keeps
HTTP, ComfyUI, history, quota, and model setup dependencies testable without
importing the legacy server module here.
"""

_IMGE_ISITMA_OKUMA = 8 * 1024 * 1024
_IMGE_ISITMA_ATLA_SN = 120
__all__ = (
    "_dosya_isit", "_imge_modeli_isitme_karari", "_iyilestirme_kaydi",
    "_iyilestirme_istegi_dogrula", "_uretim_sonrasi_iyilestirme_istegi_dogrula",
    "_iyilestirme_modelini_hazirla", "_iyilestirme_ffprobe",
    "_iyilestirme_cikti_temizle", "_iyilestirme_comfy_isini_yurut",
    "_medyayi_iyilestir", "_uretim_sonrasi_iyilestir",
)

def _dosya_isit(runtime, yol, job):
    """Model dosyasini sirali okuyup sayfa onbelligine koyar.

    ComfyUI model dosyalarini mmap-fault yoluyla yukler; sicak cache'te bu
    yukleme ~2-3 kat daha hizli tamamlanir (llama'da olculdu: sicak 14 sn,
    soguk 26-33 sn). Sirali NVMe okuma ~2400 MB/s, fault yolu 350-600 MB/s."""
    try:
        f = open(yol, "rb", buffering=0)
    except OSError:
        return
    blok = bytearray(_IMGE_ISITMA_OKUMA)
    mv = memoryview(blok)
    try:
        while not job.get("iptal"):
            if not f.readinto(mv):
                break
    finally:
        f.close()


def _imge_modeli_isitme_karari(runtime, model, llama_canliydi):
    """Isitilacak text encoder dosya yolu veya None döner."""
    if runtime.RUNPOD_MODE:
        workflow_key = {runtime.MODEL_FLUX2: "flux2", runtime.MODEL_Z_IMAGE: "z_image",
                        runtime.MODEL_FLUX3: "flux3", runtime.MODEL_KREA2: "krea2",
                        runtime.MODEL_QWEN_IMAGE_EDIT: "qwen_image_edit",
                        runtime.MODEL_QWEN_IMAGE_EDIT_TURBO: "qwen_image_edit_turbo"}.get(model)
        encoder = runtime.pod_setup.workflow(workflow_key).get("text_encoder_file") \
            if workflow_key else None
        if not encoder:
            return None
    else:
        encoder = {
            runtime.MODEL_FLUX2: runtime.TEXT_ENCODER_FILE,
            runtime.MODEL_Z_IMAGE: runtime.Z_IMAGE_TEXT_ENCODER_FILE,
            runtime.MODEL_ANIMA: runtime.ANIMA_TEXT_ENCODER_FILE,
            runtime.MODEL_MOODY_ANIMA: runtime.ANIMA_TEXT_ENCODER_FILE,
        }.get(model, runtime.TEXT_ENCODER_FILE)
    yol = runtime.os.path.join(runtime.COMFY_KOK, "models", "text_encoders", encoder)
    simdi = runtime.time.monotonic()
    son_etkilesim = getattr(runtime, "_son_goruntu_etkilesim", None)
    if not llama_canliydi and son_etkilesim is not None \
            and simdi - son_etkilesim < _IMGE_ISITMA_ATLA_SN:
        return None
    runtime._son_goruntu_etkilesim = simdi
    return yol


def _iyilestirme_kaydi(runtime, kullanici, req):
    """Resolve a media file only from the signed-in user's own history record."""
    kayit = runtime._guvenli_dosya_adi(req.get("kayit"))
    dosya = runtime._guvenli_dosya_adi(req.get("dosya"))
    if not kayit or not kayit.endswith(".json") or not dosya:
        raise ValueError("İyileştirilecek kayıt bulunamadı.")
    klasor = runtime.os.path.realpath(runtime._kullanici_gecmis(kullanici))
    meta_yolu = runtime.os.path.realpath(runtime.os.path.join(klasor, kayit))
    if not meta_yolu.startswith(klasor + runtime.os.sep) or not runtime.os.path.isfile(meta_yolu):
        raise ValueError("Bu geçmiş kaydı bulunamadı.")
    try:
        with open(meta_yolu, "r", encoding="utf-8") as kaynak:
            meta = runtime.json.load(kaynak)
    except (OSError, ValueError):
        raise ValueError("Geçmiş kaydı okunamadı.") from None
    if dosya not in (meta.get("dosyalar") or []):
        raise ValueError("Bu dosya seçilen geçmiş kaydına ait değil.")
    yol = runtime.os.path.realpath(runtime.os.path.join(klasor, dosya))
    if not yol.startswith(klasor + runtime.os.sep) or not runtime.os.path.isfile(yol):
        raise ValueError("İyileştirilecek dosya bulunamadı.")
    uzanti = runtime.os.path.splitext(dosya)[1].lower()
    video = uzanti in (".mp4", ".webm", ".mov", ".m4v")
    if not video and uzanti not in (".png", ".jpg", ".jpeg", ".webp"):
        raise ValueError("Bu dosya türü iyileştirilemiyor.")
    return {"record": kayit, "file": dosya, "path": yol,
            "meta": meta, "video": video}


def _iyilestirme_istegi_dogrula(runtime, kullanici, req):
    if not runtime.RUNPOD_MODE:
        return {"hata": "Bağımsız medya iyileştirme yalnızca RunPod kurulumunda kullanılabilir."}
    tur = req.get("postprocess")
    if tur not in ("image", "video"):
        return {"hata": "İyileştirme türü geçersiz."}
    try:
        secim = _iyilestirme_kaydi(runtime, kullanici, req)
        if secim["video"] != (tur == "video"):
            raise ValueError("Seçilen dosya ile iyileştirme türü eşleşmiyor.")
        preset = req.get("enhance_preset")
        allowed_presets = runtime.VIDEO_PRESETS if tur == "video" else runtime.IMAGE_PRESETS
        if preset not in allowed_presets:
            raise ValueError("Görüntü iyileştirme seçeneği geçersiz.")
        scale = float(req.get("scale", 2))
        if scale not in (1.0, 1.5, 2.0, 3.0, 4.0):
            raise ValueError("Büyütme katsayısı 1, 1.5, 2, 3 veya 4 olmalı.")
        fps_multiplier = int(req.get("fps_multiplier", 1))
        if fps_multiplier not in (1, 2, 3, 4):
            raise ValueError("FPS artırma 1x, 2x, 3x veya 4x olmalı.")
        if runtime.os.path.getsize(secim["path"]) > 1024 * 1024 * 1024:
            raise ValueError("1 GB üzerindeki dosyalar şu anda iyileştirilemiyor.")
    except (TypeError, ValueError, OverflowError, OSError) as exc:
        return {"hata": str(exc) or "İstek geçersiz."}
    return None


def _uretim_sonrasi_iyilestirme_istegi_dogrula(runtime, req):
    options = req.get("enhance_after")
    if not options:
        return None
    if not isinstance(options, dict):
        return {"hata": "Üretim sonrası iyileştirme ayarları geçersiz."}
    if options.get("enabled") is not True:
        return None
    if not runtime.RUNPOD_MODE:
        return {"hata": "Üretim sonrası iyileştirme yalnızca RunPod kurulumunda kullanılabilir."}
    refs = int(bool(req.get("image_base64"))) + int(bool(req.get("image2_base64")))
    media_type = "video" if runtime.is_tipini_belirle(req, refs) == runtime.IS_TIPI_VIDEO else "image"
    if options.get("type") != media_type:
        return {"hata": "Seçilen iyileştirme türü üretim türüyle uyuşmuyor."}
    presets = runtime.VIDEO_PRESETS if media_type == "video" else runtime.IMAGE_PRESETS
    if options.get("preset") not in presets:
        return {"hata": "Üretim sonrası iyileştirme modeli geçersiz."}
    try:
        scale = float(options.get("scale", 2))
        fps_multiplier = int(options.get("fps_multiplier", 1))
    except (TypeError, ValueError, OverflowError):
        return {"hata": "İyileştirme büyütme ve FPS ayarları geçersiz."}
    if scale not in (1.0, 1.5, 2.0, 3.0, 4.0):
        return {"hata": "Büyütme katsayısı 1, 1.5, 2, 3 veya 4 olmalı."}
    if fps_multiplier not in (1, 2, 3, 4) or (media_type == "image" and fps_multiplier != 1):
        return {"hata": "FPS artırma 1x, 2x, 3x veya 4x olmalı."}
    req["enhance_after"] = {"enabled": True, "type": media_type,
                            "preset": options["preset"], "scale": scale,
                            "fps_multiplier": fps_multiplier}
    return None


def _iyilestirme_modelini_hazirla(runtime, group, job):
    item = runtime.pod_setup.CATALOG.get(group)
    if not item:
        raise RuntimeError("İyileştirme modeli katalogda bulunamadı.")
    runtime.pod_setup.start(group)
    deadline = runtime.time.monotonic() + 3600
    while runtime.time.monotonic() < deadline:
        if job.get("iptal"):
            raise RuntimeError("İş durduruldu.")
        status = runtime.pod_setup.status()
        state_row = next((row for row in status["gruplar"] if row["id"] == group), {})
        state = state_row.get("durum", "idle")
        if state == "error":
            raise RuntimeError("İyileştirme modeli indirilemedi: " +
                               str(state_row.get("ayrinti") or "bilinmeyen hata")[:300])
        files = item.get("files") or []
        if files and all(runtime.pod_setup._model_verified(
                runtime.pod_setup.MODELS / f["dir"] / f["name"], f)
                for f in files if not f.get("optional")):
            return files
        job["ilerleme"] = {"asama": "model", "calisan":
                            (state_row.get("ayrinti") or "İyileştirme modeli hazırlanıyor"),
                            "oran": 0.05}
        runtime.time.sleep(0.5)
    raise TimeoutError("İyileştirme modeli 60 dakika içinde hazırlanamadı.")


def _iyilestirme_ffprobe(runtime, path):
    try:
        result = runtime.subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height,r_frame_rate,avg_frame_rate:format=duration",
             "-of", "json", path], stdout=runtime.subprocess.PIPE,
            stderr=runtime.subprocess.DEVNULL, timeout=20, check=True, text=True)
        info = runtime.json.loads(result.stdout)
        stream = (info.get("streams") or [])[0]
        frame_rate = stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "24/1"
        n, _, d = str(frame_rate).partition("/")
        fps = float(n) / max(1.0, float(d or 1))
        duration = float((info.get("format") or {}).get("duration", 0) or 0)
        return int(stream["width"]), int(stream["height"]), fps, duration
    except Exception:
        return 0, 0, 24.0, 0.0


def _iyilestirme_cikti_temizle(runtime, image):
    if not isinstance(image, dict) or image.get("type", "output") != "output":
        return
    filename = runtime._guvenli_dosya_adi(image.get("filename"))
    subfolder = str(image.get("subfolder") or "")
    root = runtime.os.path.realpath(runtime.os.path.join(runtime.COMFY_KOK, "output"))
    path = runtime.os.path.realpath(runtime.os.path.join(root, subfolder, filename or ""))
    if filename and path.startswith(root + runtime.os.sep):
        try:
            runtime.os.remove(path)
        except OSError:
            pass


def _iyilestirme_comfy_isini_yurut(runtime, job, graph):
    cid = runtime.uuid.uuid4().hex
    response = runtime.comfy_post_json("/prompt", {"prompt": graph, "client_id": cid})
    if response.get("node_errors"):
        raise RuntimeError("İyileştirme akışında bir adım başlatılamadı: " +
                           runtime.json.dumps(response["node_errors"], ensure_ascii=False)[:500])
    prompt_id = response["prompt_id"]
    job["prompt_id"] = prompt_id
    runtime._aktif["prompt_id"] = prompt_id
    runtime._aktif["sahip"] = job.get("sahip")
    runtime._aktif["baslangic"] = runtime.time.time()
    runtime._aktif["ilerleme"] = {"kuyrukta": True, "oran": 0.0}
    runtime._aktif["nodlar"] = {node_id: node.get("class_type", node_id)
                        for node_id, node in graph.items()}
    runtime.threading.Thread(target=runtime._ws_ilerleme_dinle,
                     args=(prompt_id, cid, runtime._aktif["nodlar"]), daemon=True).start()
    try:
        if job.get("iptal"):
            runtime._comfy_isini_kes(prompt_id)
        return runtime.wait_result(prompt_id, job=job)
    finally:
        if runtime._aktif.get("prompt_id") == prompt_id:
            runtime._aktif["prompt_id"] = None
            runtime._aktif["sahip"] = None
        job.pop("prompt_id", None)


def _medyayi_iyilestir(runtime, job):
    req = job.get("istek") or {}
    owner = job.get("sahip")
    selected = _iyilestirme_kaydi(runtime, owner, req)
    preset = req["enhance_preset"]
    scale = float(req.get("scale", 2))
    fps_multiplier = int(req.get("fps_multiplier", 1)) if selected["video"] else 1
    temporal_video = selected["video"] and preset in runtime.VIDEO_PRESETS
    choice = runtime.IMAGE_PRESETS.get(preset)
    groups = [runtime.VIDEO_PRESETS[preset]["group"]] if temporal_video else [choice["group"]]
    if not temporal_video and preset == "skin" and scale > 1:
        groups.append(runtime.IMAGE_PRESETS["photo"]["group"])
    if selected["video"] and fps_multiplier > 1:
        groups.append("enhance_rife")
    for group in groups:
        _iyilestirme_modelini_hazirla(runtime, group, job)
    if job.get("iptal"):
        raise RuntimeError("İş durduruldu.")
    try:
        comfy_queue = runtime.comfy_get_json("/queue")
    except Exception as exc:
        raise RuntimeError("ComfyUI kuyruğu doğrulanamadı; GPU belleği boşaltılmadı: %s" % exc) from None
    if comfy_queue.get("queue_running") or comfy_queue.get("queue_pending"):
        raise RuntimeError("ComfyUI kuyruğunda başka işler varken iyileştirme başlatılamaz.")
    job["ilerleme"] = {"asama": "iyilestirme", "calisan": "GPU belleği hazırlanıyor",
                        "oran": 0.12}
    status, _ = runtime.comfy_request(
        "POST", "/free",
        body=runtime.json.dumps({"unload_models": True, "free_memory": True}).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    if status != 200:
        raise RuntimeError("İyileştirmeden önce ComfyUI GPU belleği boşaltılamadı (%s)." % status)

    temp_root = None
    uploaded_path = None
    started = runtime.time.perf_counter()
    output_blobs = []
    try:
        if selected["video"]:
            input_dir = runtime.os.path.join(runtime.COMFY_KOK, "input")
            runtime.os.makedirs(input_dir, exist_ok=True)
            temp_root = runtime.tempfile.mkdtemp(prefix="webui_enhance_", dir=input_dir)
            src_w, src_h, src_fps, duration = _iyilestirme_ffprobe(selected["path"])
            if not src_w or not src_h or src_fps <= 0:
                raise RuntimeError("Video ölçüleri veya kare hızı okunamadı.")
            if duration > 120:
                raise RuntimeError("Şu anda 2 dakikadan uzun videolar iyileştirilemiyor.")
            output_fps = src_fps * fps_multiplier
            if output_fps > 120:
                raise RuntimeError("FPS artırımı 120 FPS sınırını aşamaz.")
            if duration and duration * src_fps * fps_multiplier > 7200:
                raise RuntimeError("Video çok uzun; FPS artırımı sonrası kare sayısı fazla.")
            source_pattern = runtime.os.path.join(temp_root, "source_%08d.jpg")
            extract = runtime.subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", selected["path"],
                 "-map", "0:v:0", "-vsync", "0", "-q:v", "2", source_pattern, "-y"],
                stdout=runtime.subprocess.DEVNULL, stderr=runtime.subprocess.PIPE, timeout=900, check=False)
            if extract.returncode:
                raise RuntimeError("Videodan kareler alınamadı: " +
                                   extract.stderr.decode("utf-8", "replace")[-400:])
            source_files = sorted((name for name in runtime.os.listdir(temp_root)
                                   if name.startswith("source_") and name.endswith(".jpg")))
            if not source_files:
                raise RuntimeError("Videoda işlenecek kare bulunamadı.")
            frame_count = len(source_files)
            if frame_count < 2 and fps_multiplier > 1:
                raise RuntimeError("FPS artırımı için videoda en az iki kare olmalı.")
            if frame_count * fps_multiplier > 7200:
                raise RuntimeError("Video çok uzun; FPS artırımı sonrası kare sayısı fazla.")
            if temporal_video:
                batch_size, step = 5, 4
                chunks = runtime.seedvr2_video_chunks(frame_count)
                starts = [start for start, _ in chunks]
                target_short = max(16, min(4096, int(round(min(src_w, src_h) * scale))))
                target_short -= target_short % 2
            else:
                model_scale = choice["model_scale"]
                per_frame = max(1, src_w * src_h * 12 * (fps_multiplier + model_scale ** 2))
                try:
                    import psutil
                    available = int(psutil.virtual_memory().available * 0.2)
                except Exception:
                    available = 2 * 1024 ** 3
                batch_size = max(2 if fps_multiplier > 1 else 1,
                                 min(4, available // per_frame))
                step = max(1, batch_size - 1) if fps_multiplier > 1 else batch_size
                starts = list(range(0, frame_count, step))
                if fps_multiplier > 1 and len(starts) > 1 and frame_count - starts[-1] < 2:
                    starts.pop()
            output_index = 0
            video_output_size = None
            batches = len(starts)
            for batch_index, start_index in enumerate(starts):
                if job.get("iptal"):
                    raise RuntimeError("İş durduruldu.")
                end_index = min(frame_count, start_index + batch_size)
                frame_names = [runtime.os.path.relpath(
                    runtime.os.path.join(temp_root, name), input_dir).replace(runtime.os.sep, "/")
                    for name in source_files[start_index:end_index]]
                if not frame_names:
                    continue
                actual_count = len(frame_names)
                if temporal_video:
                    frame_names.extend([frame_names[-1]] * (5 - len(frame_names)))
                    graph = runtime.build_seedvr2_video_enhancement(
                        frame_names, target_short, fps_multiplier,
                        seed=int(runtime.time.time_ns() % (2 ** 32)),
                        model_file=runtime.VIDEO_PRESETS[preset]["file"])
                else:
                    graph, _ = runtime.build_video_enhancement(frame_names, preset, scale,
                                                        fps_multiplier, src_fps)
                job["ilerleme"] = {"asama": "video",
                                   "calisan": ("SeedVR2 videoyu birlikte iyileştiriyor (%d/%d); ses korunacak" if temporal_video else "Video kareleri iyileştiriliyor (%d/%d); ses korunacak") %
                                              (batch_index + 1, batches),
                                   "oran": 0.1 + 0.8 * batch_index / max(1, batches)}
                entry = _iyilestirme_comfy_isini_yurut(runtime, job, graph)
                images = []
                for output in (entry.get("outputs") or {}).values():
                    for image in output.get("images", []) or []:
                        try:
                            blob = runtime.fetch_view_image(image)
                            if blob:
                                images.append(blob)
                        finally:
                            _iyilestirme_cikti_temizle(runtime, image)
                if temporal_video:
                    expected = (actual_count - 1) * fps_multiplier + 1
                    images = images[:expected]
                    if start_index > 0 and images:
                        images = images[1:]
                elif fps_multiplier > 1 and start_index > 0 and images:
                    images = images[1:]
                for blob in images:
                    if video_output_size is None:
                        try:
                            from io import BytesIO
                            from PIL import Image
                            with Image.open(BytesIO(blob)) as first_frame:
                                video_output_size = first_frame.size
                        except Exception:
                            pass
                    frame_path = runtime.os.path.join(temp_root, "processed_%08d.png" % output_index)
                    with open(frame_path, "wb") as frame_file:
                        frame_file.write(blob)
                    output_index += 1
            if temporal_video and fps_multiplier > 1 and output_index:
                last_frame = runtime.os.path.join(temp_root, "processed_%08d.png" % (output_index - 1))
                for _ in range(fps_multiplier - 1):
                    runtime.shutil.copyfile(last_frame, runtime.os.path.join(
                        temp_root, "processed_%08d.png" % output_index))
                    output_index += 1
            if temporal_video and output_index != frame_count * fps_multiplier:
                raise RuntimeError("SeedVR2 kare sayısı beklenen sonuçla uyuşmadı (%d/%d)." %
                                   (output_index, frame_count * fps_multiplier))
            if not output_index:
                raise RuntimeError("İyileştirilmiş video kareleri kaydedilemedi.")
            output_path = runtime.os.path.join(temp_root, "result.mp4")
            encode_base = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                 "-framerate", "%.6f" % output_fps,
                 "-i", runtime.os.path.join(temp_root, "processed_%08d.png"),
                 "-i", selected["path"], "-map", "0:v:0", "-map", "1:a?",
                 "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                 "-pix_fmt", "yuv420p", "-t", "%.6f" % (frame_count / src_fps),
                 "-movflags", "+faststart"]
            encode = runtime.subprocess.run(
                encode_base + ["-c:a", "copy", output_path],
                stdout=runtime.subprocess.DEVNULL, stderr=runtime.subprocess.PIPE,
                timeout=1200, check=False)
            if encode.returncode:
                encode = runtime.subprocess.run(
                    encode_base + ["-c:a", "aac", "-b:a", "192k", output_path],
                    stdout=runtime.subprocess.DEVNULL, stderr=runtime.subprocess.PIPE,
                    timeout=1200, check=False)
            if encode.returncode or not runtime.os.path.isfile(output_path):
                raise RuntimeError("Video ve ses birleştirilemedi: " +
                                   encode.stderr.decode("utf-8", "replace")[-400:])
            if runtime.os.path.getsize(output_path) > 1024 ** 3:
                raise RuntimeError("İyileştirilmiş video 1 GB sınırını aşıyor; daha düşük büyütme seç.")
            with open(output_path, "rb") as output_file:
                output_video = output_file.read()
            quota_error = runtime._kota_kontrol(owner, yeni_bayt=len(output_video), yeni_kayit=1)
            if quota_error:
                raise RuntimeError(quota_error)
            runtime._is_sonucu_baslat(job)
            poster = runtime._video_kapak_uret(output_video)
            video_meta = dict(selected["meta"])
            for key in ("dosyalar", "poster_file", "dosya_bayt", "tarih", "yildiz"):
                video_meta.pop(key, None)
            video_meta.update({"parent_kayit": selected["record"], "workflow": "enhancement",
                               "is_tipi": runtime.IS_TIPI_VIDEO_IYILESTIR,
                               "enhance_preset": preset, "enhance_scale": scale,
                               "fps_multiplier": fps_multiplier,
                               "source_fps": src_fps, "fps": output_fps,
                               "width": (video_output_size[0] if video_output_size else round(src_w * scale)),
                               "height": (video_output_size[1] if video_output_size else round(src_h * scale)),
                               "generation_ms": round((runtime.time.perf_counter() - started) * 1000)})
            history_id, files = runtime.gecmise_kaydet(owner, [output_video], video_meta,
                                               video=True, poster_blob=poster)
            job["sonuc"] = {"video_url": "/gecmis/%s" % runtime.urllib.parse.quote(files[0]),
                            "video_poster_url": "/gecmis/%s" % runtime.urllib.parse.quote(history_id + "_poster.jpg") if poster else None,
                            "prompt": video_meta.get("prompt", ""), "width": video_meta["width"],
                            "height": video_meta["height"], "fps": output_fps,
                            "suresn": video_meta.get("suresn"), "is_tipi": runtime.IS_TIPI_VIDEO_IYILESTIR,
                            "gecmis": history_id, "gecmis_dosyalar": files, "is_id": job["is_id"]}
        else:
            with open(selected["path"], "rb") as image_file:
                image_bytes = image_file.read()
            uploaded = runtime.upload_image_to_comfy(
                image_bytes, "webui_enhance_%s.png" % runtime.uuid.uuid4().hex[:10])
            uploaded_path = runtime.os.path.join(runtime.COMFY_KOK, "input", uploaded.get("name", ""))
            graph, _ = runtime.build_image_enhancement(uploaded.get("name"), preset, scale)
            job["ilerleme"] = {"asama": "goruntu", "calisan": "Görsel iyileştiriliyor", "oran": 0.1}
            entry = _iyilestirme_comfy_isini_yurut(runtime, job, graph)
            for output in (entry.get("outputs") or {}).values():
                for image in output.get("images", []) or []:
                    try:
                        blob = runtime.fetch_view_image(image)
                        if blob:
                            output_blobs.append(blob)
                    finally:
                        _iyilestirme_cikti_temizle(runtime, image)
            if not output_blobs:
                raise RuntimeError("İyileştirilmiş görsel bulunamadı.")
            quota_error = runtime._kota_kontrol(owner, yeni_bayt=sum(map(len, output_blobs)),
                                        yeni_kayit=len(output_blobs))
            if quota_error:
                raise RuntimeError(quota_error)
            runtime._is_sonucu_baslat(job)
            image_meta = dict(selected["meta"])
            for key in ("dosyalar", "poster_file", "dosya_bayt", "tarih", "yildiz"):
                image_meta.pop(key, None)
            image_meta.update({"parent_kayit": selected["record"], "workflow": "enhancement",
                               "is_tipi": runtime.IS_TIPI_GORUNTU_IYILESTIR,
                               "enhance_preset": preset, "enhance_scale": scale,
                               "generation_ms": round((runtime.time.perf_counter() - started) * 1000)})
            try:
                from io import BytesIO
                from PIL import Image
                with Image.open(BytesIO(output_blobs[0])) as image:
                    image_meta["width"], image_meta["height"] = image.size
            except Exception:
                image_meta["width"] = round(float(image_meta.get("width") or 0) * scale)
                image_meta["height"] = round(float(image_meta.get("height") or 0) * scale)
            history_id, files = runtime.gecmise_kaydet(owner, output_blobs, image_meta)
            job["sonuc"] = {"gorseller": ["data:image/png;base64," + base64.b64encode(blob).decode("ascii")
                                            for blob in output_blobs],
                            "prompt": image_meta.get("prompt", ""), "model": image_meta.get("model"),
                            "width": image_meta.get("width"), "height": image_meta.get("height"),
                            "is_tipi": runtime.IS_TIPI_GORUNTU_IYILESTIR,
                            "gecmis": history_id, "gecmis_dosyalar": files, "is_id": job["is_id"]}
        job["ilerleme"] = {"bitti": True, "oran": 1.0}
        job["durum"] = "bitti"
        job["bitis"] = runtime.time.time()
    finally:
        if uploaded_path:
            try:
                runtime.os.remove(uploaded_path)
            except OSError:
                pass
        if temp_root:
            try:
                runtime.shutil.rmtree(temp_root, ignore_errors=True)
            except OSError:
                pass


def _uretim_sonrasi_iyilestir(runtime, job, media_type):
    original_request = job.get("istek") or {}
    options = original_request.get("enhance_after") or {}
    if not options.get("enabled"):
        return
    original_result = job.get("sonuc") or {}
    record, files = original_result.get("gecmis"), original_result.get("gecmis_dosyalar") or []
    if not record or not files:
        original_result["iyilestirme_hata"] = "Asıl sonuç kaydedilemediği için yükseltme başlatılamadı."
        job["sonuc"] = original_result
        return
    enhancement_request = dict(original_request)
    enhancement_request.update({
        "postprocess": media_type, "kayit": record + ".json", "dosya": files[0],
        "enhance_preset": options["preset"], "scale": options["scale"],
        "fps_multiplier": options["fps_multiplier"],
    })
    job["istek"] = enhancement_request
    job["ilerleme"] = {"asama": "iyilestirme", "calisan": "İyileştirme modeli hazırlanıyor",
                        "oran": 0.05}
    try:
        _medyayi_iyilestir(runtime, job)
    except Exception as exc:
        original_result["iyilestirme_hata"] = str(exc)[:300]
        job["sonuc"] = original_result
        job["durum"] = "calisiyor"
    finally:
        job["istek"] = original_request
