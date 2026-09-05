def test_api_requires_secret_header():
    from fastapi.testclient import TestClient
    import main

    client = TestClient(main.app)
    del client.headers[main.API_SECRET_HEADER]
    response = client.get("/settings")
    assert response.status_code == 401


def test_health_remains_available_without_secret(monkeypatch):
    from fastapi.testclient import TestClient
    from database import crypto
    import main

    monkeypatch.setattr(
        crypto,
        "get_cipher",
        lambda: type("Cipher", (), {"key_source": "test"})(),
    )
    client = TestClient(main.app)
    del client.headers[main.API_SECRET_HEADER]
    response = client.get("/health")
    assert response.status_code == 200


def test_api_secret_requires_allowed_origin():
    from fastapi.testclient import TestClient
    import main

    client = TestClient(main.app)
    assert client.get("/api-secret").status_code == 403
    assert client.get("/api-secret", headers={"Origin": "https://example.com"}).status_code == 403
    response = client.get("/api-secret", headers={"Origin": "tauri://localhost"})
    assert response.status_code == 200
    assert response.json() == {"secret": main.API_SECRET}


def test_allowed_origin_preflight_succeeds():
    from fastapi.testclient import TestClient
    import main

    client = TestClient(main.app)
    del client.headers[main.API_SECRET_HEADER]
    response = client.options(
        "/settings",
        headers={
            "Origin": "tauri://localhost",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": main.API_SECRET_HEADER,
        },
    )
    assert 200 <= response.status_code < 300
