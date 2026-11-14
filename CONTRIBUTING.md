# Katkı rehberi

## Başlamadan önce

1. `AGENTS.md` ve ilgili `docs/feature-guides/` belgesini okuyun.
2. Değişiklik alanını `webui_flux2/`, `docker/`, `tests/` veya dokümantasyon olarak sınırlandırın.
3. İlk değişiklikten önce `git status --short` ile çalışma ağacını kontrol edin. Başkasının değişikliklerini ezmeyin.

## Özellik geliştirme

- API ve arayüz sözleşmesini birlikte tanımlayın. UI var olan API uçlarını kullanmalı; yanıt şeması ve hata/iptal/ilerleme davranışları geriye dönük uyumlu kalmalıdır.
- Yeni RunPod API route ailelerini `webui_flux2/api/legacy_*_routes.py` içinde ayırın; mevcut Handler uyumluluk sınırını ancak karşılık gelen sözleşme testleri hazır olduğunda kaldırın. İş kurallarını mevcut alana göre `services/`, `kasa_modules/`, `llm_runtime/` veya `pod_setup_modules/` içine ekleyin. `server.py`, `kasa.py`, `llm.py` ve `pod_setup.py` çağıran kod için facade olarak kalır.
- Yeni React ekranlarını `frontend/src/features/<özellik>/` altında küçük, bağımsız bileşenler olarak kurun; istekleri typed `frontend/src/lib/api/` istemcisine ekleyin. 8189 üretim alanında medya için geniş merkezi alanı, mobilde sabit erişilebilir prompt/üretim kontrollerini ve klavye/focus durumlarını koruyun. Diğer özellikler React'e taşınana kadar eski arayüze giden erişimi bırakmayın.
- Kullanıcı ayarı, evrensel ayar, geçici kuyruk durumu ve kalıcı kayıt için ayrı sahiplik belirleyin.
- Kaynak dosyası, statik dosya, katalog veya bağımlılık eklendiğinde `Dockerfile.gallery` kopyalama listesini ve `app_update.py` güncelleme kapsamını kontrol edin.
- Yeni model/workflow’da `docker/models.json` tek katalog kaynağıdır. Workflow dosyası ve model dosyası adları birbiriyle uyumlu olmalıdır.

## Doğrulama komutları

RunPod Docker imajı FastAPI/Uvicorn'u kurar. Frontend kaynakları `webui_flux2/frontend/` altındadır; Vite test/build adımları Docker'ın Node 24 derleme aşamasında yürür. Yerelde frontend'i denemek için:

```sh
cd webui_flux2/frontend
npm ci
npm test
npm run build
```

API uyumluluk testini FastAPI test araçları kurulu Python ortamında çalıştır:

```sh
PYTHONPATH=webui_flux2 python3 -m pytest -q tests/test_*.py
```

Kod katkısında temel kontroller:

```sh
python3 -m compileall -q webui_flux2 tests pod-launch.py
PYTHONPATH=webui_flux2 python3 -m pytest -q tests/test_*.py
node --check webui_flux2/static/app.js
node --check webui_flux2/static/setup.js
node tests/app_selection_smoke.js
```

Frontend test ve paketleme Docker imajının Node 24 aşamasında da çalışır:

```sh
cd webui_flux2/frontend
npm test
npm run build
```

Tam imaj doğrulaması GitHub Actions workflow-unda katalog kontrolleri, Docker build, volume’suz ilk açılış, sentetik hesap ve HTTP sağlık kontrollerini içerir. Bu doğrulamalar gerçek kullanıcı hesabına erişmemelidir. Bu komutlar dokümantasyon-only değişiklik için gerekli değildir.

## Yayınlama sınırı

- GitHub Actions imajı uzakta derler ve `gallery-<commit>`, `latest`, `beta` etiketlerini günceller. Stable promosyonu ayrı ve açık bir adımdır.
- Dockerfile veya supervisor değişikliği yeni imaj gerektirir. Uygulama içi güncelleme, bunları değiştiremez.
- Stable etiketini, template’i veya canlı Pod’u değiştirmeyin; görev açıkça bunu istemedikçe yayın yapmayın.

## PR/teslim kontrolü

- `git diff --check` ve `git diff` ile yalnız beklenen dosyaların değiştiğini doğrulayın.
- Secret, medya, model, kullanıcı verisi veya geçici çalışma çıktısı commit etmeyin.
- Sonuçta değişen dosyaları, çalıştırılan kontrolleri ve doğrulanmayan noktaları belirtin.
