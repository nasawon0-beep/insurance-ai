"""local-engine liveness와 상세 진단 분리 회귀 테스트."""
import time

from fastapi.testclient import TestClient


def test_health_is_lightweight_and_does_not_call_heavy_diagnostics(monkeypatch):
    import main

    def fail_urlopen(*args, **kwargs):  # pragma: no cover - 호출되면 실패
        raise AssertionError("/health must not call Ollama")

    def fail_runtime():  # pragma: no cover - 호출되면 실패
        raise AssertionError("/health must not call ollama_runtime_status")

    monkeypatch.setattr(main.urllib.request, "urlopen", fail_urlopen)
    monkeypatch.setattr(main, "ollama_runtime_status", fail_runtime)
    monkeypatch.setattr(main, "optimization_recommendations", lambda status: (_ for _ in ()).throw(AssertionError("/health must not optimize")))

    started = time.perf_counter()
    response = TestClient(main.app).get("/health")
    elapsed = time.perf_counter() - started

    assert response.status_code == 200
    assert response.json() == {"local_engine": "ok"}
    assert elapsed < 0.1


def test_health_details_degrades_when_diagnostics_fail(monkeypatch):
    import main

    monkeypatch.setattr(main.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("ollama slow")))
    monkeypatch.setattr(main, "ollama_runtime_status", lambda: (_ for _ in ()).throw(RuntimeError("runtime failed")))

    response = TestClient(main.app).get("/health/details")
    body = response.json()

    assert response.status_code == 200
    assert body["local_engine"] == "ok"
    assert body["ollama"] == "disconnected"
    assert "ollama slow" in body["error"]
    assert body["ollama_runtime"]["error"] == "runtime failed"


def test_diagnostics_degrades_when_detail_steps_fail(monkeypatch):
    import importlib
    import main

    router_module = importlib.import_module("database.router")
    monkeypatch.setattr(router_module._backup, "list_backups", lambda: (_ for _ in ()).throw(OSError("backup failed")))
    monkeypatch.setattr(router_module.repo, "dashboard_counts", lambda conn: (_ for _ in ()).throw(RuntimeError("counts failed")))
    monkeypatch.setattr(router_module.repo, "audit_stats", lambda conn: (_ for _ in ()).throw(RuntimeError("audit failed")))

    response = TestClient(main.app).get("/diagnostics")
    body = response.json()

    assert response.status_code == 200
    assert body["engine"]["local_engine"] == "ok"
    assert body["backups"]["error"] == "backup failed"
    assert body["data"]["error"] == "counts failed"
    assert body["audit"]["error"] == "audit failed"
