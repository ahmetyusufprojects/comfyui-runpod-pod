"""Legacy HTTP route family kept for API contract compatibility."""

from __future__ import annotations

ROUTE_PATHS = frozenset(('/api/durdur', '/api/is/iptal', '/api/kuyruk', '/api/kuyruk/temizle', '/api/llm/hazirla'))

def supports(path, runtime):
    return path in ROUTE_PATHS

def handle_post(self, parsed, runtime):
    if not supports(parsed.path, runtime):
        return
    if parsed.path == '/api/durdur':
        kullanici = self._auth_gerekli()
        if kullanici is None:
            return
        if self._kasa_gerekli(kullanici) is None:
            return
        req = self._json_oku()
        with runtime._isler_kilit:
            job = runtime._isler.get(req.get('is_id')) if req.get('is_id') else None
            if job is not None and job.get('sahip') != kullanici:
                job = None
            if job is None:
                active_ids = {runtime._aktif_llm.get('is_id')}
                active_prompt = runtime._aktif.get('prompt_id')
                if active_prompt is not None:
                    active_ids.update((j['is_id'] for j in runtime._isler.values() if j.get('prompt_id') == active_prompt))
                job = next((j for j in reversed(list(runtime._isler.values())) if j.get('sahip') == kullanici and j.get('durum') == 'calisiyor' and (j.get('is_id') in active_ids)), None)
        durduruldu = job is not None and runtime._isi_iptal_et(job)
        self._send(200, runtime.json.dumps({'durduruldu': durduruldu, 'kuyruk': job is not None}))
        return

    if parsed.path == '/api/is/iptal':
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
        job = runtime._isler.get(req.get('is_id'))
        if job is None or job.get('sahip') != kullanici:
            self._send(404, '{"hata":"bulunamadi"}')
            return
        iptal = runtime._isi_iptal_et(job)
        self._send(200, runtime.json.dumps({'iptal': iptal, 'is_id': job['is_id'], 'durum': job['durum']}))
        return

    if parsed.path == '/api/kuyruk':
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
        if 'prompt_destek' in req and (not isinstance(req.get('prompt_destek'), bool)):
            self._send(400, '{"hata":"prompt_destek boolean olmali"}')
            return
        if 'postprocess' in req:
            hata = runtime._iyilestirme_istegi_dogrula(kullanici, req)
        else:
            hata = runtime._is_dogrula(req)
            if not hata:
                hata = runtime._uretim_sonrasi_iyilestirme_istegi_dogrula(req)
        if hata:
            self._send(400, runtime.json.dumps(hata))
            return
        kota_hata = runtime._kota_kontrol(kullanici, yeni_bayt=0, yeni_kayit=1)
        if kota_hata:
            self._send(400, runtime.json.dumps({'hata': kota_hata}))
            return
        if self._kasa_kapaniyor_mu(kullanici):
            return
        job, sira = runtime._kuyruga_ekle(req, kullanici)
        if job is None:
            self._send(409, '{"hata":"bellek bosaltilirken yeni is kuyruga eklenemez"}')
            return
        self._send(200, runtime.json.dumps({'is_id': job['is_id'], 'sira': sira, 'durum': 'kuyrukta'}))
        return

    if parsed.path == '/api/kuyruk/temizle':
        kullanici = self._auth_gerekli()
        if kullanici is None:
            return
        if self._kasa_gerekli(kullanici) is None:
            return
        with runtime._isler_kilit:
            bekleyen = [i for i in runtime._kuyruk if runtime._isler[i]['sahip'] == kullanici]
            for is_id in bekleyen:
                runtime._bekleyen_isi_iptal_et(runtime._isler[is_id])
        self._send(200, runtime.json.dumps({'temizlendi': len(bekleyen)}))
        return

    if parsed.path == '/api/llm/hazirla':
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
        model = req.get('llm_model')
        if model is not None and (not isinstance(model, str) or model not in {item['id'] for item in runtime.llm.model_profilleri()}):
            self._send(400, '{"hata":"LLM model seçimi geçersiz"}')
            return
        job, sira = runtime._kuyruga_ekle({'llm_model': model} if model else {}, kullanici, tur='llm_preload')
        if job is None:
            self._send(409, '{"hata":"bellek bosaltilirken yeni is kuyruga eklenemez"}')
            return
        self._send(200, runtime.json.dumps({'queued': True, 'is_id': job['is_id'], 'sira': sira}))
        return
