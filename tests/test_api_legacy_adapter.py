from __future__ import annotations

import json

from fastapi.testclient import TestClient

from api import create_apps, create_legacy_app


class FakeHandler:
    """Small stand-in for Handler; it emits the same HTTP primitives."""

    def do_GET(self):
        if self.path == "/api/ben":
            if self.headers.get("Cookie") == "session=ok":
                self._send(200, '{"kullanici":"synthetic","rol":"admin"}',
                           "application/json")
            else:
                self._send(401, '{"hata":"giris gerekli"}', "application/json")
            return
        if self.path == "/status":
            body = json.dumps({"port": self.server.server_port})
            self._send(200, body)
            return
        self._send(404, '{"hata":"bulunamadi"}')

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length else ""
        if self.path == "/echo":
            self._send(201, body, "application/json", [
                ("Set-Cookie", "first=a; HttpOnly"),
                ("Set-Cookie", "second=b; HttpOnly"),
                ("X-Legacy", "preserved"),
            ])
            return
        self._send(404, '{"hata":"bulunamadi"}')

    def _send(self, code, body, ctype="application/json", extra_headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in extra_headers or []:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)


def test_two_apps_are_bound_to_the_existing_ports():
    apps = create_apps(FakeHandler)
    main = TestClient(apps.main)
    setup = TestClient(apps.setup)

    assert main.get("/status").json() == {"port": 8189}
    assert setup.get("/status").json() == {"port": 8192}


def test_native_health_routers_keep_existing_payloads_per_port():
    apps = create_apps(
        FakeHandler,
        main_health=lambda: (200, {"pid": 123, "hazir": True}),
        setup_health=lambda: (200, {"hazir": True, "hata": False,
                                   "parolasiz": True,
                                   "storage_mode": "ephemeral",
                                   "storage_persistent": False}),
    )
    main = TestClient(apps.main)
    setup = TestClient(apps.setup)

    assert main.get("/api/sunucu-hazir").json() == {"pid": 123, "hazir": True}
    assert setup.get("/api/setup-state").json() == {
        "hazir": True, "hata": False, "parolasiz": True,
        "storage_mode": "ephemeral", "storage_persistent": False,
    }
    assert main.get("/api/setup-state").status_code == 404
    assert setup.get("/api/sunucu-hazir").status_code == 404


def test_main_auth_status_json_and_cache_header_are_forwarded():
    app = create_legacy_app(FakeHandler, port=8189)
    client = TestClient(app)

    unauthenticated = client.get("/api/ben")
    assert unauthenticated.status_code == 401
    assert unauthenticated.json() == {"hata": "giris gerekli"}

    authenticated = client.get("/api/ben", headers={"Cookie": "session=ok"})
    assert authenticated.status_code == 200
    assert authenticated.json() == {"kullanici": "synthetic", "rol": "admin"}
    assert authenticated.headers["cache-control"] == "no-store"


def test_post_body_status_and_repeated_response_headers_are_preserved():
    app = create_legacy_app(FakeHandler, port=8192)
    client = TestClient(app)
    payload = b'{"islem":"duraklat"}'

    response = client.post("/echo", content=payload,
                           headers={"Content-Type": "application/json"})

    assert response.status_code == 201
    assert response.content == payload
    assert response.headers.get_list("set-cookie") == [
        "first=a; HttpOnly", "second=b; HttpOnly"
    ]
    assert response.headers["x-legacy"] == "preserved"
    assert response.headers["content-length"] == str(len(payload))


def test_raw_encoded_path_and_query_are_passed_to_legacy_handler():
    class PathHandler(FakeHandler):
        def do_GET(self):
            self._send(200, self.path, "text/plain")

    app = create_legacy_app(PathHandler, port=8189)
    response = TestClient(app).get("/a%2Fb?q=one%20two")
    assert response.status_code == 200
    assert response.text == "/a%2Fb?q=one%20two"


def test_only_legacy_supported_methods_are_routed():
    app = create_legacy_app(FakeHandler, port=8189)
    response = TestClient(app).put("/status")
    assert response.status_code == 405


def test_invalid_legacy_port_is_rejected():
    try:
        create_legacy_app(FakeHandler, port=8188)
    except ValueError as exc:
        assert "8188" in str(exc)
    else:
        raise AssertionError("unsupported port must be rejected")
