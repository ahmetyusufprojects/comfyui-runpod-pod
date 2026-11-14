"""Installer API handlers extracted from the legacy HTTP facade.

A runtime namespace is passed explicitly while shared services are separated
from the compatibility layer in later migration slices.
"""

from __future__ import annotations


def handle_setup_get(self, parsed, runtime):
    if parsed.path in ('/', '/index.html'):
        if parsed.query and runtime.urllib.parse.parse_qs(parsed.query).get('ui') == ['next']:
            self._serve_react_preview(parsed)
            return
        with open(runtime.os.path.join(runtime.BASE_DIR, 'setup.html'), 'rb') as stream:
            self._send(200, stream.read(), 'text/html; charset=utf-8')
        return
    if parsed.path.startswith('/assets/'):
        self._serve_react_preview(parsed)
        return
    if parsed.path.startswith('/static/'):
        name = parsed.path[len('/static/'):]
        if name not in ('setup.js', 'setup.css'):
            self._send(404, '{"hata":"bulunamadi"}')
            return
        try:
            with open(runtime.os.path.join(runtime.BASE_DIR, 'static', name), 'rb') as stream:
                body = stream.read()
        except OSError:
            self._send(404, '{"hata":"bulunamadi"}')
            return
        content_type = 'application/javascript; charset=utf-8' if name.endswith('.js') else 'text/css; charset=utf-8'
        self._send(200, body, content_type)
        return
    if parsed.path in ('/api/sunucu-hazir', '/api/setup-state'):
        code, payload = runtime._setup_health_state()
        self._send(code, runtime.json.dumps(payload, ensure_ascii=False))
        return
    if parsed.path == '/api/kurulum-durumu':
        if not runtime.pod_setup.is_ready():
            self._send(503, '{"hata":"Sistem hazırlanıyor."}')
        else:
            storage = runtime.pod_setup.storage_status()
            self._send(200, runtime.json.dumps({'kurulu': runtime._auth_store.kurulu_mu(), 'runpod': True, **storage}))
        return
    if not runtime.pod_setup.is_ready():
        self._send(503, '{"hata":"Sistem hazırlanıyor."}')
        return
    if parsed.path == '/api/r2-backups':
        if not self._setup_yetkili():
            return
        try:
            state = runtime.r2_backups.status()
            state.update(runtime.r2_backups.list_backups())
            state['error'] = ''
        except runtime.r2_backups.R2Error as exc:
            configured = bool(runtime.os.environ.get('R2_ENDPOINT') or runtime.os.environ.get('R2_ACCOUNT_ID')) and bool(runtime.os.environ.get('R2_ACCESS_KEY_ID')) and bool(runtime.os.environ.get('R2_SECRET_ACCESS_KEY'))
            state = {'configured': configured, 'connected': False, 'bucket': runtime.os.environ.get('R2_BUCKET', 'moody-webui-backups'), 'backups': [], 'error': str(exc)[:240]}
        self._send(200, runtime.json.dumps(state, ensure_ascii=False))
        return
    if parsed.path == '/api/r2-backup-download':
        if not self._setup_yetkili():
            return
        object_id = (runtime.urllib.parse.parse_qs(parsed.query).get('id') or [''])[0]
        try:
            item = runtime.r2_backups.get_object(object_id)
        except runtime.r2_backups.R2Error as exc:
            status = 404 if 'NoSuchKey' in str(exc) else 502
            self._send(status, runtime.json.dumps({'hata': str(exc)[:240]}, ensure_ascii=False))
            return
        size = int(item.get('ContentLength', 0))
        self.send_response(200)
        self.send_header('Content-Type', 'application/octet-stream')
        self.send_header('Content-Length', str(size))
        self.send_header('Content-Disposition', 'attachment; filename="webui-system-' + object_id + '"')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        body = item['Body']
        try:
            while True:
                chunk = body.read(1024 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)
        except OSError:
            pass
        finally:
            body.close()
        return
    if parsed.path == '/api/backup-progress':
        if not self._setup_yetkili():
            return
        token = (runtime.urllib.parse.parse_qs(parsed.query).get('token') or [''])[0]
        state = runtime._backup_progress_get(token) if runtime._backup_upload_path(token) else None
        if state is None:
            self._send(404, '{"hata":"Yedek yükleme oturumu bulunamadı."}')
        else:
            self._send(200, runtime.json.dumps(state, ensure_ascii=False))
        return
    if parsed.path == '/api/ben':
        if self._setup_parolasiz():
            self._send(200, runtime.json.dumps({'kullanici': 'Kurulum paneli', 'rol': 'admin', 'parolasiz': True}, ensure_ascii=False))
            return
        kullanici = self._oturum_kullanici()
        if kullanici is None:
            self._send(401, '{"hata":"giris gerekli"}')
        else:
            self._send(200, runtime.json.dumps({'kullanici': kullanici, 'rol': runtime._auth_store.rol(kullanici)}))
        return
    if parsed.path == '/api/pod-kurulum':
        if not self._setup_yetkili():
            return
        self._send(200, runtime.json.dumps(runtime.pod_setup.status(), ensure_ascii=False))
        return
    if parsed.path == '/api/app-update':
        if not self._setup_yetkili():
            return
        state = dict(runtime._app_update_state)
        state.setdefault('kanal', runtime.app_update.image_channel())
        if state.get('status') == 'idle':
            try:
                state = runtime.json.loads((runtime.app_update.ROOT / 'result.json').read_text(encoding='utf-8'))
            except (OSError, ValueError):
                pass
            state.setdefault('kanal', runtime.app_update.image_channel())
        self._send(200, runtime.json.dumps(state, ensure_ascii=False))
        return
    if parsed.path == '/api/system-settings':
        if not self._setup_yetkili():
            return
        self._send(200, runtime.json.dumps(runtime.pod_setup.system_settings(), ensure_ascii=False))
        return
    if parsed.path == '/api/loras' and runtime.RUNPOD_MODE:
        if self._auth_gerekli() is None:
            return
        query = runtime.urllib.parse.parse_qs(parsed.query or '')
        model = (query.get('model') or [None])[0]
        self._send(200, runtime.json.dumps({'loras': runtime.pod_setup.lora_status(model)}, ensure_ascii=False))
        return
    if parsed.path == '/api/log':
        if not self._setup_yetkili():
            return
        query = runtime.urllib.parse.parse_qs(parsed.query or '')
        source = (query.get('kaynak') or ['setup'])[0]
        try:
            count = max(10, min(500, int((query.get('satir') or ['200'])[0])))
            lines = runtime._log_tail(source, count)
        except ValueError as exc:
            self._send(400, runtime.json.dumps({'hata': str(exc)}))
            return
        self._send(200, runtime.json.dumps({'kaynak': source, 'satirlar': lines}, ensure_ascii=False))
        return
    self._send(404, '{"hata":"bulunamadi"}')


