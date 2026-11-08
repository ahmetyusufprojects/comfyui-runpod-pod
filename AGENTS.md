# Codex rehberi: ComfyUI RunPod Pod

Bu dosya, depo içindeki geliştirme ve bakım görevlerinde geçerlidir. Önce etkin dalı, çalışma ağacını ve ilgili kodu incele. Buradaki durum notları 2026-11-08 tarihinde doğrulandı; kod ve katalog her zaman bu metinden daha yetkilidir.

## Dal ve dağıtım temeli

- İnceleme sırasında etkin dal `work`, HEAD `d9ff80f64bc98ac9c08bbf60412350012dcb3e39` idi. Bu SHA, GitHub `main` dalının ucu ile aynıydı. Değişiklikten önce çalışma ağacı temizdi.
- GitHub’da `runpod/gallery-first` adlı ayrı bir dal da var. İnceleme anındaki ucu `3f8858259273019563716971a4ae479f88e7c23c`; etkin `main` ile aynı dal değildir. Bu dalda `docker/models.json`, `docker/Dockerfile.gallery`, `.github/workflows/gallery-image.yml`, `webui_flux2/pod_setup.py`, `webui_flux2/flex_store.py`, `webui_flux2/app_update.py` ve ayrı kurucu arayüzü bulunuyor. Bu kodu `main` içinde varmış gibi kullanma. O dala geçilirse kendi dosyalarını ve güncel HEAD’ini yeniden incele; kaynaklar `runpod_pod/` altında değil, `webui_flux2/` ve `docker/` altındadır.
- Etkin `main` için RunPod Pod şablonu `create-runpod-template.ps1` ile kurulur. Varsayılan imaj `runpod/worker-comfyui:main-base`, konteyner diski 120 GB, kalıcı volume varsayılanı 0 GB ve açılan portlar `8189/http` ile `8188/http` şeklindedir.
- Şablonun başlangıç komutu GitHub `main` arşivinden `docker/bootstrap-runpod.sh` dosyasını indirip çalıştırır. Bu betik uygulama kaynaklarını da `main` arşivinden alır, ComfyUI’yi `/comfyui` altında başlatır, `llama.cpp` CUDA sunucusunu indirir ve WebUI’yi açar. Çalışan Pod yeni imaja kendiliğinden geçmez; Pod yeniden başlatma, ayrı imaj/şablon güncellemesi anlamına gelmez. Canlı Pod’u durdurma veya yeniden başlatma öncesinde kullanıcıdan açık istek olmalı ve çalışan işlerin/verilerin etkisi incelenmelidir.
- `docker/Dockerfile` ile `build-push.ps1`, elle etiketlenip gönderilen özel bir Docker imajı üretme yoludur. Etkin `main` dalında `.github/workflows/` altında GHCR imajı oluşturan GitHub Actions akışı yoktur. Bu nedenle özel imaj akışını RunPod şablonunun varsayılan akışı sanma.

## Sistemin doğrulanmış davranışı

Durum etiketleri etkin `main` dalına göredir:

