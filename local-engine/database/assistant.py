"""
AI 문의 — 자연어로 고객 관리(CRM) 데이터를 질의응답.

프론트의 'AI 문의' 탭이 `POST /assistant/ask` 로 질문을 보내면 여기서
  1) 파이썬으로 집계값을 선계산하고([집계] 블록),
  2) 전 고객의 압축 요약을 만들어([고객 데이터] 블록),
  3) 그 둘 + 최근 대화 + 질문을 작은 LLM(qwen2.5:7b)에게 넘겨 문장으로 답하게 한다.

의도 분류기는 두지 않는다. 서버는 무상태 — 대화 기록은 프론트가 매 요청에 재전송한다.

주민등록번호는 컨텍스트에 절대 넣지 않는다: 여기서는 rrn 을 빼고 주는
`repo.list_customers` / `repo.list_policies` / `repo.list_consultations` 만 쓴다
(`_get_customer_raw` / `customer_detail` 는 rrn 원문을 담으므로 사용 금지).
녹취 transcript · coverage_json 원문도 제외한다.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.request
from datetime import date, timedelta
from typing import Any, Optional

from . import repo

logger = logging.getLogger(__name__)

OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
# 문의 답변은 조회·문장화라 작은 모델로. 없으면 인테이크와 같은 모델.
LLM_MODEL = os.environ.get(
    "ASSISTANT_LLM_MODEL", os.environ.get("INTAKE_LLM_MODEL", "qwen2.5:7b")
)
SUMMARY_LLM_MODEL = os.environ.get("ASSISTANT_SUMMARY_LLM_MODEL", "qwen2.5:3b")
COMPLEX_LLM_MODEL = os.environ.get(
    "ASSISTANT_COMPLEX_LLM_MODEL", os.environ.get("ASSISTANT_LLM_MODEL", "qwen2.5:7b")
)


def _int_env(name: str, default: int, *, minimum: int = 0) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(minimum, value)


def _default_num_thread() -> int:
    """상원님 Windows PC 논리 코어 수에 맞추되, 환경변수로 언제든 끌 수 있게 한다."""
    raw = os.environ.get("OLLAMA_NUM_THREAD")
    if raw and raw.lower() == "auto":
        return 0
    if raw:
        return _int_env("OLLAMA_NUM_THREAD", 0, minimum=0)
    return max(1, os.cpu_count() or 1)


KEEP_ALIVE = os.environ.get(
    "OLLAMA_ASSISTANT_KEEP_ALIVE",
    os.environ.get("OLLAMA_LLM_KEEP_ALIVE", os.environ.get("OLLAMA_KEEP_ALIVE", "10m")),
)
NUM_CTX = _int_env("OLLAMA_ASSISTANT_NUM_CTX", _int_env("OLLAMA_NUM_CTX", 2048), minimum=512)
NUM_PREDICT = _int_env("OLLAMA_ASSISTANT_NUM_PREDICT", 512, minimum=128)
NUM_THREAD = _default_num_thread()


def _ollama_options() -> dict[str, int | float]:
    options: dict[str, int | float] = {
        "temperature": 0,
        "num_predict": NUM_PREDICT,
        "num_ctx": NUM_CTX,
    }
    if NUM_THREAD:
        options["num_thread"] = NUM_THREAD
    return options

_MEMO_CAP = 200
_HISTORY_TURNS = 6
_MAX_CUSTOMER_BLOCKS = 8
_RECENT_N = 5
_BROAD_KEYWORDS = ("전체", "모두", "전부", "다 합", "합치면", "몇 명", "몇명", "명단", "평균", "각각")
_CONTACT_KEYWORDS = ("주소", "이메일", "메일", "사는 곳")

_RRN13_RE = re.compile(r"\d{6}[-\s]?\d{7}")

# 질문이 "내가(제가·직접) 가입시킨 계약만" 을 묻는지 판별.
_OWN_Q_RE = re.compile(
    r"내\s*계약"
    r"|(?:내가|제가)\s*(?:직접\s*)?가입시킨"
    r"|(?:내가|제가)\s*(?:직접\s*)?(?:판매|모집|체결)한"
    r"|(?:내가|제가)\s*(?:직접\s*)?팔[았은]"
)


def _is_own_only_question(question: str) -> bool:
    return bool(_OWN_Q_RE.search(question or ""))

def _is_broad_question(question: str) -> bool:
    return any(keyword in (question or "") for keyword in _BROAD_KEYWORDS)


_SYSTEM_PROMPT = """당신은 한 보험 설계사의 고객 관리(CRM) 데이터를 조회해 주는 AI 비서입니다. 아래 규칙을 반드시 지키세요.

0. [고객 보장 데이터] 블록이 제공된 경우, 그 안의 계약·담보·보장현황 데이터로 직접 답하세요. 블록이 없을 때만 "약관에 질문하세요"라고 안내하세요.
1. 오직 [고객 데이터], [집계], [고객 보장 데이터]에 있는 내용만 근거로 답하세요. 거기에 없는 사실·숫자·날짜는 추측하거나 지어내지 마세요.
2. [고객 데이터]에는 질문 관련 고객만 실릴 수 있다. [전체 고객 명단]에 이름이 있는데 상세가 없으면 어느 분인지 되물어라. 질문한 고객이나 정보가 명단에도 없으면 answer 에 "해당 정보를 찾지 못했습니다."라고 쓰고 no_data 를 true 로 두세요.
3. 금액·날짜·전화번호는 데이터에 적힌 값을 그대로 옮기세요. 반올림·환산하지 마세요.
4. 보험료 합계·인원수·만기 임박 목록 같은 집계는 [집계] 블록의 값을 그대로 쓰세요. 직접 계산하지 마세요.
5. 같은 이름의 고객이 여러 명이면 생년월일로 구분해 모두 안내하거나, 어느 분인지 되물으세요.
6. [고객 보장 데이터] 블록이 없는 상태에서 특정 보험의 약관 내용(보장 금액·조건 등)을 물으면 "약관 내용은 고객 상세의 '이 고객 약관에 질문'에서 확인하세요"라고 답하고 no_data 를 true 로 두세요.
7. 주민등록번호는 데이터에 없으며 답하지 않습니다.
8. 답변은 한국어로 1~4문장.
9. 반드시 아래 JSON 하나만 출력하세요. 앞뒤에 다른 말을 붙이지 마세요.

