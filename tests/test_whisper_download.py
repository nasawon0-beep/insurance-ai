import errno
import threading
import time
from urllib.error import HTTPError, URLError

import pytest

from whisper import transcriber


@pytest.fixture(autouse=True)
def reset_download_state(monkeypatch):
    monkeypatch.setattr(transcriber, "_backend", lambda: "faster")
    transcriber._fw_model.cache_clear()
    with transcriber._dl_lock:
        transcriber._dl.update(
            state="absent", pct=None, mb=0, total_mb=0, reason=None,
            detail=None, started_at=None, updated_at=None,
        )
    transcriber._ProgressTqdm._agg = {"seen": 0.0, "total": 0.0}
    yield


def test_classify_expected_reasons(monkeypatch):
    monkeypatch.setattr(transcriber.shutil, "disk_usage", lambda _: type("D", (), {"free": 2_000_000_000})())
    assert transcriber._classify(URLError("offline")) == "network"
    assert transcriber._classify(HTTPError("x", 403, "forbidden", {}, None)) == "blocked"
    assert transcriber._classify(HTTPError("x", 407, "proxy", {}, None)) == "blocked"
    assert transcriber._classify(OSError(errno.ENOSPC, "full")) == "disk"
    if transcriber.requests is not None:
        assert transcriber._classify(transcriber.requests.exceptions.ConnectionError()) == "network"
        assert transcriber._classify(transcriber.requests.exceptions.ReadTimeout()) == "blocked"
    if transcriber.httpx is not None:
        assert transcriber._classify(transcriber.httpx.ConnectError("offline")) == "network"
        assert transcriber._classify(transcriber.httpx.ReadTimeout("slow")) == "blocked"


def test_status_local_cache_changes_absent_to_ready(monkeypatch):
    import faster_whisper.utils

    monkeypatch.setattr(faster_whisper.utils, "download_model", lambda *a, **kw: "/cached")
    assert transcriber.status()["state"] == "ready"


def test_prewarm_success_and_failure(monkeypatch):
    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "snapshot_download", lambda *a, **kw: "/cached")
    monkeypatch.setattr(transcriber, "_fw_model", lambda: object())
    assert transcriber.prewarm(blocking=True)["state"] == "ready"

    with transcriber._dl_lock:
        transcriber._dl["state"] = "absent"
    monkeypatch.setattr(huggingface_hub, "snapshot_download", lambda *a, **kw: (_ for _ in ()).throw(URLError("offline")))
    with pytest.raises(URLError):
        transcriber.prewarm(blocking=True)
    failed = transcriber.status()
    assert failed["state"] == "failed"
    assert failed["reason"] == "network"


def test_prewarm_is_single_flight(monkeypatch):
    import huggingface_hub

    entered = threading.Event()
    release = threading.Event()
    calls = []

    def download(*a, **kw):
        calls.append(1)
        entered.set()
        release.wait(2)

    monkeypatch.setattr(huggingface_hub, "snapshot_download", download)
    monkeypatch.setattr(transcriber, "_fw_model", lambda: object())
    transcriber.prewarm()
    assert entered.wait(1)
    transcriber.prewarm()
    release.set()
    for _ in range(50):
        if transcriber.status()["state"] == "ready":
            break
        time.sleep(0.01)
    assert len(calls) == 1


def test_progress_tqdm_updates_bytes():
    small = transcriber._ProgressTqdm(total=100, disable=True)
    small.update(100)
    with transcriber._dl_lock:
        assert transcriber._dl["pct"] == 100
    large = transcriber._ProgressTqdm(total=900, disable=True)
    large.update(100)
    with transcriber._dl_lock:
        assert transcriber._dl["pct"] == 20
        assert transcriber._ProgressTqdm._agg == {"seen": 200.0, "total": 1000.0}


def test_prewarm_fails_before_download_when_disk_is_low(monkeypatch):
    import huggingface_hub

    monkeypatch.setattr(transcriber.shutil, "disk_usage", lambda _: type("D", (), {"free": 1})())

    def no_network_download(*a, **kw):
        if not kw.get("local_files_only"):
            pytest.fail("download called")
        raise FileNotFoundError("not cached")

    monkeypatch.setattr(huggingface_hub, "snapshot_download", no_network_download)
    assert transcriber.prewarm(blocking=True)["reason"] == "disk"


def test_free_bytes_uses_existing_parent_for_clean_install(monkeypatch, tmp_path):
    model_dir = tmp_path / "missing" / "models"
    checked = []
    monkeypatch.setattr(transcriber, "_MODEL_DIR", model_dir)
    monkeypatch.setattr(
        transcriber.shutil,
        "disk_usage",
        lambda path: checked.append(path) or type("D", (), {"free": 2_000_000_000})(),
    )
    assert transcriber._free_bytes() == 2_000_000_000
    assert checked == [tmp_path]


def test_prewarm_cached_model_skips_disk_check(monkeypatch):
    import faster_whisper.utils

    monkeypatch.setattr(faster_whisper.utils, "download_model", lambda *a, **kw: "/cached")
    monkeypatch.setattr(
        transcriber.shutil, "disk_usage", lambda _: pytest.fail("disk check called")
    )
    assert transcriber.prewarm(blocking=True)["state"] == "ready"


def test_blocking_wait_timeout_does_not_change_download_state(monkeypatch):
    with transcriber._dl_lock:
        transcriber._dl.update(state="downloading", reason=None, detail=None)
    times = iter((0, 20 * 60 + 1))
    monkeypatch.setattr(transcriber.time, "monotonic", lambda: next(times))

    result = transcriber.prewarm(blocking=True)

    assert result["state"] == "downloading"
    with transcriber._dl_lock:
        assert transcriber._dl["state"] == "downloading"
        assert transcriber._dl["reason"] is None


def test_cli_prewarm_never_uses_network(monkeypatch, tmp_path):
    import huggingface_hub

    monkeypatch.setattr(transcriber, "_backend", lambda: "cli")
    monkeypatch.setattr(transcriber, "GGML_MODEL", str(tmp_path / "missing.bin"))
    monkeypatch.setattr(huggingface_hub, "snapshot_download", lambda *a, **kw: pytest.fail("network called"))
    result = transcriber.prewarm(blocking=True)
    assert result["state"] == "failed"
    assert result["reason"] == "other"


def test_transcribe_faster_not_ready_starts_prewarm(monkeypatch):
    monkeypatch.setattr(transcriber, "_faster_status_dict", lambda: {"state": "absent"})
    called = []
    monkeypatch.setattr(transcriber, "prewarm", lambda blocking=False: called.append(blocking))
    with pytest.raises(transcriber.ModelNotReady):
        transcriber._transcribe_faster("audio.wav", "ko")
    assert called == [False]
