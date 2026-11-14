# ADR 0001: FastAPI ve React’e kademeli geçiş

- Durum: Kabul edildi; geçişin ilk dilimi beta kaynakta uygulandı.
- Karar: RunPod 8189/8192 FastAPI/Uvicorn ASGI ön yüzü kullanır. İki sağlık uç noktası native router'dadır; route aileleri `api/legacy_*_routes.py` dosyalarına ayrılmış olsa da mevcut Handler sözleşmesi geçici adaptörle korunur. İş kuralları `services/`, `kasa_modules/`, `llm_runtime/` ve `pod_setup_modules/` paketlerine taşınmıştır; eski modüller uyumluluk facade'ıdır. React + TypeScript + Vite + Tailwind + Lucide üretim ekranının ilk dilimini sunar; sohbet, prompt stüdyosu, arşiv ve kurucu akışlarının taşınması sonraki dilimlerdir.
- Gerekçe: Mevcut `server.py` ve büyük JS dosyalarının sorumlulukları büyümüş; UI bileşenleri, API şemaları ve erişim durumları daha test edilebilir olmalı.
- Değişmezler: JSON API sözleşmesi ve kullanıcı verisi korunur; 8189 ana uygulaması ile 8192 bağımsız kurucu davranışı kalır. Kuyruk ilerleme/iptal, auth, log, yedekleme ve mobil kullanım parity’si sağlanmadan eski UI kaldırılmaz.
- Geçiş: API bridge, ana/kurucu route adapter'ları, servis modülleri ve üretim UI için sentetik testler vardır. UI komponent/API testleri Vitest ile; full route taşıması FastAPI sözleşme testleriyle; mobil ve kullanıcı akışları tarayıcı/Playwright ile sonraki aşamalarda yapılmalıdır. Kullanılmayan eski UI ancak özellik parity'si kanıtlandıktan sonra kaldırılabilir.
- Sahip: Ana ajan mimari ve entegrasyon; alt ajanlar disjoint görevlerde Luna High (sözleşme/mimari), Luna Medium (izole ekran/test/doküman) olarak çalışabilir.
