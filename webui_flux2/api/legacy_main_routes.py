"""Main WebUI API handlers extracted from the legacy HTTP facade.

The legacy handler remains the wire-compatibility boundary. Runtime services
are passed explicitly so subsequent slices can replace this adapter cleanly.
"""

from __future__ import annotations

from . import legacy_auth_routes, legacy_admin_routes, legacy_media_routes, legacy_queue_routes, legacy_ai_routes

def handle_main_get(self, runtime):
    parsed = runtime.urllib.parse.urlparse(self.path)
    if runtime.RUNPOD_MODE and getattr(self.server, 'server_port', 8189) == runtime.SETUP_PORT:
        self._setup_get(parsed)
        return
    if parsed.path == '/api/sunucu-hazir':
        code, payload = runtime._main_health_state()
        self._send(code, runtime.json.dumps(payload))
        return
    if runtime.RUNPOD_MODE and (not runtime.pod_setup.is_ready()) and (parsed.path not in ('/', '/index.html', '/static/app.css', '/static/app.js')) and (not parsed.path.startswith('/assets/')):
        self._send(503, '{"hata":"Sistem hazırlanıyor."}')
        return
    if runtime._kapatma_istegi.is_set() and parsed.path not in ('/', '/index.html', '/static/app.css', '/static/app.js') and (not parsed.path.startswith('/assets/')):
        self._send(503, '{"hata":"WebUI güvenli biçimde kapanıyor"}')
        return
    if parsed.path in ('/', '/index.html'):
        if runtime.RUNPOD_MODE and parsed.query and (runtime.urllib.parse.parse_qs(parsed.query).get('ui') == ['next']):
            self._serve_react_preview(parsed)
        else:
            with open(runtime.os.path.join(runtime.BASE_DIR, 'index.html'), 'rb') as f:
                self._send(200, f.read(), 'text/html; charset=utf-8')
    elif runtime.RUNPOD_MODE and parsed.path.startswith('/assets/'):
        self._serve_react_preview(parsed)
    elif parsed.path.startswith('/static/'):
        ad = parsed.path[len('/static/'):]
        if ad not in ('app.css', 'app.js'):
            self._send(404, '{"hata":"bulunamadi"}')
        else:
            try:
                with open(runtime.os.path.join(runtime.BASE_DIR, 'static', ad), 'rb') as f:
                    veri = f.read()
            except OSError:
                self._send(404, '{"hata":"bulunamadi"}')
            else:
                ctype = 'text/css; charset=utf-8' if ad.endswith('.css') else 'application/javascript; charset=utf-8'
                self._send(200, veri, ctype)
    elif parsed.path == '/api/pod-kurulum' and runtime.RUNPOD_MODE:
        if self._admin_gerekli() is not None:
            self._send(200, runtime.json.dumps(runtime.pod_setup.status(), ensure_ascii=False))
    elif parsed.path == '/api/durum':
        info = {'comfy': False, 'modeller': {}, 'llm': {}, 'uretim_suruyor': False, 'kuyruk_bekleyen': 0}
        try:
            runtime.comfy_get_json('/system_stats')
            info['comfy'] = True
        except Exception as e:
            info['hata'] = str(e)[:200]
        models_dir = runtime.os.path.join(runtime.COMFY_KOK, 'models')
        for model_key, files in (('modeller', (('diffusion', 'diffusion_models', runtime.DIFFUSION_FILE), ('text_encoder', 'text_encoders', runtime.TEXT_ENCODER_FILE), ('vae', 'vae', runtime.VAE_FILE))), ('z_image_modeller', (('diffusion', 'diffusion_models', runtime.Z_IMAGE_DIFFUSION_FILE), ('text_encoder', 'text_encoders', runtime.Z_IMAGE_TEXT_ENCODER_FILE), ('vae', 'vae', runtime.Z_IMAGE_VAE_FILE))), ('anima_modeller', (('diffusion', 'diffusion_models', runtime.ANIMA_DIFFUSION_FILE), ('text_encoder', 'text_encoders', runtime.ANIMA_TEXT_ENCODER_FILE), ('vae', 'vae', runtime.ANIMA_VAE_FILE))), ('moody_anima_modeller', (('diffusion', 'diffusion_models', runtime.MOODY_ANIMA_DIFFUSION_FILE), ('text_encoder', 'text_encoders', runtime.ANIMA_TEXT_ENCODER_FILE), ('vae', 'vae', runtime.ANIMA_VAE_FILE)))):
            info[model_key] = {}
            for key, sub, fn in files:
                info[model_key][key] = {'dosya': fn, 'var': runtime.os.path.isfile(runtime.os.path.join(models_dir, sub, fn))}
        try:
            info['llm'] = runtime.llm.durum(detayli=True)
            info['llm_models'] = runtime.llm.model_profilleri()
        except Exception:
            info['llm'] = {'canli': False}
            info['llm_models'] = []
        if runtime.RUNPOD_MODE:
            try:
                info['model_instances'] = runtime.pod_setup.model_options()
            except Exception:
                info['model_instances'] = []
        try:
            info['uretim_suruyor'] = runtime._aktif.get('prompt_id') is not None
            info['kuyruk_bekleyen'] = len(runtime._kuyruk)
            info['vram_bos_mb'] = runtime._vram_bos_mb()
        except Exception:
            pass
        self._send(200, runtime.json.dumps(info))
    elif parsed.path == '/api/kurulum-durumu':
        if runtime.RUNPOD_MODE and (not runtime.pod_setup.is_ready()):
            self._send(503, runtime.json.dumps({'hata': 'Sistem hazırlanıyor.'}))
        else:
            storage = runtime.pod_setup.storage_status() if runtime.RUNPOD_MODE else {}
            self._send(200, runtime.json.dumps({'kurulu': runtime._auth_store.kurulu_mu(), 'runpod': runtime.RUNPOD_MODE, **storage}))
    elif parsed.path == '/api/ben':
        kullanici = self._oturum_kullanici()
        if kullanici is None:
            self._send(401, '{"hata":"giris gerekli"}')
        else:
            self._send(200, runtime.json.dumps({'kullanici': kullanici, 'rol': runtime._auth_store.rol(kullanici)}))
    elif parsed.path == '/api/loras' and runtime.RUNPOD_MODE:
        if self._auth_gerekli() is None:
            return
        query = runtime.urllib.parse.parse_qs(parsed.query or '')
        model = (query.get('model') or [None])[0]
        self._send(200, runtime.json.dumps({'loras': runtime.pod_setup.lora_status(model)}, ensure_ascii=False))
    elif parsed.path == '/api/kasa/durum':
        kullanici = self._auth_gerekli()
        if kullanici is None:
            return
        self._send(200, runtime.json.dumps(runtime._kasa_durum_bilgi(kullanici), ensure_ascii=False))
    elif parsed.path == '/api/backup-export':
        parola = self.headers.get('X-Backup-Password') or ''
        try:
            if runtime.RUNPOD_MODE:
                if self._admin_gerekli() is None:
                    return
                medya_haric = self.headers.get('X-Backup-Media') == 'exclude'
                with runtime._sistem_yedegi_hazirla(parola, medya_haric=medya_haric) as path:
                    self._send_file(path, 'webui-system.wuis.gpg')
            else:
                kullanici = self._auth_gerekli()
                if kullanici is None or self._kasa_gerekli(kullanici) is None:
                    return
                path = runtime.kasa.yedek_uret(kullanici)
                try:
                    self._send_file(path, 'webui-%s.wui.gpg' % kullanici)
                finally:
                    runtime.os.remove(path)
        except (runtime.kasa.KasaHatasi, OSError, RuntimeError) as exc:
            self._send(409, runtime.json.dumps({'hata': str(exc)[:300]}, ensure_ascii=False))
    elif parsed.path == '/api/kullanicilar':
        if self._admin_gerekli() is None:
            return
        liste = []
        for k in runtime._auth_store.liste():
            try:
                mb, kayit = runtime._kullanim_hesapla(k['kullanici'])
            except Exception:
                mb, kayit = (0, 0)
            k['kullanim_mb'] = mb
            k['kayit_sayisi'] = kayit
            liste.append(k)
        self._send(200, runtime.json.dumps({'kullanicilar': liste}, ensure_ascii=False))
    elif parsed.path == '/api/ilerleme':
        kullanici = self._auth_gerekli()
        if kullanici is None:
            return
        if self._kasa_gerekli(kullanici) is None:
            return
        pid = runtime._aktif.get('prompt_id')
        if pid is not None and runtime._aktif.get('sahip') != kullanici:
            self._send(200, runtime.json.dumps({'aktif': False, 'ilerleme': None, 'gecen': 0}))
            return
        ilerleme = runtime._aktif.get('ilerleme')
        if isinstance(ilerleme, dict) and 'oran' not in ilerleme:
            ilerleme = dict(ilerleme)
            ilerleme['oran'] = runtime._oran_hesapla(ilerleme)
        self._send(200, runtime.json.dumps({'aktif': pid is not None, 'ilerleme': ilerleme, 'gecen': round(runtime.time.time() - runtime._aktif['baslangic'], 1) if runtime._aktif.get('baslangic') else 0}))
    elif parsed.path == '/api/sistem':
        kullanici = self._auth_gerekli()
        if kullanici is None:
            return
        if self._kasa_gerekli(kullanici) is None:
            return
        try:
            bos = runtime._vram_bos_mb()
        except Exception:
            bos = None
        try:
            ram_bilgi = runtime._ram_bilgi()
        except Exception:
            ram_bilgi = {'toplam_mb': None, 'bos_mb': None, 'yuzde': None}
        try:
            comfy_bilgi = runtime.comfy_get_json('/system_stats')
            cihazlar = comfy_bilgi.get('devices') or []
            comfy_vram = [{'ad': c.get('name'), 'bos_mb': round((c.get('vram_free') or 0) / (1024 * 1024)), 'toplam_mb': round((c.get('vram_total') or 0) / (1024 * 1024))} for c in cihazlar]
            comfy_acik = True
        except Exception:
            comfy_vram = []
            comfy_acik = False
        try:
            llm_bilgi = runtime.llm.durum(detayli=True)
        except Exception:
            llm_bilgi = {'canli': False}
        aktif_llm = None
        if runtime._aktif_llm.get('sahip') == kullanici and runtime._aktif_llm.get('is_id'):
            aktif_llm = {'is_id': runtime._aktif_llm['is_id'], 'tur': runtime._aktif_llm.get('tur'), 'durum': runtime._aktif_llm.get('durum'), 'gecen_sn': round(runtime.time.time() - runtime._aktif_llm['baslangic'], 1)}
        self._send(200, runtime.json.dumps({'vram_bos_mb': bos, 'ram': ram_bilgi, 'comfy_acik': comfy_acik, 'comfy_vram': comfy_vram, 'llm': llm_bilgi, 'llm_is': aktif_llm, 'kuyruk_bekleyen': len(runtime._kuyruk), 'aktif_is_var': runtime._aktif.get('prompt_id') is not None}, ensure_ascii=False))
        return
    elif parsed.path == '/api/log':
        if self._admin_gerekli() is None:
            return
        qs = runtime.urllib.parse.parse_qs(parsed.query or '')
        hangi = (qs.get('kaynak') or ['llama'])[0]
        try:
            satir = max(10, min(500, int((qs.get('satir') or ['200'])[0])))
        except Exception:
            satir = 200
        try:
            satirlar = runtime._log_tail(hangi, satir)
        except ValueError as exc:
            self._send(400, runtime.json.dumps({'hata': str(exc)}))
            return
        self._send(200, runtime.json.dumps({'kaynak': hangi, 'satirlar': satirlar}, ensure_ascii=False))
        return
    elif parsed.path == '/api/prompt-gecmisi':
        kullanici = self._auth_gerekli()
        if kullanici is None:
            return
        if self._kasa_gerekli(kullanici) is None:
            return
        qs = runtime.urllib.parse.parse_qs(parsed.query or '')
        try:
            limit = max(1, min(200, int((qs.get('limit') or ['50'])[0])))
        except Exception:
            limit = 50
        arama = (qs.get('q') or [''])[0]
        mod = (qs.get('mod') or [''])[0]
        self._send(200, runtime.json.dumps({'kayitlar': runtime.prompt_gecmisi_oku(kullanici, limit, arama, mod)}, ensure_ascii=False))
        return
    elif parsed.path == '/api/kutuphane':
        kullanici = self._auth_gerekli()
        if kullanici is None:
            return
        if self._kasa_gerekli(kullanici) is None:
            return
        self._send(200, runtime.json.dumps({'fotograflar': runtime.kutuphane_listesi(kullanici)}, ensure_ascii=False))
    elif parsed.path.startswith('/kutuphane/'):
        kullanici = self._auth_gerekli()
        if kullanici is None:
            return
        if self._kasa_gerekli(kullanici) is None:
            return
        kutuphane_dir = runtime._kullanici_kutuphane(kullanici)
        ad = runtime.urllib.parse.unquote(parsed.path[len('/kutuphane/'):])
        qs = runtime.urllib.parse.parse_qs(parsed.query or '')
        thumb = (qs.get('thumb') or [''])[0] in ('1', 'evet', 'true')
        ad = runtime._guvenli_dosya_adi(ad)
        if not ad:
            self._send(400, '{"hata":"gecersiz ad"}')
            return
        yol = runtime.os.path.abspath(runtime.os.path.join(kutuphane_dir, ad))
        if thumb:
            taban = ad[:-4] if ad.lower().endswith('.png') else ad
            t_yol = runtime.os.path.abspath(runtime.os.path.join(kutuphane_dir, taban + '_thumb.jpg'))
            if runtime.os.path.isfile(t_yol):
                yol = t_yol
        if not yol.startswith(runtime.os.path.abspath(kutuphane_dir) + runtime.os.sep) or not runtime.os.path.isfile(yol):
            self._send(404, '{"hata":"bulunamadi"}')
            return
        ctype = runtime.mimetypes.guess_type(yol)[0] or 'application/octet-stream'
        with open(yol, 'rb') as f:
            self._send(200, f.read(), ctype)
    elif parsed.path == '/api/arsiv':
        kullanici = self._auth_gerekli()
        if kullanici is None:
            return
        if self._kasa_gerekli(kullanici) is None:
            return
        qs = runtime.urllib.parse.parse_qs(parsed.query or '')
        try:
            sayfa = int((qs.get('sayfa') or ['1'])[0])
        except Exception:
            sayfa = 1
        try:
            adet = int((qs.get('adet') or ['24'])[0])
        except Exception:
            adet = 24
        arama = (qs.get('q') or [''])[0]
        try:
            kok = runtime._kullanici_koku(kullanici)
            self._send(200, runtime.json.dumps(runtime.arsiv.arsiv_listesi(kok, sayfa, adet, arama), ensure_ascii=False))
        except ValueError:
            self._send(409, '{"hata":"kasa kilitli"}')
    elif parsed.path.startswith('/api/arsiv/gorsel/') or parsed.path.startswith('/api/arsiv/thumb/'):
        kullanici = self._auth_gerekli()
        if kullanici is None:
            return
        if self._kasa_gerekli(kullanici) is None:
            return
        thumb = parsed.path.startswith('/api/arsiv/thumb/')
        on = len('/api/arsiv/thumb/') if thumb else len('/api/arsiv/gorsel/')
        ozet = runtime.urllib.parse.unquote(parsed.path[on:])
        if not runtime.re.fullmatch('[a-f0-9]{64}', ozet):
            self._send(400, '{"hata":"gecersiz hash"}')
            return
        try:
            kok = runtime._kullanici_koku(kullanici)
        except ValueError:
            self._send(409, '{"hata":"kasa kilitli"}')
            return
        if thumb:
            yol = runtime.arsiv.arsiv_thumb(kok, ozet)
            if not yol or not runtime.os.path.isfile(yol):
                self._send(404, '{"hata":"bulunamadi"}')
                return
            with open(yol, 'rb') as f:
                self._send(200, f.read(), 'image/jpeg')
            return
        veri = runtime.arsiv.arsiv_verisi(kok, ozet)
        if not veri:
            self._send(404, '{"hata":"bulunamadi"}')
            return
        self._send(200, veri[1], veri[0])
    elif parsed.path == '/api/promptlar':
        kullanici = self._auth_gerekli()
        if kullanici is None:
            return
        if self._kasa_gerekli(kullanici) is None:
            return
        self._send(200, runtime.json.dumps({'promptlar': runtime.promptlari_oku(kullanici)}, ensure_ascii=False))
    elif parsed.path == '/api/gecmis':
        kullanici = self._auth_gerekli()
        if kullanici is None:
            return
        if self._kasa_gerekli(kullanici) is None:
            return
        qs = runtime.urllib.parse.parse_qs(parsed.query or '')
        try:
            sayfa = int((qs.get('sayfa') or ['1'])[0])
        except Exception:
            sayfa = 1
        try:
            adet = int((qs.get('adet') or ['20'])[0])
        except Exception:
            adet = 20
        arama = (qs.get('q') or [''])[0]
        favoriler = (qs.get('favoriler') or [''])[0] in ('1', 'true', 'evet')
        albume_gore = (qs.get('albume_gore') or [''])[0] in ('1', 'true', 'evet')
        album_id = (qs.get('album_id') or [None])[0]
        self._send(200, runtime.json.dumps(runtime.gecmis_listesi(kullanici, sayfa, adet, arama, favoriler, albume_gore, album_id), ensure_ascii=False))
    elif parsed.path.startswith('/gecmis/'):
        kullanici = self._auth_gerekli()
        if kullanici is None:
            return
        if self._kasa_gerekli(kullanici) is None:
            return
        gecmis_dir = runtime._kullanici_gecmis(kullanici)
        ad = runtime._guvenli_dosya_adi(runtime.urllib.parse.unquote(parsed.path[len('/gecmis/'):]))
        if not ad:
            self._send(400, '{"hata":"gecersiz ad"}')
            return
        yol = runtime.os.path.abspath(runtime.os.path.join(gecmis_dir, ad))
        if not yol.startswith(runtime.os.path.abspath(gecmis_dir) + runtime.os.sep) or not runtime.os.path.isfile(yol):
            self._send(404, '{"hata":"bulunamadi"}')
            return
        ctype = runtime.mimetypes.guess_type(yol)[0] or 'application/octet-stream'
        with open(yol, 'rb') as f:
            self._send(200, f.read(), ctype)
    elif parsed.path == '/api/ayarlar':
        kullanici = self._auth_gerekli()
        if kullanici is None:
            return
        if self._kasa_gerekli(kullanici) is None:
            return
        ayar = runtime._ayar_oku(kullanici)
        ayar.setdefault('gecmis_limiti', 0)
        ayar.setdefault('llm_ctx', 8000)
        ayar.setdefault('llm_prompt_destek_yaraticilik', 0.7)
        ayar.setdefault('llm_prompt_destek_uzunluk', 'orta')
        ayar.setdefault('uretim_varsayilan_steps', 9)
        ayar.setdefault('uretim_varsayilan_seed', -1)
        ayar.setdefault('uretim_varsayilan_max_side', 1536)
        ayar.setdefault('uretim_prompt_destek', False)
        ayar.setdefault('uretim_prompt_ceviri', False)
        ayar.setdefault('uretim_prompt_duzenle', False)
        llm_varsayilanlar = {'sistem': runtime.llm.VARSAYILAN_SISTEM_PROMPTU, 'sohbet': runtime.llm.VARSAYILAN_SOHBET_SISTEM_PROMPTU, 'mod': dict(runtime.llm.VARSAYILAN_MOD_TALIMATI)}
        self._send(200, runtime.json.dumps({'ayarlar': ayar, 'llm_varsayilanlar': llm_varsayilanlar}, ensure_ascii=False))
    elif parsed.path == '/api/isler':
        kullanici = self._auth_gerekli()
        if kullanici is None:
            return
        if self._kasa_gerekli(kullanici) is None:
            return
        with runtime._isler_kilit:
            isler = sorted((j for j in runtime._isler.values() if j.get('sahip') == kullanici), key=lambda j: j.get('olustu', 0))
        self._send(200, runtime.json.dumps({'isler': [runtime._is_ozet(j) for j in isler]}, ensure_ascii=False))
    elif parsed.path.startswith('/api/is/'):
        kullanici = self._auth_gerekli()
        if kullanici is None:
            return
        if self._kasa_gerekli(kullanici) is None:
            return
        is_id = runtime.urllib.parse.unquote(parsed.path[len('/api/is/'):])
        job = runtime._isler.get(is_id)
        if job is None or job.get('sahip') != kullanici:
            self._send(404, '{"hata":"bulunamadi"}')
            return
        self._send(200, runtime.json.dumps(runtime._is_detay(job), ensure_ascii=False))
    else:
        self._send(404, '{"hata":"bulunamadi"}')


def handle_main_post(self, parsed, runtime):
    if not self._origin_kontrol():
        return
    for route_module in (
        legacy_auth_routes, legacy_admin_routes, legacy_media_routes,
        legacy_queue_routes, legacy_ai_routes,
    ):
        if route_module.supports(parsed.path, runtime):
            route_module.handle_post(self, parsed, runtime)
            return
    if parsed.path == '/api/uret':
        self._send(410, '{"hata":"/api/uret kaldirildi; /api/kuyruk kullan"}')
        return
    self._send(404, '{"hata":"bulunamadi"}')