def handle_setup_post(self, parsed, runtime):
    if not self._origin_kontrol():
        return
    if not runtime.pod_setup.is_ready():
        self._send(503, '{"hata":"Sistem hazırlanıyor."}')
        return
    if parsed.path == '/api/r2-backup-create':
        if not self._setup_yetkili():
            return
        request = self._json_oku()
        if '__hata__' in request:
            self._send(400, runtime.json.dumps({'hata': request['__hata__']}))
            return
        password = self.headers.get('X-Backup-Password') or ''
        if not password:
            self._send(400, '{"hata":"Yedek parolası gerekli."}')
            return
        token = runtime.uuid.uuid4().hex
        name = str(request.get('name') or 'webui-system').strip()[:80] or 'webui-system'
        media_excluded = bool(request.get('exclude_media'))

        def create_backup():
            staged = None
            with runtime._sistem_yedegi_hazirla(password, medya_haric=media_excluded) as path:
                fd, staged = runtime.tempfile.mkstemp(prefix='webui-r2-backup-', suffix='.wuis.gpg')
                runtime.os.close(fd)
                runtime.os.remove(staged)
                runtime.os.replace(path, staged)
            try:
                total = runtime.os.path.getsize(staged)
                runtime._backup_progress_set(token, status='running', stage='R2 deposuna yükleniyor', bytes=0, total=total, error='')
                result = runtime.r2_backups.upload_file(staged, name, runtime._r2_progress_callback(token, 'R2 deposuna yükleniyor', total))
                return {'id': result['id'], 'name': result['name'], 'size': result['size']}
            finally:
                if staged:
                    try:
                        runtime.os.remove(staged)
                    except OSError:
                        pass
        try:
            runtime._r2_start_job(token, create_backup, "Şifreli yedek R2'ye kaydedildi")
        except Exception as exc:
            self._send(503, runtime.json.dumps({'hata': str(exc)[:240]}, ensure_ascii=False))
            return
        self._send(202, runtime.json.dumps({'job_id': token}, ensure_ascii=False))
        return
    if parsed.path == '/api/r2-backup-save':
        if not self._setup_yetkili():
            return
        request = self._json_oku()
        token = request.get('token')
        path = runtime._backup_upload_path(token)
        if not path or not runtime.os.path.isfile(path):
            self._send(404, runtime.json.dumps({'hata': "Önce şifreli yedek dosyasını bu Pod'a yükleyin."}, ensure_ascii=False))
            return
        name = str(request.get('name') or 'webui-system').strip()[:80] or 'webui-system'

        def save_uploaded_backup():
            total = runtime.os.path.getsize(path)
            runtime._backup_progress_set(token, status='running', stage='R2 deposuna yükleniyor', bytes=0, total=total, error='')
            result = runtime.r2_backups.upload_file(path, name, runtime._r2_progress_callback(token, 'R2 deposuna yükleniyor', total))
            try:
                runtime.os.remove(path)
            except OSError:
                pass
            return {'id': result['id'], 'name': result['name'], 'size': result['size']}
        runtime._r2_start_job(token, save_uploaded_backup, "Şifreli yedek R2'ye kaydedildi")
        self._send(202, runtime.json.dumps({'job_id': token}, ensure_ascii=False))
        return
    if parsed.path == '/api/r2-backup-prepare-restore':
        if not self._setup_yetkili():
            return
        request = self._json_oku()
        object_id = request.get('id')
        if not runtime.r2_backups.is_backup_id(object_id):
            self._send(400, '{"hata":"Yedek kaydı kimliği geçersiz."}')
            return
        token = request.get('token') or runtime.uuid.uuid4().hex
        if not runtime._backup_upload_path(token):
            self._send(400, '{"hata":"Yedek oturumu geçersiz."}')
            return

        def prepare_restore():
            destination = runtime._backup_upload_path(token)
            runtime.os.makedirs(runtime._BACKUP_UPLOAD_DIR, mode=448, exist_ok=True)
            if runtime.os.path.isfile(destination):
                size = runtime.os.path.getsize(destination)
                return {'size': size, 'reused': True}
            meta = runtime.r2_backups.head_backup(object_id)
            total = meta['size']
            if total <= 0 or total > 100 * 1024 * 1024 * 1024:
                raise RuntimeError('Yedek boş veya 100 GiB sınırını aşıyor.')
            if runtime.shutil.disk_usage(runtime._BACKUP_UPLOAD_DIR).free < total + 256 * 1024 * 1024:
                raise RuntimeError('Yedeği indirmek için geçici diskte yeterli boş alan yok.')
            partial = destination + '.uploading'
            runtime._backup_progress_set(token, status='running', stage="R2'den şifreli yedek indiriliyor", bytes=0, total=total, error='')
            try:
                size = runtime.r2_backups.download_file(object_id, partial, runtime._r2_progress_callback(token, "R2'den şifreli yedek indiriliyor", total))
                runtime.os.replace(partial, destination)
            except Exception:
                try:
                    runtime.os.remove(partial)
                except OSError:
                    pass
                raise
            return {'size': size, 'reused': False}
        runtime._r2_start_job(token, prepare_restore, 'Şifreli yedek indirildi; parola ile geri yükleyebilirsiniz')
        self._send(202, runtime.json.dumps({'token': token}, ensure_ascii=False))
        return
    if parsed.path == '/api/r2-backup-delete':
        if not self._setup_yetkili():
            return
        request = self._json_oku()
        try:
            object_id = request.get('id')
            if not runtime.r2_backups.is_backup_id(object_id):
                raise runtime.r2_backups.R2Error('Yedek kaydı kimliği geçersiz.')
            runtime.r2_backups.delete_backup(object_id)
            self._send(200, '{"deleted":true}')
        except runtime.r2_backups.R2Error as exc:
            self._send(400, runtime.json.dumps({'hata': str(exc)[:240]}, ensure_ascii=False))
        return
    if parsed.path == '/api/backup-export':
        if not self._setup_yetkili():
            return
        parola = self.headers.get('X-Backup-Password') or ''
        medya_haric = self.headers.get('X-Backup-Media') == 'exclude'
        try:
            with runtime._sistem_yedegi_hazirla(parola, medya_haric=medya_haric) as path:
                self._send_file(path, 'webui-system.wuis.gpg')
        except (runtime.kasa.KasaHatasi, OSError, RuntimeError) as exc:
            if not self.wfile.closed:
                self._send(409, runtime.json.dumps({'hata': str(exc)[:300]}, ensure_ascii=False))
        return
    if parsed.path == '/api/backup-upload':
        if not self._setup_yetkili():
            return
        try:
            length = int(self.headers.get('Content-Length', 0))
        except (TypeError, ValueError):
            length = 0
        if length <= 0 or length > 100 * 1024 * 1024 * 1024:
            self._send(413, '{"hata":"Yedek boş veya 100 GiB sınırını aşıyor."}')
            return
        partial = None
        try:
            runtime.os.makedirs(runtime._BACKUP_UPLOAD_DIR, mode=448, exist_ok=True)
            try:
                runtime.os.chmod(runtime._BACKUP_UPLOAD_DIR, 448)
            except OSError:
                pass
            if runtime.shutil.disk_usage(runtime._BACKUP_UPLOAD_DIR).free < length + 256 * 1024 * 1024:
                self._send(507, '{"hata":"Yedeği saklamak için geçici diskte yeterli boş alan yok."}')
                return
            token = runtime.uuid.uuid4().hex
            destination = runtime._backup_upload_path(token)
            partial = destination + '.uploading'
            with open(partial, 'wb') as stream:
                remaining = length
                while remaining:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise OSError('Yedek aktarımı yarıda kesildi.')
                    stream.write(chunk)
                    remaining -= len(chunk)
                stream.flush()
                runtime.os.fsync(stream.fileno())
            runtime.os.replace(partial, destination)
            runtime._backup_progress_set(token, status='uploaded', stage="Yedek Pod'a yüklendi", bytes=length, files=0, error='')
            self._send(200, runtime.json.dumps({'token': token, 'size': length}))
        except OSError as exc:
            if partial:
                try:
                    runtime.os.remove(partial)
                except OSError:
                    pass
            self._send(507, runtime.json.dumps({'hata': str(exc)[:200]}, ensure_ascii=False))
        return
    if parsed.path == '/api/backup-import':
        if not self._setup_yetkili():
            return
        parola = self.headers.get('X-Backup-Password') or ''
        token = None
        temp = None
        retained_upload = self.headers.get('Content-Type', '').split(';', 1)[0].strip() == 'application/json'
        if retained_upload:
            request = self._json_oku()
            token = request.get('token')
            temp = runtime._backup_upload_path(token)
            if not temp or not runtime.os.path.isfile(temp):
                self._send(404, '{"hata":"Yüklenen yedek bulunamadı; dosyayı yeniden seçip yükleyin."}')
                return
        else:
            try:
                length = int(self.headers.get('Content-Length', 0))
            except (TypeError, ValueError):
                length = 0
            if length <= 0 or length > 100 * 1024 * 1024 * 1024:
                self._send(413, '{"hata":"Yedek boş veya 100 GiB sınırını aşıyor."}')
                return
            fd, temp = runtime.tempfile.mkstemp(prefix='system-import-', suffix='.wuis.gpg')
            try:
                with runtime.os.fdopen(fd, 'wb') as stream:
                    remaining = length
                    while remaining:
                        chunk = self.rfile.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise OSError('Yedek aktarımı yarıda kesildi.')
                        stream.write(chunk)
                        remaining -= len(chunk)
            except OSError as exc:
                try:
                    runtime.os.remove(temp)
                except OSError:
                    pass
                self._send(400, runtime.json.dumps({'hata': str(exc)}, ensure_ascii=False))
                return
        length = runtime.os.path.getsize(temp)
        try:
            free_bytes = runtime.shutil.disk_usage(runtime.DATA_ROOT).free
        except OSError:
            free_bytes = 0
        if free_bytes < length * 2 + 512 * 1024 * 1024:
            if not retained_upload:
                try:
                    runtime.os.remove(temp)
                except OSError:
                    pass
            self._send(507, '{"hata":"Yedeği güvenle geri yüklemek için geçici diskte yeterli boş alan yok."}')
            return
        try:
            if token:
                runtime._backup_progress_set(token, status='running', stage='Parola ve yedek doğrulanıyor', bytes=0, files=0, error='')
            if not runtime._system_backup_lock.acquire(blocking=False):
                raise RuntimeError('Başka bir sistem yedeği işlemi sürüyor.')
            try:
                with runtime._isler_kilit, runtime.pod_setup._lock:
                    if runtime._kuyruk_calismasi or runtime._ceviri_calismasi or runtime._kuyruk or runtime._ceviri_kuyrugu or runtime.pod_setup._active or runtime.pod_setup._queue:
                        raise RuntimeError('Yedeği yüklemeden önce iş ve indirmeleri bitirin.')
                with runtime._http_yazma_kosulu:
                    if runtime._kapatma_istegi.is_set():
                        raise RuntimeError('Sistem başka bir işlem yürütüyor.')
                    runtime._kapatma_istegi.set()
                    if not runtime._http_yazma_kosulu.wait_for(lambda: not runtime._http_yazma_istekleri, timeout=30):
                        runtime._kapatma_istegi.clear()
                        raise RuntimeError('Kayıt işlemleri tamamlanmadı.')
                try:
                    with runtime._isler_kilit, runtime.pod_setup._lock:
                        if runtime._kuyruk_calismasi or runtime._ceviri_calismasi or runtime._kuyruk or runtime._ceviri_kuyrugu or runtime.pod_setup._active or runtime.pod_setup._queue:
                            raise RuntimeError('İçe aktarma sırasında yeni bir iş kuyruğa girdi.')

                    def import_progress(state):
                        if token:
                            runtime._backup_progress_set(token, stage=state.get('durum', 'Geri yükleniyor'), files=state.get('dosya', 0), bytes=state.get('bayt', 0))
                        if state.get('durum') == 'sisteme_yerlestiriliyor':
                            closed = runtime.kasa.hepsini_kapat()
                            if any((not value for value in closed.values())):
                                raise RuntimeError('Açık bir kullanıcı kasası mühürlenemedi; veri değiştirilmedi.')
                    result = runtime.kasa.sistem_yedegi_ice_aktar(temp, parola, runtime.DATA_ROOT, runtime.pod_setup._system_settings_path, ilerleme=import_progress)
                    runtime.pod_setup.refresh_data_lock()
                    merged = runtime.pod_setup.refresh_after_system_import()
                    result['model_sayisi'] = merged['model_count']
                    result['lora_sayisi'] = merged['lora_count']
                    result['kurulum_sirasina_eklenen'] = merged.get('queued_after_restore', 0)
                    runtime.pod_setup.checkpoint()
                    if token:
                        runtime._backup_progress_set(token, status='complete', stage='Geri yükleme tamamlandı; eksik kurulum sırası başlatıldı', files=0, bytes=length, error='')
                        try:
                            runtime.os.remove(temp)
                        except OSError:
                            pass
                finally:
                    runtime._kapatma_istegi.clear()
            finally:
                runtime._system_backup_lock.release()
            self._send(200, runtime.json.dumps(result, ensure_ascii=False))
        except (runtime.kasa.KasaHatasi, OSError, RuntimeError) as exc:
            if token:
                runtime._backup_progress_set(token, status='error', stage='Yedek korundu; parolayı veya dosyayı kontrol edin', error=str(exc)[:240])
            self._send(400, runtime.json.dumps({'hata': str(exc)}, ensure_ascii=False))
        finally:
            if not retained_upload and temp:
                try:
                    runtime.os.remove(temp)
                except OSError:
                    pass
        return
    if runtime._kapatma_istegi.is_set():
        self._send(503, '{"hata":"Sistem yedeği hazırlanıyor; kısa süre sonra yeniden deneyin."}')
        return
    if parsed.path == '/api/giris':
        if self._setup_parolasiz():
            self._send(409, '{"hata":"Kurulum panelinde giriş gerekmiyor."}')
            return
        req = self._json_oku()
        if '__hata__' in req:
            self._send(400, runtime.json.dumps({'hata': req['__hata__']}))
            return
        try:
            username = (req.get('kullanici') or '').strip()
            token, role = runtime._auth_store.giris(username, req.get('sifre') or '', self.client_address[0])
        except runtime.auth.AuthHatasi:
            self._send(401, '{"hata":"Kullanıcı adı veya şifre hatalı."}')
            return
        self._send(200, runtime.json.dumps({'kullanici': username, 'rol': role}), ek_baslik=self._oturum_cerezi(token))
        return
    if parsed.path == '/api/cikis':
        runtime._auth_store.cikis(self._cerez_token())
        self._send(200, '{"cikis":true}', ek_baslik=self._cerez_sil())
        return
    if parsed.path == '/api/app-update':
        if not self._setup_yetkili():
            return
        if runtime.os.environ.get('WEBUI_UPDATE_SUPERVISOR') != '1':
            self._send(409, '{"hata":"Bu imaj güncelleme başlatıcısını içermiyor; yeni Pod açın."}')
            return
        if not runtime._app_update_lock.acquire(blocking=False):
            self._send(409, '{"hata":"Güncelleme zaten sürüyor."}')
            return
        runtime._app_update_state = {'status': 'preparing', 'kanal': runtime.app_update.image_channel()}
        runtime.threading.Thread(target=runtime._app_update_worker, daemon=True).start()
        self._send(202, '{"status":"preparing"}')
        return
    if parsed.path == '/api/pod-kurulum':
        if not self._setup_yetkili():
            return
        if runtime._kapatma_istegi.is_set():
            self._send(409, '{"hata":"Güncelleme için uygulama kapanıyor."}')
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
                runtime.pod_setup.remove(req.get('grup'))
            elif req.get('islem') == 'sira':
                runtime.pod_setup.move(req.get('grup'), req.get('yon'))
            else:
                runtime.pod_setup.start(req.get('grup'))
        except ValueError as exc:
            self._send(400, runtime.json.dumps({'hata': str(exc)}, ensure_ascii=False))
        except (OSError, RuntimeError) as exc:
            self._send(409, runtime.json.dumps({'hata': str(exc)}, ensure_ascii=False))
        else:
            self._send(202, runtime.json.dumps(runtime.pod_setup.status(), ensure_ascii=False))
        return
    if parsed.path == '/api/system-settings':
        if not self._setup_yetkili():
            return
        req = self._json_oku()
        if '__hata__' in req:
            self._send(400, runtime.json.dumps({'hata': req['__hata__']}))
            return
        try:
            action = req.get('islem', 'kaydet')
            if action == 'baglanti':
                result = runtime.pod_setup.update_model_link(req.get('grup'), req.get('dosya'), req.get('baglanti'), restore=False)
            elif action == 'varsayilan':
                result = runtime.pod_setup.update_model_link(req.get('grup'), req.get('dosya'), '', restore=True)
            elif action == 'kaydet':
                result = runtime.pod_setup.update_system_settings(req)
            elif action == 'lora_ekle':
                result = runtime.pod_setup.add_lora(req)
            elif action == 'lora_ayar':
                result = runtime.pod_setup.configure_lora(req.get('id'), req)
            elif action == 'lora_sil':
                result = runtime.pod_setup.delete_lora(req.get('id'))
            elif action == 'model_ekle':
                result = runtime.pod_setup.create_model_instance(req)
            elif action == 'model_ad':
                result = runtime.pod_setup.rename_model_instance(req.get('id'), req.get('name'))
            elif action == 'model_sil':
                result = runtime.pod_setup.delete_model_instance(req.get('id'))
            else:
                raise ValueError('Bilinmeyen sistem ayarı işlemi.')
        except ValueError as exc:
            self._send(400, runtime.json.dumps({'hata': str(exc)}, ensure_ascii=False))
            return
        except RuntimeError as exc:
            self._send(409, runtime.json.dumps({'hata': str(exc)}, ensure_ascii=False))
            return
        except OSError:
            self._send(500, '{"hata":"Sistem ayarı kaydedilemedi."}')
            return
        self._send(200, runtime.json.dumps(result, ensure_ascii=False))
        return
    if parsed.path == '/api/storage-clean':
        if not self._setup_yetkili():
            return
        req = self._json_oku()
        if '__hata__' in req:
            self._send(400, runtime.json.dumps({'hata': req['__hata__']}))
            return
        if req.get('onay') is not True:
            self._send(400, '{"hata":"Temizleme onayı gerekli."}')
            return
        with runtime._isler_kilit:
            active_jobs = any((job.get('durum') in ('kuyrukta', 'calisiyor') or (hasattr(job.get('olay'), 'is_set') and (not job['olay'].is_set())) for job in runtime._isler.values()))
            queued_jobs = bool(runtime._kuyruk or runtime._ceviri_kuyrugu)
        if active_jobs or queued_jobs:
            self._send(409, '{"hata":"Önce devam eden üretim ve sohbet işlerini bitirin."}')
            return
        if runtime._app_update_state.get('status') in ('preparing', 'downloading', 'applying'):
            self._send(409, '{"hata":"Uygulama güncellemesi sürerken depolama temizlenemez."}')
            return
        try:
            result = runtime.pod_setup.clear_rebuildable_storage()
        except (OSError, RuntimeError) as exc:
            self._send(409, runtime.json.dumps({'hata': str(exc)}, ensure_ascii=False))
            return
        self._send(200, runtime.json.dumps(result, ensure_ascii=False))
        return
    self._send(404, '{"hata":"bulunamadi"}')
