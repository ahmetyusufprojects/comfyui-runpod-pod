from __future__ import annotations

import base64
import io
import sys

from PIL import Image


def _llm_module():
    root = str(__import__("pathlib").Path(__file__).resolve().parents[1] / "webui_flux2")
    if root not in sys.path:
        sys.path.insert(0, root)
    import llm
    return llm


def test_legacy_import_is_the_live_engine_module():
    llm = _llm_module()
    from llm_runtime import engine

    assert llm is engine
    for name in ("uret", "sohbet_et", "baslat", "durdur", "model_profilleri",
                 "model_ayarla", "LLMHatasi", "SISTEM_PROMPTU", "MOD_TALIMATI",
                 "_RUNPOD_PROFILES", "_uretim_sistem_mesaji"):
        assert hasattr(llm, name)


def test_prompt_module_reads_live_compatibility_settings(monkeypatch):
    llm = _llm_module()
    monkeypatch.setattr(llm, "SISTEM_PROMPTU", "sentetik sistem yönergesi")
    monkeypatch.setitem(llm.MOD_TALIMATI, "fikir", "sentetik fikir yönergesi")

    message = llm._uretim_sistem_mesaji("fikir", "tr", "orta")

    assert message.startswith("sentetik sistem yönergesi\nsentetik fikir yönergesi")
    assert "positive_tr" in message and "negative" in message


def test_prompt_json_contract_helpers_are_reexported():
    llm = _llm_module()

    parsed = llm._json_plus('```json\n{"positive":"scene",}\n```')
    assert parsed == {"positive": "scene"}
    assert llm._sema_uygun({"positive": " scene "}) == {"positive": " scene "}
    assert llm._sema_uygun({"positive": " "}) is None
    normalized = llm._normalizasyon(
        {"positive": "scene", "tags": ["one", "two"]}, None, 12,
        {"model": "synthetic"})
    assert normalized["positive_tr"] == "scene"
    assert normalized["tags"] == ["one", "two"]
    assert normalized["model"] == "synthetic"


def test_transport_helpers_keep_image_and_sse_contracts():
    llm = _llm_module()
    from llm_runtime import transport

    assert transport.parse_stream_line('data: {"choices":[]}') == {"choices": []}
    assert llm._aki_parca("data: [DONE]") == "__DONE__"

    image = Image.new("RGB", (4, 3), color="red")
    output = io.BytesIO()
    image.save(output, format="PNG")
    data = base64.b64encode(output.getvalue()).decode("ascii")
    url = llm._gorsel_url({"mime": "image/png", "veri": data})
    assert url.startswith("data:image/jpeg;base64,")


def test_metrics_helpers_use_shared_compatibility_state(monkeypatch):
    llm = _llm_module()
    from llm_runtime import metrics

    monkeypatch.setattr(llm, "_uretim", {"aktif": False, "baslama": 0.0,
                                          "ilk_token": None, "ilk_zaman": None})
    monkeypatch.setattr(llm, "_son_olcum", {})
    monkeypatch.setattr(metrics, "slot_data", lambda _runtime: {
        "slots": [{"is_processing": True,
                   "next_token": [{"n_decoded": 8}],
                   "n_prompt_tokens": 15,
                   "n_prompt_tokens_processed": 7}]
    })

    llm._olcum_baslat()
    value = llm.canli_olcum()
    llm._olcum_bitir({"token": 8})

    assert value["aktif"] is True
    assert value["token"] == 8
    assert value["prompt_token"] == 7
    assert llm.canli_olcum() == {"token": 8, "aktif": False}
