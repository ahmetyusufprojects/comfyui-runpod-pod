# VIDEO PLANI — MiniMax H3 referans-video (r2v), çok kullanıcılı, izole veri

Aşağıdaki metni yeni bir oturumda asistana ver. Bu bir PLANLAMA + UYGULAMA dosyasıdır:
önce 2. bölümü uygula, sonra 4. bölümü sırayla bitir, her maddede 6. bölümün testlerini yap.

## 0. AMAÇ (3 kural, pazarlıksız)

1. Video üretimi mevcut kullanıcı sistemine uyar: iki kullanıcı birbirinin videosunu GÖREMEZ;
   admin İÇERİK göremez (yalnız kullanıcı yönetimi + kota/bilgi).
2. Hiçbir şey bilgisayarın dışına ÇIKMAZ: yeni bağımlılık, harici servis, telemetri yok.
3. Görüntü hattı BOZULMAZ: mevcut saf-üretim + düzenleme akışı aynen çalışır.

## 1. MEVCUT DURUM (değiştirmeden önce oku, bozma)

Portable kök:
`C:\Users\Ahmet Yusuf AYDIN\Desktop\ComfyUI_windows_portable_nvidia\ComfyUI_windows_portable`

- Web arayüz: `...\webui_flux2\server.py` + `index.html` (çok kullanıcılı, `veriler/<ad>/` izolasyonlu).
  `server.py` içinde video şeması HAZIR ama üretim kapalı: `IS_TIPI_VIDEO = "video-uretim"`,
  `is_tipini_belirle` video isteğini ayırır, `_is_dogrula` + `_isi_calistir` temiz hata döner,
  arayüzde `VIDEO_HAZIRLIK = {suresn: 0, fps: 0}` taşınır.
- Video workflow (UI-format, API-format DEĞİL):
  `...\ComfyUI\user\default\workflows\video_minimax_h3_r2v.json` (29 düğüm, 33 bağlantı).
  Merkez düğüm: `MiniMaxH3ReferenceToVideo` (widgets: `["", 1344, 768, 124, "match"]`
  = prompt, genişlik, yükseklik, kare-sayısı, mod). Yanında: `CreateVideo [24, 8, ...]`
  (24 kare/sn?), `SaveVideo ["video/MiniMax_H3", ...]`, `ResolutionSelector`,
  `KSamplerSelect ["res_multistep"]`, `BasicScheduler ["simple", 20, 1]`,
  `VAEDecode` + `VAEDecodeAudio`, 2x `LoadImage` (referans kareler), 2x `VAELoader`,
  `LoraLoaderModelOnly`, `BasicGuider`, `SamplerCustomAdvanced`, `RandomNoise`.
