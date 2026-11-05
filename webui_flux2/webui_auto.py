"""webui_auto.py - localhost:8189 webui_flux2 etkileşim testi scripti.
Tek dosya, stdlib-only (urllib + http.cookiejar), Playwright bağımlılığı yok.

Kullanim:
    python webui_auto.py [kullanici] [sifre] [kasa_parola]

Ornek:
    python webui_auto.py admin TestParola!2026 TestParola!2026
"""
import http.client
import http.cookiejar
import json
import time
import sys

BASE = "127.0.0.1:8189"


class WebUIClient:
    def __init__(self, base="127.0.0.1:8189"):
        self.base = base
        self.cj = http.cookiejar.CookieJar()

    def _conn(self, timeout=10):
        return http.client.HTTPConnection(self.base, timeout=timeout)

    def get(self, path, timeout=10):
        conn = self._conn(timeout)
        conn.request("GET", path, headers={"Cookie": self._cookie_header()})
        r = conn.getresponse()
        data = r.read()
        self._update_cookies(r.getheaders())
        conn.close()
        return r.status, data.decode("utf-8", errors="replace")

    def post(self, path, body=None, timeout=30):
        conn = self._conn(timeout)
        payload = json.dumps(body).encode("utf-8") if body else None
        headers = {"Content-Type": "application/json",
                   "Cookie": self._cookie_header()}
        conn.request("POST", path, body=payload, headers=headers)
        r = conn.getresponse()
        data = r.read()
        self._update_cookies(r.getheaders())
        conn.close()
        return r.status, data.decode("utf-8", errors="replace")

    def _cookie_header(self):
        cookies = []
        for c in self.cj:
            cookies.append(f"{c.name}={c.value}")
        return "; ".join(cookies)

    def _update_cookies(self, headers):
        for h in headers:
            if h[0].lower() == "set-cookie":
                cookie_str = h[1]
                if "=" in cookie_str:
                    name_val = cookie_str.split(";")[0]
                    if "=" in name_val:
                        name, value = name_val.split("=", 1)
                        c = http.cookiejar.Cookie(
                            version=0, name=name, value=value,
                            port=None, port_specified=False,
                            domain="127.0.0.1", domain_specified=True,
                            domain_initial_dot=False, path="/",
                            path_specified=True, secure=False,
                            expires=None, discard=True,
                            comment=None, comment_url=None,
                            rest={"HttpOnly": None},
                            rfc2109=False
                        )
                        self.cj.set_cookie(c)


def durum(client):
    kod, veri = client.get("/api/durum")
    if kod == 200:
        try:
            return json.loads(veri)
        except Exception:
            return {"raw": veri[:200]}
    return {"hata": f"HTTP {kod}"}


def giris(client, kullanici, sifre):
    kod, veri = client.post("/api/giris",
                            {"kullanici": kullanici, "sifre": sifre})
    return kod, veri


def kurulum(client, kullanici, sifre):
    kod, j = client.post("/api/kurulum",
                         {"kullanici": kullanici, "sifre": sifre})
    return kod, j


def kasaya_ac(client, parola):
    kod, veri = client.post("/api/kasa/ac", {"parola": parola})
    return kod, veri


def kuyruga_ekle(client, istek):
    kod, veri = client.post("/api/kuyruk", istek)
    return kod, veri


def is_durum(client, is_id, timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        kod, veri = client.get(f"/api/is/{is_id}", timeout=10)
        if kod == 200:
            try:
                j = json.loads(veri)
                durum = j.get("durum")
                if durum in ("bitti", "hata", "durduruldu"):
                    return j
                if j.get("ilerleme"):
                    print(f"  [{durum}] {j['ilerleme'].get('calisan','')} "
                          f"{j['ilerleme'].get('oran',0)*100:.0f}%")
            except Exception:
                pass
        time.sleep(2)
    return {"hata": "zaman asimi"}


def main():
    print("=== Web UI Etkilesim Testi ===")
    client = WebUIClient()
    d = durum(client)
    print(f"Durum: comfy={d.get('comfy')}, vram_bos={d.get('vram_bos_mb')}MB, "
          f"kuyruk={d.get('kuyruk_bekleyen')}")
    if not d.get("comfy"):
        print("ComfyUI bagli degil!")
        return 1

    # Komut satiri argumanlari
    kullanici = sys.argv[1] if len(sys.argv) > 1 else "admin"
    sifre = sys.argv[2] if len(sys.argv) > 2 else "TestParola!2026"
    kasa_parola = sys.argv[3] if len(sys.argv) > 3 else None

    # Giris
    kod, j = giris(client, kullanici, sifre)
    print(f"Giris: {kod} {j}")
    if kod != 200:
        print("Giris basarisiz. Kurulum yapiliyor...")
        kod, j = kurulum(client, kullanici, sifre)
        print(f"Kurulum: {kod} {j}")
        if kod != 200:
            print("Kurulum basarisiz. Islem durduruluyor.")
            return 1
        kod, j = giris(client, kullanici, sifre)
        print(f"Giris (sonra): {kod} {j}")
        if kod != 200:
            print("Giris basarisiz. Islem durduruluyor.")
            return 1

    # Kasayi ac (vault password gerekli)
    if kasa_parola:
        kod, j = kasaya_ac(client, kasa_parola)
        print(f"Kasa ac: {kod} {j}")
        if kod != 200:
            print("Kasa acilamadi. Gorsel uretimi icin kasa parolasi gerekli.")
            return 1
    else:
        print("Kasa sifresi verilmedi. Kasa acilmiyor.")
        print("Gorsel uretimi icin: python webui_auto.py admin <sifre> <kasa_sifresi>")
        print("Sadece durum ve giris testleri yapilacak.")

    # Basit gorsel uretim testi (sadece kasa aciksa)
    if kasa_parola:
        istek = {
            "prompt": "otomasyon testi: kedi, gun isigi, sinematik",
            "negative": "yazi, filigran",
            "width": 512, "height": 512,
            "max_side": 512, "seed": 42,
            "steps": 4, "cfg": 1.0,
            "sampler": "euler", "model": "flux2", "workflow": "tek"
        }
        kod, j = kuyruga_ekle(client, istek)
        print(f"Kuyruk: {kod} {j}")
        if kod == 200 and j.get("is_id"):
            son = is_durum(client, j["is_id"])
            print(f"Sonuc: {son.get('durum')} {son.get('hata','')}")
            if son.get("sonuc"):
                print(f"Gorsel sayisi: {len(son['sonuc'].get('gorseler') or [])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
