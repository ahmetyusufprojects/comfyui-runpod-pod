"""Arşiv/kütüphane 'alt alta' görünüm testi (Playwright, izinli).
Yeni test kullanıcısı oluşturur, arşiv sekmesinde akış görünümünü kontrol eder."""
import os
import sys
import time
import json
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8199"
KOK = os.path.dirname(os.path.abspath(__file__))
TEST_KULLANICI = "testakis_" + str(int(time.time()))[-6:]
TEST_SIFRE = "Test12345!"
KASA_SIFRE = "TestKasa12345!"

# Test kullanıcısını sunucu API'siyle oluştur (gerçek kullanıcılara dokunma)
import auth
store = auth.AuthStore(os.path.join(KOK, "kullanicilar.json"))
try:
    store.kullanici_ekle(TEST_KULLANICI, TEST_SIFRE, rol="uye")
    print("test kullanici:", TEST_KULLANICI)
except Exception as e:
    print("kullanici ekleme:", e)

hatalar = []
with sync_playwright() as p:
    tarayici = p.chromium.launch(headless=True)
    sayfa = tarayici.new_page(viewport={"width": 1280, "height": 800})
    try:
        sayfa.goto(BASE, wait_until="domcontentloaded", timeout=30000)
        sayfa.wait_for_selector("#girAd", timeout=15000)
        # Giriş
        sayfa.fill("#girAd", TEST_KULLANICI)
        sayfa.fill("#girSifre", TEST_SIFRE)
        sayfa.click("#girBtn")
        sayfa.wait_for_timeout(1500)
        # Kasa kur
        if sayfa.is_visible("#kasaKurKart"):
            sayfa.fill("#kasaKur1", KASA_SIFRE)
            sayfa.fill("#kasaKur2", KASA_SIFRE)
            sayfa.check("#kasaRisk")
            sayfa.click("#kasaKurBtn")
            sayfa.wait_for_timeout(2500)
        elif sayfa.is_visible("#kasaAcKart"):
            sayfa.fill("#kasaParola", KASA_SIFRE)
            sayfa.click("#kasaAcBtn")
            sayfa.wait_for_timeout(2500)
        # Kasa hala kapaliysa API ile kur
        kasaDurum = sayfa.evaluate("""async () => {
          const r = await fetch('/api/kasa/durum');
          return await r.json();
        }""")
        print("kasa durum:", kasaDurum)
        if not kasaDurum.get("acik"):
            if kasaDurum.get("kasa_var"):
                sayfa.evaluate("""async (s) => {
                  await fetch('/api/kasa/ac', {method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({parola: s})});
                }""", KASA_SIFRE)
            else:
                sayfa.evaluate("""async (s) => {
                  await fetch('/api/kasa/olustur', {method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({parola: s, parola2: s, risk: true})});
                }""", KASA_SIFRE)
            sayfa.wait_for_timeout(1500)
        print("giris+kasa tamam")

        # Arşive test görselleri içe aktar (farklı en-boy oranları)
        import base64, io
        from PIL import Image
        def png(w, h, renk):
            b = io.BytesIO(); Image.new("RGB", (w, h), renk).save(b, "PNG")
            return base64.b64encode(b.getvalue()).decode()
        gorseller = [
            {"mime": "image/png", "veri": png(1024, 1536, (200, 30, 30)), "ad": "dikey.png"},
            {"mime": "image/png", "veri": png(1536, 1024, (30, 200, 30)), "ad": "yatay.png"},
            {"mime": "image/png", "veri": png(1024, 1024, (30, 30, 200)), "ad": "kare.png"},
        ]
        sonuc = sayfa.evaluate("""async (gorseller) => {
          const r = await fetch('/api/arsiv/ice-aktar', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({gorseller})
          });
          return await r.json();
        }""", gorseller)
        print("ice aktarim:", sonuc)

        # Arşiv sekmesine geç
        sayfa.click('button[data-menu="arsiv"]')
        sayfa.wait_for_timeout(1500)

        # Akış görünümüne geç
        sayfa.click("#arsivAkisBtn")
        sayfa.wait_for_timeout(800)

        # Kontrol: her .lib-kart-gorsel yüksekliği ~akış-gorsel-y kadar olmalı
        olcum = sayfa.evaluate("""() => {
          const kok = getComputedStyle(document.documentElement)
            .getPropertyValue('--akis-gorsel-y').trim();
          const kartlar = Array.from(document.querySelectorAll('#arsivIzgara.akis .lib-kart-gorsel'));
          return {
            akisY: kok,
            kartSayisi: kartlar.length,
            kartlar: kartlar.slice(0, 6).map(k => {
              const r = k.getBoundingClientRect();
              return {w: Math.round(r.width), h: Math.round(r.height)};
            })
          };
        }""")
        print("olcum:", json.dumps(olcum, ensure_ascii=False))

        # Doğrulama: yükseklikler ~akış-gorsel-y'ye yakın olmalı (en fazla %15 fark)
        hedef = 800 - 84  # viewport yüksekliği - üst bar
        for i, k in enumerate(olcum["kartlar"]):
            if abs(k["h"] - hedef) > hedef * 0.15:
                hatalar.append("kart %d yukseklik %d, hedef ~%d" % (i, k["h"], hedef))
            if k["w"] <= 0:
                hatalar.append("kart %d genislik 0" % i)

        # Bilgi görselin altında ve görselden yer çalmamalı
        bilgi = sayfa.evaluate("""() => {
          const g = document.querySelector('#arsivIzgara.akis .lib-kart-gorsel');
          const b = document.querySelector('#arsivIzgara.akis .lib-kart-bilgi');
          if(!g || !b) return null;
          const gr = g.getBoundingClientRect(), br = b.getBoundingClientRect();
          return {gorselAlt: Math.round(gr.bottom), bilgiUst: Math.round(br.top),
                  bilgiYukseklik: Math.round(br.height)};
        }""")
        print("bilgi:", bilgi)
        if bilgi and bilgi["bilgiUst"] < bilgi["gorselAlt"] - 2:
            hatalar.append("bilgi gorselin ustunde/icerisinde")

        sayfa.screenshot(path=os.path.join(KOK, "test_akis_arsiv.png"), full_page=False)
        print("ekran goruntusu: test_akis_arsiv.png")

        # Kütüphane akışını da kontrol et
        sayfa.click('button[data-menu="kutuphane"]')
        sayfa.wait_for_timeout(1200)
        sayfa.click("#libAkisBtn")
        sayfa.wait_for_timeout(800)
        sayfa.screenshot(path=os.path.join(KOK, "test_akis_kutuphane.png"), full_page=False)
        print("ekran goruntusu: test_akis_kutuphane.png")

    except Exception as e:
        hatalar.append("test hatasi: %s" % e)
        try:
            sayfa.screenshot(path=os.path.join(KOK, "test_akis_hata.png"))
        except Exception:
            pass
    finally:
        tarayici.close()

if hatalar:
    print("HATALAR:")
    for h in hatalar:
        print(" -", h)
    sys.exit(1)
print("AKIS TESTI GECTI")