{"answer": "핵심 답변", "used_customer_names": ["답변에 실제로 사용한 고객 이름"], "grounded": true, "no_data": false}
"""


def _call_llm(prompt: str, model: str) -> dict:
    """intake.py::_call_llm 와 동일 형태. JSON 파싱 실패 시 {}."""
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "keep_alive": KEEP_ALIVE,
        "options": _ollama_options(),
    }
    req = urllib.request.Request(
        f"{OLLAMA_BASE}/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    try:
        parsed = json.loads((data.get("response") or "").strip())
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


# ---------- 컨텍스트 조립 ----------

def _age(birth_date: Optional[str], today: Optional[date] = None) -> Optional[int]:
    today = today or date.today()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", str(birth_date or ""))
    if not m:
        return None
    by, bm, bd = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= bm <= 12 and 1 <= bd <= 31):
        return None
    return today.year - by - ((today.month, today.day) < (bm, bd))


def _won(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _is_yearly(cycle: Optional[str]) -> bool:
    c = str(cycle or "").strip().upper()
    if c in ("YEARLY", "연", "연납", "년납"):
        return True
    # "매년납" / "1년납" 등 변형도 연납으로 (단 "매월납" 은 아님)
    return ("년" in c or "연" in c) and "월" not in c


def _is_lump_sum(cycle: Optional[str]) -> bool:
    c = str(cycle or "").strip().upper()
    if not c:
        return False
    return any(k in c for k in ("일시", "LUMP", "ONE_TIME", "ONETIME", "SINGLE"))


def _monthly_premium(policy: dict) -> int:
    """계약 1건의 월 환산 보험료. 연납이면 ÷12(내림), 일시납은 0(월납 아님)."""
    if _is_lump_sum(policy.get("payment_cycle")):
        return 0
    prem = _won(policy.get("premium")) or 0
    return prem // 12 if _is_yearly(policy.get("payment_cycle")) else prem


def _aggregate_hints(conn) -> str:
    """[집계] 블록: 파이썬 선계산. LLM 은 이 값을 그대로 문장화만 한다."""
    today = date.today()
    customers = repo.list_customers(conn, limit=10000)  # rrn 없는 목록 뷰
    counts = repo.dashboard_counts(conn)

    # 상태별 인원수
    status_counts = {"가입": 0, "미가입": 0, "가망": 0, "해지": 0}
    for c in customers:
        st = c.get("effective_status") or "미가입"
        status_counts[st] = status_counts.get(st, 0) + 1

    # 월납 보험료 합계 (ACTIVE 만, 연납 ÷12) + 보험사별 가입 고객
    monthly_sum = 0
    insurer_map: dict[str, set] = {}
    # 계약자 ≠ 피보험자 계약 (역질문용: "X가 계약자인 계약 뭐야")
    contractor_lines: list[str] = []
    for c in customers:
        cname = c.get("name") or "?"
        for p in repo.list_policies(conn, c["id"]):
            ins = (p.get("insurer") or "").strip()
            if ins:
                insurer_map.setdefault(ins, set()).add(cname)
            if p.get("status") == "ACTIVE":
                monthly_sum += _monthly_premium(p)
            ph_nm = (p.get("policyholder_name") or "").strip()
            if ph_nm and ph_nm != cname:
                prod = (p.get("product_name") or p.get("insurer") or "계약").strip()
                contractor_lines.append(
                    f"계약자 {ph_nm} → 피보험자 {cname} 의 {prod}"
                )

    # 만기 임박: 90일치를 한 번에 뽑아 30 / 60 / 90 버킷으로 나눔
    exp_rows = repo.expiring_policies(conn, (today + timedelta(days=90)).isoformat())
    buckets: dict[str, list[str]] = {"30일 내": [], "60일 내": [], "90일 내": []}
    for p in exp_rows:
        d = p.get("days")
        if d is None or d < 0:  # 이미 만기 지난 계약은 '임박' 버킷에서 제외
            continue
        key = "30일 내" if d <= 30 else ("60일 내" if d <= 60 else "90일 내")
        buckets[key].append(
            f"{p.get('customer_name') or '?'} / {p.get('product_name') or '?'} / "
            f"{p.get('end_date')} (D-{d})"
        )

    # 이번 달 / 다음 달 생일자
    bdays = repo.upcoming_birthdays(conn, 62)
    this_m, next_m = today.month, (today.month % 12) + 1
    bday_groups: dict[int, list[str]] = {this_m: [], next_m: []}
    for b in bdays:
        m = re.match(r"^\d{4}-(\d{2})-(\d{2})$", str(b.get("birth_date") or ""))
        if not m:
            continue
        mo = int(m.group(1))
        if mo in bday_groups:
            bday_groups[mo].append(
                f"{b['name']} ({m.group(1)}-{m.group(2)}, 만 {b['turning_age']}세)"
            )

    # 후속 연락 예정 / 연체
    fups = repo.upcoming_follow_ups(conn, (today + timedelta(days=90)).isoformat())
    tstr = today.isoformat()
    fu_upcoming, fu_overdue = [], []
    for f in fups:
        at = f.get("follow_up_at") or ""
        line = f"{f.get('customer_name') or '?'} — {at} 「{f.get('title') or '상담'}」"
        (fu_overdue if at < tstr else fu_upcoming).append(line)

    def _blk(title: str, items: list[str]) -> str:
        if not items:
            return f"- {title}: 없음"
        return f"- {title}:\n" + "\n".join(f"    · {x}" for x in items)

    lines = [
        f"- 고객 상태별 인원: 가입 {status_counts['가입']} · 미가입 {status_counts['미가입']} · "
        f"가망 {status_counts['가망']} · 해지 {status_counts['해지']} (총 {len(customers)}명)",
        f"- 전체 계약 {counts['policies']}건 (ACTIVE {counts['active_policies']}건)",
        f"- 전 고객 월납 보험료 합계(ACTIVE, 연납은 월환산, 일시납 제외): {monthly_sum:,}원",
        _blk("만기 임박 (30일 내)", buckets["30일 내"]),
        _blk("만기 임박 (31~60일)", buckets["60일 내"]),
        _blk("만기 임박 (61~90일)", buckets["90일 내"]),
        _blk(f"이번 달({this_m}월) 생일", bday_groups[this_m]),
        _blk(f"다음 달({next_m}월) 생일", bday_groups[next_m]),
        _blk("후속 연락 예정", fu_upcoming),
        _blk("후속 연락 연체", fu_overdue),
        _blk(
            "보험사별 가입 고객",
            [f"{ins}: {', '.join(sorted(names))}" for ins, names in sorted(insurer_map.items())],
        ),
    ]
    if contractor_lines:
        lines.append(_blk("계약자≠피보험자 계약", contractor_lines))
    return "\n".join(lines)


def _activity_key(customer: dict) -> str:
    return customer.get("last_consulted_at") or customer.get("next_follow_up") or customer.get("created_at") or ""


def _customer_search_text(question: str, history: Optional[list]) -> str:
    searchable = [question or ""]
    for turn in (history or [])[-_HISTORY_TURNS:]:
        if isinstance(turn, dict) and turn.get("role") in ("user", "assistant"):
            searchable.append(str(turn.get("content") or ""))
    return " ".join(searchable).replace(" ", "")


def _exact_customer_matches(conn, question: str, history: Optional[list]) -> list[dict]:
    customers = repo.list_customers(conn, limit=10000)
    haystack = _customer_search_text(question, history)
    return [
        customer for customer in customers
        if len(str(customer.get("name") or "").strip().replace(" ", "")) > 1
        and str(customer.get("name") or "").strip().replace(" ", "") in haystack
    ]


def _pick_customers(conn, question: str, history: Optional[list]) -> Optional[list[dict]]:
    customers = repo.list_customers(conn, limit=10000)
    haystack = _customer_search_text(question, history)
    exact = _exact_customer_matches(conn, question, history)
    if exact:
        matched = exact
    elif _is_broad_question(question) or _is_own_only_question(question):
        return None
    else:
        matched = []
    for customer in customers:
        name = str(customer.get("name") or "").strip()
        compact = name.replace(" ", "")
        if not exact and len(compact) == 3 and compact[-2:] in haystack:
            matched.append(customer)
    candidates = matched or sorted(customers, key=_activity_key, reverse=True)[:_RECENT_N]
    candidates = sorted(candidates, key=_activity_key, reverse=True)
    omitted = max(0, len(candidates) - _MAX_CUSTOMER_BLOCKS)
    picked = [dict(c) for c in candidates[:_MAX_CUSTOMER_BLOCKS]]
    if omitted and picked:
        picked[0]["_omitted_count"] = omitted
    return picked


def _customer_roster(customers_all: list[dict]) -> str:
    counts: dict[str, int] = {}
    for customer in customers_all:
        name = customer.get("name") or "?"
        counts[name] = counts.get(name, 0) + 1
    labels = []
    for customer in customers_all:
        name = customer.get("name") or "?"
        if counts[name] > 1:
            birth = str(customer.get("birth_date") or "")[:4]
            name += f"({birth or '생년미상'})"
        labels.append(name)
    return "[전체 고객 명단] " + ", ".join(labels)


def _customer_summary(
    conn, own_only: bool = False, customers: Optional[list[dict]] = None,
    with_contact_extra: bool = False,
) -> str:
    """[고객 데이터] 블록: 고객마다 압축 1블록. rrn·transcript·coverage_json 제외.

    own_only=True 면 각 고객의 계약 목록에서 is_own(내가 가입시킴) 계약만 남긴다.
    (그래도 고객 자체는 목록에 유지하고, 내 계약이 없으면 그렇게 표시한다.)"""
    today = date.today()
    default_customers = customers is None
    all_customers = repo.list_customers(conn, limit=10000) if default_customers else customers
    omitted = int(all_customers[0].get("_omitted_count") or 0) if all_customers else 0
    customers = all_customers
    if not customers:
        return "(등록된 고객이 없습니다.)"

    out: list[str] = []
    for c in customers:
        cid = c["id"]
        name = c.get("name") or "?"
        bd = c.get("birth_date") or None
        age = _age(bd, today)
        bv = repo._birthday_view(bd, today) if bd else None
        head_bits = [
            f"{bd}생" if bd else "생일 미상",
            f"만{age}" if age is not None else None,
            f"생일 D-{bv['days_until']}" if bv else None,
            f"상태:{c.get('effective_status') or '미가입'}",
            f"☎{c.get('phone') or '없음'}",
        ]
        if default_customers or with_contact_extra:
            head_bits.append(f"✉{c.get('email') or '없음'}")
        out.append(f"■ {name} [id:{cid[:8]}…] " + " · ".join(b for b in head_bits if b))

        memo = (c.get("memo") or "").strip().replace("\n", " ")
        if len(memo) > _MEMO_CAP:
            memo = memo[:_MEMO_CAP] + "…"
        contact = f"주소: {c.get('address') or '없음'}  |  " if default_customers or with_contact_extra else ""
        out.append(f"  {contact}메모: {memo or '없음'}")

        policies = repo.list_policies(conn, cid)
        if own_only:
            policies = [p for p in policies if p.get("is_own")]
        # 만기일 오름차순으로 출력 — LLM 이 "만기 제일 빠른 계약" 을 바로 읽게. 만기 없는 건 뒤로.
        policies.sort(key=lambda p: p.get("end_date") or "9999-12-31")
        if own_only and not policies:
            out.append("  이 고객은 본인이 가입시킨 계약이 없습니다.")
        else:
            active = sum(1 for p in policies if p.get("status") == "ACTIVE")
            psum = sum(_monthly_premium(p) for p in policies if p.get("status") == "ACTIVE")
            soonest = c.get("soonest_expiry")
            out.append(
                f"  계약 {len(policies)}건(ACTIVE {active}) · 월납합계 {psum:,}원"
                + (f" · 가장 빠른 만기 {soonest}" if soonest and not own_only else "")
            )
            for p in policies:
                prem = _won(p.get("premium"))
                cyc = "일시납" if _is_lump_sum(p.get("payment_cycle")) else ("연납" if _is_yearly(p.get("payment_cycle")) else "월납")
                end = p.get("end_date") or None
                d = repo._days_between(end, today) if end else None
                end_txt = f"만기 {end}" + (f"(D-{d})" if d is not None else "") if end else "만기 미상"
                own_mark = " [내 계약]" if p.get("is_own") else " [타사/미확인]"
                ph_nm = (p.get("policyholder_name") or "").strip()
                ph_rel = (p.get("policyholder_rel") or "").strip()
                ph_mark = (
                    f" · 계약자: {ph_nm}" + (f"({ph_rel})" if ph_rel else "")
                    if ph_nm else ""
                )
                out.append(
                    "   - "
                    + " / ".join(
                        x for x in [
                            p.get("insurer") or "?",
                            p.get("product_name") or "?",
                            f"{cyc} {prem:,}원" if prem is not None else f"{cyc} 미상",
                            end_txt,
                            f"보험기간 {p.get('insured_period')}" if p.get("insured_period") else None,
                            p.get("status") or "?",
                        ] if x
                    )
                    + own_mark
                    + ph_mark
                )

        kons = repo.list_consultations(conn, cid)  # consulted_at DESC
        if kons:
            k = kons[0]
            fu = k.get("follow_up_at") or ""
            done = k.get("follow_up_done_at") or ""
            tail = f" 후속 {fu} 예정" if (fu and not done) else ""
            out.append(
                f"  최근상담 {k.get('consulted_at') or '?'} 「{k.get('title') or '제목 없음'}」{tail}"
            )
        out.append("")

    if omitted:
        out.append(f"(외 {omitted}명은 생략)")
    return "\n".join(out).rstrip()


def _customer_coverage_block(conn, customer_id: str) -> str:
    """customer_id 지정 시 해당 고객의 계약 + 보장현황을 텍스트 블록으로 반환.
    rrn·transcript·coverage_json 원문은 제외하고, 보장현황(coverage_json)은 파싱해서 요약."""
    customer = repo.get_customer(conn, customer_id)
    if not customer:
        return ""
    name = customer.get("name") or "?"
    lines: list[str] = [f"[고객 보장 데이터] — {name}"]

    # 1. 계약 목록
    policies = repo.list_policies(conn, customer_id)
    if policies:
        lines.append("■ 계약 목록:")
        for p in policies:
            prem = p.get("premium")
            cyc = "일시납" if _is_lump_sum(p.get("payment_cycle")) else (
                "연납" if _is_yearly(p.get("payment_cycle")) else "월납"
            )
            end = p.get("end_date") or "만기미상"
            ins = p.get("insurer") or "?"
            prod = p.get("product_name") or "?"
            ip = p.get("insured_period") or ""
            prem_txt = f"{cyc} {prem:,}원" if prem is not None else f"{cyc} 미상"
            status = p.get("status") or "?"
            line = f"  - {ins} / {prod} / {prem_txt} / 만기:{end}"
            if ip:
                line += f" / 보험기간:{ip}"
            line += f" / {status}"
            lines.append(line)
    else:
        lines.append("■ 등록된 계약 없음")

    # 2. 보장현황 (consultations.coverage_json) — 가장 최근 항목
    consultations = repo.list_consultations(conn, customer_id)
    cov_items: list[dict] = []
    for k in consultations:
        cj = k.get("coverage_json")
        if not cj:
            continue
        try:
            parsed = json.loads(cj)
            if isinstance(parsed, list) and parsed:
                cov_items = parsed
                break  # 최신 것 하나면 충분
        except (json.JSONDecodeError, TypeError):
            continue

    if cov_items:
        lines.append("■ 보장현황 (보장분석서 기준):")
        for item in cov_items:
            n = item.get("name") or "?"
            st = item.get("status") or "?"
            cur = item.get("current")
            rec = item.get("recommended")
            pct = item.get("pct")
            amt_txt = ""
            if cur is not None:
                amt_txt += f" 현재:{cur}"
            if rec is not None:
                amt_txt += f" 권장:{rec}"
            if pct is not None:
                amt_txt += f" ({pct}%)"
            lines.append(f"  · {n}: {st}{amt_txt}")

    return "\n".join(lines)


_DB_QUESTION_KEYWORDS = (
    "보장", "담보", "보험료", "월납", "만기", "계약", "보험", "얼마", "가입",
    "가입목록", "가입 목록", "보장금액", "암", "뇌", "심장", "수술", "종수술",
)
_COVERAGE_AMOUNT_KEYWORDS = ("얼마", "금액", "한도", "있어", "있니", "있나", "보장")
_PREMIUM_KEYWORDS = ("보험료", "월납", "납입", "얼마 내", "얼마내")
_EXPIRY_KEYWORDS = ("만기", "끝", "종료")
_POLICY_KEYWORDS = ("계약", "보험", "가입", "가입목록", "가입 목록", "리스트", "목록")
_STOP_COVERAGE_TOKENS = {
    "얼마", "있어", "있니", "있나", "보장", "보험", "보험료", "월납", "납입", "만기",
    "계약", "가입", "목록", "리스트", "알려줘", "뭐야", "무엇", "고객", "현황",
}


def _has_any_keyword(question: str, keywords: tuple[str, ...]) -> bool:
    compact = (question or "").replace(" ", "")
    return any(k.replace(" ", "") in compact for k in keywords)


_RAG_QUESTION_KEYWORDS = ("약관", "보장내용", "가입조건", "면책")
_SUMMARIZE_QUESTION_KEYWORDS = ("요약", "정리", "설명")
_DB_ROUTE_KEYWORDS = (
    "보장", "보험료", "만기", "계약", "보험", "가입", "가입목록",
    "담보", "보장금액", "암", "뇌", "심장", "수술", "종수술",
)


def _looks_like_named_db_question(question: str) -> bool:
    """고객명처럼 보이는 2~4자 한글 이름 + DB 키워드 조합인지 판별."""
    compact = (question or "").replace(" ", "")
    if not _has_any_keyword(compact, _DB_ROUTE_KEYWORDS):
        return False
    for keyword in _DB_ROUTE_KEYWORDS:
        k = keyword.replace(" ", "")
        if not k or k not in compact:
            continue
        if re.search(rf"[가-힣]{{2,4}}(?:의)?{re.escape(k)}", compact):
            return True
    return False


def _classify_question(question: str) -> str:
    """질문 라우팅 타입을 분류한다."""
    q = question or ""
    if _has_any_keyword(q, _RAG_QUESTION_KEYWORDS):
        return "rag"
    if _has_any_keyword(q, _DB_ROUTE_KEYWORDS) or _looks_like_named_db_question(q):
        return "db_direct"
    if _has_any_keyword(q, _SUMMARIZE_QUESTION_KEYWORDS):
        return "summarize"
    return "local_llm"


def route_question(question: str) -> str:
    """외부 테스트/로그용 라우터. 반환값: db_direct / local_llm / rag."""
    route = _classify_question(question)
    return "local_llm" if route == "summarize" else route


def _format_money(value: Any) -> str:
    won = _won(value)
    if won is not None:
        return f"{won:,}원"
    text = str(value or "").strip()
    return text or "미상"


def _format_coverage_amount(value: Any) -> str:
    if value is None:
        return "미상"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{int(value):,}원"
    text = str(value).strip()
    return text or "미상"


def _latest_coverage_items(conn, customer_id: str) -> list[dict]:
    """최근 상담의 coverage_json을 파싱해 보장 항목만 반환한다."""
    for k in repo.list_consultations(conn, customer_id):
        cj = k.get("coverage_json")
        if not cj:
            continue
        try:
            parsed = json.loads(cj)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
    return []


def _coverage_question_tokens(question: str, customer_name: str) -> list[str]:
    q = (question or "").replace(customer_name or "", " ")
    tokens = re.findall(r"[가-힣A-Za-z0-9]{2,}", q)
    out: list[str] = []
    for tok in tokens:
        if tok in _STOP_COVERAGE_TOKENS:
            continue
        if any(stop in tok for stop in _STOP_COVERAGE_TOKENS):
            # "얼마있어" 같은 질의어 결합 토큰은 제외하되, "뇌진단비" 같은 담보명은 유지.
            stripped = tok
            for stop in _STOP_COVERAGE_TOKENS:
                stripped = stripped.replace(stop, "")
            if len(stripped) < 2:
                continue
            tok = stripped
        if tok and tok not in out:
            out.append(tok)
    return out


def _coverage_item_matches(question: str, customer_name: str, item: dict) -> bool:
    name = str(item.get("name") or "").strip()
    if not name:
        return False
    compact_q = (question or "").replace(" ", "")
    compact_name = name.replace(" ", "")
    if compact_name and compact_name in compact_q:
        return True
    for tok in _coverage_question_tokens(question, customer_name):
        if tok in compact_name or compact_name in tok:
            return True
    return False


def _policy_line(policy: dict) -> str:
    prem = policy.get("premium")
    cyc = "일시납" if _is_lump_sum(policy.get("payment_cycle")) else (
        "연납" if _is_yearly(policy.get("payment_cycle")) else "월납"
    )
    end = policy.get("end_date") or "만기미상"
    return (
        f"{policy.get('insurer') or '?'} / {policy.get('product_name') or '?'} / "
        f"{cyc} {_format_money(prem)} / 만기 {end} / {policy.get('status') or '?'}"
    )


def _rule_based_coverage_answer(
    question: str,
    coverage_block: str,
    customer_name: str,
    policies: Optional[list[dict]] = None,
    coverage_items: Optional[list[dict]] = None,
    *,
    allow_no_match_message: bool = True,
) -> Optional[str]:
    """DB 질문은 Ollama 없이 템플릿 답변. 매칭 실패 시 None 또는 미발견 문구."""
    if not coverage_block:
        return None
    q = question or ""
    policies = policies or []
    coverage_items = coverage_items or []

    # [고객명] 보험료 → ACTIVE 계약 월 환산 합산
    if _has_any_keyword(q, _PREMIUM_KEYWORDS):
        active = [p for p in policies if p.get("status") == "ACTIVE"]
        total = sum(_monthly_premium(p) for p in active)
        return f"{customer_name} 고객의 ACTIVE 계약 월납 환산 보험료 합계는 {total:,}원입니다."

    # [고객명] 만기 → ACTIVE 계약 중 가장 빠른 만기
    if _has_any_keyword(q, _EXPIRY_KEYWORDS):
        active_ends = [str(p.get("end_date")) for p in policies if p.get("status") == "ACTIVE" and p.get("end_date")]
        if active_ends:
            soonest = min(active_ends)
            lines = [_policy_line(p) for p in policies if p.get("status") == "ACTIVE" and str(p.get("end_date")) == soonest]
            return f"{customer_name} 고객의 가장 빠른 만기는 {soonest}입니다.\n" + "\n".join(f"  · {line}" for line in lines)
        return f"{customer_name} 고객의 ACTIVE 계약 중 등록된 만기일을 찾지 못했습니다."

    # [고객명] 계약 → 계약 목록
    if _has_any_keyword(q, _POLICY_KEYWORDS) and not _has_any_keyword(q, _COVERAGE_AMOUNT_KEYWORDS):
        if policies:
            return f"{customer_name} 고객의 등록 계약은 {len(policies)}건입니다.\n" + "\n".join(
                f"  · {_policy_line(p)}" for p in policies
            )
        return f"{customer_name} 고객의 등록 계약이 없습니다."

    # [고객명] [보장명] 얼마 → coverage_json 현재 금액
    if _has_any_keyword(q, _COVERAGE_AMOUNT_KEYWORDS):
        matched_items = [item for item in coverage_items if _coverage_item_matches(q, customer_name, item)]
        if matched_items:
            lines: list[str] = []
            for item in matched_items:
                name = item.get("name") or "?"
                current = _format_coverage_amount(item.get("current"))
                status = item.get("status") or "상태 미상"
                rec = item.get("recommended")
                rec_txt = f", 권장 {_format_coverage_amount(rec)}" if rec is not None else ""
                lines.append(f"{name}: 현재 {current} ({status}{rec_txt})")
            return f"{customer_name} 고객의 보장현황입니다.\n" + "\n".join(f"  · {line}" for line in lines)

    # Ollama 장애 fallback용: coverage_block 텍스트에서도 한 번 더 찾는다.
    q_tokens = _coverage_question_tokens(q, customer_name)
    lines = coverage_block.splitlines()
    matched: list[str] = []
    for line in lines:
        if not line.strip().startswith("·") and "·" not in line:
            continue
        for tok in q_tokens:
            if tok in line:
                matched.append(line.strip().lstrip("· "))
                break
    if matched:
        return f"{customer_name} 고객의 보장현황:\n" + "\n".join(f"  · {m}" for m in matched)
    if allow_no_match_message and _has_any_keyword(q, _DB_QUESTION_KEYWORDS):
        return f"{customer_name} 고객의 보장현황에서 해당 정보를 찾지 못했습니다."
    return None


def build_context(conn, question: Optional[str] = None) -> str:
    """[집계] + [고객 데이터] 를 한 문자열로."""
    if question is None:
        summary = _customer_summary(conn)
        roster = ""
    else:
        picked = _pick_customers(conn, question, None)
        contact = any(k in question for k in _CONTACT_KEYWORDS)
        summary = _customer_summary(conn) if picked is None else _customer_summary(conn, customers=picked, with_contact_extra=contact)
        roster = "" if picked is None else "\n\n" + _customer_roster(repo.list_customers(conn, limit=10000))
    return (
        f"[집계]\n{_aggregate_hints(conn)}\n\n"
        f"[고객 데이터]\n{summary}{roster}"
    )


def _serialize_history(history: Optional[list]) -> str:
    if not history:
        return ""
    lines = []
    for t in history[-_HISTORY_TURNS:]:
        if not isinstance(t, dict):
            continue
        role = t.get("role")
        content = str(t.get("content") or "").strip()
        if not content:
            continue
        lines.append(("사용자: " if role == "user" else "AI: ") + content)
    return "\n".join(lines)


def _rag_answer(question: str) -> dict:
    """약관/RAG 질문은 기본 7B를 사용하고, 14B는 RAG_LLM_MODEL 명시 시에만 쓴다."""
    from rag.answerer import answer as rag_answer

    return rag_answer(question, top_k=4)


def _response_log(route: str, started: float, model: str, customer_id: Optional[str], fallback: bool) -> dict:
    return {
        "route": route,
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
        "model": model,
        "used_customer_id": customer_id,
        "fallback": fallback,
    }


def _with_response_log(result: dict, log: dict) -> dict:
    result = dict(result)
    result["response_log"] = log
    result["elapsed_ms"] = log["elapsed_ms"]
    result["route"] = log["route"]
    logger.info("assistant_response %s", json.dumps(log, ensure_ascii=False))
    return result


def _this_month_expiry_answer(conn) -> str:
    today = date.today()
    next_month = date(today.year + (1 if today.month == 12 else 0), 1 if today.month == 12 else today.month + 1, 1)
    rows = repo.expiring_policies(conn, (next_month - timedelta(days=1)).isoformat())
    lines = []
    for p in rows:
        end = str(p.get("end_date") or "")
        if end[:7] != today.strftime("%Y-%m"):
            continue
        lines.append(f"{p.get('customer_name') or '?'} / {p.get('product_name') or '?'} / {end}")
    if not lines:
        return "이번 달 만기 예정 계약은 없습니다."
    return "이번 달 만기 예정 계약입니다.\n" + "\n".join(f"  · {line}" for line in lines)


def _direct_db_answer(conn, question: str, history: Optional[list], customer_id: Optional[str]) -> Optional[dict]:
    """단순 CRM 조회는 Ollama 없이 즉시 답한다."""
    q = question or ""
    own_only = _is_own_only_question(q)
    if _has_any_keyword(q, _EXPIRY_KEYWORDS) and any(k in q for k in ("이번 달", "이번달", "이달")) and not customer_id:
        return {
            "answer": _this_month_expiry_answer(conn),
            "used_customers": [],
            "data_scope": "all",
            "model": "db_direct",
            "no_data": False,
            "grounded": True,
            "own_only": own_only,
            "routing_type": "db_direct",
        }

    if not customer_id:
        exact_matches = _exact_customer_matches(conn, q, history)
        if len(exact_matches) == 1:
            customer_id = exact_matches[0]["id"]
        elif len(exact_matches) > 1:
            names = ", ".join(
                f"{c.get('name') or '?'}({str(c.get('birth_date') or '')[:10] or '생년미상'})"
                for c in exact_matches[:5]
            )
            return {
                "answer": f"같은 이름의 고객이 여러 명입니다. 어느 분인지 확인해 주세요: {names}",
                "used_customers": [{"id": c["id"], "name": c.get("name") or ""} for c in exact_matches[:5]],
                "data_scope": "filtered",
                "model": "db_direct",
                "no_data": True,
                "grounded": True,
                "own_only": own_only,
                "routing_type": "db_direct",
            }

    if not customer_id:
        return None

    c = repo.get_customer(conn, customer_id)
    if not c:
        return None
    customer_name = c.get("name") or ""
    policies = repo.list_policies(conn, customer_id)
    coverage_items = _latest_coverage_items(conn, customer_id)
    coverage_block = _RRN13_RE.sub("[주민번호 제외]", _customer_coverage_block(conn, customer_id))
    direct_answer = _rule_based_coverage_answer(
        q,
        coverage_block,
        customer_name,
        policies,
        coverage_items,
        allow_no_match_message=True,
    )
    if not direct_answer:
        return None
    return {
        "answer": direct_answer,
        "used_customers": [{"id": customer_id, "name": customer_name}],
        "data_scope": "filtered",
        "model": "db_direct",
        "no_data": "찾지 못했습니다" in direct_answer,
        "grounded": True,
        "own_only": own_only,
        "routing_type": "db_direct",
    }


def answer(
    conn, question: str, history: Optional[list] = None, model: Optional[str] = None,
    customer_id: Optional[str] = None,
) -> dict:
    """질문 → {answer, used_customers, data_scope, model, no_data, grounded}.

    customer_id 가 있으면 해당 고객의 계약·보장현황을 [고객 보장 데이터] 블록으로 추가해
    LLM 이 직접 보장 금액을 답할 수 있게 한다. Ollama 불능 시 규칙기반 fallback 적용.
    """
    started = time.perf_counter()
    routing_type = route_question(question)
    # 고객 상세 화면에서는 고객명 휴리스틱보다 customer_id 지정 DB 질문을 우선한다.
    if customer_id and _has_any_keyword(question, _DB_QUESTION_KEYWORDS) and routing_type != "rag":
        routing_type = "db_direct"
    model = model or (SUMMARY_LLM_MODEL if routing_type == "summarize" else COMPLEX_LLM_MODEL)
    own_only = _is_own_only_question(question)

    if routing_type == "rag":
        rag = _rag_answer(question)
        return _with_response_log({
            **rag,
            "used_customers": [],
            "data_scope": "rag",
            "no_data": bool(rag.get("abstained")) or not bool(rag.get("grounded", True)),
            "own_only": own_only,
            "routing_type": "rag",
        }, _response_log("rag", started, model, customer_id, False))

    if routing_type == "db_direct":
        direct = _direct_db_answer(conn, question, history, customer_id)
        if direct:
            log_customer_id = (direct.get("used_customers") or [{}])[0].get("id") if direct.get("used_customers") else customer_id
            return _with_response_log(direct, _response_log("db_direct", started, "db_direct", log_customer_id, False))
        return _with_response_log({
            "answer": "해당 고객 또는 조회 정보를 찾지 못했습니다.",
            "used_customers": [],
            "data_scope": "filtered" if customer_id else "all",
            "model": "db_direct",
            "no_data": True,
            "grounded": True,
            "own_only": own_only,
            "routing_type": "db_direct",
        }, _response_log("db_direct", started, "db_direct", customer_id, False))

    if routing_type == "db_direct" and not customer_id:
        exact_matches = _exact_customer_matches(conn, question, history)
        if len(exact_matches) == 1:
            customer_id = exact_matches[0]["id"]

    # customer_id 지정 + DB성 질문이면 Ollama 호출 전에 즉시 템플릿 답변을 시도한다.
    coverage_block = ""
    customer_name = ""
    direct_policies: list[dict] = []
    direct_coverage_items: list[dict] = []
    if customer_id:
        coverage_block = _customer_coverage_block(conn, customer_id)
        c = repo.get_customer(conn, customer_id)
        customer_name = (c.get("name") or "") if c else ""
        # RRN 패턴이 들어있으면 제거 (안전장치)
        coverage_block = _RRN13_RE.sub("[주민번호 제외]", coverage_block)
        if coverage_block and customer_name and _has_any_keyword(question, _DB_QUESTION_KEYWORDS):
            direct_policies = repo.list_policies(conn, customer_id)
            direct_coverage_items = _latest_coverage_items(conn, customer_id)
            direct_answer = _rule_based_coverage_answer(
                question,
                coverage_block,
                customer_name,
                direct_policies,
                direct_coverage_items,
                allow_no_match_message=False,
            )
            if direct_answer:
                return _with_response_log({
                    "answer": direct_answer,
                    "used_customers": [{"id": customer_id, "name": customer_name}],
                    "data_scope": "filtered",
                    "model": "rule_based_db",
                    "no_data": False,
                    "grounded": True,
                    "own_only": own_only,
                    "routing_type": "db_direct",
                }, _response_log("db_direct", started, "rule_based_db", customer_id, False))

    hints = _aggregate_hints(conn)
    picked = _pick_customers(conn, question, history)
    contact = any(k in question for k in _CONTACT_KEYWORDS)
    summary = _customer_summary(conn, own_only=own_only) if picked is None else _customer_summary(conn, own_only=own_only, customers=picked, with_contact_extra=contact)
    roster = "" if picked is None else "\n\n" + _customer_roster(repo.list_customers(conn, limit=10000))
    history_lines = _serialize_history(history)

    own_directive = (
        "\n\n[지시] 사용자는 본인이 직접 가입시킨 계약만 묻고 있다. "
        "[내 계약] 표시된 계약만 근거로 답하고, 나머지는 언급하지 마라."
        if own_only else ""
    )

    prompt = (
        _SYSTEM_PROMPT
        + own_directive
        + f"\n\n[집계]\n{hints}"
        + f"\n\n[고객 데이터]\n{summary}{roster}"
        + (f"\n\n{coverage_block}" if coverage_block else "")
        + f"\n\n[이전 대화]\n{history_lines or '(없음)'}"
        + f"\n\n[질문]\n{question}\n\n[출력 JSON]"
    )

    # Ollama 호출 — 실패 시 규칙기반 fallback
    try:
        data = _call_llm(prompt, model)
    except Exception:
        data = {}

    if not data or "answer" not in data:
        # Ollama 불능: customer_id + coverage_block 있으면 키워드 매칭으로 직접 답변
        fallback_answer = None
        if coverage_block and customer_name:
            fallback_answer = _rule_based_coverage_answer(
                question,
                coverage_block,
                customer_name,
                direct_policies or (repo.list_policies(conn, customer_id) if customer_id else []),
                direct_coverage_items or (_latest_coverage_items(conn, customer_id) if customer_id else []),
            )
        if fallback_answer:
            return _with_response_log({
                "answer": fallback_answer,
                "used_customers": [{"id": customer_id, "name": customer_name}] if customer_id else [],
                "data_scope": "filtered",
                "model": "rule_based_fallback",
                "no_data": False,
                "grounded": True,
                "own_only": own_only,
                "routing_type": "db_direct",
            }, _response_log("db_direct", started, "rule_based_fallback", customer_id, True))
        data = {
            "answer": "답변을 생성하지 못했습니다.",
            "used_customer_names": [],
            "grounded": False,
            "no_data": False,
        }

    no_data = bool(data.get("no_data"))
    used_customers: list[dict] = []
    # customer_id 지정 시 해당 고객을 used_customers에 항상 포함
    if customer_id and not no_data:
        c = repo.get_customer(conn, customer_id)
        if c:
            used_customers.append({"id": customer_id, "name": c.get("name") or ""})
    if not no_data:
        seen: set[str] = {c["id"] for c in used_customers}
        for nm in data.get("used_customer_names") or []:
            if not isinstance(nm, str) or not nm.strip():
                continue
            for hit in repo.find_customers_by_name(conn, nm.strip()):
                if hit["id"] not in seen:
                    seen.add(hit["id"])
                    used_customers.append({"id": hit["id"], "name": hit["name"]})

    route_name = "local_llm" if routing_type in ("local_llm", "summarize", "db_direct") else routing_type
    return _with_response_log({
        "answer": data.get("answer") or "답변을 생성하지 못했습니다.",
        "used_customers": used_customers,
        "data_scope": "filtered" if (picked is not None or customer_id) else "all",
        "model": model,
        "no_data": no_data,
        "grounded": bool(data.get("grounded")),
        "own_only": own_only,
        "routing_type": route_name,
    }, _response_log(route_name, started, model, customer_id, False))
