"""web_browser.py - Metin tabanli sanal tarayici.
HTML'i parse eder ve buton tiklamalarini API cagrilarina donusturur.
Webui_flux2 SPA'si icin buton-API eslemesi icerir.
Tek dosya, stdlib-only."""
import http.client
import http.cookiejar
import re
import html as html_module
import json

BASE = "127.0.0.1:8189"


def _turkce_duzelt(metin):
    """Turkce karakterleri ASCII'ye cevir (karsilastirma icin)."""
    harf_map = {
        'ı': 'i', 'İ': 'I', 'ş': 's', 'Ş': 'S', 'ğ': 'g', 'Ğ': 'G',
        'ü': 'u', 'Ü': 'U', 'ö': 'o', 'Ö': 'O', 'ç': 'c', 'Ç': 'C'
    }
    for tr, en in harf_map.items():
        metin = metin.replace(tr, en)
    return metin.lower()


class WebBrowser:
    """Metin tabanli sanal tarayici - HTML parse + API etkilesimi."""

    def __init__(self, base=BASE):
        self.base = base
        self.cj = http.cookiejar.CookieJar()
        self.current_url = "/"
        self.current_html = ""
        self.forms = []
        self.links = []
        self.buttons = []
        self.inputs = []

        # Buton metni -> API endpoint eslemesi
        self.button_api_map = {
            "giris yap": ("POST", "/api/giris", ["girAd", "girSifre"]),
            "kur ve basla": ("POST", "/api/kurulum", ["kurAd", "kurSifre"]),
            "kasayi ac": ("POST", "/api/kasa/ac", ["kasaParola"]),
            "kasayi kur": ("POST", "/api/kasa/olustur", ["kasaKur1", "kasaKur2", "kasaRisk"]),
            "kurtar ve ac": ("POST", "/api/kasa/kurtar", ["kasaKurtarParola"]),
            "sifreyi degistir": ("POST", "/api/sifre-degistir", ["sfrEski", "sfrYeni"]),
            "uret": ("POST", "/api/kuyruk", ["prompt", "negatif", "w", "h", "steps", "cfg", "seed", "sampler", "image_base64", "image2_base64"]),
            "videouret": ("POST", "/api/kuyruk", ["image_base64", "vsure", "vfps", "vmax"]),
            "gorev durdur": ("POST", "/api/durdur", []),
            "prompt uret": ("POST", "/api/prompt-uret", ["mod", "metin", "dil", "yaraticilik", "uzunluk", "gorseller"]),
            "modeli hazirla": ("POST", "/api/llm/hazirla", []),
            "gonder": ("POST", "/api/sohbet", ["mesajlar", "yaraticilik", "max_tokens"]),
            "temizle": ("POST", "/api/sohbet/kaydet", []),  # sohbet temizle/kaydet karisikligi
            "yeni sohbet": None,  # client-side only
            "yeniden olustur": None,  # client-side only
            "kayitli sohbetler": None,  # client-side only
            "fotografi kaldir": None,  # client-side only
            "kutuphaneyi temizle": ("POST", "/api/kutuphane/temizle", []),
            "gecmisi temizle": ("POST", "/api/gecmis/temizle", []),
            "siniri kaydet": ("POST", "/api/ayarlar", ["limitInput"]),
            "suresi kaydet": ("POST", "/api/ayarlar", ["beklemeInput"]),
            "sifreyi degistir": ("POST", "/api/sifre-degistir", ["sfrEski", "sfrYeni"]),
            "prompt ayarlarini kaydet": ("POST", "/api/ayarlar", ["llmSistem", "llmModFikir", "llmModGorsel", "llmModBirlestir", "llmModCeviri", "llmModSohbet"]),
            "kullanici ekle": ("POST", "/api/kullanicilar", ["admAd", "admSifre", "admRol", "admKotaMb", "admKotaKayit"]),
            "1. fotografi kaldir": None,
            "2. fotografi kaldir": None,
            "fotografi kaldir": None,
            "1. fotograf: kutuphaneden sec": None,
            "2. fotograf: kutuphaneden sec": None,
            "video uret": ("POST", "/api/kuyruk", ["image_base64", "vsure", "vfps", "vmax"]),
            "kutukluklari temizle": ("POST", "/api/kutuphane/temizle", []),
            "1'e sabitle": None,
            "2'ye sabitle": None,
            "kaydet ve uret": None,
            "prompt kutusuna uygula": None,
            "kopyala": None,
            "kaydet": None,
            "oran: kilitli": None,
            "kutucuklar": None,
            "alt alta": None,
            "daha fazla yukle": None,
            "yenile": None,
            "tumunu sil": ("POST", "/api/prompt-gecmis/temizle", []),
            "kopyala": None,
            "kapat": None,
            # Menu navigation (client-side only, changes active page)
            "uretim": None,
            "prompt studiosu": None,
            "kuyruk": None,
            "kutukphane": None,
            "promptlar": None,
            "gecmis": None,
            "sohbet": None,
            "izleme": None,
            "ayarlar": None,
            "yonetim": None,
        }

    def _conn(self, timeout=10):
        return http.client.HTTPConnection(self.base, timeout=timeout)

    def _cookie_header(self):
        cookies = []
        for c in self.cj:
            cookies.append("%s=%s" % (c.name, c.value))
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

    def goto(self, path):
        """Sayfaya git ve HTML'i parse et."""
        conn = self._conn()
        conn.request("GET", path, headers={"Cookie": self._cookie_header()})
        r = conn.getresponse()
        data = r.read().decode("utf-8", errors="replace")
        self._update_cookies(r.getheaders())
        conn.close()
        self.current_url = path
        self.current_html = data
        self._parse_html(data)
        return r.status, data

    def _parse_html(self, html):
        """HTML'i parse ederek form, buton, link ve input bul."""
        self.forms = []
        self.links = []
        self.buttons = []
        self.inputs = []

        for fm in re.findall(r"<form[^>]*>", html, re.IGNORECASE):
            action = re.search(r'action=["\']([^"\']*)', fm)
            method = re.search(r'method=["\']([^"\']*)', fm, re.IGNORECASE)
            self.forms.append({
                "action": action.group(1) if action else "",
                "method": (method.group(1).upper() if method else "GET"),
                "html": fm[:100]
            })

        for lk in re.findall(r'<a[^>]*href=["\']([^"\']*)[^>]*>(.*?)</a>', html, re.IGNORECASE | re.DOTALL):
            self.links.append({
                "href": lk[0],
                "text": html_module.unescape(re.sub(r"<[^>]+>", "", lk[1])).strip()[:80]
            })

        for bt in re.findall(r"<button[^>]*>(.*?)</button>", html, re.IGNORECASE | re.DOTALL):
            self.buttons.append({
                "text": html_module.unescape(re.sub(r"<[^>]+>", "", bt)).strip()[:80],
                "html": bt[:100]
            })
        for bt in re.findall(r'<input[^>]*type=["\']button["\'][^>]*>', html, re.IGNORECASE):
            val = re.search(r'value=["\']([^"\']*)', bt)
            self.buttons.append({
                "text": val.group(1) if val else "Button",
                "html": bt[:100]
            })

        for inp in re.findall(r"<input[^>]*>", html, re.IGNORECASE):
            itype = re.search(r'type=["\']([^"\']*)', inp, re.IGNORECASE)
            iname = re.search(r'name=["\']([^"\']*)', inp, re.IGNORECASE)
            ival = re.search(r'value=["\']([^"\']*)', inp, re.IGNORECASE)
            pid = re.search(r'id=["\']([^"\']*)', inp, re.IGNORECASE)
            placeholder = re.search(r'placeholder=["\']([^"\']*)', inp, re.IGNORECASE)
            self.inputs.append({
                "type": itype.group(1) if itype else "text",
                "name": iname.group(1) if iname else "",
                "id": pid.group(1) if pid else "",
                "value": ival.group(1) if ival else "",
                "placeholder": placeholder.group(1) if placeholder else "",
                "html": inp[:100]
            })

    def _find_input_by_id_or_name(self, target):
        """Input'u ID veya name ile bul (Turkce karakter toleransli)."""
        target_norm = _turkce_duzelt(target)
        for inp in self.inputs:
            if _turkce_duzelt(inp.get("id", "")) == target_norm or _turkce_duzelt(inp.get("name", "")) == target_norm:
                return inp
        return None

    def fill_input(self, name_or_id, value):
        """Input doldur (ID veya name ile)."""
        inp = self._find_input_by_id_or_name(name_or_id)
        if inp:
            inp["value"] = value
            print("[FILL] %s = %s" % (inp.get("id") or inp.get("name"), value))
            return True
        print("[ERROR] Input bulunamadi: %s" % name_or_id)
        return False

    def _find_button(self, button_text):
        """Butonu metne gore bul (Turkce toleransli, eslesme esnek)."""
        target_norm = _turkce_duzelt(button_text)
        target_words = set(target_norm.split())
        for btn in self.buttons:
            btn_norm = _turkce_duzelt(btn["text"])
            btn_words = set(btn_norm.split())
            # Tam eslesme veya bir kelime eslesmesi yeterli
            if target_norm == btn_norm or target_norm in btn_norm or btn_norm in target_norm:
                return btn
            if target_words & btn_words:
                return btn
        return None

    def click_button(self, button_text, form_data=None):
        """Butona tikla - API cagirisi yapmaya calisir."""
        btn = self._find_button(button_text)
        if not btn:
            print("[ERROR] Buton bulunamadi: %s" % button_text)
            return 404, "Button not found: %s" % button_text

        print("[CLICK] Buton: %s" % btn["text"])
        btn_key = _turkce_duzelt(btn["text"])

        # API mapping'den ara
        for key, mapping in self.button_api_map.items():
            if key in btn_key or btn_key in key:
                if mapping is None:
                    print("  -> Client-side islem (API yok): %s" % btn["text"])
                    return 200, "Client-side action"
                method, api_path, required_fields = mapping
                print("  -> API: %s %s" % (method, api_path))

                # Form verisini topla
                payload = {}
                if form_data:
                    payload.update(form_data)

                # Eger gerekli field'lar varsa, input degerlerini al
                for field in required_fields:
                    if field not in payload:
                        inp = self._find_input_by_id_or_name(field)
                        if inp and inp.get("value"):
                            payload[field] = inp["value"]

                if method == "POST":
                    return self.post_api(api_path, payload)
                else:
                    return self.get(api_path, params=payload)
                return

        # Eger mapping yoksa, sadece butona tiklandi gibi davran
        print("  -> API mapping yok, sadece tiklama simule edildi")
        return 200, "Button clicked (no API mapping)"

    def click_link(self, link_text):
        """Link metniyle sayfaya git."""
        for lk in self.links:
            if _turkce_duzelt(link_text) in _turkce_duzelt(lk["text"]):
                print("[CLICK] Link: %s -> %s" % (lk["text"], lk["href"]))
                return self.goto(lk["href"])
        print("[ERROR] Link bulunamadi: %s" % link_text)
        return 404, "Link not found"

    def post_api(self, path, data):
        """API'ye POST yap."""
        conn = self._conn()
        payload = json.dumps(data).encode("utf-8")
        conn.request("POST", path, body=payload,
                     headers={"Content-Type": "application/json",
                              "Cookie": self._cookie_header()})
        r = conn.getresponse()
        data_resp = r.read().decode("utf-8", errors="replace")
        self._update_cookies(r.getheaders())
        conn.close()
        print("  -> Yanit: %s %s" % (r.status, data_resp[:200]))
        return r.status, data_resp

    def get(self, path, params=None):
        conn = self._conn()
        if params:
            qs = "&".join("%s=%s" % (k, v) for k, v in params.items() if v)
            if qs:
                path += "?" + qs
        conn.request("GET", path, headers={"Cookie": self._cookie_header()})
        r = conn.getresponse()
        data = r.read().decode("utf-8", errors="replace")
        self._update_cookies(r.getheaders())
        conn.close()
        self.current_url = path
        self.current_html = data
        self._parse_html(data)
        return r.status, data

    def print_state(self):
        print("\n=== Sayfa: %s ===" % self.current_url)
        print("Inputlar (%d):" % len(self.inputs))
        for i, inp in enumerate(self.inputs):
            print("  [%d] type=%s id=%s name=%s value=%s placeholder=%s" % (
                i, inp["type"], inp["id"] or "-", inp["name"] or "-",
                inp["value"] or "-", inp["placeholder"] or "-"))
        print("Butonlar (%d):" % len(self.buttons))
        for i, btn in enumerate(self.buttons):
            print("  [%d] %s" % (i, btn["text"]))
        print("Linkler (%d):" % len(self.links))
        for i, lk in enumerate(self.links):
            print("  [%d] %s -> %s" % (i, lk["text"], lk["href"]))
        print()
