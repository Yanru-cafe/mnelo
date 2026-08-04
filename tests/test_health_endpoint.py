import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mcp_server


def test_health_route_is_registered_and_reports_hygiene():
    app = mcp_server._build_sse_app("test-token")
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/health" in paths


def test_health_endpoint_returns_json():
    from starlette.testclient import TestClient

    app = mcp_server._build_sse_app("test-token")
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"ok", "degraded"}
    assert "hygiene" in body
    assert "purge_backlog" in body["hygiene"]
    assert "importance_below_floor" in body["hygiene"]
    assert "freshness" in body["hygiene"]


def test_health_endpoint_error_schema_is_stable(monkeypatch):
    from starlette.testclient import TestClient

    monkeypatch.setattr(mcp_server, "_mem_instance", None)
    monkeypatch.setattr(mcp_server, "_get_mem", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    response = TestClient(mcp_server._build_sse_app("test-token")).get("/health")
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "hygiene": {
        "purge_backlog": None,
        "importance_below_floor": None,
        "freshness": None,
    }}


def test_health_endpoint_reuses_singleton(monkeypatch):
    from starlette.testclient import TestClient

    class FakeMemory:
        def stats(self):
            return {"hygiene": {"purge_backlog": 0, "decay_floor_chunks": 0, "freshness": 1.0}}

    fake = FakeMemory()
    calls = []
    monkeypatch.setattr(mcp_server, "_mem_instance", None)
    monkeypatch.setattr(mcp_server, "_get_mem", lambda: calls.append(1) or fake)
    client = TestClient(mcp_server._build_sse_app("test-token"))
    assert client.get("/health").status_code == 200
    monkeypatch.setattr(mcp_server, "_mem_instance", fake)
    assert client.get("/health").status_code == 200
    assert len(calls) == 1