- **Mevcut:** Ana web arayüzü `webui_flux2/index.html`, `webui_flux2/static/app.js` ve `webui_flux2/static/app.css` içindedir. `webui_flux2/server.py` oturum, API, iş kuyruğu ve kullanıcıya özel verileri yönetir.
- **Mevcut:** Görsel üretim ve düzenleme ComfyUI’ye `webui_flux2/comfy_client.py` ve `webui_flux2/image_graphs.py` üzerinden gider. Model dosya adları ve bazı iş akışları kodda sabittir.
- **Mevcut (kod yolu):** Referans görselden sessiz video üretimi için arayüz, iş doğrulama/kuyruk mantığı, ComfyUI iş akışı ve MP4 geçmişi `webui_flux2/server.py`, `webui_flux2/static/app.js` ve `webui_flux2/video_r2v_api.json` içinde bulunur. `webui_flux2/VIDEO_PLANI.md` eski veya ileriye dönük notlar içerebilir; güncel durumu belirlemek için çalışan kodu esas al. Canlı GPU üzerinde başarılı üretim ayrıca doğrulanmadıkça doğrulanmış sayılmaz.
- **Mevcut:** Prompt çeviri/destek kodu `webui_flux2/prompt_translation.py` ve `webui_flux2/llm.py` içindedir. llama.cpp tabanlı sohbet ve prompt akışları `llm.py` üzerinden yürür. Kullanıcının mevcut JSON/API sözleşmelerini koru.
- **Mevcut:** Hesap, ilk yönetici kurulumu ve giriş `webui_flux2/auth.py` ile `server.py` içindedir. Kasa GPG ile şifrelenir (`webui_flux2/kasa.py`); kullanıcı sohbeti, geçmişi ve kütüphanesi açık kasaya ait kullanıcı dizininde tutulur. Arşiv/kütüphane kodu `webui_flux2/arsiv.py` ve `server.py` içindedir.
- **Kısmen mevcut:** `docker/download_models.py` yalnızca kodda sabitlenmiş `llm`, `flux2` ve `zimage` gruplarını yönetir. WebUI açılışında varsayılan olarak arka planda indirme başlar; `SKIP_MODEL_DOWNLOAD=1` bunu kapatır. Arayüz ilk 60 saniyede indirme sırasını değiştirebilir. Etkin `main` içinde `docker/models.json` kataloğu, bağımsız model kurucu ekranı, modelleri ad/bağlantıyla ekleyip kaldırma veya model envanterini serbestçe yönetme doğrulanmadı. Modelleri esnek yönetme isteği ürün tercihidir; uygulanmış özellik gibi sunma.
- **Etkin `main` içinde yok:** Bağımsız kurucu/indirme ekranı ve `8192/http` hizmeti yoktur. `8189/http` ana WebUI, `8188/http` ComfyUI portudur. `8188` şablonda ayrıca dışarı açılmıştır; bunu kurucu portu sanma.
- **Etkin `main` içinde yok:** `webui_flux2/app_update.py` gibi uygulama içi güncelleme kodu yoktur. Yeni Pod başlangıç betiği kaynak arşivini `main` dalından indirir; çalışan uygulamanın kendi kendini güncellemesi olarak değerlendirme.
- **Kısmen mevcut:** `docker/download_models.py` küçük bir sabit model listesini içerir; bu dosya `docker/models.json` ile eşdeğer, kapsamlı bir katalog değildir. Model veya iş akışı değiştirirken `image_graphs.py`, `server.py`, indirici ve ilgili arayüzü birlikte incele. İsteğe bağlı model yardımcı dosyalarını zorunlu varsayma.
- **Kısmen mevcut:** `create-runpod-template.ps1` volume oluşturmayı varsayılan olarak kapatır; pozitif `-VolumeInGb` verilirse `/workspace/user-data` volume bağlar. `docker/bootstrap-runpod.sh` modelleri `/models` altında, uygulama/ComfyUI’yi konteyner diskinde tutar. Etkin `main` içinde RunPod Flex yedekleme kodu doğrulanmadı. `/workspace/user-data` için mevcut şifreli kasa akışından başka kalıcılık/yedekleme varsayma; yeni volume oluşturma veya volume büyütme.
- **Plan/henüz doğrulanmadı:** Çalışan RunPod Pod üzerinde GPU ile görsel/video üretimi, model indirme erişimi ve canlı dağıtım doğrulaması bu depo incelemesinde yapılmadı. README veya test komutları tek başına canlı doğrulama kanıtı değildir.

## Etkin `main` için başlıca kaynaklar

- `webui_flux2/index.html`, `webui_flux2/static/app.js`, `webui_flux2/static/app.css`: ana arayüz.
- `webui_flux2/server.py`: API, kimlik doğrulama sınırları, kullanıcı işleri/kuyruğu, sohbet, geçmiş, albüm ve kurulum davranışı.
- `webui_flux2/image_graphs.py`, `webui_flux2/comfy_client.py`, `webui_flux2/video_r2v_api.json`: ComfyUI iş akışları ve istemcisi.
- `webui_flux2/llm.py`, `webui_flux2/prompt_translation.py`: llama.cpp, sohbet ve prompt desteği.
- `webui_flux2/auth.py`, `webui_flux2/kasa.py`, `webui_flux2/arsiv.py`: hesap, şifreli kasa ve arşiv/kütüphane.
- `docker/download_models.py`, `docker/start.sh`, `docker/bootstrap-runpod.sh`, `docker/Dockerfile`: indirme, başlangıç, RunPod önyükleme ve özel imaj.
- `create-runpod-template.ps1`, `build-push.ps1`, `README.md`: şablon ve elle imaj oluşturma talimatları.

