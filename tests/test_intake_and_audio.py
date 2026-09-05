"""
빠른 등록(텍스트 → 필드 추출)과 녹취(오디오 → 전사 → 요약 → 상담 이력) 테스트.

LLM / Whisper 호출은 전부 목으로 대체 (느리고 비결정적이라).
"""
import base64

import pytest

pytest.importorskip("httpx")

_TEST_KEY_B64 = base64.b64encode(b"0" * 32).decode("ascii")

INTAKE_TEXT = """김민지
010-1234-5678
900101-2345678
효동로 291 금호아파트 101-1054
주부"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    from database import crypto

    monkeypatch.setenv("CUSTOMER_DB_PATH", str(tmp_path / "c.sqlite3"))
    monkeypatch.setenv("CUSTOMER_DB_KEY_B64", _TEST_KEY_B64)
    crypto.reset_cache()
    from fastapi.testclient import TestClient
    import main

    yield TestClient(main.app)
    crypto.reset_cache()


# ---------- 빠른 등록 (텍스트 파싱) ----------

def test_intake_parse_extracts_fields(client, monkeypatch):
    monkeypatch.setenv("RRN_INPUT_ENABLED", "1")
    from database import intake

    monkeypatch.setattr(
        intake,
        "_call_llm",
        lambda *a, **k: {
            "name": "김민지",
            "phone": "01012345678",
            "birth_date": "1990-01-01",
            "gender": "F",
            "email": None,
            "address": "효동로 291 금호아파트 101-1054",
            "occupation": "주부",
            "rrn": "900101-2345678",
            "memo": None,
        },
    )

    r = client.post("/customers/intake/parse", json={"text": INTAKE_TEXT})
    assert r.status_code == 200, r.text
    f = r.json()["fields"]
    assert f["name"] == "김민지"
    assert f["phone"] == "010-1234-5678"       # 11자리 → 하이픈 정규화
    assert f["rrn"] == "9001012345678"          # 13자리 정규형
    assert f["occupation"] == "주부"
    assert f["gender"] == "F" and f["birth_date"] == "1990-01-01"
    assert r.json()["warnings"] == []

    # 추출값을 그대로 등록에 넘기면 저장된다
    created = client.post("/customers", json={k: v for k, v in f.items() if v})
    assert created.status_code == 201
    body = created.json()
    assert body["occupation"] == "주부"
    assert body["rrn"] == "9001012345678"


def test_intake_parse_warns_on_bad_rrn(client, monkeypatch):
    monkeypatch.setenv("RRN_INPUT_ENABLED", "1")
    from database import intake

    monkeypatch.setattr(
        intake, "_call_llm", lambda *a, **k: {"name": "홍길동", "rrn": "12345"}
    )
    r = client.post("/customers/intake/parse", json={"text": "홍길동 12345"})
    assert r.status_code == 200
    assert any("주민등록번호" in w for w in r.json()["warnings"])


def test_intake_parse_requires_text(client):
    assert client.post("/customers/intake/parse", json={"text": ""}).status_code == 422


def test_intake_recovers_full_rrn_when_llm_splits_it(client, monkeypatch):
    """LLM 이 붙어있는 13자리를 '앞6=생년월일 / 뒤7=rrn' 으로 쪼개도 원문에서 복구한다."""
    monkeypatch.setenv("RRN_INPUT_ENABLED", "1")
    from database import intake

    monkeypatch.setattr(
        intake, "_call_llm",
        lambda *a, **k: {"name": "이영희", "birth_date": "1991-02-01", "rrn": "1621916"},
    )
    r = client.post("/customers/intake/parse", json={"text": "이영희 9102011621916"})
    f = r.json()["fields"]
    assert f["rrn"] == "9102011621916"
    assert not r.json()["warnings"]


def test_bulk_recovers_full_rrn_per_person_from_source(client, monkeypatch):
    """여러 명이면 이름 옆 주민번호로 각자 복구 (LLM 이 뒷자리를 오염시켜도)."""
    monkeypatch.setenv("RRN_INPUT_ENABLED", "1")
    from database import intake

    monkeypatch.setattr(
        intake, "_call_llm",
        lambda *a, **k: {"customers": [
            {"name": "박철수", "rrn": "1234567890"},   # 뒤에 없는 숫자가 붙음
            {"name": "이순자", "rrn": "7503034567890"},
        ]},
    )
    rows = intake.extract_multiple("박철수 9002021234567 그리고 이순자 7503034567890")
    got = {x["fields"]["name"]: x["fields"]["rrn"] for x in rows}
    assert got["박철수"] == "9002021234567"
    assert got["이순자"] == "7503034567890"


def test_intake_moves_rrn_out_of_memo(client, monkeypatch):
    """모델이 주민번호를 memo 에 넣어도 rrn 칸으로 옮기고 memo 에서 지운다."""
    monkeypatch.setenv("RRN_INPUT_ENABLED", "1")
    from database import intake

    monkeypatch.setattr(
        intake, "_call_llm",
        lambda *a, **k: {"name": "김테스트", "memo": "850101-1234567, 보험료 10만원대"},
    )
    r = client.post("/customers/intake/parse", json={"text": "김테스트 850101-1234567 10만원대"})
    f = r.json()["fields"]
    assert f["rrn"] == "8501011234567"
    assert "1234567" not in (f["memo"] or "")
    assert "10만원대" in (f["memo"] or "")


# ---------- 녹취 → 상담 이력 ----------


@pytest.mark.parametrize(("filename", "expected"), [
    ("통화 01083518688#_260827_190045.m4a", ("010-8351-8688", "2026-08-27T19:00:45")),
    ("녹음 2024-08-27 19시 00분.m4a", (None, "2024-08-27T19:00:00")),
    ("녹음 2024. 8. 27. 오후 7:00.m4a", (None, "2024-08-27T19:00:00")),
    ("20240827_190045.m4a", (None, "2024-08-27T19:00:45")),
    ("회의 2024-08-27_19.00.m4a", (None, "2024-08-27T19:00:00")),
    ("통화 01099998888.m4a", ("010-9999-8888", None)),
    ("회의 240827.m4a", (None, "2024-08-27T00:00:00")),
    ("새로운 녹음 1.m4a", (None, None)),
    ("통화 01083518688#_991332_296099.m4a", ("010-8351-8688", None)),
    ("녹음 2024-08-27 25:00.m4a", (None, None)),
    ("녹음 2024. 8. 27. 오후 25:00.m4a", (None, None)),
    ("녹음 01012345678 2024-08-27 19:00.m4a", ("010-1234-5678", "2024-08-27T19:00:00")),
    ("녹음 01012345678 2024-08-27.m4a", ("010-1234-5678", "2024-08-27T00:00:00")),
])
def test_parse_call_filename_patterns(filename, expected):
    from database.router import _parse_call_filename

    assert _parse_call_filename(filename) == expected


def test_capture_audio_warns_when_filename_has_no_phone_or_datetime(client, monkeypatch):
    import importlib

    router = importlib.import_module("database.router")

    monkeypatch.setattr(router, "_transcribe_and_summarize", lambda *a, **k: {
        "stt": {"text": "상담 내용", "audio_minutes": 0.1},
        "summary": {
            "title": "상담", "summary": "요약", "key_points": [],
            "customer_interests": [], "action_items": [], "follow_up_at": None,
        },
        "fn_phone": None,
        "fn_dt": None,
    })
    monkeypatch.setattr(router, "extract_customer_fields", lambda *a, **k: {
        "fields": {"name": "홍길동", "phone": None}, "warnings": [],
    })

    r = client.post(
        "/capture",
        files={"file": ("새로운 녹음 1.m4a", b"fake", "audio/m4a")},
    )
    assert r.status_code == 200, r.text
    assert "파일명에서 전화번호·통화일시를 읽지 못했습니다. 상담 저장 시 직접 입력해 주세요." in r.json()["items"][0]["warnings"]

@pytest.fixture
def whisper_mocked(monkeypatch):
    import whisper

    monkeypatch.setattr(
        whisper,
        "transcribe",
        lambda *a, **k: {
            "text": "안녕하세요 고객님 지난번 문의하신 암보험 관련해서 연락드렸습니다 ...",
            "segments": [],
            "language": "ko",
            "audio_minutes": 3.5,
            "model": "large-v3",
        },
    )
    monkeypatch.setattr(
        whisper,
        "summarize",
        lambda *a, **k: {
            "title": "암보험 문의 후속 통화",
            "summary": "고객이 암 진단비 보장 한도를 문의함. 5천만원 플랜 안내함.",
            "key_points": ["암 진단비 한도 문의", "5천만원 플랜 안내"],
            "customer_interests": ["암보험"],
            "action_items": ["견적서 발송"],
            "follow_up_at": "2026-09-15",
        },
    )


def test_from_audio_creates_consultation(client, whisper_mocked):
    cid = client.post("/customers", json={"name": "녹취고객"}).json()["id"]

    r = client.post(
        f"/customers/{cid}/consultations/from-audio",
        files={"file": ("call.m4a", b"\x00\x01\x02fake-audio", "audio/m4a")},
        data={"channel": "전화 녹취"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["consultation"]["title"] == "암보험 문의 후속 통화"
    assert "5천만원 플랜" in body["consultation"]["content"]
    assert body["consultation"]["transcript"].startswith("안녕하세요")
    assert body["consultation"]["follow_up_at"] == "2026-09-15"
    assert body["consultation"]["channel"] == "전화 녹취"
    assert body["audio_minutes"] == 3.5

    # 고객 상세의 상담 이력에 들어가 있다
    detail = client.get(f"/customers/{cid}").json()
    assert len(detail["consultations"]) == 1
    assert detail["consultations"][0]["transcript"].startswith("안녕하세요")


def test_from_audio_transcript_encrypted_at_rest(client, whisper_mocked, tmp_path):
    cid = client.post("/customers", json={"name": "녹취암호화"}).json()["id"]
    client.post(
        f"/customers/{cid}/consultations/from-audio",
        files={"file": ("call.m4a", b"fake", "audio/m4a")},
    )
    raw = (tmp_path / "c.sqlite3").read_bytes()
    assert "안녕하세요 고객님".encode("utf-8") not in raw


def test_consultation_from_audio_stores_note(client, whisper_mocked):
    cid = client.post("/customers", json={"name": "메모고객"}).json()["id"]

    r = client.post(
        f"/customers/{cid}/consultations/from-audio",
        files={"file": ("call.m4a", b"\x00fake", "audio/m4a")},
        data={
            "note": "김부장님과의 갱신 상담 통화\n둘째 줄",
            "consulted_at": "2025-11-03",
        },
    )
    assert r.status_code == 201, r.text
    k = r.json()["consultation"]
    assert k["content"].startswith("[업로드 메모] 김부장님과의 갱신 상담 통화")
    assert "5천만원 플랜" in k["content"]  # 원래 요약도 그대로 뒤에 붙는다
    assert k["consulted_at"] == "2025-11-03"


def test_consultation_from_audio_note_defaults_unchanged(client, whisper_mocked):
    """note 미전달 시 기존 동작과 동일 (회귀 방지)."""
    cid = client.post("/customers", json={"name": "무메모"}).json()["id"]
    r = client.post(
        f"/customers/{cid}/consultations/from-audio",
        files={"file": ("call.m4a", b"\x00fake", "audio/m4a")},
    )
    assert r.status_code == 201
    k = r.json()["consultation"]
    assert "[업로드 메모]" not in k["content"]
    assert k["title"] == "암보험 문의 후속 통화"


def test_from_audio_404_for_unknown_customer(client, whisper_mocked):
    r = client.post(
        "/customers/nope/consultations/from-audio",
        files={"file": ("call.m4a", b"fake", "audio/m4a")},
    )
    assert r.status_code == 404


# ---------- 진단 ----------

def test_diagnostics_bundle(client):
    d = client.get("/diagnostics").json()
    assert set(d) >= {"engine", "customer_db", "whisper", "data", "rag"}
    assert "credits" not in d  # 크레딧 시스템 제거 — 재도입 방지
    assert d["customer_db"]["encryption"].startswith("AES-256-GCM")
    assert "large-v3" in d["whisper"]["model"]
    assert "customers" in d["data"]


# ---------- 녹취 → 신규 고객 (미리보기) ----------

import importlib

_router_mod = importlib.import_module("database.router")  # 'database.router' 는 __init__ 에서 APIRouter 로 가려져 있음


def test_parse_call_filename():
    _parse_call_filename = _router_mod._parse_call_filename

    ph, dt = _parse_call_filename("통화 01083518688#_260827_190045.m4a")
    assert ph == "010-8351-8688"
    assert dt == "2026-08-27T19:00:45"

    ph2, dt2 = _parse_call_filename("recording_01033334444.mp3")
    assert ph2 == "010-3333-4444" and dt2 is None

    assert _parse_call_filename("memo.m4a") == (None, None)


def test_intake_from_audio_preview(client, whisper_mocked, monkeypatch):
    monkeypatch.setattr(
        _router_mod,
        "extract_customer_fields",
        lambda *a, **k: {
            "fields": {"name": "김연숙", "phone": None, "birth_date": None, "gender": None,
                       "email": None, "address": None, "occupation": None, "rrn": None, "memo": None},
            "warnings": [],
            "model": "qwen2.5:14b",
        },
    )

    r = client.post(
        "/customers/intake/from-audio",
        files={"file": ("통화 01083518688#_260827_190045.m4a", b"fake-audio", "audio/m4a")},
    )
    assert r.status_code == 200, r.text
    b = r.json()
    # 파일명에서 전화번호/일시 채워짐
    assert b["fields"]["phone"] == "010-8351-8688"
    assert b["consultation"]["consulted_at"] == "2026-08-27T19:00:45"
    assert b["consultation"]["channel"] == "전화 녹취"
    assert b["consultation"]["transcript"].startswith("안녕하세요")
    assert b["consultation"]["title"] == "암보험 문의 후속 통화"
    # 이 단계에선 저장 안 됨
    assert client.get("/customers").json()["customers"] == []

    # 프론트가 하듯: 고객 생성 → 상담 붙이기
    cid = client.post("/customers", json={k: v for k, v in b["fields"].items() if v}).json()["id"]
    k = client.post(f"/customers/{cid}/consultations", json=b["consultation"])
    assert k.status_code == 201
    detail = client.get(f"/customers/{cid}").json()
    assert len(detail["consultations"]) == 1
    assert detail["consultations"][0]["consulted_at"] == "2026-08-27T19:00:45"


def test_intake_from_audio_warns_when_filename_has_no_phone_or_datetime(
    client, whisper_mocked, monkeypatch
):
    monkeypatch.setattr(
        _router_mod,
        "extract_customer_fields",
        lambda *a, **k: {"fields": {"name": "김연숙", "phone": None}, "warnings": []},
    )

    r = client.post(
        "/customers/intake/from-audio",
        files={"file": ("새로운 녹음 1.m4a", b"fake-audio", "audio/m4a")},
    )
    assert r.status_code == 200, r.text
    assert "파일명에서 전화번호·통화일시를 읽지 못했습니다. 상담 저장 시 직접 입력해 주세요." in r.json()["warnings"]


# ---------- 홈 던져넣기 (/capture) — 통합: 1명이든 N명이든 items[] ----------

def _mock_bulk(monkeypatch, customers):
    from database import intake

    monkeypatch.setattr(intake, "_call_llm", lambda *a, **k: {"customers": customers})


def test_capture_text_single_new(client, monkeypatch):
    _mock_bulk(monkeypatch, [{"name": "신규자", "phone": "01011112222", "address": "서울"}])
    b = client.post("/capture", data={"text": "신규자 010-1111-2222 서울"}).json()
    assert len(b["items"]) == 1
    it = b["items"][0]
    assert it["match"] is None and it["consultation"] is None
    assert it["fields"]["name"] == "신규자" and it["fields"]["phone"] == "010-1111-2222"


def test_capture_text_multiple(client, monkeypatch):
    cid = client.post("/customers", json={"name": "기존", "phone": "010-3434-5656"}).json()["id"]
    _mock_bulk(monkeypatch, [
        {"name": "새사람", "phone": "01099998888"},
        {"name": "기존", "phone": "010-3434-5656"},
        {"name": "이순신", "birth_date": "1985-05-05", "rrn": "8505051234567"},
    ])
    b = client.post("/capture", data={"text": "세 줄"}).json()
    assert len(b["items"]) == 3
    assert b["items"][0]["match"] is None
    assert b["items"][1]["match"]["id"] == cid and b["items"][1]["match_reason"] == "phone"


def test_capture_audio_single_with_consultation(client, whisper_mocked, monkeypatch):
    cid = client.post("/customers", json={"name": "김연숙", "phone": "010-8351-8688"}).json()["id"]
    monkeypatch.setattr(
        _router_mod, "extract_customer_fields",
        lambda *a, **k: {"fields": {k2: None for k2 in
            ["name", "phone", "birth_date", "gender", "email", "address", "occupation", "rrn", "memo"]},
            "warnings": [], "model": "x"},
    )
    b = client.post(
        "/capture",
        files={"file": ("통화 01083518688#_260827_190045.m4a", b"fake", "audio/m4a")},
    ).json()
    assert len(b["items"]) == 1
    it = b["items"][0]
    assert it["match"]["id"] == cid  # 파일명 전화번호로 매칭
    assert it["consultation"]["consulted_at"] == "2026-08-27T19:00:45"
    assert it["consultation"]["transcript"].startswith("안녕하세요")


def test_capture_requires_input(client):
    assert client.post("/capture", data={"text": ""}).status_code == 400


def test_capture_text_file_is_analyzed(client, monkeypatch):
    """.txt 파일을 끌어다 넣어도 텍스트처럼 분석한다."""
    _mock_bulk(monkeypatch, [
        {"name": "파일사람", "phone": "01055556666", "occupation": "자영업"},
    ])
    b = client.post(
        "/capture",
        files={"file": ("고객메모.txt", "파일사람 010-5555-6666 자영업".encode("utf-8"), "text/plain")},
    ).json()
    assert len(b["items"]) == 1
    assert b["items"][0]["fields"]["name"] == "파일사람"
    assert b["items"][0]["consultation"] is None


def test_capture_context_is_prepended_for_analysis(client, monkeypatch):
    """업로드 파일에 딸린 참고 정보(context)는 저장이 아니라 추출 프롬프트에 들어간다."""
    from database import intake

    seen = {}

    def fake_call_llm(prompt, model=None, *a, **k):
        seen["prompt"] = prompt
        return {"customers": [{"name": "최민성", "phone": "01012345678"}]}

    monkeypatch.setattr(intake, "_call_llm", fake_call_llm)
    b = client.post(
        "/capture",
        data={"context": "최민성 KB손해보험"},
        files={"file": ("제안서.txt", "고객 관련 텍스트".encode("utf-8"), "text/plain")},
    ).json()
    assert len(b["items"]) == 1
    assert "최민성 KB손해보험" in seen["prompt"]
    assert "[참고 정보]" in seen["prompt"]


def test_capture_context_defaults_empty(client, monkeypatch):
    """context 미전송 시 프롬프트에 참고 정보 머리말이 붙지 않는다 (기존 동작 유지)."""
    from database import intake

    seen = {}
    monkeypatch.setattr(
        intake, "_call_llm",
        lambda prompt, *a, **k: (seen.__setitem__("prompt", prompt), {"customers": [{"name": "홍길동"}]})[1],
    )
    client.post(
        "/capture",
        files={"file": ("메모.txt", "홍길동 자영업".encode("utf-8"), "text/plain")},
    )
    assert "[참고 정보]" not in seen["prompt"]


_EMPTY_POLICY = {k: None for k in
                 ["insurer", "product_name", "plan_type", "premium_won",
                  "payment_cycle", "insured_period", "payment_period", "issued_date"]}


def _mock_single(monkeypatch, fields, policy=None, policies_list=None):
    full = {k: None for k in
            ["name", "phone", "birth_date", "gender", "email", "address", "occupation", "rrn", "memo"]}
    full.update(fields)
    monkeypatch.setattr(
        _router_mod, "extract_customer_fields",
        lambda *a, **k: {"fields": full, "warnings": [], "model": "x"},
    )
    import database.intake as _di

    pol = {**_EMPTY_POLICY, **(policy or {})}
    monkeypatch.setattr(
        _di, "extract_policy_fields",
        lambda *a, **k: {"policy": pol, "warnings": [], "model": "x"},
    )
    plist = [{**_EMPTY_POLICY, **p} for p in (policies_list or [])]
    monkeypatch.setattr(
        _di, "extract_policies_list",
        lambda *a, **k: {"policies": plist, "warnings": [], "model": "x"},
    )


def test_capture_pdf_is_analyzed(client, monkeypatch):
    """PDF 를 넣으면 글자를 뽑아 (단건) 고객정보로 분석한다."""
    monkeypatch.setattr(
        _router_mod, "_pdf_extract_text",
        lambda data, max_pages=8: "계약자 김서류\n010-7777-1234\n서울시 강남구\n회사원",
    )
    _mock_single(monkeypatch, {"name": "김서류", "phone": "010-7777-1234", "address": "서울시 강남구"})
    b = client.post(
        "/capture",
        files={"file": ("청약서.pdf", b"%PDF-1.4 fake", "application/pdf")},
    ).json()
    assert len(b["items"]) == 1
    assert b["items"][0]["fields"]["name"] == "김서류"
    assert b["items"][0]["fields"]["phone"] == "010-7777-1234"
    assert b["items"][0]["consultation"] is None
    assert "policies" in b["items"][0]  # 보험 문서 → policies 키 포함


def test_capture_pdf_extracts_policy_and_coverages(client, monkeypatch):
    """보험 제안서 PDF → 고객 + 보험계약(증권 헤더) + 가입담보목록 원문."""
    doc = (
        "정지은 고객님을 위한 가입제안서\n계약자\n정지은 (여 31세)\n"
        "보험회사 한화손해보험\n상품명 한화 시그니처 여성 건강보험4.0\n"
        "가입담보목록\n순번 가입담보\n상해사망 15,000만원\n암진단비 4,000만원\n"
        "약관을 참고 하시기 바랍니다\n"
    )
    monkeypatch.setattr(_router_mod, "_pdf_extract_text", lambda data, max_pages=8: doc)
    _mock_single(
        monkeypatch,
        {"name": "정지은", "gender": "F"},
        policy={"insurer": "한화손해보험", "product_name": "한화 시그니처 여성 건강보험4.0",
                "plan_type": "보장", "premium_won": 107244, "payment_cycle": "MONTHLY",
                "insured_period": "90세만기", "payment_period": "30년납"},
    )
    b = client.post(
        "/capture", files={"file": ("정지은님.pdf", b"%PDF-1.7 x", "application/pdf")},
    ).json()
    it = b["items"][0]
    assert it["policies"][0]["insurer"] == "한화손해보험"
    assert it["policies"][0]["premium_won"] == 107244
    assert "상해사망" in it["coverages_text"] and "암진단비" in it["coverages_text"]
    # 가입제안서 → '검토' 상담 이력 1건이 함께 딸려온다
    k = it["consultation"]
    assert k and k["channel"] == "가입제안서"
    assert "한화 시그니처" in k["title"]
    assert "107,244원" in k["content"]

    # 프론트가 하듯: 고객 생성 → 상담 붙이기 → 상세에 남는다
    cid = client.post("/customers", json={"name": it["fields"]["name"]}).json()["id"]
    assert client.post(f"/customers/{cid}/consultations", json=k).status_code == 201
    detail = client.get(f"/customers/{cid}").json()
    assert detail["consultations"][0]["channel"] == "가입제안서"


def test_capture_proposal_contractor_differs_from_insured(client, monkeypatch):
    """가입제안서에서 계약자 ≠ 피보험자 → 피보험자 기준으로 등록, 계약자는 계약 필드로."""
    doc = (
        "손유진 고객님을 위한 가입제안서 (피보험자 안우성고객님)\n"
        "보험회사 NH농협생명\n상품명 NH올원더풀간병안심요양보험\n"
        "약관을 참고 하시기 바랍니다\n"
    )
    monkeypatch.setattr(_router_mod, "_pdf_extract_text", lambda data, max_pages=8: doc)
    _mock_single(
        monkeypatch,
        {"name": "손유진"},
        policy={
            "insurer": "NH농협생명",
            "product_name": "NH올원더풀간병안심요양보험",
            "policyholder_name": "손유진",
            "insured_name": "안우성",
        },
    )
    b = client.post(
        "/capture", files={"file": ("손유진님.pdf", b"%PDF-1.7 x", "application/pdf")},
    ).json()
    it = b["items"][0]
    assert it["fields"]["name"] == "안우성"                     # 피보험자 기준 등록
    assert it["policies"][0]["policyholder_name"] == "손유진"   # 계약자는 계약에 실림
    assert "insured_name" not in it["policies"][0]
    assert any("계약자" in w and "피보험자" in w for w in it["warnings"])


def test_capture_bojang_analysis_multi_policy(client, monkeypatch):
    """보장분석서 → 여러 건의 보유계약 + 마스킹된 이름은 파일명에서 복구 + 가짜 주민번호 제거."""
    doc = (
        "나*원 고객님을 위한 간편보장분석\n남 36세 · 1991.02.01 · 상령일 08월01일\n"
        "보장현황 미가입 부족 충분\n"
        "질병사망 부족 11% 일반암 진단비 미가입 0%\n"
        "보장 1,100만원 권장금액 1억원 보장 0원 권장금액 1억5,000만원\n"
        "보유계약리스트\n한화생명 케어백간병플러스보험 110세 매월납/10년 105,002원\n"
        "삼성화재 건강보험 New내돈내삼 90세 매월납/56년 92,734원\n"
        "GA2-2지점 이영희 컨설턴트 010-2345-6789\n"
    )
    monkeypatch.setattr(_router_mod, "_pdf_extract_text", lambda data, max_pages=8: doc)
    # 모델이 마스킹된 이름·가짜 주민번호(생년월일 9자리)를 냈다고 가정
    _mock_single(
        monkeypatch,
        {"name": "나*원", "gender": "M", "birth_date": "1991-02-01", "rrn": "199102013"},
        policies_list=[
            {"insurer": "한화생명", "product_name": "케어백간병플러스보험", "premium_won": 105002,
             "insured_period": "110세", "payment_period": "매월납/10년"},
            {"insurer": "삼성화재", "product_name": "건강보험 New내돈내삼", "premium_won": 92734,
             "insured_period": "90세", "payment_period": "매월납/56년"},
        ],
    )
    b = client.post(
        "/capture",
        files={"file": ("260901_M보장분석_이영희님.pdf", b"%PDF x", "application/pdf")},
    ).json()
    it = b["items"][0]
    assert it["doc_type"] == "보장분석"
    assert it["fields"]["name"] == "이영희"       # 파일명에서 복구
    assert it["fields"]["rrn"] is None            # 9자리 → 가짜로 보고 제거
    assert it["fields"]["birth_date"] == "1991-02-01"
    assert len(it["policies"]) == 2
    assert {p["insurer"] for p in it["policies"]} == {"한화생명", "삼성화재"}
    k = it["consultation"]
    assert k["channel"] == "보장분석"
    assert "105,002원" in k["content"] and "92,734원" in k["content"]
    # 보장현황이 구조화되어 나온다 (표로 렌더)
    cs = {c["name"]: c for c in it["coverage_status"]}
    assert cs["질병사망"]["status"] == "부족" and cs["질병사망"]["pct"] == 11
    assert cs["질병사망"]["recommended"] == "1억원"
    assert cs["일반암 진단비"]["status"] == "미가입"
    assert "질병사망: 부족 11%" in k["content"]        # 상담이력 텍스트에도 정리돼 들어감

    # 보장현황 표가 상담이력에 구조화(JSON)로 저장되어, 고객 상세에서 표로 다시 볼 수 있다
    import json as _json
    assert _json.loads(k["coverage_json"])[0]["name"] == "질병사망"
    cid = client.post("/customers", json={"name": it["fields"]["name"]}).json()["id"]
    client.post(f"/customers/{cid}/consultations", json=k)
    saved = client.get(f"/customers/{cid}").json()["consultations"][0]
    assert _json.loads(saved["coverage_json"])[1]["name"] == "일반암 진단비"


def test_capture_scanned_pdf_reports_no_text(client, monkeypatch):
    """글자도 없고 OCR 도 아무것도 못 뽑으면 422."""
    monkeypatch.setattr(_router_mod, "_pdf_extract_text", lambda data, max_pages=8: "")
    monkeypatch.setattr(_router_mod, "_ocr_pdf", lambda *a, **k: "")
    r = client.post("/capture", files={"file": ("스캔.pdf", b"%PDF fake", "application/pdf")})
    assert r.status_code == 422
    assert "글자" in r.json()["detail"]


def test_capture_pdf_ocr_fallback(client, monkeypatch):
    """글자 없는 PDF → OCR 폴백으로 텍스트를 얻어 고객정보 분석 (지어낸 주민번호는 버림)."""
    monkeypatch.setattr(_router_mod, "_pdf_extract_text", lambda data, max_pages=8: "")
    monkeypatch.setattr(
        _router_mod, "_ocr_pdf",
        lambda *a, **k: "정지은 고객님을 위한 가입제안서\n계약자\n정지은 (여 31세)\n직업\n수동 포장원, 2급\n모집자 이영희 010-2345-6789",
    )
    # 모델이 문서에 없는 주민번호를 지어냈다고 가정 → _strip_fabricated 가 걸러야 한다
    _mock_single(monkeypatch, {"name": "정지은", "occupation": "수동 포장원", "rrn": "6208051234567", "birth_date": "1962-08-05", "gender": "M"})
    b = client.post(
        "/capture",
        files={"file": ("정지은님.pdf", b"%PDF-1.7 vector-only", "application/pdf")},
    ).json()
    it = b["items"][0]
    f = it["fields"]
    assert len(b["items"]) == 1
    assert f["name"] == "정지은"
    assert f["occupation"] == "수동 포장원"
    assert f["rrn"] is None  # 문서에 없던 주민번호 → 창작으로 보고 버림
    assert f["gender"] == "F"  # "(여 31세)" 표기로 보정
    # 주민번호는 없지만 "(여 31세)" 로 생년(연도)은 추정된다 (issued_date 없으면 올해 - 31)
    from datetime import date as _d
    assert f["birth_date"] == f"{_d.today().year - 31}-01-01"
    assert any("추정" in w for w in it["warnings"])


def test_estimate_birthdate_from_age():
    est = _router_mod._estimate_birthdate_from_age
    age_in_text = _router_mod._age_in_text

    # "90세만기" 는 나이로 오인하지 않는다
    assert age_in_text("보험기간 90세만기/30년납") is None
    assert age_in_text("계약자 정지은 (여 31세)") == 31

    # 보험상령일(10월 12일) → 생일 4월 12일. 발행일 2026-01-26 엔 생일 전 → 1994.
    f = {}
    w = est(f, "계약자 정지은 (여 31세)\n보험나이 변경일자 2026년 10월 12일", "2026-01-26")
    assert f["birth_date"] == "1994-04-12"
    assert w and "추정" in w

    # 상령일 없으면 연도만 (01-01)
    f2 = {}
    est(f2, "피보험자 (남 45세)", "2026-01-26")
    assert f2["birth_date"] == "1981-01-01"

    # 이미 생년월일/주민번호 있으면 건드리지 않음
    f3 = {"birth_date": "1990-05-05"}
    assert est(f3, "(여 31세)", "2026-01-26") is None
    assert f3["birth_date"] == "1990-05-05"


def test_capture_pdf_estimates_birthdate(client, monkeypatch):
    doc = "정지은 고객님을 위한 가입제안서\n계약자 정지은 (여 31세)\n보험나이 변경일자: 매년 10월 12일\n보험기간 90세만기/30년납"
    monkeypatch.setattr(_router_mod, "_pdf_extract_text", lambda data, max_pages=8: doc)
    _mock_single(monkeypatch, {"name": "정지은", "gender": "F"},
                 policy={"insurer": "한화손해보험", "issued_date": "2026-01-26"})
    b = client.post("/capture", files={"file": ("x.pdf", b"%PDF x", "application/pdf")}).json()
    it = b["items"][0]
    assert it["fields"]["birth_date"] == "1994-04-12"
    assert any("추정" in w for w in it["warnings"])


def test_capture_unsupported_binary_file(client):
    r = client.post(
        "/capture",
        files={"file": ("사진.png", b"\x89PNG\r\n\x1a\n\xff\xd8\xff", "image/png")},
    )
    assert r.status_code == 415


def test_capture_name_plus_birthdate_is_same_person(client, monkeypatch):
    client.post("/customers", json={"name": "이순신", "birth_date": "1970-01-01"})
    cid = client.post("/customers", json={"name": "이순신", "birth_date": "1985-05-05", "phone": "010-2020-3030"}).json()["id"]
    _mock_bulk(monkeypatch, [{"name": "이순신", "birth_date": "1985-05-05", "rrn": "8505051234567", "address": "부산"}])
    b = client.post("/capture", data={"text": "이순신 부산"}).json()
    assert b["items"][0]["match"]["id"] == cid
    assert b["items"][0]["match_reason"] == "name+birthdate"


def test_customers_match_endpoint(client):
    cid = client.post("/customers", json={"name": "정약용", "phone": "010-7000-8000", "birth_date": "1990-03-03"}).json()["id"]

    r = client.get("/customers/match", params={"phone": "01070008000"}).json()
    assert r["match"]["id"] == cid and r["match_reason"] == "phone"

    r = client.get("/customers/match", params={"name": "정약용", "birth_date": "1990-03-03"}).json()
    assert r["match"]["id"] == cid and r["match_reason"] == "name+birthdate"

    r = client.get("/customers/match", params={"name": "정약용", "birth_date": "2000-01-01"}).json()
    assert r["match_reason"] == "name"  # 이름만

    assert client.get("/customers/match", params={"name": "없는사람"}).json()["match"] is None


# ---------- 여러 명 일괄 등록 ----------

def test_intake_bulk_parses_multiple_and_flags_dups(client, monkeypatch):
    from database import intake

    # 1명은 이미 존재하게 만들어 중복으로 잡히는지 확인
    client.post("/customers", json={"name": "기등록", "phone": "010-9000-1000"})

    monkeypatch.setattr(
        intake, "_call_llm",
        lambda *a, **k: {"customers": [
            {"name": "새사람하나", "phone": "01011112222", "occupation": "회사원"},
            {"name": "기등록", "phone": "010-9000-1000"},
            {"name": None, "address": "부산"},  # 이름 없음 → 경고
        ]},
    )
    r = client.post("/customers/intake/bulk", json={"text": "여러 줄\n텍스트"})
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 3

    assert items[0]["fields"]["name"] == "새사람하나"
    assert items[0]["fields"]["phone"] == "010-1111-2222"  # 정규화
    assert items[0]["match"] is None

    assert items[1]["match"] is not None and items[1]["match_reason"] == "phone"
    assert items[1]["match"]["name"] == "기등록"

    assert any("이름" in w for w in items[2]["warnings"])


def test_intake_bulk_requires_text(client):
    assert client.post("/customers/intake/bulk", json={"text": ""}).status_code == 422


def test_birthdate_normalization(client, monkeypatch):
    from database import intake

    # 8자리 → 하이픈
    monkeypatch.setattr(intake, "_call_llm", lambda *a, **k: {"name": "A", "birth_date": "19620805"})
    assert client.post("/customers/intake/parse", json={"text": "x"}).json()["fields"]["birth_date"] == "1962-08-05"

    # 주민번호에서 유도 (birth_date 가 엉망일 때)
    monkeypatch.setattr(intake, "_call_llm", lambda *a, **k: {"name": "B", "birth_date": "몰라", "rrn": "9003031234567"})
    assert client.post("/customers/intake/parse", json={"text": "x"}).json()["fields"]["birth_date"] == "1990-03-03"


# ---------- 실사용 GA 문서(가입제안서/보장분석) 추출 보강 ----------

# 문서 맨 앞에서만 한 번 나오는 표제(고유 문구) + 줄바꿈 없는 긴 필러(반복 문구라 그 자체엔
# '계약자'/'피보험자' 로 시작하는 줄이 없음) — 강한 앵커의 500자 되감기로도 표제까지는 안 닿을 만큼 길다.
_NH_BOILERPLATE = "계약전 알릴의무사항 안내문. " + ("이 약관은 계약자와 피보험자 사이의 권리 의무를 정합니다. " * 40)
_NH_SUMMARY = (
    "\n손유진 고객님을 위한 상품제안서\n"
    "계약자 손유진 여자 36세 2027-02-26 Q0514 01 위험4급\n"
    "피보험자 안우성 남자 40세 2027-04-01 Q0073 01 위험3급\n"
    "세부유형 NH올원더풀간병안심요양보험(무)해약미지급형2형(간편Ⅲ) 납입주기 월납\n"
    "실납입보험료 61,090\n"
)


def test_proposal_body_skips_boilerplate():
    doc = _NH_BOILERPLATE + _NH_SUMMARY
    body = _router_mod._proposal_body(doc)
    assert "실납입보험료" in body
    assert "계약자 손유진" in body
    assert "계약전 알릴의무사항" not in body  # 문서 맨 앞 표제(수천 자 앞)는 빠져야 한다


def test_proposal_body_prefers_strong_anchor_over_far_cover_title():
    """표지의 '고객님을 위한 가입제안서' 문구가 실제 표(다른 페이지)와 멀리 떨어져 있으면
    표지 대신 보험료 요약표 쪽을 앵커로 잡아야 한다 (실제 메트라이프 59쪽 제안서에서 재현된 버그)."""
    cover = "고객님을 위한 가입제안서\n" + ("약관 유의사항 안내 문구입니다.\n" * 400)  # 표와 멀리 떨어진 표지
    table = (
        "계약자 양영철 님 54세 (남자) 납입주기 월납\n"
        "피보험자 양영철 님 54세 (남자) 진단여부 무진단\n"
        "구분 가입금액 보험기간 납입기간 보험료\n"
        "합계보험료 ＄89.25 (￦134,390)\n"
    )
    doc = cover + table
    body = _router_mod._proposal_body(doc)
    assert "합계보험료" in body
    assert "계약자 양영철" in body


def test_parties_from_text():
    parties = _router_mod._parties_from_text

    nh = "계약자 손유진 여자 36세 2027-02-26 Q0514 01 위험4급\n피보험자 안우성 남자 40세 2027-04-01 Q0073 01 위험3급\n"
    assert parties(nh) == {"policyholder_name": "손유진", "insured_name": "안우성"}

    metlife = "계약자 양영철 님 54세 (남자) 납입주기 월납\n피보험자 양영철 님 54세 (남자) 진단여부 무진단\n"
    r = parties(metlife)
    assert r["policyholder_name"] == "양영철" and r["insured_name"] == "양영철"

    assert parties("(피보험자 안우성고객님)")["insured_name"] == "안우성"

    assert parties("홍길동 고객님을 위한 가입제안서")["policyholder_name"] == "홍길동"


def test_premium_from_text():
    premium = _router_mod._premium_from_text

    assert premium("실납입보험료 61,090\n납입주기 월납") == {"premium": 61090, "payment_cycle": "MONTHLY"}

    r = premium("합계보험료 ＄89.25 (￦134,390)")
    assert r["premium"] == 134390


def test_capture_proposal_fills_contractor_and_premium(client, monkeypatch):
    """NH 가입제안서: LLM 이 계약자/피보험자/보험료를 못 뽑아도(sparse) 규칙 기반으로 채운다."""
    doc = _NH_BOILERPLATE + _NH_SUMMARY
    monkeypatch.setattr(_router_mod, "_pdf_extract_text", lambda data, max_pages=8: doc)
    _mock_single(
        monkeypatch,
        {"name": None},
        policy={"insurer": "NH농협생명", "product_name": "NH올원더풀간병안심요양보험(무)해약미지급형2형(간편Ⅲ)"},
    )
    b = client.post(
        "/capture", files={"file": ("nh제안서.pdf", b"%PDF-1.7 x", "application/pdf")},
    ).json()
    it = b["items"][0]
    assert it["fields"]["name"] == "안우성"
    assert it["policies"][0]["policyholder_name"] == "손유진"
    assert it["policies"][0]["premium_won"] == 61090
    assert it["policies"][0]["payment_cycle"] == "MONTHLY"
    assert any("계약자" in w and "피보험자" in w for w in it["warnings"])


def test_parse_coverage_status_ga_total_layout():
    """GA '전체 보장현황' 비교표(미가입/부족 NN% 형식이 아닌) 레이아웃도 파싱한다."""
    doc = (
        "강현철 님의 전체 보장현황\n"
        "※ 기준담보/권장금액 : 기본형(37개)/표준형\n"
        "상해사망 7,100만 100만 2,000만 5,000만 -\n"
        "질병통원의료비 - - - - -\n"
        "사망\n"
        "호남GA 좋은사람들 2026-09-02\n"
    )
    cs = {c["name"]: c for c in _router_mod._parse_coverage_status(doc)}
    assert cs["상해사망"]["status"] == "가입"
    assert cs["상해사망"]["current"] == "7,100만"
    assert cs["질병통원의료비"]["status"] == "미가입"
    assert "사망" not in cs
    assert not any("호남GA" in n for n in cs)
