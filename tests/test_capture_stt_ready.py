from fastapi.responses import JSONResponse

import importlib

router = importlib.import_module("database.router")
import whisper
from whisper import transcriber


def test_audio_model_not_ready_returns_409(monkeypatch):
    state = {
        "backend": "faster", "model": "small", "state": "downloading",
        "pct": None, "mb": 0, "total_mb": 0, "reason": None,
        "detail": None, "updated_at": "now",
    }
    monkeypatch.setattr(whisper, "transcribe", lambda _: (_ for _ in ()).throw(transcriber.ModelNotReady(state)))
    response = router._transcribe_and_summarize(b"audio", "call.m4a")
    assert isinstance(response, JSONResponse)
    assert response.status_code == 409
    assert b'"stt_status"' in response.body


def test_audio_ready_uses_existing_summary_path(monkeypatch):
    monkeypatch.setattr(whisper, "transcribe", lambda _: {"text": "hello", "audio_minutes": 1})
    monkeypatch.setattr(whisper, "summarize", lambda _: {"title": "ok"})
    result = router._transcribe_and_summarize(b"audio", "call.m4a")
    assert result["stt"]["text"] == "hello"
    assert result["summary"]["title"] == "ok"


def test_non_audio_kind_is_unchanged():
    assert router._upload_kind("note.txt") != "audio"
    assert router._upload_kind("proposal.pdf") != "audio"
