"""Legacy HTTP route family kept for API contract compatibility."""

from __future__ import annotations

ROUTE_PATHS = frozenset(('/api/ayarlar', '/api/prompt-gecmisi/sil', '/api/prompt-gecmisi/temizle', '/api/prompt-gecmisi/yildiz', '/api/prompt-uret', '/api/sohbet', '/api/sohbet/kaydet', '/api/sohbet/sil', '/api/sohbetler', '/api/sohbetler/temizle'))

def supports(path, runtime):
    return path in ROUTE_PATHS

def handle_post(self, parsed, runtime):
    if not supports(parsed.path, runtime):
        return
    if parsed.path == '/api/ayarlar':
        kullanici = self._auth_gerekli()
        if kullanici is None:
            return
        if self._kasa_gerekli(kullanici) is None:
            return
        try:
            length = int(self.headers.get('Content-Length', 0))
            req = runtime.json.loads(self.rfile.read(length).decode('utf-8') or '{}')
        except Exception as e:
            self._send(400, runtime.json.dumps({'hata': 'istek okunamadi: %s' % e}))
            return
        if not isinstance(req, dict):
            self._send(400, '{"hata":"ayarlar sözlük olmalı"}')
            return
        try:
            with runtime._AYARLAR_KILIT:
                ayar = runtime._ayar_oku(kullanici)
                runtime._ayar_degisikliklerini_uygula(ayar, req)
                runtime._ayar_yaz(kullanici, ayar)
        except ValueError as e:
            self._send(400, runtime.json.dumps({'hata': str(e)}, ensure_ascii=False))
            return
        except OSError:
            self._send(500, '{"hata":"ayarlar kaydedilemedi"}')
            return
        if 'gecmis_limiti' in req:
            runtime._gecmis_buda(kullanici)
        llm_ctx_uygulama = None
        if 'llm_ctx' in req:
            llm_ctx_uygulama = {'istenen': ayar['llm_ctx'], 'uygulandi': False, 'model_yeniden_baslatilacak': False, 'calisan_ctx': None, 'not': 'Bir sonraki LLM işinde uygulanır'}
        self._send(200, runtime.json.dumps({'ayarlar': ayar, 'llm_ctx_uygulama': llm_ctx_uygulama}, ensure_ascii=False))
        return

    if parsed.path == '/api/prompt-gecmisi/sil':
        kullanici = self._auth_gerekli()
        if kullanici is None:
            return
        if self._kasa_gerekli(kullanici) is None:
            return
        req = self._json_oku()
        if '__hata__' in req:
            self._send(400, runtime.json.dumps({'hata': req['__hata__']}))
            return
        n = runtime.prompt_gecmisi_sil(kullanici, str(req.get('id') or ''))
        self._send(200, runtime.json.dumps({'silinen': n}))
        return

    if parsed.path == '/api/prompt-gecmisi/temizle':
        kullanici = self._auth_gerekli()
        if kullanici is None:
            return
        if self._kasa_gerekli(kullanici) is None:
            return
        self._send(200, runtime.json.dumps({'temizlendi': runtime.prompt_gecmisi_temizle(kullanici)}))
        return

    if parsed.path == '/api/prompt-gecmisi/yildiz':
        kullanici = self._auth_gerekli()
        if kullanici is None:
            return
        if self._kasa_gerekli(kullanici) is None:
            return
        req = self._json_oku()
        if '__hata__' in req:
            self._send(400, runtime.json.dumps({'hata': req['__hata__']}))
            return
        ok = runtime.prompt_gecmisi_yildiz(kullanici, str(req.get('id') or ''), bool(req.get('yildiz')))
        self._send(200 if ok else 404, runtime.json.dumps({'yildiz': ok}))
        return

    if parsed.path == '/api/prompt-uret':
        kullanici = self._auth_gerekli()
        if kullanici is None:
            return
        if self._kasa_gerekli(kullanici) is None:
            return
        if self._kasa_kapaniyor_mu(kullanici):
            return
        try:
            length = int(self.headers.get('Content-Length', 0))
        except Exception:
            length = 0
        if length > 20 * 1024 * 1024:
            self._send(413, '{"hata":"istek gövdesi çok büyük (20 MB üstü)"}')
            return
        req = self._json_oku()
        if '__hata__' in req:
            self._send(400, runtime.json.dumps({'hata': req['__hata__']}))
            return
        if req.get('llm_model') is not None and req.get('llm_model') not in {item['id'] for item in runtime.llm.model_profilleri()}:
            self._send(400, '{"hata":"Seçilen LLM modeli bulunamadı."}')
            return
        profile = req.get('model_prompt_profile', 'auto')
        if profile not in ('auto', 'general', 'z_image', 'flux3', 'krea2', 'illustrious', 'minimaxh3', 'wan22'):
            self._send(400, '{"hata":"gecersiz prompt profili"}')
            return
        if 'bbox_mode' in req and (not isinstance(req.get('bbox_mode'), bool)):
            self._send(400, '{"hata":"bbox_mode boolean olmali"}')
            return
        if req.get('bbox_mode') and profile != 'flux3':
            self._send(400, '{"hata":"bbox modu Flux 3 prompt profili gerektirir"}')
            return
        if profile in ('minimaxh3', 'wan22') and req.get('mod') != 'video' or (req.get('mod') == 'video' and profile not in ('auto', 'general', 'minimaxh3', 'wan22')):
            self._send(400, '{"hata":"video promptu için seçili video modeli veya Genel profilini seç"}')
            return
        job, sira = runtime._kuyruga_ekle(req, kullanici, tur='llm_prompt')
        if job is None:
            self._send(409, '{"hata":"bellek bosaltilirken yeni is kuyruga eklenemez"}')
            return
        self._send(200, runtime.json.dumps({'queued': True, 'is_id': job['is_id'], 'sira': sira}))
        return

    if parsed.path == '/api/sohbet':
        kullanici = self._auth_gerekli()
        if kullanici is None:
            return
        if self._kasa_gerekli(kullanici) is None:
            return
        if self._kasa_kapaniyor_mu(kullanici):
            return
        req = self._json_oku()
        if '__hata__' in req:
            self._send(400, runtime.json.dumps({'hata': req['__hata__']}))
            return
        if req.get('llm_model') is not None and req.get('llm_model') not in {item['id'] for item in runtime.llm.model_profilleri()}:
            self._send(400, '{"hata":"Seçilen LLM modeli bulunamadı."}')
            return
        ham = req.get('mesajlar') or []
        if not isinstance(ham, list) or not ham:
            self._send(400, runtime.json.dumps({'hata': 'mesajlar listesi gerekli'}))
            return
        if 'ek_talimat' in req and (not isinstance(req.get('ek_talimat'), str)):
            self._send(400, '{"hata":"ek_talimat metin olmali"}')
            return
        job, sira = runtime._kuyruga_ekle(req, kullanici, tur='llm_chat')
        if job is None:
            self._send(409, '{"hata":"bellek bosaltilirken yeni is kuyruga eklenemez"}')
            return
        self._send(200, runtime.json.dumps({'queued': True, 'is_id': job['is_id'], 'sira': sira}))
        return

    if parsed.path == '/api/sohbet/kaydet':
        kullanici = self._auth_gerekli()
        if kullanici is None:
            return
        if self._kasa_gerekli(kullanici) is None:
            return
        try:
            length = int(self.headers.get('Content-Length', 0))
            req = runtime.json.loads(self.rfile.read(length).decode('utf-8') or '{}')
        except Exception as e:
            self._send(400, runtime.json.dumps({'hata': 'istek okunamadi: %s' % e}))
            return
        mesajlar = req.get('mesajlar')
        if not isinstance(mesajlar, list) or not mesajlar:
            self._send(400, runtime.json.dumps({'hata': 'mesajlar listesi gerekli'}))
            return
        try:
            yanit = str(req.get('yanit') or '')
            olcum = req.get('sonuc') or {}
            if not isinstance(olcum, dict):
                olcum = {}
            save_req = {'mesajlar': mesajlar, 'sohbet_id': req.get('id'), 'baslik': req.get('baslik')}
            runtime._sohbet_kaydet(kullanici, mesajlar, yanit, olcum, req.get('baslik'), req.get('id'))
            self._send(200, runtime.json.dumps({'kaydedildi': True}))
        except Exception as e:
            self._send(500, runtime.json.dumps({'hata': str(e)[:1000]}))
        return

    if parsed.path == '/api/sohbet/sil':
        kullanici = self._auth_gerekli()
        if kullanici is None:
            return
        if self._kasa_gerekli(kullanici) is None:
            return
        try:
            length = int(self.headers.get('Content-Length', 0))
            req = runtime.json.loads(self.rfile.read(length).decode('utf-8') or '{}')
        except Exception as e:
            self._send(400, runtime.json.dumps({'hata': 'istek okunamadi: %s' % e}))
            return
        ok = runtime._sohbet_sil(kullanici, req.get('id'))
        self._send(200 if ok else 404, runtime.json.dumps({'silindi': ok}))
        return

    if parsed.path == '/api/sohbetler':
        kullanici = self._auth_gerekli()
        if kullanici is None:
            return
        if self._kasa_gerekli(kullanici) is None:
            return
        qs = runtime.urllib.parse.parse_qs(parsed.query or '')
        is_id = (qs.get('id') or [None])[0]
        liste = runtime._sohbet_oku(kullanici)
        if is_id:
            kayit = next((k for k in liste if k.get('id') == is_id), None)
            if kayit is None:
                self._send(404, '{"hata":"bulunamadi"}')
            else:
                self._send(200, runtime.json.dumps(kayit, ensure_ascii=False))
        else:
            self._send(200, runtime.json.dumps({'sohbetler': liste}, ensure_ascii=False))
        return

    if parsed.path == '/api/sohbetler/temizle':
        kullanici = self._auth_gerekli()
        if kullanici is None:
            return
        if self._kasa_gerekli(kullanici) is None:
            return
        ok = runtime._sohbet_temizle(kullanici)
        self._send(200 if ok else 404, runtime.json.dumps({'temizlendi': ok}))
        return
