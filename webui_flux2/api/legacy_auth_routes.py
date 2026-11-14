"""Legacy HTTP route family kept for API contract compatibility."""

from __future__ import annotations

ROUTE_PATHS = frozenset(('/api/cikis', '/api/giris', '/api/kasa/ac', '/api/kasa/kapat', '/api/kasa/kurtar', '/api/kasa/nabiz', '/api/kasa/olustur', '/api/kasa/parola', '/api/kasa/sure', '/api/kullanici/ekle', '/api/kullanici/kota', '/api/kullanici/sil', '/api/kurulum', '/api/sifre'))

def supports(path, runtime):
    return path in ROUTE_PATHS

def handle_post(self, parsed, runtime):
    if not supports(parsed.path, runtime):
        return
    if parsed.path == '/api/cikis':
        kullanici = self._oturum_kullanici()
        if kullanici is not None:
            try:
                runtime._kasa_kapat_arka(kullanici)
            except Exception as e:
                self._send(500, runtime.json.dumps({'hata': 'kasa kapanışı başlatılamadı: %s' % e}, ensure_ascii=False))
                return
        runtime._auth_store.cikis(self._cerez_token())
        self._send(200, '{"cikis":true}', ek_baslik=self._cerez_sil() + [('Clear-Site-Data', '"cache", "cookies", "storage"')])
        return

    if parsed.path == '/api/giris':
        req = self._json_oku()
        if '__hata__' in req:
            self._send(400, runtime.json.dumps({'hata': req['__hata__']}))
            return
        try:
            token, rol = runtime._auth_store.giris((req.get('kullanici') or '').strip(), req.get('sifre') or '', self.client_address[0])
        except runtime.auth.AuthHatasi:
            self._send(401, runtime.json.dumps({'hata': 'kullanici adi veya sifre hatali'}))
            return
        self._send(200, runtime.json.dumps({'kullanici': (req.get('kullanici') or '').strip(), 'rol': rol}), ek_baslik=self._oturum_cerezi(token))
        return

    if parsed.path == '/api/kasa/ac':
        kullanici = self._auth_gerekli()
        if kullanici is None:
            return
        req = self._json_oku()
        parola = req.get('parola') or ''
        try:
            req['parola'] = ''
        except Exception:
            pass
        try:
            runtime.kasa.ac(kullanici, parola)
            hata = None
        except runtime.kasa.KasaHatasi as e:
            hata = str(e)
        except Exception:
            hata = 'kasa acilamadi'
        if hata is not None:
            self._send(403, runtime.json.dumps({'hata': hata}, ensure_ascii=False))
            return
        self._send(200, runtime.json.dumps(runtime._kasa_durum_bilgi(kullanici), ensure_ascii=False))
        return

    if parsed.path == '/api/kasa/kapat':
        kullanici = self._auth_gerekli()
        if kullanici is None:
            return
        try:
            runtime._kasa_kapat_arka(kullanici)
        except Exception as e:
            self._send(500, runtime.json.dumps({'hata': 'kasa kapanışı başlatılamadı: %s' % e}, ensure_ascii=False))
            return
        self._send(200, runtime.json.dumps(runtime._kasa_durum_bilgi(kullanici), ensure_ascii=False))
        return

    if parsed.path == '/api/kasa/kurtar':
        kullanici = self._auth_gerekli()
        if kullanici is None:
            return
        req = self._json_oku()
        parola = req.get('parola') or ''
        try:
            req['parola'] = ''
        except Exception:
            pass
        try:
            runtime.kasa.kurtar(kullanici, parola)
            runtime.kasa.ac(kullanici, parola)
            hata = None
        except runtime.kasa.KasaHatasi as e:
            hata = str(e)
        except Exception:
            hata = 'kurtarma basarisiz'
        if hata is not None:
            self._send(403, runtime.json.dumps({'hata': hata}, ensure_ascii=False))
            return
        self._send(200, runtime.json.dumps(runtime._kasa_durum_bilgi(kullanici), ensure_ascii=False))
        return

    if parsed.path == '/api/kasa/nabiz':
        kullanici = self._auth_gerekli()
        if kullanici is None:
            return
        try:
            runtime.kasa.dokun(kullanici)
        except Exception:
            pass
        self._send(200, runtime.json.dumps(runtime._kasa_durum_bilgi(kullanici), ensure_ascii=False))
        return

    if parsed.path == '/api/kasa/olustur':
        kullanici = self._auth_gerekli()
        if kullanici is None:
            return
        req = self._json_oku()
        if '__hata__' in req:
            self._send(400, runtime.json.dumps({'hata': req['__hata__']}))
            return
        parola = req.get('parola') or ''
        parola2 = req.get('parola2') or ''
        anladim = bool(req.get('riski_anladim'))
        try:
            req['parola'] = ''
            req['parola2'] = ''
        except Exception:
            pass
        try:
            uyari = runtime.kasa.guc_uyarilari(parola)
        except Exception:
            uyari = []
        if uyari and (not anladim):
            self._send(400, runtime.json.dumps({'hata': 'parola zayif: ' + '; '.join(uyari), 'uyarilar': uyari}, ensure_ascii=False))
            return
        try:
            ham = runtime._ham_kullanici_koku(kullanici)
            kaynak = ham if runtime.os.path.isdir(ham) and runtime.os.listdir(ham) else None
            runtime.kasa.olustur(kullanici, parola, parola2, kaynak)
            runtime.kasa.ac(kullanici, parola)
            hata = None
        except runtime.kasa.KasaHatasi as e:
            hata = str(e)
        except Exception:
            hata = 'kasa olusturulamadi'
        if hata is not None:
            self._send(403, runtime.json.dumps({'hata': hata}, ensure_ascii=False))
            return
        self._send(200, runtime.json.dumps(runtime._kasa_durum_bilgi(kullanici), ensure_ascii=False))
        return

    if parsed.path == '/api/kasa/parola':
        kullanici = self._auth_gerekli()
        if kullanici is None:
            return
        if self._kasa_gerekli(kullanici) is None:
            return
        req = self._json_oku()
        if '__hata__' in req:
            self._send(400, runtime.json.dumps({'hata': req['__hata__']}, ensure_ascii=False))
            return
        mevcut = req.get('mevcut') or ''
        yeni = req.get('yeni') or ''
        tekrar = req.get('tekrar') or ''
        req['mevcut'] = req['yeni'] = req['tekrar'] = ''
        kapanis = False
        try:
            if not runtime.kasa.kapanis_baslat(kullanici):
                self._send(409, '{"hata":"kasa şu anda başka bir işlemde"}')
                return
            kapanis = True
            with runtime._http_yazma_kosulu:
                istekler = runtime._kasa_yazma_istekleri.get(kullanici)
                if istekler is not None:
                    istekler.discard(self)
                    if not istekler:
                        runtime._kasa_yazma_istekleri.pop(kullanici, None)
                runtime._http_yazma_kosulu.notify_all()
                yazmalar_bitti = runtime._http_yazma_kosulu.wait_for(lambda: not runtime._kasa_yazma_istekleri.get(kullanici), timeout=30)
            if not yazmalar_bitti:
                self._send(409, '{"hata":"başka bir kayıt işlemi sürüyor; tamamlanınca tekrar dene"}')
                return
            with runtime._isler_kilit:
                calisan_is_var = any((islem.get('sahip') == kullanici and (not islem['olay'].is_set()) for islem in runtime._isler.values()))
            if calisan_is_var:
                self._send(409, '{"hata":"önce devam eden üretim veya sohbet işlerinin bitmesini bekle"}')
                return
            try:
                runtime.kasa.parola_degistir(kullanici, mevcut, yeni, tekrar)
            except runtime.kasa.KasaHatasi as exc:
                mesaj = str(exc)
                durum = 400 if mesaj.startswith('yeni parola') else 403
                self._send(durum, runtime.json.dumps({'hata': mesaj}, ensure_ascii=False))
                return
            except Exception:
                self._send(500, '{"hata":"parola değiştirilemedi; mevcut parola korunuyor"}')
                return
            self._send(200, '{"degisti":true}')
        finally:
            if kapanis:
                runtime.kasa.kapanis_iptal(kullanici)
        return

    if parsed.path == '/api/kasa/sure':
        kullanici = self._auth_gerekli()
        if kullanici is None:
            return
        if self._kasa_gerekli(kullanici) is None:
            return
        req = self._json_oku()
        try:
            dakika = max(1, min(24 * 60, int(req.get('dakika', 15))))
        except Exception:
            self._send(400, runtime.json.dumps({'hata': 'dakika sayi olmali'}))
            return
        runtime.kasa.bekleme_koy(kullanici, dakika)
        self._send(200, runtime.json.dumps({'dakika': dakika}))
        return

    if parsed.path == '/api/kullanici/ekle':
        if not self._origin_kontrol():
            return
        if self._admin_gerekli() is None:
            return
        req = self._json_oku()
        if '__hata__' in req:
            self._send(400, runtime.json.dumps({'hata': req['__hata__']}))
            return
        try:
            bilgi = runtime._auth_store.kullanici_ekle((req.get('kullanici') or '').strip(), req.get('sifre') or '', req.get('rol', 'uye'), req.get('kota_mb', 0), req.get('kota_kayit', 0))
        except runtime.auth.AuthHatasi as e:
            self._send(400, runtime.json.dumps({'hata': str(e)}))
            return
        runtime._kullanici_klasorleri(bilgi['kullanici'])
        self._send(200, runtime.json.dumps(bilgi, ensure_ascii=False))
        return

    if parsed.path == '/api/kullanici/kota':
        if not self._origin_kontrol():
            return
        if self._admin_gerekli() is None:
            return
        req = self._json_oku()
        if '__hata__' in req:
            self._send(400, runtime.json.dumps({'hata': req['__hata__']}))
            return
        try:
            runtime._auth_store.kota_koy((req.get('kullanici') or '').strip(), req.get('kota_mb', 0), req.get('kota_kayit', 0))
        except runtime.auth.AuthHatasi as e:
            kod = 404 if e.kod == 'yok' else 400
            self._send(kod, runtime.json.dumps({'hata': str(e)}))
            return
        self._send(200, '{"kota":true}')
        return

    if parsed.path == '/api/kullanici/sil':
        if not self._origin_kontrol():
            return
        silen = self._admin_gerekli()
        if silen is None:
            return
        req = self._json_oku()
        if '__hata__' in req:
            self._send(400, runtime.json.dumps({'hata': req['__hata__']}))
            return
        hedef = (req.get('kullanici') or '').strip()
        try:
            runtime._auth_store.kullanici_sil(hedef, silen)
        except runtime.auth.AuthHatasi as e:
            kod = 404 if e.kod == 'yok' else 400
            self._send(kod, runtime.json.dumps({'hata': str(e)}))
            return
        try:
            runtime.kasa.profil_sil(hedef)
        except Exception:
            pass
        try:
            runtime.shutil.rmtree(runtime._ham_kullanici_koku(hedef), ignore_errors=True)
        except Exception:
            pass
        if runtime.RUNPOD_MODE:
            runtime.pod_setup.checkpoint()
        self._send(200, '{"silindi":true}')
        return

    if parsed.path == '/api/kurulum':
        if runtime._auth_store.kurulu_mu():
            self._send(403, '{"hata":"sistem zaten kurulu"}')
            return
        req = self._json_oku()
        if '__hata__' in req:
            self._send(400, runtime.json.dumps({'hata': req['__hata__']}))
            return
        try:
            bilgi = runtime._auth_store.ilk_admin((req.get('kullanici') or '').strip(), req.get('sifre') or '')
        except runtime.auth.AuthHatasi as e:
            kod = 400 if e.kod in ('gecersiz-ad', 'zayif-sifre') else 403
            self._send(kod, runtime.json.dumps({'hata': str(e)}))
            return
        tasinan = runtime._ilk_migrasyon(bilgi['kullanici'])
        toplam, silinen = runtime._zararli_prompt_temizle(bilgi['kullanici'])
        if runtime.RUNPOD_MODE:
            try:
                runtime.pod_setup.checkpoint()
            except Exception as exc:
                print('İlk hesap için kalıcı kopya yazılamadı (%s).' % type(exc).__name__, flush=True)
        self._send(200, runtime.json.dumps({'kullanici': bilgi['kullanici'], 'rol': bilgi['rol'], 'tasinan': tasinan, 'prompt_toplam': toplam, 'prompt_temizlenen': silinen}, ensure_ascii=False))
        return

    if parsed.path == '/api/sifre':
        if not self._origin_kontrol():
            return
        kullanici = self._auth_gerekli()
        if kullanici is None:
            return
        req = self._json_oku()
        if '__hata__' in req:
            self._send(400, runtime.json.dumps({'hata': req['__hata__']}))
            return
        try:
            runtime._auth_store.sifre_degistir(kullanici, req.get('eski'), req.get('yeni'))
        except runtime.auth.AuthHatasi as e:
            kod = 400 if e.kod in ('zayif-sifre', 'hatali') else 401
            self._send(kod, runtime.json.dumps({'hata': str(e)}))
            return
        self._send(200, '{"degisti":true}')
        return
