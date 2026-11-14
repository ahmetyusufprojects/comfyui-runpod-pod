# API ve UI özelliği ekleme

Bu rehber RunPod'da geçiş sürerken API ve UI özelliği eklemek içindir. FastAPI sağlık router'ları ile legacy route aile adaptörleri, servis facade'ları ve React üretim arayüzünün ilk dilimi birlikte çalışır. Sohbet, prompt stüdyosu, albüm ve kurucu akışları henüz eski UI'dadır.

## 1. Sözleşmeyi önce belirle

- İlgili mevcut ekranı ve `server.py` içindeki handler/endpoint’i bul. Ana WebUI 8189, kasa açılışından bağımsız kurucu 8192 üzerinde çalışır; endpoint davranışı portla ayrılabilir.
- İstek alanları, JSON yanıtı, hata biçimi, yetki gereksinimi, iptal/ilerleme ve kalıcılık sahipliğini yazılı belirle.
- Eski JSON alanlarını kaldırma/yeniden adlandırma. Yeni alanlar geriye uyumlu ve varsayılanlı olmalı.

## 2. Mevcut kaynakta uygula

- Yeni RunPod API uçları için `webui_flux2/api/` altında `APIRouter` ve özelliğe ait service kullan. Mevcut route aileleri `api/legacy_*_routes.py` içindedir ve Handler sözleşmesine dispatch eder. Var olan endpoint'i native router'a taşırken eski yol ve JSON şekli için sözleşme testi ekle; `api/legacy.py` geçiş sırasında kalan Handler yollarını yürütür.
- Özellik henüz taşınmamışsa ilgili iş kuralını uygun `services/`, `kasa_modules/`, `llm_runtime/` veya `pod_setup_modules/` paketinde tut; `server.py` içine ek yığma yapma. Windows portable davranışını koru.
- React ana ekranı `frontend/src/features/` içindedir; HTTP çağrıları `frontend/src/lib/api/` typed istemcisinden geçer. Eski ekran davranışı `static/app.js`/`app.css`/`index.html`, kurucu davranışı `setup.js`/`setup.css`/`setup.html` içindedir. React'e taşınmamış ekranlara görünür bağlantı bırak.
- Uzun işler kuyruk, job ID, durum sorgusu, iptal ve uygulama yeniden başlatma ile tutarlı yürümelidir. Polling arayüzü 8192’de giriş ekranından bağımsız çalışabilmelidir.
- Secret, kullanıcı parolası, kasa anahtarı veya tam hata trace’i UI/log yanıtına yazdırma.

## 3. Dağıtım paketine ekle

- Yeni Python modülü `docker/Dockerfile.gallery` içindeki açık `COPY` listesine eklenmeli.
- Yeni legacy UI dosyaları açık `COPY` listesine, gerekiyorsa `app_update.py` allowlist'ine eklenmeli. React dosyaları Docker'ın Node build aşamasından `frontend-dist/` olarak imaja kopyalanır; her React bundle değişikliği yeni imaj gerektirir.
- Yeni Python kaynak modülleri `app_update.py` allowlist/required set’ine de eklenmeli; aksi hâlde çalışan Pod uygulama güncellemesi o dosyayı alamaz.
- Dockerfile/supervisor veya Linux bağımlılığı değiştiyse uygulama güncellemesi yetmez; yeni imaj gerekir.

## 4. Doğrulama

Mevcut kontroller: Python derleme, FastAPI API sözleşme testleri, React/Vitest testleri ve eski JS syntax/smoke kontrolleri. Workflow ayrıca Vite build, no-volume açılışı ve sentetik kurulum hesabını kontrol eder. Özellik için API istek/yanıtını, hata/iptal durumunu ve her iki portta ilgili görünümü sentetik veride doğrula. Gerçek hesap/kasa veya live volume verisi kullanma.

## Ekranları kademeli taşıma

API sözleşmesini React bileşeninden bağımsız tut. Tipli istemci, yükleme/boş/hata/disabled/focus/selected/submitting/success durumları ve erişilebilir kontroller ekle. Route başına bağımsız feature bileşenleri kullan; mevcut API yanıtlarını typed client ile eşle. `/?ui=next` üzerinde 8189 üretim dikey dilimi (hesap/kasa geçidi, geniş çıktı alanı, model ve görsel/video kontrolleri, kuyruk/iptal/sonuç) çalışır; diğer ekranların taşındığını varsayma. Playwright veya tarayıcı akışını login/kasa, kuyruk ve mobil panel için sonraki aşamalarda ekle.
