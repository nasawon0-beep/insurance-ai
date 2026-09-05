from fastapi.testclient import TestClient

import main
from whisper import transcriber


def test_stt_status_requires_secret(monkeypatch):
    monkeypatch.setattr(transcriber, "status", lambda: {
        "backend": "faster", "model": "small", "state": "downloading",
        "pct": 10, "mb": 1, "total_mb": 10, "reason": None,
        "detail": None, "updated_at": "now",
    })
    client = TestClient(main.app)
    client.headers.pop(main.API_SECRET_HEADER, None)
    assert client.get("/stt/status").status_code == 401
    response = client.get("/stt/status", headers={main.API_SECRET_HEADER: main.API_SECRET})
    assert response.status_code == 200
    assert response.json()["state"] == "downloading"
