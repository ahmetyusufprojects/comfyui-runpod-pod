# Kalıcı veri ve yedekler

## Bugünkü alanlar

- İmaj varsayılanları: `POD_VOLUME_MOUNT=/workspace`, `POD_MODEL_STORAGE=/models`, `WEBUI_DATA_ROOT=/opt/runtime/user-data`, `POD_ALLOW_EPHEMERAL_DATA=1`.
- Modeller, ComfyUI, cache ve çalışma runtime’ı container diskine konur; Pod/container yeniden yaratmada kalıcı olduğu garanti edilmez.
- Volume bağlanmışsa kalıcılık yalnızca `POD_FLEX_PREFIX` ve RunPod Secret olarak sağlanan `POD_FLEX_ENCRYPTION_KEY` birlikte bulunduğunda açılır. Anahtar olmadan uygulama container diskinde geçici çalışır ve volume'e kayıt yazmaz. RunPod template değişkeninde Secret seçicisini kullanın: `{{ RUNPOD_SECRET_POD_FLEX_ENCRYPTION_KEY }}`. Secret değeri komut satırına, loga veya Git'e konmamalıdır. Bakınız [RunPod template secret kullanımı](https://www.runpod.io/blog/building-runpod-templates-part-2-python-api).
- Volume yoksa uygulama container diskinde ephemeral modda çalışabilir.
- R2 yedek akışı `r2_backups.py` ve `kasa.py` üzerinden şifreli arşiv biçimi kullanır. R2 kimlik bilgileri secret olarak sağlanmalı, loglanmamalıdır.

## Flex şifreleme sınırı


`flex_store.py`, arşivi yalnız container diskindeki geçici dosyalarda hazırlar; hesap kaydından oturumları çıkarır, genel sistem ayarlarını ve GPG ile zaten şifreli kasa dosyalarını ekler, sonra bütün arşivi GPG/AES-256 ile şifreler. Volume'e yalnız `{timestamp}-{plaintext-tar-sha256}.tar.gpg` yazılır. Restore sırasında GPG doğrulaması ve tar özeti kontrol edilmeden hiçbir kayıt açılmaz. Yanlış anahtarla kayıt geri yüklenmez.

Anahtar sağlandığında başlangıçta dar kapsamlı geçiş, seçili Flex prefix'i altındaki yalnız şu bilinen düz kayıtları işler: eski ad biçimindeki `.tar` hesap snapshot'ları ve `system-settings.json` / sürümlü `system-settings-….json` ayarları. Her kayıt önce container diskine alınır, AES-256 ile şifrelenir, şifre çözülerek SHA-256 ile doğrulanır; ancak ardından volume'deki düz kaynak silinir. Bir adım başarısız olursa düz kaynak korunur ve başlangıç hata verir. Şifreli eski kasalar ve `.sure` değerleri yeni şifreli snapshot içinde korunur. Hesap kaydı container diskinde zaten varsa ama genel ayar dosyası yoksa yalnız ayarlar geri alınır; mevcut hesap dosyasının üstüne yazılmaz. Bilinmeyen dosyalar okunmaz, taşınmaz veya silinmez.

Bu geçiş, gerçek volume'ün tamamının şifreli ya da temiz olduğu iddiası değildir. Uygulamanın tanımadığı veya başka bir prefix'te duran eski dosyalar yerinde kalabilir; bu çalışma sırasında canlı volume elle incelenmez. Testleri yalnız sentetik geçici dizinlerde çalıştırın.

## Veri şeması veya yedek değiştirme

1. Kullanıcı verisi, evrensel ayar, kasa, model kayıtları ve medya kapsamını ayrı ayrı tanımla. Dışa aktarma/import sözleşmesini ve sürüm alanını belirt.
2. Hassas tüm parçaların şifreleme sınırını tanımla; yalnız arşiv uzantısına veya GPG ile korunan kasa alt dosyalarına bakıp tüm yedeğin şifreli olduğunu varsayma.
3. Geri yüklemeyi aşamalı ve tekrar denenebilir tasarla: arşiv bir kez alındıktan sonra parola denemeleri tekrar ağ transferi başlatmamalı. Hash/manifest ve geçici alan temizliği ekle.
4. Eski yedeklerle uyumluluğu migration katmanında çöz; yeni imaj kataloğu/varsayılanları eski export tarafından geriye alınmamalı.
5. Sentetik hesap, yanlış/doğru parola, bozuk arşiv, tekrar deneme, eksik medya, volume yok/var ve yeniden başlatma senaryolarıyla doğrula. Gerçek kullanıcı yedeği veya volume verisini kullanma.
