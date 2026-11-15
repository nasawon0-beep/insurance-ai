from fastapi.testclient import TestClient
import pytest
from synthetic_account_binding import AUTHORIZATION, FIELDS, bind_synthetic_account

import main
from whisper import transcriber


def test_stt_status_requires_secret(monkeypatch):
    bind_synthetic_account(monkeypatch, main.app)
    monkeypatch.setattr(transcriber, "status", lambda: {
        "backend": "faster", "model": "small", "state": "downloading",
        "pct": 10, "mb": 1, "total_mb": 10, "reason": None,
        "detail": None, "updated_at": "now",
    })
    client = TestClient(main.app)
    client.headers.pop('Authorization', None)
    client.headers.pop(main.API_SECRET_HEADER, None)
    assert client.get("/stt/status").status_code == 401
    assert client.get('/stt/status', headers={'Authorization': AUTHORIZATION}).status_code == 401
    for authorization in ('', 'Bearer synthetic-foreign-account'):
        assert client.get('/stt/status', headers={main.API_SECRET_HEADER: main.API_SECRET,
                          'Authorization': authorization}).status_code == 401
    response = client.get("/stt/status", headers={main.API_SECRET_HEADER: main.API_SECRET,
                                                'Authorization': AUTHORIZATION})
    assert response.status_code == 200
    assert response.json()["state"] == "downloading"


@pytest.mark.parametrize('fault', ['unbound', 'expired'])
def test_stt_rejects_invalid_binding_before_transcriber(monkeypatch, fault):
    bind_synthetic_account(monkeypatch, main.app, expires_at=0 if fault == 'expired' else 4102444800)
    if fault == 'unbound':
        for name in FIELDS:
            monkeypatch.delenv(name, raising=False)
    def forbidden_status():
        pytest.fail('transcriber must not run before account authorization')
    monkeypatch.setattr(transcriber, 'status', forbidden_status)
    client = TestClient(main.app)
    response = client.get('/stt/status', headers={'Authorization': AUTHORIZATION})
    assert response.status_code == 401
