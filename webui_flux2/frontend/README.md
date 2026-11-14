# Yeni WebUI istemcisi

Bu React + TypeScript + Vite başlangıç alanı RunPod imajında paketlenir ve `/?ui=next` adresinde önizlenir. Şimdilik yalnız sağlık/durum ekranı içerir; tam üretim, sohbet, albüm ve kurucu akışları eski arayüzde kalır.

```sh
npm ci
npm run dev:main   # 8189 ekranı; Vite API proxy'si localhost:8189'a gider
npm run dev:setup  # 8192 ekranı; kurucu sağlık API'si localhost:8192'ye gider
npm test
npm run build
```

Ekran, gerçek tarayıcı portu `8189` veya `8192` ise onu seçer. Vite geliştirme sunucusunda gezinme için `?surface=main|setup` parametresi kullanılır. Otomatik kontroller yalnız mock API kullanır; canlı RunPod bağlantısı yapmaz.
