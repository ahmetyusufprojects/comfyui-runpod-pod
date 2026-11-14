# Model ve workflow ekleme

## Katalog kaynağı

`docker/models.json` şema 2 tek doğruluk kaynağıdır. Model grupları engine, file listesi, hedef dizin/ad, sürüm/revizyon ve gerekirse `depends_on` taşır. Dosya kaynağı HF (sabit revision), Civitai (version ID) veya SHA-256 zorunlu HTTPS URL olabilir. Workflow profilleri aynı dosyanın `workflows` bölümündedir.

## Değişiklik adımları

1. Yeni grup için mevcut şemadaki bir grubu örnek al; `enabled`, engine, indirme dosyaları, hedef dizin, boyut/hash/revision ve bağımlılıkları doğru tanımla. Token/secret değeri ekleme.
2. Dosya adını kullanan workflow/loader kodunu bul. Model adlarını ayrıca sabitleyip katalogla çelişki yaratma. Workflow’un loader sınıfı, sampler/steps, CFG, çözünürlük profili ve isteğe bağlı bileşenlerini birlikte doğrula.
3. Aynı runtime dosyasını kullanan diğer modelleri koru. Model değişikliği, eski model verilerini veya mevcut kullanıcı indirmelerini silme anlamına gelmez.
4. Yeni graph JSON/Python modülü varsa Docker açık `COPY` listesine ve app update kapsamına ekle.
5. `.github/workflows/gallery-image.yml` içindeki katalog kontrolleri/smoke senaryolarını yeni davranışla güncelle.

## Doğrulama sınırları

CI’da katalog parse/şema beklentileri ve graph yapısı kontrolleri bulunur. Model gerçek üretimi GPU gerektirir; katalog/build testi gerçek modelin indirildiği veya çıktının doğrulandığı anlamına gelmez. İndirme testi gerekiyorsa kullanıcı hesabı değil, izinli public veya ayrı sentetik ortam kullanılmalı; görev açıkça istemedikçe pahalı GPU açılmamalıdır.

## React kurucuya geçiş planı

Gelecekte model kartı, dosya bileşenleri ve workflow profili UI’dan düzenlenebilir olabilir. Bu, katalog şemasını istemci tarafında serbestçe değiştirmek anlamına gelmemeli: API doğrulaması, revision/hash kontrolü, default’a dönme ve UI/API şema sürümü gerekir. Özellik mevcut kaynakta yoktur.
