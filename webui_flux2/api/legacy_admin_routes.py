"""Legacy HTTP route family kept for API contract compatibility."""

from __future__ import annotations

ROUTE_PATHS = frozenset(('/api/pod-kurulum', '/api/sistem/bosalti', '/api/sunucu-kapat'))

def supports(path, runtime):
    if path == '/api/pod-kurulum':
        return runtime.RUNPOD_MODE
    return path in ROUTE_PATHS

def handle_post(self, parsed, runtime):
    if not supports(parsed.path, runtime):
        return
    if parsed.path == '/api/pod-kurulum' and runtime.RUNPOD_MODE:
        if self._admin_gerekli() is None:
            return
        req = self._json_oku()
        try:
            if req.get('islem') == 'iptal':
                runtime.pod_setup.cancel(req.get('grup'))
            elif req.get('islem') == 'duraklat':
                runtime.pod_setup.pause(req.get('grup'))
            elif req.get('islem') == 'devam':
                runtime.pod_setup.start(req.get('grup'))
            elif req.get('islem') == 'kaldir':
                with runtime._isler_kilit:
                    if runtime._kuyruk_calismasi or runtime._ceviri_calismasi or runtime._kuyruk or runtime._ceviri_kuyrugu:
                        raise RuntimeError('Modeli kaldırmak için işlerin bitmesini bekleyin.')
                    runtime.pod_setup.remove(req.get('grup'))
            elif req.get('islem') == 'sira':
                runtime.pod_setup.move(req.get('grup'), req.get('yon'))
            else:
                runtime.pod_setup.start(req.get('grup'))
        except ValueError as hata:
            self._send(400, runtime.json.dumps({'hata': str(hata)}))
        except (OSError, RuntimeError) as hata:
            self._send(409, runtime.json.dumps({'hata': str(hata)}))
        else:
            self._send(202, runtime.json.dumps(runtime.pod_setup.status(), ensure_ascii=False))
        return

    if parsed.path == '/api/sistem/bosalti':
        kullanici = self._auth_gerekli()
        if kullanici is None:
            return
        if self._kasa_gerekli(kullanici) is None:
            return
        with runtime._isler_kilit:
            if runtime._bellek_bosaltma_aktif or runtime._kuyruk_calismasi or runtime._kuyruk or (runtime._aktif.get('prompt_id') is not None) or (runtime._aktif_llm.get('is_id') is not None):
                self._send(409, runtime.json.dumps({'hata': 'Bellek, WebUI işi veya kuyruğu çalışırken boşaltılamaz.'}, ensure_ascii=False))
                return
            runtime._bellek_bosaltma_aktif = True
        try:
            llm_kapandi = False
            try:
                if runtime.llm.durum().get('canli'):
                    llm_kapandi = runtime._llm_kapat_ve_bekle()
                else:
                    llm_kapandi = True
            except Exception:
                llm_kapandi = True
            ok, neden = runtime._comfy_bosalti_ve_dogrula(zorla=True)
            if not ok:
                self._send(503, runtime.json.dumps({'hata': neden, 'vram_bos_mb': runtime._vram_bos_mb(), 'gerekli_vram_mb': runtime.LLM_VRAM_GEREK_MB, 'llm_kapandi': llm_kapandi}, ensure_ascii=False))
                return
            self._send(200, runtime.json.dumps({'bosaltildi': True, 'vram_bos_mb': runtime._vram_bos_mb(), 'gerekli_vram_mb': runtime.LLM_VRAM_GEREK_MB, 'llm_kapandi': llm_kapandi}, ensure_ascii=False))
        finally:
            with runtime._isler_kilit:
                runtime._bellek_bosaltma_aktif = False
        return

    if parsed.path == '/api/sunucu-kapat':
        if self.client_address[0] not in ('127.0.0.1', '::1'):
            self._send(403, '{"hata":"yalniz bu makineden"}')
            return
        req = self._json_oku()
        muhurlensin = req.get('mod') != 'yeniden_baslat'
        with runtime._kapatma_istek_kilit:
            if runtime._kapatma_istegi.is_set():
                self._send(409, '{"hata":"WebUI zaten kapanıyor"}')
                return
            runtime._kapatma_istegi.set()
        try:
            self._send(200, '{"kapaniyor":true}')
        finally:
            runtime.threading.Thread(target=runtime._sunucu_kapat_arka, args=(muhurlensin,), daemon=True).start()
        return
