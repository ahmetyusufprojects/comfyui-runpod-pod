# API ve UI özelliği ekleme

Bu rehber RunPod'da geçiş sürerken API ve UI özelliği eklemek içindir. FastAPI native sağlık router'ları ve React durum önizlemesi vardır; özelliklerin büyük bölümü halen mevcut Handler/JS içindedir.

## 1. Sözleşmeyi önce belirle

- İlgili mevcut ekranı ve `server.py` içindeki handler/endpoint’i bul. Ana WebUI 8189, kasa açılışından bağımsız kurucu 8192 üzerinde çalışır; endpoint davranışı portla ayrılabilir.
- İstek alanları, JSON yanıtı, hata biçimi, yetki gereksinimi, iptal/ilerleme ve kalıcılık sahipliğini yazılı belirle.
- Eski JSON alanlarını kaldırma/yeniden adlandırma. Yeni alanlar geriye uyumlu ve varsayılanlı olmalı.

## 2. Mevcut kaynakta uygula

- Yeni RunPod API uçları için `webui_flux2/api/` altında `APIRouter` ve özelliğe ait service kullan. Var olan endpoint'i yeni router'a taşırken eski yol ve JSON şekli için sözleşme testi ekle; `api/legacy.py` yalnız taşınmamış yolları geçici olarak yürütür.
- Özellik henüz taşınmamışsa ilgili iş kuralını mevcut sahip modülde tut; `server.py` içine ek yığma yapma. Windows portable davranışını koru.
- Ana ekran davranışı `static/app.js`/`app.css`/`index.html`; kurucu davranışı `setup.js`/`setup.css`/`setup.html` içindedir.
- Uzun işler kuyruk, job ID, durum sorgusu, iptal ve uygulama yeniden başlatma ile tutarlı yürümelidir. Polling arayüzü 8192’de giriş ekranından bağımsız çalışabilmelidir.
- Secret, kullanıcı parolası, kasa anahtarı veya tam hata trace’i UI/log yanıtına yazdırma.

## 3. Dağıtım paketine ekle

- Yeni Python modülü `docker/Dockerfile.gallery` içindeki açık `COPY` listesine eklenmeli.
- Yeni UI dosyaları aynı şekilde açık `COPY` listesine eklenmeli.
- `app_update.py` dosya allowlist/required set’i de yeni dosyayı içermeli; aksi hâlde çalışan Pod uygulama güncellemesi o dosyayı alamaz.
- Dockerfile/supervisor veya Linux bağımlılığı değiştiyse uygulama güncellemesi yetmez; yeni imaj gerekir.

## 4. Doğrulama

Mevcut kontroller: Python derleme, FastAPI API sözleşme testleri, React/Vitest testleri ve eski JS syntax/smoke kontrolleri. Workflow ayrıca Vite build, no-volume açılışı ve sentetik kurulum hesabını kontrol eder. Özellik için API istek/yanıtını, hata/iptal durumunu ve her iki portta ilgili görünümü sentetik veride doğrula. Gerçek hesap/kasa veya live volume verisi kullanma.

## Ekranları kademeli taşıma

API sözleşmesini React bileşeninden bağımsız tut. Tipli istemci, yükleme/boş/hata/disabled/focus/selected/submitting/success durumları ve erişilebilir kontroller ekle. Route başına bağımsız feature bileşenleri kullan; mevcut API yanıtlarını typed client ile eşle. Şimdiki `/?ui=next` ekranı yalnız bağlantı durumunu gösterir; tam işlevli sayfa olarak tanıtma. Playwright akışını, login/kasa ve kurucu API akışları React'e taşınırken CI'a ekle.
