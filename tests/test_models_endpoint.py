"""Model discovery through the web worker without an inference request."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from fastapi.testclient import TestClient

from app.server import app


@contextmanager
def catalog_server():
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.path, self.headers.get("Authorization")))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"data":[{"id":"example-a"},{"id":"example-b"}]}')

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/v1", calls
        finally:
            server.shutdown()
            thread.join()


def test_models_route_forwards_only_catalog_request(monkeypatch):
    with catalog_server() as (base, calls), TestClient(app, base_url="http://localhost") as client:
        monkeypatch.setenv("JJOKGYEOL_DECISION_BASE_URLS", base)
        response = client.post("/api/decision/models", json={"provider": "lmstudio", "base_url": base,
                                                            "api_key": "catalog-fixture"})
        assert response.status_code == 200, response.text
        assert response.json() == {"models": ["example-a", "example-b"]}
        assert response.headers["cache-control"] == "no-store"
        assert calls == [("/v1/models", "Bearer catalog-fixture")]


def test_models_route_rejects_insecure_page_key_before_contact(monkeypatch):
    monkeypatch.setenv("JJOKGYEOL_ALLOWED_HOSTS", "testserver")
    with catalog_server() as (base, calls), TestClient(app, base_url="http://testserver") as client:
        monkeypatch.setenv("JJOKGYEOL_DECISION_BASE_URLS", base)
        response = client.post("/api/decision/models", json={"provider": "chat", "base_url": base,
                                                            "api_key": "catalog-fixture"})
        assert response.json()["code"] == "address"
        assert calls == []
