from __future__ import annotations

import json
import threading
from types import SimpleNamespace

from fastapi.testclient import TestClient

from api import create_legacy_app
from api.legacy_setup_routes import handle_setup_get, handle_setup_post


class FakePodSetup:
    def __init__(self):
        self.ready = True
        self.updated = None

    def is_ready(self):
        return self.ready

    def storage_status(self):
        return {"storage_mode": "ephemeral", "storage_persistent": False}

    def status(self):
        return {"hazir": True, "sira": []}

    def system_settings(self):
        return {"models": [], "settings": {}}

    def update_system_settings(self, payload):
        self.updated = payload
        return {"saved": True}


class FakeHandler:
    def do_GET(self):
        handle_setup_get(self, __import__("urllib.parse").parse.urlsplit(self.path), self.runtime)

    def do_POST(self):
        handle_setup_post(self, __import__("urllib.parse").parse.urlsplit(self.path), self.runtime)

    def _send(self, code, body, ctype="application/json", ek_baslik=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in ek_baslik or []:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _serve_react_preview(self, parsed):
        self._send(404, '{"hata":"bulunamadi"}')

    def _setup_yetkili(self):
        return True

    def _origin_kontrol(self):
        return True

    def _json_oku(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length).decode("utf-8") or "{}")


def make_runtime(tmp_path):
    setup = FakePodSetup()
    return setup, SimpleNamespace(
        BASE_DIR=str(tmp_path),
        RUNPOD_MODE=True,
        SETUP_PORT=8192,
        pod_setup=setup,
        _auth_store=SimpleNamespace(kurulu_mu=lambda: False),
        _setup_health_state=lambda: (200, {"hazir": True, "storage_mode": "ephemeral"}),
        _app_update_state={"status": "idle"},
        _kapatma_istegi=threading.Event(),
        os=__import__("os"),
        json=json,
    )


def client_for(tmp_path):
    setup, runtime = make_runtime(tmp_path)
    FakeHandler.runtime = runtime
    return setup, TestClient(create_legacy_app(FakeHandler, port=8192))


def test_setup_health_and_first_run_status_keep_payloads(tmp_path):
    _, client = client_for(tmp_path)
    assert client.get("/api/setup-state").json() == {
        "hazir": True, "storage_mode": "ephemeral"
    }
    assert client.get("/api/kurulum-durumu").json() == {
        "kurulu": False, "runpod": True,
        "storage_mode": "ephemeral", "storage_persistent": False,
    }


def test_setup_reports_not_ready_without_calling_storage(tmp_path):
    setup, client = client_for(tmp_path)
    setup.ready = False
    response = client.get("/api/kurulum-durumu")
    assert response.status_code == 503
    assert response.json() == {"hata": "Sistem hazırlanıyor."}


def test_setup_settings_get_returns_existing_shape(tmp_path):
    _, client = client_for(tmp_path)
    response = client.get("/api/system-settings")
    assert response.status_code == 200
    assert response.json() == {"models": [], "settings": {}}


def test_setup_settings_post_forwards_payload_and_keeps_status(tmp_path):
    setup, client = client_for(tmp_path)
    payload = {"islem": "kaydet", "llm": {"context": 8192}}
    response = client.post("/api/system-settings", json=payload)
    assert response.status_code == 200
    assert response.json() == {"saved": True}
    assert setup.updated == payload


def test_setup_settings_post_maps_validation_error_to_400(tmp_path):
    setup, client = client_for(tmp_path)
    setup.update_system_settings = lambda _payload: (_ for _ in ()).throw(
        ValueError("sentetik doğrulama hatası")
    )
    response = client.post("/api/system-settings", json={"islem": "kaydet"})
    assert response.status_code == 400
    assert response.json() == {"hata": "sentetik doğrulama hatası"}
