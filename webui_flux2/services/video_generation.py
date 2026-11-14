"""Video workflow construction and execution service."""

from __future__ import annotations


_VIDEO_ASAMA = {'LoadImage': 'Referanslar okunuyor', 'UNETLoader': 'Video modeli yukleniyor', 'CLIPLoader': 'Metin kodlayici yukleniyor', 'VAELoader': 'VAE yukleniyor', 'LoraLoaderModelOnly': 'LoRA yukleniyor', 'MiniMaxH3ReferenceToVideo': 'Referanslar isleniyor', 'WanVaceToVideo': 'Wan video alanı hazırlanıyor', 'ModelSamplingSD3': 'Wan zamanlama ayarı yükleniyor', 'KSampler': 'Video örnekleniyor', 'KSamplerSelect': 'Ornekleyici hazirlaniyor', 'BasicScheduler': 'Zamanlama kuruluyor', 'RandomNoise': 'Gurultu hazirlaniyor', 'BasicGuider': 'Yonlendirici hazirlaniyor', 'SamplerCustomAdvanced': 'Video ornekleniyor', 'VAEDecode': 'Video cozuluyor', 'VAEDecodeAudio': 'Ses cozuluyor', 'CreateVideo': 'Video birlestiriliyor', 'SaveVideo': 'Video kaydediliyor'}


def is_tipini_belirle(req, n_ref, *, runtime):
    """Istek + referans sayisindan is tipini sec. Moduler nokta: yeni tip buraya eklenir."""
    ham = (req.get('is_tipi') or '').strip() if isinstance(req.get('is_tipi'), str) else ''
    if ham in runtime.IS_TIPLERI:
        return ham
    if ham in ('video', 'video-uret', 'video-uretim-hazirlik'):
        return runtime.IS_TIPI_VIDEO
    return runtime.IS_TIPI_GORUNTU if n_ref == 0 else runtime.IS_TIPI_DUZENLEME


def video_parametreleri(req, *, runtime):
    """Video alanlari: simdilik goruntu islerinde yoksayilir, semada tasinir."""
    try:
        suresn = float(req.get('suresn', 0) or 0)
    except Exception:
        suresn = 0
    try:
        fps = float(req.get('fps', 0) or 0)
    except Exception:
        fps = 0
    return {'suresn': suresn if suresn > 0 else 0, 'fps': fps if fps > 0 else 0}


def _video_fps_yuvarla(fps, *, runtime):
    """Sablon disi fps'i en yakin secenege yuvarlar (8/12/24)."""
    try:
        fps = float(fps)
    except Exception:
        return 24
    if fps <= 0:
        return 24
    return min(runtime.VIDEO_FPSLER, key=lambda s: (abs(s - fps), s))


def _video_kare_sayisi(suresn, fps, *, runtime):
    """Kare = sure*fps; modelin 17k+5 izgara kosuluna yukari yuvarlanir."""
    try:
        n = max(5, int(round(float(suresn) * float(fps))))
    except Exception:
        n = 124
    n = max(5, min(3600, n))
    while n % 17 != 5:
        n += 1
    return n


def _wan22_kare_sayisi(suresn, fps, *, runtime):
    """Wan video latent length follows 4n+1 frames (ComfyUI Wan workflow)."""
    try:
        n = max(5, int(round(float(suresn) * float(fps))))
    except Exception:
        n = 5
    n = max(5, min(3600, n))
    return n + (1 - n) % 4


def _video_boyut(w, h, max_kenar=None, *, runtime):
    if max_kenar is None:
        max_kenar = runtime.VIDEO_MAX_KENAR
    "En uzun kenari max_kenar'a esitle (oran korunur, 32'nin kati)."
    try:
        w, h = (int(w), int(h))
    except Exception:
        w, h = (max_kenar, 768)
    if w < 32 or h < 32:
        w, h = (max_kenar, 768)
    f = max_kenar / float(max(w, h))
    w2 = max(32, int(round(w * f / 32.0)) * 32)
    h2 = max(32, int(round(h * f / 32.0)) * 32)
    return (w2, h2)


