import base64
import asyncio
import importlib
import io
import json
import sys
import types

import pytest
from fastapi import UploadFile
from starlette.datastructures import Headers

pytest.importorskip("httpx")

_MB = 1024 * 1024
_KEY = base64.b64encode(b"0" * 32).decode("ascii")


@pytest.fixture
def client(tmp_path, monkeypatch):
    from database import crypto
    database_router = importlib.import_module("database.router")
    parser_router = importlib.import_module("parser.router")

    monkeypatch.setenv("CUSTOMER_DB_PATH", str(tmp_path / "customers.sqlite3"))
    monkeypatch.setenv("CUSTOMER_DB_KEY_B64", _KEY)
    monkeypatch.setenv("ENGINE_BACKUP", "0")
    monkeypatch.setattr(database_router, "MAX_AUDIO", _MB)
    monkeypatch.setattr(database_router, "MAX_PDF", _MB)
    monkeypatch.setattr(database_router, "MAX_SHEET", _MB)
    monkeypatch.setattr(database_router, "MAX_TEXT", _MB)
    monkeypatch.setattr(parser_router, "MAX_PDF", _MB)
    crypto.reset_cache()
    from fastapi.testclient import TestClient
    import main

    monkeypatch.setattr(main, "_MAX_REQUEST_BYTES", _MB)
    yield TestClient(main.app)
    crypto.reset_cache()


def _file(name, data, content_type="application/octet-stream", headers=None):
    value = (name, data, content_type)
    if headers is not None:
        value += (headers,)
    return {"file": value}


def test_env_limits_are_configurable(monkeypatch):
    import database.uploads as uploads

    with monkeypatch.context() as patch:
        for name in ("AUDIO", "PDF", "SHEET", "TEXT"):
            patch.setenv(f"UPLOAD_MAX_{name}_MB", "1")
        uploads = importlib.reload(uploads)
        assert (uploads.MAX_AUDIO, uploads.MAX_PDF, uploads.MAX_SHEET, uploads.MAX_TEXT) == (_MB,) * 4
    importlib.reload(uploads)


def test_content_length_rejects_without_reading():
    from database.uploads import read_upload_capped

    class NeverRead(io.BytesIO):
        def read(self, *args):
            raise AssertionError("body must not be read")

    upload = UploadFile(NeverRead(b"x"), filename="large.pdf", headers=Headers({"content-length": str(_MB + 1)}))
    with pytest.raises(Exception) as exc:
        asyncio.run(read_upload_capped(upload, _MB))
    assert exc.value.status_code == 413
    assert exc.value.detail == "파일이 너무 큽니다. 최대 1MB."


@pytest.mark.parametrize("size,rejected", [(_MB, False), (_MB + 1, True)])
def test_read_upload_capped_exact_boundary(size, rejected):
    from database.uploads import read_upload_capped

    upload = UploadFile(io.BytesIO(b"x" * size), filename="boundary.bin")
    if rejected:
        with pytest.raises(Exception) as exc:
            asyncio.run(read_upload_capped(upload, _MB))
        assert exc.value.status_code == 413
    else:
        data = asyncio.run(read_upload_capped(upload, _MB))
        assert len(data) == _MB


@pytest.mark.parametrize("size,rejected", [(_MB, False), (_MB + 1, True)])
def test_non_numeric_content_length_falls_back_to_body_limit(size, rejected):
    from database.uploads import read_upload_capped

    upload = UploadFile(
        io.BytesIO(b"x" * size),
        filename="forged.bin",
        headers=Headers({"content-length": "abc"}),
    )
    if rejected:
        with pytest.raises(Exception) as exc:
            asyncio.run(read_upload_capped(upload, _MB))
        assert exc.value.status_code == 413
    else:
        data = asyncio.run(read_upload_capped(upload, _MB))
        assert len(data) == _MB


def test_forged_small_content_length_is_caught_by_streaming(client):
    response = client.post(
        "/capture",
        files=_file("notes.txt", b"x" * (_MB + 1), "text/plain", {"content-length": "1"}),
    )
    assert response.status_code == 413
    assert "1MB" in response.json()["detail"]


def test_request_content_length_over_limit_is_rejected(client):
    response = client.post(
        "/capture",
        content=b"",
        headers={"content-length": str(_MB + 1)},
    )
    assert response.status_code == 413
    assert "1MB" in response.json()["detail"]


def test_small_text_only_request_passes_request_limit(client, monkeypatch):
    intake = importlib.import_module("database.intake")
    monkeypatch.setattr(intake, "extract_multiple", lambda text: [{"fields": {}, "warnings": []}])

    response = client.post("/capture", data={"text": "고객 상담 메모"})
    assert response.status_code == 200, response.text


