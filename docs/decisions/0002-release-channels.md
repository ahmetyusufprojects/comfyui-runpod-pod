# ADR 0002: İmaj kanalları ve yayın sınırı

- Durum: Mevcut davranış.
- Karar: Her başarılı GitHub Actions derlemesi `gallery-<commit>`, `latest` ve `beta` etiketlerini yayınlar. `stable`, yalnız açık promosyon veya `docker/stable-image.txt` ile seçilen imaja taşınır.
- Sonuç: Yeni GHCR imajı açık Pod’u veya My Templates kaydını değiştirmez. Template imaj etiketi kendi başına güncellenmelidir. `pod-launch.py` varsayılanı beta; stable seçimi açıkça yapılır.
- Güvenlik/işletim: Secret’lar Git dosyalarında tutulmaz. Stable promosyonu ve canlı Pod/template güncellemesi ayrı yetkili işlemdir; sıradan kaynak değişikliği bunları yapmaz.
