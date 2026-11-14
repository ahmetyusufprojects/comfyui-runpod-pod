# ADR 0001: FastAPI ve React’e kademeli geçiş

- Durum: Kabul edildi; geçişin ilk dilimi beta kaynakta uygulandı.
- Karar: RunPod 8189/8192 artık FastAPI/Uvicorn ASGI ön yüzü kullanır. İki sağlık uç noktası native router'dadır; diğer mevcut Handler yolları geçici adaptörle korunur. React + TypeScript + Vite + Tailwind + Lucide durum ekranı önizleme olarak paketlenir. Tam üretim/sohbet/kurucu UI taşıması sonraki dilimlerdir.
- Gerekçe: Mevcut `server.py` ve büyük JS dosyalarının sorumlulukları büyümüş; UI bileşenleri, API şemaları ve erişim durumları daha test edilebilir olmalı.
- Değişmezler: JSON API sözleşmesi ve kullanıcı verisi korunur; 8189 ana uygulaması ile 8192 bağımsız kurucu davranışı kalır. Kuyruk ilerleme/iptal, auth, log, yedekleme ve mobil kullanım parity’si sağlanmadan eski UI kaldırılmaz.
- Geçiş: API bridge ve health contract testleri mevcut. UI komponent/API testleri Vitest ile; full route taşıması FastAPI sözleşme testleriyle; son UI akışları Playwright ile ayrı aşamalarda yapılmalıdır. Kullanılmayan eski UI ancak özellik parity'si kanıtlandıktan sonra kaldırılabilir.
- Sahip: Ana ajan mimari ve entegrasyon; alt ajanlar disjoint görevlerde Luna High (sözleşme/mimari), Luna Medium (izole ekran/test/doküman) olarak çalışabilir.