- Diskteki modeller (`...\ComfyUI\models\`):
  - `diffusion_models/minimaxH3_comfy_3086408.safetensors` (~21 GB)
  - `vae/minimax_h3_video_vae_fp16.safetensors` + `minimax_h3_video_vae_int8_convrot.safetensors`
  - `vae/minimax_h3_audio_vae_fp32.safetensors`
  - `text_encoders/qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors` (~15,7 GB)
  - `text_encoders/qwen_3_8b_fp8mixed.safetensors` (görüntü hattınınki)
  - `loras/minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors` (~2 GB)
- UYARI — isim uyumsuzluğu (uygulamadan ÖNCE çözülür, 2. bölüm):
  workflow dosyası diskte OLMAYAN adlar ister:
  `minimax_h3_ref2va_pruned_int8_convrot.safetensors` (UNET),
  `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` (CLIP),
  `minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors` (LoRA).
  21 GB dosyaları YENİDEN ADLANDIRMA (indirme/bozulma riski); onun yerine API şablonunda
  diskteki gerçek adlar kullanılır. Şablon dosyadaki adlarla birebir çalışırsa o adlar
  tercih edilir — karar 2. bölümde verilir, tek yerde not düşülür.
- Ses: workflow'da ses hattı var (`<Audio 1>` + audio VAE). Faz 1'de SES YOK
  (sessiz video); ses fazlalar listesine kalır, ilk kabulde aranmaz.

## 2. ÖNCE YAP (oturum başında)

1. `8188` ve `8189` dinleyicilerini kontrol et. Çalışan `server.py` varsa ÖLDÜRME, ikinci
   sunucu açma. ComfyUI çalışmıyorsa video duman testi yapılamaz — bunu rapora yaz,
   dosya-tabanlı adımlara devam et.
2. `video_minimax_h3_r2v.json`'u ComfyUI'de aç (gerekirse canvas üzerinden),
   `MiniMaxH3ReferenceToVideo` düğümünün 5 widget'ının anlamını düğüm kaynağında doğrula
   (tahminle değil: prompt/w/h/kare/mod sırası). Sonucu 3. bölüme not düş.
3. Model adı kararını ver (1. bölümdeki uyarı): şablon diskteki adları kullanır.
   Diskte olmayan ad için dosya ARAMA/İNDİRME — internetten model indirmek bu planda YOK.
4. `server.py` içinde `IS_TIPI_VIDEO`, `_is_dogrula`, `_isi_calistir` video dallarını ve
   `video_parametreleri` fonksiyonunu baştan oku. Görüntü hattına dokunmadan çalışılacağını
   teyit et.

## 3. MİMARİ KARARLAR (tartışma yok, aynen uygula)

- Şablon: `webui_flux2/video_r2v_api.json` — workflow'un ComfyUI canvas'ından
  "Save (API Format)" ile alınmış hali, SADECE bu dosya düzenlenir (orijinal UI workflow'una
  DOKUNMA). Düğüm kimlikleri sabitlenir; parametreler `{prompt, ref1, ref2, width, height,
  frames, seed}` yer-tutucularla verilir. Yeni video tipi eklenirse ikinci şablon dosyası
  açılır, mevcut şablon değişmez.
- Üretim fonksiyonu: `_video_uret(job, req, ...)` — `_isi_calistir` içindeki video dalı
  buraya çağrır; görüntü gövdesi aynen kalır. Adımlar: referansları ComfyUI'ye yükle
  (`upload_image_to_comfy` YENİDEN KULLANILIR), şablonu doldur, `/prompt` gönder,
  `wait_result` ile bekle (mevcut), çıktıyı `/view` ile çek (mp4), `fetch_view_image`
  uzantısı: video dosyası da okunabilir (aynı endpoint, `type=output`).
- Kayıt: `veriler/<ad>/gecmis/` altına `.mp4` + aynı adlı `.json` (meta: prompt, seed,
  width, height, frames/suresn/fps, mod=`"referans-video"`, `is_tipi="video-uretim"`).
  Referans fotoğraflar görüntü hattındaki gibi `kutuphane/`ye eklenir (aynı fonksiyon).
- Geçmiş yardımcıları genelleştirilir, ÇOĞALTILMAZ: `gecmise_kaydet` uzantıyı blob'dan
  değil çağrıdan alır (`video=True` bayrağı); `gecmis_listesi` mp4'ü `"<video>"` ile
  sunulacak `url` ile döner; `_guvenli_dosya_adi` zaten `.mp4`'e izin verir
  (regex'e dokunma). Budama/kota/silme mantığı dosyaya değil kayda bakar — değişmez.
- Kota: mevcut `_kota_kontrol` bayt-tabanlıdır, videoda da çalışır (mp4 baytı dahildir).
  Video varsayılan kotası konmaz (0=sınırsız); admin isterse kullanıcı kartından kısar.
- İşler: tek worker aynen kalır (21 GB model + video VAE; paralel ASLA). `sahip` etiketi,
  filtreler, iptal kuralları görüntüyle aynıdır. `/api/ilerleme` video işinde de
  `_aktif["sahip"]` kapsamındadır.
- Parametre sınırları (kullanıcıyı reddetmeden, en küçük düzeltmeyle):
  `suresn` 1-10 sn, `fps` 8/12/24 (şablon dışı değer en yakına yuvarlanır),
  çözünürlük en uzun kenar 1344 (şablonun 1344x768 varsayılanı; 16'nın katı),
  kare sayısı = suresn*fps (şablonun 124 varsayılanıyla tutarlı aralık).
- Arayüz: mevcut kartlar korunur; "Video" bölümü (süre/fps/çözünürlük + Üret) ve
  `<video controls>` sonuç/geçmiş oynatıcı eklenir. `isTipiSec` referans+video-modu
  seçerse `'video-uretim'` döner; `son_is` 404 sessizliği aynen geçerlidir.

## 4. YAPILACAKLAR (sırayla; atlama yok)

### A. Şablon + model eşleşmesi (arayüze dokunmadan, ComfyUI ile test edilir)
- UI workflow'undan API-format şablon çıkarılır → `webui_flux2/video_r2v_api.json`.
  Diskteki GERÇEK model/LoRA/VAE adları yazılır (1. bölümdeki 3 uyumsuz ad çözülür).
- ComfyUI canvas'ında şablonun elle doldurulmuş hali BİR kez çalıştırılıp mp4 alınır
  (şablonun sağlamlık kanıtı; dosya adı rapora yazılır).

### B. Sunucu: video üretim gövdesi
- `_video_uret(job, ...)`: şablon doldurma (prompt kaçışsız — API JSON'udur, metin
  düğüme aynen konur), referans yükleme, `/prompt`, `wait_result`, `/view` ile mp4,
  kota yazım-öncesi kontrolü (aşım → iş `hata: "kota doldu"`, yarım dosya yok),
  `kutuphane/` + `gecmis/` (mp4+json) yazımı, `image_base64` bellekten atılır (mevcut
  `finally` aynen çalışır).
- `_is_dogrula` video dalı AÇILIR (şu anki ret kalkar); geçersiz video isteği temiz 400.

### C. Geçmiş/kütüphane genellemesi
- `gecmise_kaydet(..., video=...)`, `gecmis_listesi` (mp4 url + `tur: "video"` alanı),
  dosya sunumu (`/gecmis/<ad>.mp4`) oturum-kapsamlı (mevcut guard'lar; yeni guard YOK,
  mevcut guard'lar parametre alır).
- Kayıt sayımı `.json` üzerinden aynen devam eder (mp4 ayrıca sayılmaz).

### D. Arayüz (mobil-öncelikli, Türkçe, mevcut kartlar korunur)
- Video kartı: süre (1-10 sn), fps (8/12/24), çözünürlük bilgisi; Üret video işi açar,
  ilerleme mevcut barda izlenir.
- Sonuç + geçmişte `<video controls preload="none">` (otomatik oynatma YOK, kota dostu).
- Admin paneline dokunma (içerik önizleme yasağı videoda da geçerli).

### E. Sertleştirme
- `Secure` hariç çerez bayrakları aynen; video istekleri de Origin perdesinden geçer
  (mevcut `do_POST` başı — yeni kod YOK, kapsama alınır).
- 21 GB model VRAM notu rapora yazılır (düşük VRAM'de kuyrukta bekleme normaldir).

### F. Kota + temizlik
- Dolan kullanıcıya `{"hata":"kota doldu"}`; admin artırınca devam (mevcut akış).
- `gecmis_limiti` budaması video kayıtlarını da kapsar (kayıt-tabanlı, kod değişmezse
  dokunma).

## 5. KURALLAR

- ComfyUI çekirdeğine, modellere, UI workflow dosyasına, 8188 bayraklarına DOKUNMA.
- Yeni bağımlılık YOK. İnternete istek YOK (model indirme dahil).
- Değişiklikler küçük ve katmanlı: video gövdesi `_video_uret` içinde, şablon ayrı
  dosyada; görüntü hattı ve auth/izolasyon kodu etkilenmez.
- Her özellikten sonra: `py_compile` + ilgili endpoint'e `curl` duman testi.
  Sunucu yeniden başlatılacaksa eski süreci `CommandLine -like '*server.py*'` ile bulup
  ÖNCE öldür, tek süreç başlat, portu doğrula.
- Kısa, teknik Türkçe yaz. Bitince telefondan denenecek test listesi ver.

## 6. TEST PLANI (curl; PowerShell'de `-d` yerine `--data @dosya` kullan)

- Şablon kanıtı: ComfyUI'de elle doldurulan şablon mp4 üretir (dosya adı raporda).
- `POST /api/kuyruk` `{is_tipi:"video-uretim", ...}` oturumlu → `is_id` döner;
  anonimsiz → 401. Geçersiz süre (`suresn: 99`) → 400 veya en-yakına yuvarlama (karar
  3. bölümde sabitlenir, test ona göre).
- ComfyUI açıkken: video işi `bitti` → `veriler/<ad>/gecmis/` altında `.mp4` + `.json`
  oluşur; `/gecmis/<ad>.mp4` oturum sahibine 200, başkasına 404, anomime 401.
- İş izolasyonu: A üretirken B'nin `/api/isler`'ı BOŞ, B A'nın `is_id`sini sorunca 404.
- Kota: kota_kayit dolu kullanıcıda video kuyruk isteği temiz 400, yarım dosya yok.
- Gerileme: bir görüntü üretimi uçtan uca çalışır (video kodu görüntü hattını kırmadı).

## 7. KABUL KRİTERLERİ

- Tüm 6. bölüm testleri geçer; `py_compile` temiz; tek sunucu süreci (8189).
- Video kayıtları kullanıcı-klasöründe ayrı; çapraz okuma her yolda 404/401.
- Görüntü hattında davranış değişikliği yok; worker hâlâ TEK.
- Telefonda giriş → video üretim → oynatma → çıkış akışı 4 maddelik listeyle doğrulanır.

## 8. FAZ DIŞI (bu planda YAPILMAZ, not düşülür)

- Sesli video (`<Audio 1>` hattı, audio VAE): ayrı plan.
- Metinden-video (referanssız) ve çoklu-sahne: ayrı plan.
- Model indirme/değişimi, UI workflow dosyasının düzenlenmesi.
