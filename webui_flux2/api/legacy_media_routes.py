"""Legacy HTTP route family kept for API contract compatibility."""

from __future__ import annotations

ROUTE_PATHS = frozenset(('/api/arsiv/ice-aktar', '/api/arsiv/sil', '/api/arsiv/sil-coklu', '/api/gecmis/sil', '/api/gecmis/temizle', '/api/gecmis/yildiz', '/api/kutuphane/sil', '/api/kutuphane/temizle', '/api/promptlar', '/api/promptlar/sil'))

def supports(path, runtime):
    return path in ROUTE_PATHS

def handle_post(self, parsed, runtime):
    if not supports(parsed.path, runtime):
        return
    if parsed.path == '/api/arsiv/ice-aktar':
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
        if length > 200 * 1024 * 1024:
            self._send(413, '{"hata":"istek gövdesi çok büyük (200 MB üstü)"}')
            return
        req = self._json_oku()
        if '__hata__' in req:
            self._send(400, runtime.json.dumps({'hata': req['__hata__']}))
            return
        gorseller = req.get('gorseller')
        if not isinstance(gorseller, list) or not gorseller:
            self._send(400, '{"hata":"gorseller listesi gerekli"}')
            return
        if len(gorseller) > 200:
            self._send(400, '{"hata":"tek seferde en fazla 200 görsel"}')
            return
        try:
            kok = runtime._kullanici_koku(kullanici)
        except ValueError:
            self._send(409, '{"hata":"kasa kilitli"}')
            return
        sonuc = runtime.arsiv.arsiv_ice_aktar(kok, gorseller)
        self._send(200, runtime.json.dumps(sonuc, ensure_ascii=False))
        return

    if parsed.path == '/api/arsiv/sil':
        kullanici = self._auth_gerekli()
        if kullanici is None:
            return
        if self._kasa_gerekli(kullanici) is None:
            return
        req = self._json_oku()
        if '__hata__' in req:
            self._send(400, runtime.json.dumps({'hata': req['__hata__']}))
            return
        ozet = str(req.get('hash') or '')
        if not runtime.re.fullmatch('[a-f0-9]{64}', ozet):
            self._send(400, '{"hata":"gecersiz hash"}')
            return
        try:
            kok = runtime._kullanici_koku(kullanici)
        except ValueError:
            self._send(409, '{"hata":"kasa kilitli"}')
            return
        silindi = runtime.arsiv.arsiv_sil(kok, ozet)
        self._send(200, runtime.json.dumps({'silindi': silindi}))
        return

    if parsed.path == '/api/arsiv/sil-coklu':
        kullanici = self._auth_gerekli()
        if kullanici is None:
            return
        if self._kasa_gerekli(kullanici) is None:
            return
        req = self._json_oku()
        if '__hata__' in req:
            self._send(400, runtime.json.dumps({'hata': req['__hata__']}))
            return
        hashes = req.get('hashes')
        if not isinstance(hashes, list) or len(hashes) > 100:
            self._send(400, '{"hata":"En fazla 100 fotoğraf seçilebilir."}')
            return
        try:
            kok = runtime._kullanici_koku(kullanici)
            silinen = runtime.arsiv.arsiv_sil_coklu(kok, hashes)
        except ValueError as exc:
            self._send(400, runtime.json.dumps({'hata': str(exc)}, ensure_ascii=False))
            return
        except (OSError, RuntimeError):
            self._send(409, '{"hata":"Arşiv kopyaları silinemedi."}')
            return
        self._send(200, runtime.json.dumps({'silinen': len(silinen), 'silinen_hashler': silinen}))
        return

    if parsed.path == '/api/gecmis/sil':
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
        ok = runtime.gecmis_kaydi_sil(kullanici, req.get('kayit'))
        self._send(200 if ok else 404, runtime.json.dumps({'silindi': ok}))
        return

    if parsed.path == '/api/gecmis/temizle':
        kullanici = self._auth_gerekli()
        if kullanici is None:
            return
        if self._kasa_gerekli(kullanici) is None:
            return
        self._send(200, runtime.json.dumps({'temizlendi': runtime.gecmis_temizle(kullanici)}))
        return

    if parsed.path == '/api/gecmis/yildiz':
        kullanici = self._auth_gerekli()
        if kullanici is None:
            return
        if self._kasa_gerekli(kullanici) is None:
            return
        req = self._json_oku()
        if '__hata__' in req:
            self._send(400, runtime.json.dumps({'hata': req['__hata__']}))
            return
        if not isinstance(req.get('yildiz'), bool):
            self._send(400, '{"hata":"yildiz boolean olmali"}')
            return
        ok = runtime.gecmis_kaydi_yildiz(kullanici, req.get('kayit'), req.get('yildiz'), req.get('dosya'))
        self._send(200 if ok else 404, runtime.json.dumps({'yildiz': bool(ok)}))
        return

    if parsed.path == '/api/kutuphane/sil':
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
        ok = runtime.kutuphaneden_sil(kullanici, req.get('ad'))
        self._send(200 if ok else 404, runtime.json.dumps({'silindi': ok}))
        return

    if parsed.path == '/api/kutuphane/temizle':
        kullanici = self._auth_gerekli()
        if kullanici is None:
            return
        if self._kasa_gerekli(kullanici) is None:
            return
        self._send(200, runtime.json.dumps({'temizlendi': runtime.kutuphane_temizle(kullanici)}))
        return

    if parsed.path == '/api/promptlar':
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
        ad = (req.get('ad') or '').strip()
        if not ad:
            self._send(400, runtime.json.dumps({'hata': 'ad gerekli'}))
            return
        kayit = {'ad': ad, 'prompt': req.get('prompt', ''), 'negative': req.get('negative', ''), 'model': req.get('model') if req.get('model') in (runtime.MODEL_FLUX2, runtime.MODEL_Z_IMAGE, runtime.MODEL_ANIMA, runtime.MODEL_MOODY_ANIMA) else runtime.MODEL_FLUX2, 'workflow': req.get('workflow', 'tek'), 'steps': req.get('steps', 4), 'cfg': req.get('cfg', 1.0), 'sampler': req.get('sampler', 'euler'), 'width': req.get('width', 1024), 'height': req.get('height', 1024), 'max_side': req.get('max_side', 1536)}
        if runtime._istem_zararli_mi(kayit):
            self._send(400, runtime.json.dumps({'hata': 'bu icerik kaydedilemez'}))
            return
        self._send(200, runtime.json.dumps({'kaydedildi': True, 'promptlar': runtime.prompt_kaydet(kullanici, kayit)}, ensure_ascii=False))
        return

    if parsed.path == '/api/promptlar/sil':
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
        ok = runtime.prompt_sil(kullanici, (req.get('ad') or '').strip())
        self._send(200 if ok else 404, runtime.json.dumps({'silindi': ok}))
        return
