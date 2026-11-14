# WebUI istemcisi

RunPod’da 8189 ana WebUI ve 8192 kurucu ekranı aynı React + TypeScript + Vite uygulamasını kullanır. Yeni arayüz varsayılandır; geçici geri dönüş için `/?ui=legacy` veya 8192 portunda `/?ui=legacy` açılır. Windows portable arayüzü bu geçişten etkilenmez.

`src/features/` her iş alanını ayrı tutar: üretim ve üretim ayarları, prompt stüdyosu, sohbet, galeri/arşiv, kuyruk ve izleme, kullanıcı ayarları ve kurucu. İstekler mevcut JSON sözleşmelerini `src/lib/api/` üzerinden kullanır. API tarafı henüz eski Handler’ı çağıran uyumluluk köprüsüdür; yeni endpoint eklemeden önce eski arayüzdeki payload ve yanıtı inceleyin.

React bundle’ı `Dockerfile.gallery` içindeki Node aşamasında derlenip `/opt/webui_flux2/frontend-dist/` altına kopyalanır. React değişikliği yeni imaj gerektirir; çalışan imajın uygulama içi kaynak güncellemesi bu bundle’ı derlemez. Tam arayüzün görsel ve mobil incelemesi, kullanıcıyla yapılacak toplu değerlendirme için ayrıca bekletiliyor.

```sh
npm ci
npm run dev:main   # 8189 ekranı; Vite API proxy'si localhost:8189'a gider
npm run dev:setup  # 8192 ekranı; kurucu sağlık API'si localhost:8192'ye gider
npm test
npm run build
```

Ekran, gerçek tarayıcı portu `8189` veya `8192` ise onu seçer. Vite geliştirme sunucusunda gezinme için `?surface=main|setup` parametresi kullanılır. Otomatik React testleri sentetik API kullanır; canlı RunPod verisine bağlanmaz. İmaj CI’si ayrıca Linux container açılışı ve API smoke kontrollerini çalıştırır.
