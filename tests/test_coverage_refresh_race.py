import base64
import hashlib

import pytest

pytest.importorskip("httpx")


@pytest.fixture
def client(tmp_path, monkeypatch):
    from database import crypto, coverage

    monkeypatch.setenv("CUSTOMER_DB_PATH", str(tmp_path / "race.sqlite3"))
    monkeypatch.setenv("CUSTOMER_DB_KEY_B64", base64.b64encode(b"r" * 32).decode("ascii"))
    monkeypatch.setenv("RAG_DB_PATH", str(tmp_path / "rag.sqlite3"))
    monkeypatch.setenv("DEV_SKIP_AUTH", "1")
    crypto.reset_cache()
    monkeypatch.setattr(coverage, "rag_search", lambda *args, **kwargs: {"hits": []})
    from fastapi.testclient import TestClient
    import main

    yield TestClient(main.app)
    crypto.reset_cache()


def _ledger(source_key: str, source_hash: str) -> dict:
    return {
        "source_hash": source_hash,
        "source_file_name": f"{source_hash}.pdf",
        "source_page": 2,
        "source_occurrence_key": source_key,
        "insurer": "안전보험",
        "product_name": "안전상품",
        "rider_name": "암진단",
        "rider_name_raw": "암진단",
        "standard_name": "일반암 진단비",
        "standard_name_raw": "암진단",
        "amount": 10_000_000,
        "amount_text": "1,000만원",
        "amount_status": "known",
    }


def _observed(amount: int, page: int) -> list[dict]:
    return [{
        "coverage_name": "일반암 진단비",
        "source_coverage_name": "암진단",
        "amount": amount,
        "amount_text": f"{amount // 10_000:,}만원",
        "amount_status": "known",
        "source_status": "부족" if amount == 10_000_000 else "충분",
        "source_page": page,
    }]


def _logical_db_fingerprint() -> str:
    from database.db import connect

    conn = connect()
    try:
        digest = hashlib.sha256()
        for statement in conn.iterdump():
            digest.update(statement.encode("utf-8"))
            digest.update(b"\n")
        return digest.hexdigest()
    finally:
        conn.close()


def _new_customer_with_ledger(client, name: str, source_hash: str) -> str:
    cid = client.post("/customers", json={"name": name}).json()["id"]
    saved = client.post(
        f"/customers/{cid}/policy-coverages/bulk",
        json={"review_confirmed": True, "items": [_ledger(f"ledger:{source_hash}:2:0:1", source_hash)]},
    )
    assert saved.status_code == 201, saved.text
    return cid


def test_stale_ui_revision_is_rejected_without_any_db_change(client):
    cid = _new_customer_with_ledger(client, "동시성방어고객", "race-source")
    stale = client.post(
        f"/customers/{cid}/coverage-analysis/recalculate",
        json={"force": True, "source": "calculated_before_capture"},
    )
    newest = client.post(
        f"/customers/{cid}/coverage-analysis/recalculate",
        json={"force": True, "source": "capture_pdf", "source_hash": "race-source", "observed_summary": _observed(10_000_000, 6)},
    )
    assert stale.status_code == newest.status_code == 200
    before = _logical_db_fingerprint()

    rejected = client.post(
        f"/customers/{cid}/coverage-analysis/link-refresh",
        json={"audience": "internal", "run_id": stale.json()["run_id"]},
    )
    assert rejected.status_code == 409, rejected.text
    assert "최신" in rejected.json()["detail"]
    assert _logical_db_fingerprint() == before
    latest = client.get(f"/customers/{cid}/coverage-analysis", params={"audience": "internal"}).json()
    assert latest["run_id"] == newest.json()["run_id"]
    item = next(i for c in latest["categories"] for i in c["items"] if i["coverage_name"] == "일반암 진단비")
    assert (item["amount_source_kind"], item["current_amount"], item["observed_source_page"]) == (
        "observed_summary", 10_000_000, 6
    )


def test_compare_and_swap_blocks_run_created_after_latest_get(client):
    cid = _new_customer_with_ledger(client, "GETPOST경쟁고객", "cas-source")
    first = client.post(
        f"/customers/{cid}/coverage-analysis/recalculate",
        json={"force": True, "source": "capture_pdf", "source_hash": "cas-source", "observed_summary": _observed(10_000_000, 6)},
    )
    selected = client.get(f"/customers/{cid}/coverage-analysis", params={"audience": "internal"}).json()
    assert selected["run_id"] == first.json()["run_id"]

    newer = client.post(
        f"/customers/{cid}/coverage-analysis/recalculate",
        json={"force": True, "source": "concurrent_capture", "source_hash": "cas-source", "observed_summary": _observed(20_000_000, 7)},
    )
    before = _logical_db_fingerprint()
    rejected = client.post(
        f"/customers/{cid}/coverage-analysis/link-refresh",
        json={"audience": "internal", "run_id": selected["run_id"]},
    )
    assert rejected.status_code == 409, rejected.text
    assert _logical_db_fingerprint() == before
    assert client.get(f"/customers/{cid}/coverage-analysis", params={"audience": "internal"}).json()["run_id"] == newer.json()["run_id"]


def test_latest_explicit_revision_keeps_its_selected_source_in_multi_source_customer(client):
    cid = client.post("/customers", json={"name": "다중원본고객"}).json()["id"]
    saved = client.post(
        f"/customers/{cid}/policy-coverages/bulk",
        json={
            "review_confirmed": True,
            "items": [
                _ledger("ledger:source-a:2:0:1", "source-a"),
                {**_ledger("ledger:source-b:2:0:1", "source-b"), "product_name": "안전상품B"},
            ],
        },
    )
    assert saved.status_code == 201, saved.text
    source_a = client.post(
        f"/customers/{cid}/coverage-analysis/recalculate",
        json={"force": True, "source": "capture_pdf", "source_hash": "source-a", "observed_summary": _observed(10_000_000, 6)},
    )
    source_b = client.post(
        f"/customers/{cid}/coverage-analysis/recalculate",
        json={"force": True, "source": "capture_pdf", "source_hash": "source-b", "observed_summary": _observed(20_000_000, 7)},
    )
    assert source_a.status_code == source_b.status_code == 200

    refreshed = client.post(
        f"/customers/{cid}/coverage-analysis/link-refresh",
        json={"audience": "internal", "run_id": source_b.json()["run_id"]},
    )
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["source_run_id"] == source_b.json()["run_id"]
    assert refreshed.json()["source_summary_items"] == 1
    body = client.get(
        f"/customers/{cid}/coverage-analysis",
        params={"audience": "internal", "run_id": refreshed.json()["run_id"]},
    ).json()
    item = next(i for c in body["categories"] for i in c["items"] if i["coverage_name"] == "일반암 진단비")
    assert (item["amount_source_kind"], item["current_amount"], item["observed_source_page"]) == (
        "observed_summary", 20_000_000, 7
    )
