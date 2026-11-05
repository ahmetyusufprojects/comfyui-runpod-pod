"""Uploaded-photo preparation helpers. Optional image libraries stay lazy."""


def exif_duzelt(im):
    """Telefon fotosundaki EXIF yonunu piksele isler (yan donme biter).
    EXIF yoksa aynen doner; yeni bagimlilik yok (Pillow)."""
    try:
        from PIL import ImageOps as _ImageOps
        return _ImageOps.exif_transpose(im)
    except Exception:
        return im


def fotograf_hazirla(blob, max_side, olcekle):
    """Yuklenen fotoyu PNG'ye cevirip (gerekirse) max_side'a kucultur.
    Donus: (gonderilecek_bytes, (w0,h0) kaynak, (w1,h1) hedef, kucultuldu_mu).
    Basarisiz olursa orijinal bytes ile (None, None, False) doner."""
    try:
        try:
            from pillow_heif import register_heif_opener
            register_heif_opener()
        except Exception:
            pass
        from PIL import Image as _PILImage
        import io as _bio
        with _PILImage.open(_bio.BytesIO(blob)) as _im:
            _im = exif_duzelt(_im).convert("RGB")
            w0, h0 = _im.width, _im.height
            if olcekle:
                w1, h1 = scale_to_max_side(w0, h0, max_side)
            else:
                w1, h1 = (w0 // 16) * 16, (h0 // 16) * 16
            if (w1, h1) != (w0, h0):
                _im = _im.resize((w1, h1), _PILImage.LANCZOS)
            buf = _bio.BytesIO()
            _im.save(buf, format="PNG")
            return buf.getvalue(), (w0, h0), (w1, h1), (w1, h1) != (w0, h0)
    except Exception:
        return blob, None, None, False


def scale_to_max_side(w, h, max_side):
    """En uzun kenari max_side'a esitle (oran korunur, hem buyutur hem kucultur). 16'nin kati."""
    f = max_side / float(max(w, h))
    w2 = max(16, int(round(w * f / 16.0)) * 16)
    h2 = max(16, int(round(h * f / 16.0)) * 16)
    return w2, h2