def _video_sablon_yukle(profile_name=None, *, runtime):
    """video_r2v_api.json'u okur; '_' ile baslayan aciklama anahtarlarini atar."""
    with open(runtime.VIDEO_SABLON_DOSYA, 'r', encoding='utf-8') as f:
        ham = runtime.json.load(f)
    if runtime.RUNPOD_MODE:
        workflow = runtime.pod_setup.workflow('video')
        profiles = workflow.get('profiles') or {}
        requested = 'quality' if profile_name == 'fast' else profile_name or workflow.get('default_profile', 'quality')
        profile = profiles.get(requested) or profiles.get(workflow.get('default_profile', 'quality'))
        if not profile:
            raise RuntimeError('Video modeli models.json içinde tanımlı değil.')
        group_state = {item['id']: item['durum'] for item in runtime.pod_setup.status()['gruplar']}
        if group_state.get(profile.get('group')) not in ('ready', 'installed'):
            raise RuntimeError('Seçilen video profili kurulu değil. Önce ayrı kurulum ekranından model grubunu hazırlayın.')
        for node, field, key in (('127', 'unet_name', 'unet_file'), ('128', 'clip_name', 'text_encoder_file'), ('119', 'vae_name', 'video_vae_file'), ('120', 'vae_name', 'audio_vae_file')):
            if workflow.get(key):
                ham[node]['inputs'][field] = workflow[key]
        if profile.get('lora_file'):
            ham['145']['inputs']['lora_name'] = profile['lora_file']
        else:
            ham['126']['inputs']['model'] = ['127', 0]
            ham.pop('145', None)
        ham['124']['inputs']['steps'] = int(profile.get('steps', 20))
    elif profile_name == 'normal':
        ham['126']['inputs']['model'] = ['127', 0]
        ham.pop('145', None)
        ham['124']['inputs']['steps'] = 20
    return {k: v for k, v in ham.items() if not k.startswith('_')}


def _wan22_video_sablon_yukle(*, runtime):
    if not runtime.RUNPOD_MODE or not runtime.pod_setup.workflow_ready('wan22'):
        raise RuntimeError('Wan 2.2 modeli hazır değil. Önce Kurulum ekranından Wan 2.2 grubunu indirin.')
    workflow = runtime.pod_setup.workflow('wan22')
    with open(runtime.WAN22_VIDEO_SABLON_DOSYA, 'r', encoding='utf-8') as stream:
        ham = runtime.json.load(stream)
    ham['1']['inputs']['ckpt_name'] = workflow.get('checkpoint_file')
    ham['2']['inputs']['clip_name'] = workflow.get('text_encoder_file')
    ham['6']['inputs']['shift'] = float(workflow.get('shift', 8))
    ham['7']['inputs']['steps'] = int(workflow.get('steps', 4))
    ham['7']['inputs']['cfg'] = float(workflow.get('cfg', 1))
    ham['7']['inputs']['sampler_name'] = workflow.get('sampler', 'sa_solver')
    ham['7']['inputs']['scheduler'] = workflow.get('scheduler', 'beta')
    return {key: value for key, value in ham.items() if not key.startswith('_')}


def _video_diskten_bul(sonrasi_sn, *, runtime):
    """SaveVideo history'ye dosya adi koymazsa diske bak: bu isten sonra
    yazilmis en yeni MiniMax_H3 mp4'un baytlarini doner (yoksa None).
    Tek worker oldugundan taze dosya bu ise aittir."""
    klasor = runtime.os.path.join(runtime.COMFY_KOK, 'output', 'video')
    try:
        adaylar = []
        prefixes = ('MiniMax_H3', 'Wan22')
        for ad in runtime.os.listdir(klasor):
            if not ad.startswith(prefixes) or not ad.lower().endswith('.mp4'):
                continue
            yol = runtime.os.path.join(klasor, ad)
            try:
                mt = runtime.os.path.getmtime(yol)
            except Exception:
                continue
            if runtime.os.path.isfile(yol) and mt >= sonrasi_sn - 5:
                adaylar.append((mt, yol))
        if not adaylar:
            return None
        adaylar.sort()
        with open(adaylar[-1][1], 'rb') as f:
            return f.read()
    except Exception:
        return None


