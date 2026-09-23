import base64
import json
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("httpx")


@pytest.fixture
def client(tmp_path, monkeypatch):
    from database import crypto
    from fastapi.testclient import TestClient
    import main

    monkeypatch.setenv("CUSTOMER_DB_PATH", str(tmp_path / "customers.sqlite3"))
    monkeypatch.setenv("CUSTOMER_DB_KEY_B64", base64.b64encode(b"a" * 32).decode())
    monkeypatch.setenv("RAG_DB_PATH", str(tmp_path / "rag.sqlite3"))
    monkeypatch.setenv("DEV_SKIP_AUTH", "0")
    crypto.reset_cache()
    yield TestClient(main.app)
    crypto.reset_cache()


def _customer(client, actor=None):
    headers = {"X-Actor-Id": actor} if actor else {}
    r = client.post("/customers", json={"name": "감사 고객"}, headers=headers)
    assert r.status_code == 201
    return r.json()


def test_customer_actor_fields_filter_and_global_queries(client, monkeypatch):
    c = _customer(client, "agent-7")
    rows = client.get(f"/customers/{c['id']}/audit").json()
    assert rows[0]["action"] == "create" and rows[0]["entity"] == "customer"
    assert rows[0]["actor"] == "agent-7"

    assert client.patch(f"/customers/{c['id']}", json={"phone": "010-1234-5678", "memo": "secret"}).status_code == 200
    assert client.patch(f"/customers/{c['id']}", json={"rrn": "900101-1234568"}).status_code == 200
    rows = client.get("/audit", params={"customer_id": c["id"], "limit": 20}).json()
    updates = [x for x in rows if x["action"] == "update"]
    assert len(updates) == 1
    assert updates[0]["fields"] == "memo,phone"
    assert "secret" not in updates[0]["fields"]
    assert client.get("/audit", params={"limit": 1}).status_code == 200
    assert client.get("/diagnostics").json()["audit"]["count"] == 2

    from database import repo
    monkeypatch.setattr(repo, "log_audit", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("fail")))
    assert client.post("/customers", json={"name": "감사 실패 격리"}).status_code == 201


def test_unknown_actor_policy_consultation_and_follow_up(client):
    c = _customer(client)
    assert client.get(f"/customers/{c['id']}/audit").json()[0]["actor"] == "unknown"
    p = client.post(f"/customers/{c['id']}/policies", json={"insurer": "A"}).json()
    assert client.patch(f"/policies/{p['id']}", json={"memo": "private"}).status_code == 200
    assert client.delete(f"/policies/{p['id']}").status_code == 204
    k = client.post(f"/customers/{c['id']}/consultations", json={"consulted_at": "2026-09-04", "content": "private"}).json()
    assert client.patch(f"/consultations/{k['id']}", json={"title": "private"}).status_code == 200
    assert client.post(f"/consultations/{k['id']}/follow-up/complete").status_code == 200
    assert client.post(f"/consultations/{k['id']}/follow-up/reopen").status_code == 200
    reopened = client.get(f"/customers/{c['id']}/audit").json()[0]
    assert reopened["action"] == "update" and reopened["entity"] == "consultation"
    assert reopened["fields"] == "follow_up_done_at"
    assert client.delete(f"/consultations/{k['id']}").status_code == 204
    rows = client.get(f"/customers/{c['id']}/audit").json()
    assert all(x["customer_id"] == c["id"] for x in rows)
    assert any(x["entity"] == "policy" and x["action"] == "delete" for x in rows)
    assert any(x["entity"] == "consultation" and x["fields"] == "follow_up_done_at" for x in rows)
    assert all("private" not in (x["fields"] or "") for x in rows)


def test_delete_404_and_noop_mutation_audits(client):
    c = _customer(client)
    initial = len(client.get("/audit").json())
    assert client.patch(f"/customers/{c['id']}", json={}).status_code == 200
    assert client.patch(f"/customers/{c['id']}", json={"rrn": "900101-1234568"}).status_code == 200
    assert len(client.get("/audit").json()) == initial

    assert client.patch("/customers/missing", json={"memo": "x"}).status_code == 404
    assert client.delete("/customers/missing").status_code == 404
    assert len(client.get("/audit").json()) == initial

    assert client.delete(f"/customers/{c['id']}").status_code == 204
    rows = client.get("/audit").json()
    assert len(rows) == initial + 1
    assert rows[0]["action"] == "delete" and rows[0]["entity"] == "customer"


def test_policy_document_attach_and_detach_audits(client, build_pdf):
    c = _customer(client)
    p = client.post(f"/customers/{c['id']}/policies", json={"insurer": "A"}).json()
    response = client.post(
        f"/policies/{p['id']}/document",
        files={"file": ("policy.pdf", build_pdf(["policy terms"]), "application/pdf")},
    )
    assert response.status_code == 200, response.text
    assert client.delete(f"/policies/{p['id']}/document").status_code == 204
    rows = client.get(f"/customers/{c['id']}/audit").json()
    document_updates = [
        row for row in rows
        if row["action"] == "update" and row["entity"] == "policy" and "document_id" in row["fields"]
    ]
    assert len(document_updates) == 2


def test_audit_auth_limit_and_batch_undo(client):
    import main

    secret = client.headers.pop(main.API_SECRET_HEADER)
    try:
        assert client.get("/audit").status_code == 401
    finally:
        client.headers[main.API_SECRET_HEADER] = secret
    assert client.get("/audit", params={"limit": 1001}).status_code == 422

    batch_id = "a" * 32
    response = client.post(
        "/customers",
        json={"name": "배치 고객"},
        headers={"X-Import-Batch": batch_id},
    )
    assert response.status_code == 201
    assert client.post(f"/capture/batches/{batch_id}/undo").status_code == 200
    rows = client.get("/audit").json()
    undo = next(row for row in rows if row["action"] == "delete" and row["entity"] == "import")
    assert undo["entity_id"] == batch_id
    assert undo["fields"] == "1 customers, 0 policies, 0 consultations, 0 rrn"


def test_import_commit_writes_one_summary_without_personal_data(client):
    csv_text = "이름,메모\n개인정보이름,절대저장금지\n"
    r = client.post(
        "/import/commit",
        files={"file": ("audit.csv", csv_text.encode(), "text/csv")},
        data={"mapping": json.dumps({"name": "이름", "memo": "메모"}), "dedupe": "merge"},
    )
    assert r.status_code == 200
    rows = [x for x in client.get("/audit").json() if x["entity"] == "import"]
    assert len(rows) == 1 and rows[0]["action"] == "create"
    assert "created" in rows[0]["fields"]
    assert "개인정보이름" not in rows[0]["fields"] and "절대저장금지" not in rows[0]["fields"]


def test_prune_audit(tmp_path):
    from database.db import connect, init_schema
    from database import repo

    conn = connect(tmp_path / "audit.sqlite3")
    init_schema(conn)
    now = datetime.now(timezone.utc)
    for row_id, age in (("old", 731), ("recent", 729)):
        conn.execute(
            "INSERT INTO audit_log VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (row_id, "tester", "create", "customer", row_id, row_id, None, (now - timedelta(days=age)).isoformat()),
        )
    conn.commit()
    assert repo.prune_audit(conn, 730) == 1
    assert [r["id"] for r in conn.execute("SELECT id FROM audit_log")] == ["recent"]
    conn.close()
