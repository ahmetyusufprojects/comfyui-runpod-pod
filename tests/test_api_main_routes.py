from __future__ import annotations

import json
import threading
from types import SimpleNamespace
import urllib.parse
from urllib.parse import urlparse

from fastapi.testclient import TestClient

from api import create_legacy_app
from api.legacy_main_routes import handle_main_get, handle_main_post


class FakeHandler:
    runtime = None

    def do_GET(self):
        handle_main_get(self, self.runtime)

    def do_POST(self):
        handle_main_post(self, urlparse(self.path), self.runtime)

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

    def _origin_kontrol(self):
        return True

    def _setup_parolasiz(self):
        return False

    def _oturum_cerezi(self, token):
        return [("Set-Cookie", f"session={token}; HttpOnly; SameSite=Lax")]

    def _auth_gerekli(self):
        return "synthetic"

    def _kasa_gerekli(self, user):
        return user

    def _kasa_kapaniyor_mu(self, _user):
        return False

    def _json_oku(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length).decode("utf-8") or "{}")


def make_client():
    auth_store = SimpleNamespace(
        giris=lambda _user, _password, _ip: ("synthetic-token", "admin")
    )
    runtime = SimpleNamespace(
        RUNPOD_MODE=False,
        SETUP_PORT=8192,
        _kapatma_istegi=threading.Event(),
        _main_health_state=lambda: (200, {"hazir": True, "pid": 101}),
        _auth_store=auth_store,
        auth=SimpleNamespace(AuthHatasi=ValueError),
        json=json,
        urllib=urllib,
        llm=SimpleNamespace(model_profilleri=lambda: []),
        _kuyruga_ekle=lambda request, _user, *, tur: (
            {"is_id": "synthetic-job", "tur": tur}, 1
        ),
    )
    FakeHandler.runtime = runtime
    return TestClient(create_legacy_app(FakeHandler, port=8189))


def test_main_health_endpoint_preserves_shape_and_no_store_header():
    response = make_client().get("/api/sunucu-hazir")
    assert response.status_code == 200
    assert response.json() == {"hazir": True, "pid": 101}
    assert response.headers["cache-control"] == "no-store"


def test_login_route_keeps_cookie_and_json_contract():
    response = make_client().post(
        "/api/giris", json={"kullanici": "synthetic", "sifre": "test-only"}
    )
    assert response.status_code == 200
    assert response.json() == {"kullanici": "synthetic", "rol": "admin"}
    assert "session=synthetic-token" in response.headers["set-cookie"]


def test_prompt_queue_route_keeps_existing_job_response():
    response = make_client().post("/api/prompt-uret", json={"prompt": "synthetic"})
    assert response.status_code == 200
    assert response.json() == {
        "queued": True, "is_id": "synthetic-job", "sira": 1
    }


def test_unknown_route_and_method_keep_http_semantics():
    client = make_client()
    assert client.post("/api/no-such-route", json={}).status_code == 404
    assert client.put("/api/prompt-uret", json={}).status_code == 405
