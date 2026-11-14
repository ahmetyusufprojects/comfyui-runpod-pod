# ADR 0003: Container, volume ve yedek sınırları

- Durum: Kabul edildi; yeni Flex snapshot ve eski bilinen kayıt biçimleri için sentetik migration testleri uygulandı. Canlı volume içeriği incelenmedi.
- Karar/hedef: Modeller, runtime, cache ve geçici çalışma dosyaları container diskinde kalır. Kalıcı kullanıcı/genel ayar verisi yalnız açıkça seçilen volume prefix’inde tutulur. R2 dış yedekleri şifreli arşiv olarak aktarılır.
- Karar: `POD_FLEX_PREFIX` ve RunPod Secret olarak `POD_FLEX_ENCRYPTION_KEY` olmadan kalıcılık kapalıdır. Yeni Flex snapshot container diskinde hazırlanıp GPG/AES-256 ile şifrelenir; volume'e yalnız doğrulanan `.tar.gpg` yazılır. Genel ayarlar bağımsız düz JSON yerine şifreli snapshot'ın içindedir.
- Eski kayıt kararı: Anahtar bulunduğunda yalnız tanınan eski `.tar` snapshot'ları ve `system-settings*.json` dosyaları uygulama başlangıcında şifrelenip doğrulandıktan sonra kaldırılır. Bilinmeyen dosyalar olduğu gibi kalır; volume'ün tamamının şifreli olduğu varsayılmaz.
- Sınır: Canlı volume içeriği bu çalışma sırasında elle incelenmez. Secret yoksa Flex'e hiçbir kayıt yazılmaz; container diskinde geçici mod kullanılır.
- Sonuç: Backup formatı şema sürümlü, bütünlük kontrollü, parola denemesini tekrar indirmeden destekleyen ve import sırasında yeni imaj kataloğunu koruyan biçimde evrilmelidir.
