# Mimari: mevcut durum ve hedef

## Şu anki dağıtım

```mermaid
flowchart LR
  BrowserMain[Tarayıcı 8189] --> MainASGI[FastAPI / Uvicorn]
  BrowserSetup[Tarayıcı 8192] --> SetupASGI[FastAPI / Uvicorn]
  MainASGI --> Bridge[Uyumluluk köprüsü]
  SetupASGI --> Bridge
  Bridge --> Handler[server.py / eski Handler]
  MainASGI --> HealthMain[FastAPI sağlık router'ı]
  SetupASGI --> HealthSetup[Kurucu durum router'ı]
  Handler --> App[auth, kasa, prompt, arsiv, llm, app_update]
  Handler --> Setup[pod_setup / R2 backups / model queue]
  App --> Comfy[ComfyUI API :8188]
  App --> Llama[llama-server :8191]
  Setup --> Catalog[docker/models.json]
  Setup --> Container[Container disk: /opt/runtime, /models]
  Setup --> Flex[Opsiyonel volume: /workspace + POD_FLEX_PREFIX]
  Setup --> R2[Cloudflare R2 S3 API]
```

- `docker/Dockerfile.gallery` Ubuntu 24.04 tabanlı küçük uygulama imajı kurar; Python venv ve birkaç Python paketi içerir. `docker/supervisor.py`, WebUI sürecini başlatır ve hazır olma denetimi yapar.
- RunPod'da 8189 ve 8192 aynı process içinde ayrı FastAPI uygulaması/Uvicorn dinleyicisi olarak çalışır. Sağlık uçları (`/api/sunucu-hazir`, `/api/setup-state`) FastAPI router'ına taşınmıştır; diğer API yolları mevcut Handler'a uyumluluk katmanından aktarılır ve response body/header/cookie biçimleri korunur.
- Eski tam arayüz varsayılandır: `index.html` + `static/app.js` + `static/app.css`; kurucu `setup.html` + `static/setup.js` + `static/setup.css` ile sunulur. Yeni React/TypeScript arayüzü şu an yalnız durum/bağlantı önizlemesidir ve iki portta `/?ui=next` ile açılır. Önizlemede üretim, sohbet, albüm, ayar veya kurucu işlevi yoktur. Windows portable `http.server` kullanmaya devam eder.
- `webui_flux2/api/legacy.py` geçiş adaptörüdür. Bu kalıcı route mimarisi değildir; yeni uçlar native `APIRouter`/service sınırına eklenmeli, eski uçlar API contract testleriyle taşınmalıdır. Büyük gövde/yanıtlar belleğe tamamen alınmadan disk tabanlı geçici dosyaya akıtılır; başlık tekrarları (özellikle Set-Cookie) korunur.
- `docker/models.json` şema 2, model grupları, dosya indirme kaynakları, bağımlılıklar ve workflow profillerinin tek katalog kaynağıdır.
- İmaj, ComfyUI ve llama-server runtime’ını imaj içine sabitlemez; kurucu runtime/model kaynaklarını çalışma sırasında container diskine yerleştirir. Tam yollar için kaynak varsayılanlarına bakın.
- `app_update.py`, uygulama ve katalog güncellemesini stage/backup/health-check/rollback çevrimiyle uygular. `Dockerfile.gallery` ve supervisor taban imajla aynı kalmalıdır; bu sınır değişirse yeni imaj gerekir.

## Ekran/işlev haritası

| Yüzey | Ana bölümler ve API sorumluluğu |
| --- | --- |
| 8189 ana WebUI | İlk kurulum/giriş/kasa açma; üretim ve üretim ayarları; prompt stüdyosu ve sırası; sohbet; arşiv/kütüphane; sistem/iş logları; kullanıcı ve genel ayarlar |
| 8192 kurucu | Kurulum durumu; model listesi/indirme kuyruğu ve işlem kontrolü; model bağlantıları/LoRA ve sistem ayarları; loglar; yedekleme/R2; depolama temizliği, restart ve imaj güncelleme |

Her bölümün uçları ve payload’ları `server.py`, `static/app.js` ve `static/setup.js` içinde tanımlıdır. Taşıma sırasında bu sözleşmeler envanterlenip API testiyle kilitlenmelidir; UI metinlerinden yeni API şeması varsayılmamalıdır.

## Hedeflenen ayrıştırma — geçiş sürüyor

```mermaid
flowchart LR
  MainUI[React + TypeScript SPA] --> API[FastAPI routers]
  SetupUI[Aynı SPA / kurucu rotası] --> API
  API --> Services[İş kuralları ve servisler]
  Services --> Ports[Depolama, modeller, Comfy, LLM, R2 arayüzleri]
  Ports --> Adapters[Mevcut Python entegrasyonları]
```

Taşıma önce JSON sözleşmesini koruyan uyumluluk katmanıyla başladı. React, TypeScript, Vite, Tailwind ve Lucide yalnız önizleme uygulamasında kuruldu. Sonraki dilimlerde API aileleri native router/service'e, UI ekranları feature bileşenlerine taşınmalı; her dilim API/auth/CSRF/iptal/ilerleme testlerinden geçtikten sonra aktif olmalıdır. JSON sözleşmesi, kullanıcı verisi, queue cancellation, upload progress, canlı log ve setup portunun bağımsız açılma özelliği korunmalıdır. FastAPI'nin [çok dosyalı uygulama/router](https://fastapi.tiangolo.com/tutorial/bigger-applications/) ve [lifespan](https://fastapi.tiangolo.com/advanced/events/) belgeleri yeni router ve servis yaşam döngüsü için başlangıç kaynağıdır.

Önerilen gelecek paket yapısı:

```text
webui_flux2/
  api/                 # FastAPI app, router ve şema
  services/            # üretim, prompt, kuyruk, model yönetimi
  domain/              # veri tipleri ve iş kuralları
  adapters/            # ComfyUI, llama-server, disk, R2
  frontend/            # Vite/React/TypeScript uygulaması
    src/app/            # route/layout ve erişim sınırları
    src/features/       # production, prompt-studio, chat, archive, setup...
    src/components/     # paylaşılan UI bileşenleri
    src/lib/api/         # tipli HTTP istemcisi ve hata eşleme
  tests/               # API sözleşmesi, servis ve UI akışları
```

Kademeli geçişin ADR’leri `docs/decisions/` içindedir.