def _video_uret(job, req, *, runtime):
    """Video uretim govdesi: sablon doldur, /prompt gonder, mp4 cek, kaydet.
    Goruntu govdesine dokunmaz; hata/kota durumunda yari dosya birakmaz."""
    sahip = job.get('sahip')
    try:
        video_model = req.get('video_model_instance') or ('wan22' if runtime.RUNPOD_MODE else 'video')
        is_wan22 = runtime.RUNPOD_MODE and video_model == 'wan22'
        vid = runtime.video_parametreleri(req)
        try:
            default_duration = 0.5 if runtime.RUNPOD_MODE else 5
            suresn = float(vid.get('suresn') or default_duration)
        except Exception:
            suresn = 0.5 if runtime.RUNPOD_MODE else 5
        suresn = max(runtime.VIDEO_SURESN_MIN, min(runtime.VIDEO_SURESN_MAX, suresn))
        fps = runtime._video_fps_yuvarla(vid.get('fps') or (8 if runtime.RUNPOD_MODE else 24))
        try:
            w0 = int(req.get('width', runtime.VIDEO_MAX_KENAR))
        except Exception:
            w0 = runtime.VIDEO_MAX_KENAR
        try:
            h0 = int(req.get('height', 768))
        except Exception:
            h0 = 768
        try:
            vmax = int(req.get('video_max_side') or (320 if runtime.RUNPOD_MODE else runtime.VIDEO_MAX_KENAR))
        except Exception:
            vmax = runtime.VIDEO_MAX_KENAR
        vmax = max(320, min(runtime.VIDEO_MAX_KENAR, vmax))
        w, h = runtime._video_boyut(w0, h0, vmax)
        frames = runtime._wan22_kare_sayisi(suresn, fps) if is_wan22 else runtime._video_kare_sayisi(suresn, fps)
        gercek_suresn = round(frames / fps, 3)
        try:
            seed = int(req.get('seed', -1))
        except Exception:
            seed = -1
        if seed < 0:
            seed = runtime.random.randint(0, 2 ** 31 - 1)
        prompt = req.get('prompt', '') or ''
        yuklenen = []
        kutular = []
        hamlar = []
        kaynak_boyutu = None
        for anahtar in ('image_base64', 'image2_base64'):
            if not req.get(anahtar):
                continue
            ham = runtime.base64.b64decode(req[anahtar])
            hamlar.append(ham)
            hazir, boyut, _, _ = runtime._fotograf_hazirla(ham, runtime.VIDEO_MAX_KENAR, False)
            if kaynak_boyutu is None and boyut:
                kaynak_boyutu = boyut
            fn = 'webui_video_%s.png' % runtime.uuid.uuid4().hex[:8]
            up = runtime.upload_image_to_comfy(hazir, fn)
            yuklenen.append(up.get('name', fn))
            kutular.append((ham, hazir))
        if not yuklenen and (not is_wan22):
            job['durum'] = 'hata'
            job['hata'] = 'video-uretim icin en az 1 referans fotograf gerekli'
            job['bitis'] = runtime.time.time()
            return
        if runtime.RUNPOD_MODE and kaynak_boyutu and (not is_wan22):
            w0, h0 = kaynak_boyutu
            w, h = runtime._video_boyut(w0, h0, vmax)
        if is_wan22:
            if yuklenen:
                raise RuntimeError('Wan 2.2 şu an metinden video üretir; referans fotoğrafını kaldırın veya MiniMax H3 seçin.')
            sablon = runtime._wan22_video_sablon_yukle()
            sablon['3']['inputs']['text'] = prompt
            sablon['5']['inputs'].update({'width': w, 'height': h, 'length': frames})
            sablon['7']['inputs']['seed'] = seed
            sablon['9']['inputs']['fps'] = fps
        else:
            profile_name = req.get('video_profile') or 'quality'
            sablon = runtime._video_sablon_yukle(profile_name)
            g = sablon['136']['inputs']
            g['prompt'] = prompt
            g['width'] = w
            g['height'] = h
            g['length'] = frames
            sablon['129']['inputs']['noise_seed'] = seed
            sablon['130']['inputs']['fps'] = fps
            sablon['137']['inputs']['image'] = yuklenen[0]
            if len(yuklenen) > 1:
                sablon['139']['inputs']['image'] = yuklenen[1]
            else:
                sablon.pop('139', None)
                g.pop('ref_images.ref_image_1', None)
        cid = runtime.uuid.uuid4().hex
        if job['iptal']:
            raise RuntimeError('İş durduruldu')
        res = runtime.comfy_post_json('/prompt', {'prompt': sablon, 'client_id': cid})
        if 'node_errors' in res and res['node_errors']:
            job['durum'] = 'hata'
            job['hata'] = 'node hatasi: %s' % runtime.json.dumps(res['node_errors'])[:500]
            job['bitis'] = runtime.time.time()
            return
        job['prompt_id'] = res['prompt_id']
        runtime._aktif['prompt_id'] = res['prompt_id']
        runtime._aktif['sahip'] = sahip
        runtime._aktif['baslangic'] = runtime.time.time()
        runtime._aktif['ilerleme'] = {'kuyrukta': True, 'oran': 0.0}
        runtime._aktif['nodlar'] = {nid: _VIDEO_ASAMA.get(v.get('class_type', nid), v.get('class_type', nid)) for nid, v in sablon.items()}
        runtime.threading.Thread(target=runtime._ws_ilerleme_dinle, args=(res['prompt_id'], cid, runtime._aktif['nodlar']), daemon=True).start()
        try:
            if job['iptal']:
                runtime._comfy_isini_kes(res['prompt_id'])
            entry = runtime.wait_result(res['prompt_id'], job=job)
        finally:
            if runtime._aktif.get('prompt_id') == res['prompt_id']:
                runtime._aktif['prompt_id'] = None
                runtime._aktif['sahip'] = None
        mp4 = None
        for node_id, out in (entry.get('outputs') or {}).items():
            adaylar = list(out.get('gifs', []) or [])
            for im in out.get('images', []) or []:
                try:
                    if str(im.get('filename', '')).lower().endswith('.mp4'):
                        adaylar.append(im)
                except Exception:
                    pass
            for im in adaylar:
                mp4 = runtime.fetch_view_image(im)
                break
            if mp4:
                break
        if not mp4:
            mp4 = runtime._video_diskten_bul(job.get('baslangic') or runtime.time.time())
        if not mp4:
            job['durum'] = 'hata'
            job['hata'] = 'video ciktisi bulunamadi'
            job['bitis'] = runtime.time.time()
            return
        kota_hata = runtime._kota_kontrol(sahip, yeni_bayt=len(mp4) + sum((len(h) for h in hamlar)), yeni_kayit=1 + len(hamlar))
        if kota_hata:
            job['durum'] = 'hata'
            job['hata'] = kota_hata
            job['bitis'] = runtime.time.time()
            return
        runtime._is_sonucu_baslat(job)
        kut1 = kut2 = None
        try:
            if len(kutular) > 0:
                kut1 = runtime.kutuphaneye_kaydet(sahip, kutular[0][0], kutular[0][1], 0, 0)
        except Exception:
            kut1 = None
        try:
            if len(kutular) > 1:
                kut2 = runtime.kutuphaneye_kaydet(sahip, kutular[1][0], kutular[1][1], 0, 0)
        except Exception:
            kut2 = None
        gecmis_kayit, gecmis_dosyalar = (None, [])
        video_poster = None
        try:
            meta = {'prompt': prompt, 'width': w, 'height': h, 'seed': seed, 'frames': frames, 'suresn': gercek_suresn, 'istenen_suresn': suresn, 'fps': fps, 'mod': 'metinden-video' if is_wan22 else 'referans-video', 'is_tipi': runtime.IS_TIPI_VIDEO, 'workflow': 'video', 'video_model': video_model, 'foto_kutuphane': kut1, 'foto_kutuphane2': kut2}
            video_poster = runtime._video_kapak_uret(mp4)
            gecmis_kayit, gecmis_dosyalar = runtime.gecmise_kaydet(sahip, [mp4], meta, video=True, poster_blob=video_poster)
        except Exception:
            gecmis_kayit, gecmis_dosyalar = (None, [])
        job['sonuc'] = {'video_url': '/gecmis/%s' % runtime.urllib.parse.quote(gecmis_dosyalar[0]) if gecmis_dosyalar else None, 'video_poster_url': '/gecmis/%s' % runtime.urllib.parse.quote(gecmis_kayit + '_poster.jpg') if gecmis_kayit and video_poster else None, 'seed': seed, 'width': w, 'height': h, 'frames': frames, 'suresn': gercek_suresn, 'istenen_suresn': suresn, 'fps': fps, 'mod': 'metinden-video' if is_wan22 else 'referans-video', 'is_tipi': runtime.IS_TIPI_VIDEO, 'video_model': video_model, 'kutuphane': kut1, 'kutuphane2': kut2, 'gecmis': gecmis_kayit, 'gecmis_dosyalar': gecmis_dosyalar, 'is_id': job['is_id']}
        runtime._uretim_sonrasi_iyilestir(job, 'video')
        job['ilerleme'] = {'bitti': True, 'oran': 1.0}
        job['durum'] = 'bitti'
        job['bitis'] = runtime.time.time()
    except Exception as e:
        if 'durduruldu' in str(e):
            job['durum'] = 'durduruldu'
        else:
            job['durum'] = 'hata'
            job['hata'] = str(e)[:1000]
        job['bitis'] = runtime.time.time()
