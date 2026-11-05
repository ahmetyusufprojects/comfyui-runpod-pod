"""Üretim sekmesinde görselin üst barın hemen altında olduğunu doğrular."""
import os
import sys
import time
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8199"
KOK = os.path.dirname(os.path.abspath(__file__))
TEST_KULLANICI = "testbar_" + str(int(time.time()))[-6:]
TEST_SIFRE = "Test12345!"
KASA_SIFRE = "TestKasa12345!"

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
        sayfa.fill("#girAd", TEST_KULLANICI)
        sayfa.fill("#girSifre", TEST_SIFRE)
        sayfa.click("#girBtn")
        sayfa.wait_for_timeout(1500)
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
        kasaDurum = sayfa.evaluate("async () => (await fetch('/api/kasa/durum')).json()")
        if not kasaDurum.get("acik"):
            sayfa.evaluate("""async (s) => {
              await fetch('/api/kasa/olustur', {method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({parola: s, parola2: s, risk: true})});
            }""", KASA_SIFRE)
            sayfa.wait_for_timeout(1500)
        print("giris+kasa tamam")

        # Arşive test görseli ekle, sonra üretim sekmesinde göster
        import base64, io
        from PIL import Image
        b = io.BytesIO(); Image.new("RGB", (1024, 1536), (200, 30, 30)).save(b, "PNG")
        gorsel = {"mime": "image/png", "veri": base64.b64encode(b.getvalue()).decode(), "ad": "dikey.png"}
        ice = sayfa.evaluate("""async (g) => {
          const r = await fetch('/api/arsiv/ice-aktar', {method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({gorseller: [g]})});
          return await r.json();
        }""", gorsel)
        print("ice aktarim:", ice)
        hash1 = ice["kayitlar"][0]["hash"] if ice.get("kayitlar") else None
        if not hash1:
            hatalar.append("ice aktarim hash yok")
            raise RuntimeError("hash yok")

        # Üretim sekmesinde görseli göster (galeri sistemini kullanarak)
        sayfa.click('button[data-menu="uretim"]')
        sayfa.wait_for_timeout(1000)
        sayfa.evaluate("""(h) => {
          const bos = document.getElementById('uretimGoruntuBos');
          const stage = document.getElementById('uretimGoruntuStage');
          if(bos) bos.hidden = true;
          if(stage) stage.hidden = false;
          const g = document.getElementById('galeri');
          if(g) g.replaceChildren();
          galeriMedyasiEkle('gorsel', '/api/arsiv/gorsel/' + h, 'test');
        }""", hash1)
        sayfa.wait_for_timeout(1500)

        # Ölçüm: üst bar (favori satırı) altı ile görsel üstü arasındaki boşluk
        olcum = sayfa.evaluate("""() => {
          const ust = document.querySelector('.uretim-ust-overlay');
          const sahne = document.querySelector('.uretim-goruntu-stage');
          const gorsel = document.querySelector('#galeri .uretim-goruntu');
          if(!ust || !sahne || !gorsel) return null;
          const ur = ust.getBoundingClientRect();
          const sr = sahne.getBoundingClientRect();
          const gr = gorsel.getBoundingClientRect();
          return {
            ustAlt: Math.round(ur.bottom),
            sahneUst: Math.round(sr.top),
            gorselUst: Math.round(gr.top),
            gorselAlt: Math.round(gr.bottom),
            bosluk: Math.round(gr.top - ur.bottom)
          };
        }""")
        print("olcum:", olcum)
        if olcum:
            if olcum["bosluk"] > 10:
                hatalar.append("ust bar ile gorsel arasi bosluk %dpx" % olcum["bosluk"])
            if olcum["gorselUst"] < olcum["ustAlt"] - 2:
                hatalar.append("gorsel ust barin altinda/icerisinde")
        else:
            hatalar.append("olcum yapilamadi")

        sayfa.screenshot(path=os.path.join(KOK, "test_bar_uretim.png"))
        print("ekran goruntusu: test_bar_uretim.png")

    except Exception as e:
        hatalar.append("test hatasi: %s" % e)
        try:
            sayfa.screenshot(path=os.path.join(KOK, "test_bar_hata.png"))
        except Exception:
            pass
    finally:
        tarayici.close()

if hatalar:
    print("HATALAR:")
    for h in hatalar:
        print(" -", h)
    sys.exit(1)
print("UST BAR TESTI GECTI")