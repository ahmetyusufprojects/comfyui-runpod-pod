# RunPod depo çalışma rehberi

Bu depo yalnızca RunPod dağıtımını kapsar; Windows portable kurulumun kaynağı değildir. Dağıtım dalı `runpod/gallery-first`’tir. Kaynak ve imaj için ana yollar:

| Alan | Kaynak |
| --- | --- |
| API uygulaması ve uyumluluk rotaları | `webui_flux2/api/app.py`, `api/legacy_*_routes.py`, `api/legacy.py` |
| API durumu/geriye uyumluluk | `webui_flux2/server.py` (Handler facade), `image_graphs.py`, `comfy_client.py` |
| Üretim, kuyruk, içerik servisleri | `webui_flux2/services/` |
| Kasa, LLM, kurucu servisleri | `webui_flux2/kasa_modules/`, `llm_runtime/`, `pod_setup_modules/`; eski `kasa.py`, `llm.py`, `pod_setup.py` facade'ları |
| Eski ana arayüz | `webui_flux2/index.html`, `static/app.js`, `static/app.css` |
| React ana üretim arayüzü | `webui_flux2/frontend/src/features/production/`, `webui_flux2/frontend/src/lib/api/`, `webui_flux2/frontend/src/ui/`, `webui_flux2/frontend/src/styles.css` (`/?ui=next`) |
| Kurucu arayüzü (8192) | `webui_flux2/setup.html`, `static/setup.js`, `static/setup.css` |
| Model/workflow kataloğu | `docker/models.json` (şema 2) |
| İmaj ve süreç yöneticisi | `docker/Dockerfile.gallery`, `docker/supervisor.py` |
| Immutability/update path | `webui_flux2/app_update.py` |
| İmaj build/release | `.github/workflows/gallery-image.yml`, `docker/stable-image.txt` |
| Pod başlatma | `pod-launch.py` |

## Güncel yapı ve planlanan geçiş

- RunPod'da 8189 ve 8192 FastAPI/Uvicorn ASGI uygulamalarıdır. İki health ucu native router'da; kalan legacy API yolları `api/legacy.py` üzerinden aynı `server.py` Handler'ına gider. Windows portable `http.server` yolunu korur.
- Ana sayfada hesap/kasa, üretim, prompt stüdyosu, sıra, arşiv/kütüphane, sohbet, izleme ve ayarlar akışları vardır. 8192 kurucusu model kuyruğu, sistem/model yönetimi, loglar, yedekleme ve bakım akışlarını sunar.
- React/TypeScript/Vite, Tailwind ve Lucide durum önizlemesi `/?ui=next` adresinde paketlenir; eski tam arayüz varsayılandır ve tüm üretim/sohbet/kurucu akışları React'e henüz taşınmamıştır. Taşıma aşamalı olmalı; API sözleşmeleri, JSON çıktıları, oturum/kasa kuralları ve arka plan işlerinin iptal/ilerleme davranışı korunmalıdır. Ayrıntı `docs/architecture.md` içindedir.
- Yeni statik/modül dosyaları Dockerfile’ın açık `COPY` listesine ve uygulama güncellemesinin izinli dosya listesine eklenmelidir. İmaj sınırı değişiklikleri yalnız yeni imajla dağıtılır.

## Veri ve erişim sınırları

- Gerçek kullanıcı/admin hesaplarını, kasaları, sohbetleri, arşivi, Flex içeriğini veya yedekleri incelemeyin. Herhangi bir doğrulama gerekiyorsa yalnız sentetik hesap/veri kullanın; canlı kullanıcı servisine bağlanmayın.
- Secret ve token değerlerini okumayın, yazdırmayın, commit etmeyin. Yalnız örnek/boş değişken adlarını belgeleyin.
- İmaja veya Git’e kullanıcı verisi, medya, modeller, çalışma kayıtları ya da secret girmemelidir.
- Container diskindeki geçici içerikle kalıcı depolama birbirinden açıkça ayrılmalı. R2 yedekleri ve yeni Flex snapshot'ları şifreli arşiv yoludur. Eski volume içeriğinin tümüyle temizlendiğini veya şifreli olduğunu varsaymayın.
- Gerçek Flex/volume içeriğini teşhis için elle listelemeyin veya açmayın. Depolama kodu gözden geçirilirken yalnız kaynak kodu ve geçici sentetik fixture kullanın. Yeni snapshot kodu yalnız `POD_FLEX_PREFIX` ve `POD_FLEX_ENCRYPTION_KEY` varsa açılır; volume'e yalnız şifreli `.tar.gpg` yazılır. Secret yoksa persistence kapalı kalır. Uygulamanın dar kapsamlı başlangıç geçişi yalnız tanıdığı eski `.tar` snapshot'ları ve `system-settings*.json` kayıtlarını, şifreli kopyayı çözerek doğruladıktan sonra taşır. Bilinmeyen dosyalara dokunmaz; eski volume'ün bütünü temizlendi diye varsaymayın.

## Değişiklik ve doğrulama

1. Özelliğin sahibi olan API, UI, katalog ve kalıcılık sözleşmelerini belirleyin; API/UI eşleşmesini birlikte güncelleyin.
2. Model ekleme/kaldırma/URL değiştirmede katalogla onu kullanan workflow/varsayılanları birlikte kontrol edin; katalog dışına dosya adı sabitlemeyin.
3. RunPod imajına giren kaynakların açık `COPY` listesini, bağımlılıkları ve app update allowlist’ini gözden geçirin.
4. Sadece istenen doğrulamaları çalıştırın. Kod değişikliği için mevcut CI kontrolleri: `python3 -m compileall -q webui_flux2 tests pod-launch.py`, `PYTHONPATH=webui_flux2 python3 -m pytest -q tests/test_*.py`, eski JavaScript syntax/smoke kontrolleri ve Docker derlemesindeki React/Vitest build adımları. Bunlar kullanıcı verisiyle değil sentetik kurulumla çalışmalıdır.
5. Dokümantasyon-only işinde uygulama testlerini/servisleri çalıştırmayın. Değişiklik öncesi/sonrası `git status --short`, sonrasında `git diff --check` ve diff incelemesi yapın.

## Beceriler ve alt ajanlar

İşin türüne uygun mevcut skill’leri kullanın: arayüz işleri için `webapp-ui-skill`; Cloudflare/R2 kodu için ilgili Cloudflare becerisi; framework geçişinde React/FastAPI’nin resmi belgeleri. Skill’in tavsiyesi kullanıcı/veri sınırlarını geçersiz kılamaz. Henüz mevcut olmayan skill’i kurmak bu depo değişikliğinin parçası değildir.

Geniş mimari çalışmada alt ajan kullanılması beklenir. **Luna High** mimari kararlar, API sözleşmesi ve risk incelemesini; **Luna Medium** sınırları belirlenmiş bağımsız UI bileşeni, test veya dokümantasyon işlerini üstlenebilir. Ajanlara açık dosya kapsamı verin; aynı dosyada eşzamanlı düzenleme yaptırmayın. Entegrasyon, diff incelemesi ve uçtan uca sözleşme sorumluluğu ana ajandadır. Bu model planlama kuralıdır; depo kodunda otomatik ajan sistemi yoktur.

React kaynakları Docker'ın Node aşamasında derlenip imaja `frontend-dist/` olarak kopyalanır. Çalışan Pod'un uygulama içi kaynak güncellemesi React bundle'ını üretmez; React değişikliğini dağıtmak için GitHub Actions üzerinden yeni beta imajı oluşturulmalıdır.
