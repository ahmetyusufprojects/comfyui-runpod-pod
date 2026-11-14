# ComfyUI WebUI — RunPod dağıtımı

Bu depo RunPod için küçük bir uygulama imajı üretir. ComfyUI, modeller ve çalışma zamanı ilk gerektiğinde Pod container diskine kurulur/indirilir; WebUI ve bağımsız kurucu aynı container içinde çalışır. Güncel dağıtım `docker/Dockerfile.gallery` ve `docker/supervisor.py` üzerinden yapılır. Eski bootstrap betikleri ve eski RunPod talimatları güncel dağıtımı anlatmaz.

## Servisler

| Adres/port | İşlev |
| --- | --- |
| `8189/http` | Ana WebUI: hesap/kasa, üretim, prompt stüdyosu, sohbet, arşiv, ayarlar ve izleme |
| `8192/http` | Bağımsız kurucu: indirme kuyruğu, modeller, sistem ayarları, günlükler, yedekleme ve bakım |
| `8188` | Container içindeki ComfyUI API’si; RunPod HTTP portu olarak sunulmaz |

RunPod imajında 8189 ve 8192 portları FastAPI/Uvicorn ile açılır. Mevcut API yolları ve JSON yanıtları, geçiş süresince `server.py` içindeki eski Handler'a uyumluluk katmanından aktarılır; sağlık uçları FastAPI router'larına taşınmıştır. Windows portable çalışması değişmeden `http.server` yolunu kullanır.

Eski, tam işlevli arayüz varsayılan olarak açılır. React/TypeScript arayüzünün ilk durum ekranı iki portta `/?ui=next` adresinden önizlenebilir; üretim, sohbet, arşiv ve kurucu işlevleri henüz bu ekrana taşınmadı. Uygulamanın veri akışlarını kademeli geçirmek için bu önizleme varsayılan arayüzü değiştirmez. Ayrıntı: [mimari](docs/architecture.md) ve [ADR’ler](docs/decisions/).

## İmaj ve sürüm etiketleri

GitHub Actions, `.github/workflows/gallery-image.yml` ile Linux imajını uzakta derler. Docker build aşamasında Node 24 ile React/TypeScript arayüzünü sınar ve üretim paketini çıkarır; Python API sözleşmeleri ve sentetik container açılışı da denetlenir. Başarılı yayında `ghcr.io/ahmetyusufprojects/comfyui-runpod-pod:gallery-<commit>` ve `:latest`, `:beta` etiketleri üretilir. `:stable` yalnızca açık promosyonla/`docker/stable-image.txt` değişikliğiyle güncellenir; yeni imaj mevcut Pod’u veya RunPod şablonunu kendiliğinden değiştirmez.

Güncel dağıtım dalı `runpod/gallery-first`’tir. `pod-launch.py` varsayılan olarak beta kanalını ve en fazla 0,32 USD/saat GPU bütçesini seçer; kararlı kanal açıkça seçilmelidir. İmaj şablonları ayrıca etiketlenir. Secrets yalnız RunPod/GitHub ayarlarından sağlanır; değerleri kaynak koda veya loglara koymayın.

## Depolama sınırı

- Modeller `/models` altında; ComfyUI, önbellekler ve uygulama çalışma dosyaları `/opt/runtime` altındadır. Bunlar container diskindedir ve container yeniden yaratıldığında kaybolabilir.
- Ana uygulama verisinin varsayılan kökü `/opt/runtime/user-data`’dır. Modeller, kullanıcı çalışma dosyaları ve uygulama ayarları container diskinde kalır. Flex'e kalıcılık yalnız `POD_FLEX_PREFIX` ve RunPod Secret olarak verilen `POD_FLEX_ENCRYPTION_KEY` birlikte varsa açılır. Bu durumda Flex'e yalnız GPG/AES-256 ile şifrelenmiş, doğrulanan `.tar.gpg` anlık görüntüsü yazılır; hesap kayıtları, genel ayarlar ve zaten şifreli kasa dosyaları bu arşivin içindedir. Anahtar yoksa uygulama container diskinde ephemeral çalışır ve volume'e yazmaz.
- R2 yedekleri `r2_backups.py` üzerinden şifreli arşiv olarak işlenir. Yeni imaj, anahtar sağlandığında tanıdığı eski `.tar` hesap snapshot'larını ve `system-settings*.json` dosyalarını şifreleyip doğrular; ancak bundan sonra bu eski kaynak dosyaları kaldırır. Tanınmayan dosyalar otomatik taşınmaz veya silinmez. Bu uygulama geçmişteki tüm volume içeriğinin şifreli olduğu anlamına gelmez. Ayrıntı: [depolama rehberi](docs/feature-guides/persistent-data-and-backups.md).

## Model kataloğu ve güncelleme

`docker/models.json` şema 2 model ve workflow kataloğunun tek kaynağıdır. Grup dosyaları, motoru, hedef yol/adı, sabit kaynak revizyonu ve bağımlılıkları burada tanımlanır; iş akışı profilleri `workflows` bölümündedir. Model/workflow ekleme ve katalog doğrulaması için [model rehberine](docs/feature-guides/adding-a-model-and-workflow.md) bakın.

Uygulama güncellemesi `app_update.py` tarafından mevcut imajın içerdiği dosya kümesi ve katalog için yapılır. Dockerfile veya supervisor değişiklikleri canlı uygulama güncellemesi değildir; yeni imaj gerekir. İzin verilen dosya listesi React/statik dosya veya yeni modül eklendiğinde güncellenmelidir.

## Geliştirme

Katkı akışı, mevcut komutlar ve güvenli doğrulama sınırları için [CONTRIBUTING.md](CONTRIBUTING.md) ve [AGENTS.md](AGENTS.md) dosyalarını okuyun. Bu depo belgeleri uygulama davranışını değiştirmez. API/UI özelliği ekleme: [rehber](docs/feature-guides/adding-an-api-ui-feature.md).