def test_health_is_unaffected_by_request_limit(client):
    response = client.get("/health")
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("name", ["recording.wav", "policy.pdf", "notes.txt"])
def test_capture_rejects_each_kind_over_its_limit(client, name):
    response = client.post("/capture", files=_file(name, b"x" * (_MB + 1)))
    assert response.status_code == 413
    assert "1MB" in response.json()["detail"]


def test_sheet_limit_applies_even_below_audio_limit(client, monkeypatch):
    router = importlib.import_module("database.router")

    monkeypatch.setattr(router, "MAX_AUDIO", 2 * _MB)
    response = client.post("/import/preview", files=_file("book.xlsx", b"x" * (_MB + 1)))
    assert response.status_code == 413
    assert "1MB" in response.json()["detail"]


def test_import_commit_rejects_oversize_and_accepts_normal_multipart(client):
    mapping = json.dumps({"name": "name"})
    too_large = client.post(
        "/import/commit",
        files=_file("customers.csv", b"x" * (_MB + 1), "text/csv"),
        data={"mapping": mapping},
    )
    normal = client.post(
        "/import/commit",
        files=_file("customers.csv", b"name\nAlice\n", "text/csv"),
        data={"mapping": mapping},
    )

    assert too_large.status_code == 413
    assert normal.status_code == 200, normal.text


def test_consultation_from_audio_rejects_oversize(client, monkeypatch):
    fake_whisper = types.ModuleType("whisper")
    fake_whisper.transcribe = lambda path: {"text": "hello", "audio_minutes": 0.1}
    fake_whisper.summarize = lambda text: {"title": "call", "summary": "done", "action_items": [], "follow_up_at": None}
    monkeypatch.setitem(sys.modules, "whisper", fake_whisper)
    customer = client.post("/customers", json={"name": "녹취고객"}).json()

    response = client.post(
        f"/customers/{customer['id']}/consultations/from-audio",
        files=_file("call.wav", b"x" * (_MB + 1), "audio/wav"),
    )
    assert response.status_code == 413


def test_policy_pdf_over_limit_is_413_not_415(client):
    customer = client.post("/customers", json={"name": "테스트"}).json()
    policy = client.post(
        f"/customers/{customer['id']}/policies", json={"insurer": "테스트보험"}
    ).json()

    response = client.post(
        f"/policies/{policy['id']}/document",
        files=_file("policy.pdf", b"x" * (_MB + 1), "application/pdf"),
    )
    assert response.status_code == 413


def test_capture_text_without_file_remains_200(client, monkeypatch):
    intake = importlib.import_module("database.intake")
    monkeypatch.setattr(intake, "extract_multiple", lambda text: [{"fields": {}, "warnings": []}])

    response = client.post("/capture", data={"text": "고객 상담 메모"})
    assert response.status_code == 200, response.text


def test_uploads_at_or_below_limits_keep_success_statuses(client, build_pdf, monkeypatch):
    router = importlib.import_module("database.router")
    intake = importlib.import_module("database.intake")

    monkeypatch.setattr(router, "extract_customer_fields", lambda text: {"fields": {}, "warnings": []})
    monkeypatch.setattr(intake, "extract_multiple", lambda text: [{"fields": {}, "warnings": []}])
    text = client.post("/capture", files=_file("notes.txt", b"customer note", "text/plain"))
    sheet = client.post("/import/preview", files=_file("customers.csv", b"name\nAlice\n", "text/csv"))
    pdf = client.post("/parse/pdf", files=_file("policy.pdf", build_pdf(["policy"]), "application/pdf"))

    fake_whisper = types.ModuleType("whisper")
    fake_whisper.transcribe = lambda path: {"text": "hello", "audio_minutes": 0.1}
    fake_whisper.summarize = lambda text: {"title": "call", "summary": "done", "action_items": [], "follow_up_at": None}
    monkeypatch.setitem(sys.modules, "whisper", fake_whisper)
    audio = client.post("/customers/intake/from-audio", files=_file("call.wav", b"audio"))

    assert text.status_code == 200, text.text
    assert sheet.status_code == 200, sheet.text
    assert pdf.status_code == 200, pdf.text
    assert audio.status_code == 200, audio.text


@pytest.mark.parametrize("path,name,extra", [
    ("/capture", "empty.txt", {}),
    ("/parse/pdf", "empty.pdf", {}),
    ("/import/preview", "empty.csv", {}),
    ("/customers/intake/from-audio", "empty.wav", {}),
])
def test_empty_files_remain_400(client, path, name, extra):
    response = client.post(path, files=_file(name, b""), data=extra)
    assert response.status_code == 400


def test_policy_extension_is_rejected_before_read(client):
    customer = client.post("/customers", json={"name": "테스트"}).json()
    policy = client.post(
        f"/customers/{customer['id']}/policies", json={"insurer": "테스트보험"}
    ).json()
    response = client.post(f"/policies/{policy['id']}/document", files=_file("notes.txt", b"x"))
    assert response.status_code == 415
