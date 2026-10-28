from __future__ import annotations

import base64
import concurrent.futures
import sqlite3

import pytest

pytest.importorskip("httpx")

_TEST_KEY_B64 = base64.b64encode(b"f" * 32).decode("ascii")


@pytest.fixture
def family_env(tmp_path, monkeypatch):
    from database import crypto

    path = tmp_path / "family-synthetic.sqlite3"
    monkeypatch.setenv("CUSTOMER_DB_PATH", str(path))
    monkeypatch.setenv("CUSTOMER_DB_KEY_B64", _TEST_KEY_B64)
    monkeypatch.setenv("RAG_DB_PATH", str(tmp_path / "rag.sqlite3"))
    crypto.reset_cache()
    yield path
    crypto.reset_cache()


@pytest.fixture
def client(family_env):
    from fastapi.testclient import TestClient
    import main

    return TestClient(main.app)


def customer(client, label: str):
    response = client.post(
        "/customers",
        json={"name": f"{label}고객", "phone": f"010-0000-00{ord(label):02d}"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def link(client, source_id: str, target_id: str, relationship: str, **extra):
    return client.post(
        f"/customers/{source_id}/family-relationships",
        json={
            "related_customer_id": target_id,
            "relationship": relationship,
            **extra,
        },
    )


def family(client, customer_id: str):
    response = client.get(f"/customers/{customer_id}/family-relationships")
    assert response.status_code == 200, response.text
    return response.json()["family_relationships"]


def by_name(rows):
    return {row["customer"]["name"]: row for row in rows}


def test_registration_batch_is_atomic_and_idempotent_after_response_loss(client):
    new_customer, spouse, child = (customer(client, key) for key in "ABC")
    body = {
        "idempotency_key": "registration-batch-exact-replay-0001",
        "relationships": [
            {"related_customer_id": spouse["id"], "relationship": "SPOUSE"},
            {
                "related_customer_id": child["id"],
                "relationship": "CHILD",
                "co_parent_customer_id": spouse["id"],
            },
        ]
    }
    first = client.post(
        f"/customers/{new_customer['id']}/family-relationships/batch",
        json=body,
    )
    assert first.status_code == 201, first.text
    assert first.json()["idempotent_replay"] is False
    assert len(first.json()["created_relationships"]) == 3

    # A committed response may be lost. The exact retry must not add another edge.
    replay = client.post(
        f"/customers/{new_customer['id']}/family-relationships/batch",
        json=body,
    )
    assert replay.status_code == 201, replay.text
    assert replay.json()["idempotent_replay"] is True
    assert len(replay.json()["created_relationships"]) == 3
    assert len(family(client, new_customer["id"])) == 2
    assert len(family(client, spouse["id"])) == 2
    assert len(family(client, child["id"])) == 2


def test_registration_batch_rolls_back_every_relationship_on_one_failure(client):
    new_customer, spouse = (customer(client, key) for key in "DE")
    failed = client.post(
        f"/customers/{new_customer['id']}/family-relationships/batch",
        json={
            "idempotency_key": "registration-batch-rollback-0001",
            "relationships": [
                {"related_customer_id": spouse["id"], "relationship": "SPOUSE"},
                {"related_customer_id": "missing-customer", "relationship": "SIBLING"},
            ]
        },
    )
    assert failed.status_code == 404, failed.text
    assert family(client, new_customer["id"]) == []
    assert family(client, spouse["id"]) == []


def test_registration_batch_same_key_different_payload_is_conflict_without_second_write(client):
    source, sibling, other = (customer(client, key) for key in "GHI")
    key = "same-key"
    first = client.post(
        f"/customers/{source['id']}/family-relationships/batch",
        json={
            "idempotency_key": key,
            "relationships": [
                {"related_customer_id": sibling["id"], "relationship": "SIBLING"},
            ],
        },
    )
    assert first.status_code == 201, first.text

    conflict = client.post(
        f"/customers/{source['id']}/family-relationships/batch",
        json={
            "idempotency_key": key,
            "relationships": [
                {
                    "related_customer_id": other["id"],
                    "relationship": "OTHER",
                    "other_description": "다른 의도",
                },
            ],
        },
    )
    assert conflict.status_code == 409, conflict.text
    assert {row["customer"]["id"] for row in family(client, source["id"])} == {sibling["id"]}


def test_registration_batch_requires_explicit_idempotency_key(client):
    source, target = (customer(client, key) for key in "JK")
    response = client.post(
        f"/customers/{source['id']}/family-relationships/batch",
        json={"relationships": [{"related_customer_id": target["id"], "relationship": "SIBLING"}]},
    )
    assert response.status_code == 422
    assert family(client, source["id"]) == []


def test_registration_batch_replays_after_new_client_reopens_database(client, family_env):
    from fastapi.testclient import TestClient
    import main

    source, target = (customer(client, key) for key in "LM")
    body = {
        "idempotency_key": "registration-batch-restart-0001",
        "relationships": [{"related_customer_id": target["id"], "relationship": "SIBLING"}],
    }
    first = client.post(f"/customers/{source['id']}/family-relationships/batch", json=body)
    assert first.status_code == 201
    assert first.json()["idempotent_replay"] is False

    with TestClient(main.app) as reopened:
        replay = reopened.post(f"/customers/{source['id']}/family-relationships/batch", json=body)
    assert replay.status_code == 201
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["created_relationships"] == first.json()["created_relationships"]
    with sqlite3.connect(family_env) as conn:
        assert conn.execute("SELECT COUNT(*) FROM family_relationships").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM family_relationship_batch_idempotency").fetchone()[0] == 1


def test_concurrent_same_key_replays_once_and_different_keys_are_isolated(family_env):
    from fastapi.testclient import TestClient
    import main

    setup = TestClient(main.app)
    source, first_target, second_target = (customer(setup, key) for key in "NOP")

    same_body = {
        "idempotency_key": "registration-batch-concurrent-same-0001",
        "relationships": [{"related_customer_id": first_target["id"], "relationship": "SIBLING"}],
    }

    def post_body(body):
        with TestClient(main.app) as local:
            response = local.post(f"/customers/{source['id']}/family-relationships/batch", json=body)
            return response.status_code, response.json()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        same_results = list(pool.map(post_body, [same_body, same_body]))
    assert sorted(status for status, _body in same_results) == [201, 201]
    assert sorted(body["idempotent_replay"] for _status, body in same_results) == [False, True]

    different_bodies = [
        {
            "idempotency_key": "registration-batch-concurrent-different-0001",
            "relationships": [{"related_customer_id": second_target["id"], "relationship": "OTHER", "other_description": "첫 요청"}],
        },
        {
            "idempotency_key": "registration-batch-concurrent-different-0002",
            "relationships": [{"related_customer_id": source["id"], "relationship": "OTHER", "other_description": "둘째 요청"}],
        },
    ]
    # Use independent source customers so operation keys and edge uniqueness are both isolated.
    other_source = customer(setup, "Q")
    different_bodies[1]["relationships"][0]["related_customer_id"] = first_target["id"]

    def post_different(index):
        cid = source["id"] if index == 0 else other_source["id"]
        with TestClient(main.app) as local:
            response = local.post(f"/customers/{cid}/family-relationships/batch", json=different_bodies[index])
            return response.status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(post_different, range(2)))
    assert statuses == [201, 201]
    with sqlite3.connect(family_env) as conn:
        assert conn.execute("SELECT COUNT(*) FROM family_relationships").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM family_relationship_batch_idempotency").fetchone()[0] == 3


def test_spouse_and_joint_child_are_atomic_bidirectional_and_reopen(client, family_env):
    a, b, c = (customer(client, key) for key in "ABC")
    assert link(client, a["id"], b["id"], "SPOUSE").status_code == 201
    created = link(
        client,
        a["id"],
        c["id"],
        "CHILD",
        co_parent_customer_id=b["id"],
    )
    assert created.status_code == 201, created.text
    assert len(created.json()["created_relationships"]) == 2

    a_rows = by_name(family(client, a["id"]))
    b_rows = by_name(family(client, b["id"]))
    c_rows = by_name(family(client, c["id"]))
    assert a_rows["B고객"]["relationship"] == "SPOUSE"
    assert a_rows["C고객"]["relationship"] == "CHILD"
    assert b_rows["A고객"]["relationship"] == "SPOUSE"
    assert b_rows["C고객"]["relationship"] == "CHILD"
    assert c_rows["A고객"]["relationship"] == "PARENT"
    assert c_rows["B고객"]["relationship"] == "PARENT"

    with sqlite3.connect(family_env) as reopened:
        assert reopened.execute("SELECT COUNT(*) FROM family_relationships").fetchone()[0] == 3


def test_child_without_joint_selection_does_not_link_spouse(client):
    a, b, c = (customer(client, key) for key in "ABC")
    assert link(client, a["id"], b["id"], "SPOUSE").status_code == 201
    assert link(client, a["id"], c["id"], "CHILD").status_code == 201
    assert {row["customer"]["name"] for row in family(client, b["id"])} == {"A고객"}


def test_late_spouse_link_only_shares_explicit_selected_children(client):
    a, b, c, d = (customer(client, key) for key in "ABCD")
    assert link(client, a["id"], c["id"], "CHILD").status_code == 201
    assert link(client, a["id"], d["id"], "CHILD").status_code == 201
    response = link(
        client,
        a["id"],
        b["id"],
        "SPOUSE",
        shared_child_ids=[c["id"]],
    )
    assert response.status_code == 201, response.text
    assert by_name(family(client, b["id"]))["C고객"]["relationship"] == "CHILD"
    assert "D고객" not in by_name(family(client, b["id"]))


def test_many_relationships_inverse_and_other_description(client):
    a, b, c, d = (customer(client, key) for key in "ABCD")
    assert link(client, a["id"], b["id"], "PARENT").status_code == 201
    assert link(client, a["id"], c["id"], "SIBLING").status_code == 201
    assert link(
        client,
        a["id"],
        d["id"],
        "OTHER",
        other_description="법정대리인",
    ).status_code == 201
    assert by_name(family(client, b["id"]))["A고객"]["relationship"] == "CHILD"
    assert by_name(family(client, c["id"]))["A고객"]["relationship"] == "SIBLING"
    assert by_name(family(client, d["id"]))["A고객"]["other_description"] == "법정대리인"


@pytest.mark.parametrize("description", ["<script>alert(1)</script>", "줄바꿈\n주입", "x" * 121])
def test_other_description_rejects_unsafe_text(client, description):
    a, b = (customer(client, key) for key in "AB")
    response = link(
        client,
        a["id"],
        b["id"],
        "OTHER",
        other_description=description,
    )
    assert response.status_code == 422


def test_self_duplicate_missing_and_contradictory_relationships_are_blocked(client):
    a, b = (customer(client, key) for key in "AB")
    assert link(client, a["id"], a["id"], "SPOUSE").status_code == 422
    assert link(client, a["id"], "outside-customer", "SPOUSE").status_code == 404
    assert link(client, a["id"], b["id"], "SPOUSE").status_code == 201
    assert link(client, a["id"], b["id"], "SPOUSE").status_code == 409
    assert link(client, a["id"], b["id"], "PARENT").status_code == 409


def test_parent_child_cycle_is_blocked(client):
    a, b, c = (customer(client, key) for key in "ABC")
    assert link(client, a["id"], b["id"], "CHILD").status_code == 201
    assert link(client, b["id"], c["id"], "CHILD").status_code == 201
    cycle = link(client, c["id"], a["id"], "CHILD")
    assert cycle.status_code == 409
    assert "순환" in cycle.json()["detail"]


def test_joint_child_validation_rolls_back_every_edge(client):
    a, b, c = (customer(client, key) for key in "ABC")
    failed = link(
        client,
        a["id"],
        c["id"],
        "CHILD",
        co_parent_customer_id=b["id"],
    )
    assert failed.status_code == 409
    assert family(client, a["id"]) == []
    assert family(client, b["id"]) == []
    assert family(client, c["id"]) == []


def test_unlink_deletes_only_one_edge_and_preserves_customers_contracts_and_other_edges(client):
    a, b, c = (customer(client, key) for key in "ABC")
    policy = client.post(
        f"/customers/{b['id']}/policies",
        json={"insurer": "합성보험", "product_name": "합성계약"},
    )
    assert policy.status_code == 201
    assert link(client, a["id"], b["id"], "SPOUSE").status_code == 201
    assert link(client, a["id"], c["id"], "SIBLING").status_code == 201
    edge = by_name(family(client, a["id"]))["B고객"]

    removed = client.delete(
        f"/customers/{a['id']}/family-relationships/{edge['id']}"
    )
    assert removed.status_code == 204, removed.text
    assert {row["customer"]["name"] for row in family(client, a["id"])} == {"C고객"}
    assert client.get(f"/customers/{b['id']}").json()["policies"][0]["id"] == policy.json()["id"]
    assert client.get(f"/customers/{c['id']}").status_code == 200


def test_customer_delete_cleans_only_incident_edges_and_not_other_customer_data(client):
    a, b, c = (customer(client, key) for key in "ABC")
    assert link(client, a["id"], b["id"], "SPOUSE").status_code == 201
    assert link(client, b["id"], c["id"], "SIBLING").status_code == 201
    assert client.delete(f"/customers/{a['id']}").status_code == 204
    assert client.get(f"/customers/{b['id']}").status_code == 200
    assert by_name(family(client, b["id"]))["C고객"]["relationship"] == "SIBLING"


def test_concurrent_duplicate_requests_create_one_canonical_edge(family_env):
    from fastapi.testclient import TestClient
    import main

    setup = TestClient(main.app)
    a, b = (customer(setup, key) for key in "AB")

    def create_once(_):
        with TestClient(main.app) as local:
            return link(local, a["id"], b["id"], "SPOUSE").status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(create_once, range(2)))
    assert statuses == [201, 409]
    with sqlite3.connect(family_env) as conn:
        assert conn.execute("SELECT COUNT(*) FROM family_relationships").fetchone()[0] == 1


def test_legacy_schema_migration_is_lossless(tmp_path, monkeypatch):
    from database import crypto, db, repo

    path = tmp_path / "legacy-family.sqlite3"
    monkeypatch.setenv("CUSTOMER_DB_KEY_B64", _TEST_KEY_B64)
    crypto.reset_cache()
    conn = db.connect(path)
    db.init_schema(conn)
    existing = repo.create_customer(conn, {"name": "기존합성고객"})
    policy = repo.create_policy(conn, existing["id"], {"product_name": "기존합성계약"})
    before = conn.execute("SELECT COUNT(*) FROM policies").fetchone()[0]
    customer_before = conn.execute("SELECT id,name FROM customers").fetchall()
    conn.execute("DROP TABLE family_relationships")
    conn.commit()
    db.init_schema(conn)
    assert conn.execute("SELECT COUNT(*) FROM policies").fetchone()[0] == before
    assert conn.execute("SELECT id,name FROM customers").fetchall() == customer_before
    assert repo.get_policy(conn, policy["id"])["product_name"] == "기존합성계약"
    assert conn.execute("SELECT COUNT(*) FROM family_relationships").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM family_relationship_batch_idempotency").fetchone()[0] == 0
    conn.close()