Bir görev `runpod/gallery-first` dalında çalışıyorsa ayrıca `docker/models.json` şema 2 kataloğunu incele. Model grupları ile bunları kullanan `workflows` kayıtlarını beraber güncelle; `optional` dosyaları her iş akışı için zorunlu sayma. Bu dala ait kurucu `webui_flux2/pod_setup.py`, kurucu arayüzü `webui_flux2/setup.html` ve `webui_flux2/static/setup.js`/`setup.css`, kalıcılık `webui_flux2/flex_store.py`, uygulama güncelleme `webui_flux2/app_update.py`, imaj ise `docker/Dockerfile.gallery` ve `.github/workflows/gallery-image.yml` içindedir. Bu yolları etkin `main` için geçerliymiş gibi kullanma.

## Kullanıcının ürün tercihleri

- Model ekleme, kaldırma, ad/bağlantı düzenleme ve aynı türden birden fazla model seçme esnekliği hedefleniyor. Bu tercih, mevcut kodda destek bulunduğu anlamına gelmez.
- Model seçiminde kullanıcı denetimini koru; kodla doğrulanmayan otomatik model düşürme veya sessiz model değiştirme ekleme.
- Prompt çıktılarının JSON yapısını ve mevcut API sözleşmelerini koru.
- Arayüz sade, kompakt ve anlaşılır olsun; kullanıcıya Türkçe ve az teknik açıklama göster.
- RunPod dağıtımı önceliklidir. Bu depoda olmayan yerel Windows portable kurulumunu veya dosyalarını değiştirdiğini iddia etme; gerekirse Windows kaynak kopyası kullanıcıdan ayrıca sağlanmalıdır.

## Güvenlik ve çalışma kuralları

- Yönetici hesabı, gerçek kullanıcı hesapları, kasalar, sohbet geçmişleri, kullanıcı dosyaları, ayarlar ve canlı Pod günlükleri üzerinde inceleme/teşhis yapma. Kod incelemesi yeterlidir. Test gerekiyorsa ayrı, geçici `WEBUI_DATA_ROOT`, `MODEL_ROOT` ve `GPG_HOME` kullan; yalnızca sentetik kullanıcı ve sahte içerik oluştur.
- `webui_flux2/test_akis.py`, `test_bar.py` ve `test_sunucu.py` gibi testleri çalıştırmadan önce hangi dosya/dizinlere yazdıklarını incele. Mevcut test verilerine veya kasa dosyalarına yönlendirme yapma.
- Parola, anahtar, oturum çerezi veya özel erişim bağlantısını kaynak koda, AGENTS.md’ye, loglara ya da Git’e yazma. Gerekli gizli değerleri yalnızca Cloud/Pod secret ayarlarında kullan; değerlerini kabuk çıktısına dökme.
- Kullanıcı istemedikçe RunPod Pod’u oluşturma, durdurma, yeniden başlatma veya canlı sürümünü değiştirme. GPU testi istendiğinde uygun düşük maliyetli tek GPU seç ve sınırlı bir iş çalıştır; uzun video üretme.
- Network Volume oluşturma veya büyütme. Depodaki şifreli kasa akışının dışına yeni kalıcılık yolu ekleme; ayrı tasarım isteği olmadan veri yerleşimini değiştirme.
- Uygulama değişikliklerinde arayüz ve ilgili API’yi birlikte incele. Değişikliğe uygun Python/JavaScript kontrollerini çalıştır. Etkin `main` dalında GitHub Actions akışı yoktur; Actions sonucu varmış gibi raporlama. Başka dalda çalışırken o dalın güncel CI yapılandırmasını ayrıca doğrula.
- Son raporda değişen dosyaları, gerçekten çalıştırılan kontrolleri ve canlı ortamda doğrulanmamış noktaları belirt.
