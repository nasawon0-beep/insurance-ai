"""
CRM 보장분석.

13개 대분류 / 46개 세부 담보 단위로 권장금액, 가입금액, 부족금액,
우선순위와 RAG 근거를 계산하고 coverage_* 테이블에 저장한다.
기존 analyze(conn, customer_id, model=None) 호출은 유지하고 keyword-only 인자를 추가했다.
"""
from __future__ import annotations

import json
import os
import sys
import os
import re
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from rag import search as rag_search

from . import repo

CATALOG_VERSION = "2026-09-25"
ANALYZER_VERSION = "2026-09-25"
MIN_SCORE_KEEP = 0.15
TOP_K_PER_SUBCOVERAGE = 3
_EXCLUDED_MVP_SUBCOVERAGES = {
    "통합암 진단비",
    "특정암 진단비",
    "뇌산정특례대상 진단비",
    "심장산정특례대상 진단비",
}

_SCHEMA_SQL = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS coverage_catalog (
    id TEXT PRIMARY KEY,
    category_id TEXT NOT NULL,
    category_group TEXT NOT NULL,
    coverage_name TEXT NOT NULL,
    display_order INTEGER NOT NULL,
    recommended_amount INTEGER NOT NULL CHECK (recommended_amount >= 0),
    base_weight INTEGER NOT NULL CHECK (base_weight IN (40, 70, 100)),
    primary_query TEXT NOT NULL,
    keywords_json TEXT NOT NULL DEFAULT '[]',
    exclude_keywords_json TEXT NOT NULL DEFAULT '[]',
    distinction_rule TEXT,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (category_group, coverage_name)
);
CREATE TABLE IF NOT EXISTS coverage_analysis_runs (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'completed' CHECK (status IN ('queued','running','completed','failed')),
    mode TEXT NOT NULL DEFAULT 'full' CHECK (mode IN ('full','incremental','manual')),
    analyzer_version TEXT NOT NULL DEFAULT '2026-09-25',
    catalog_version TEXT NOT NULL DEFAULT '2026-09-25',
    rag_strategy_json TEXT NOT NULL DEFAULT '{}',
    profile_json TEXT NOT NULL DEFAULT '{}',
    summary_customer TEXT,
    summary_internal TEXT,
    error_message TEXT,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS coverage_analysis (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES coverage_analysis_runs(id) ON DELETE CASCADE,
    customer_id TEXT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    catalog_id TEXT REFERENCES coverage_catalog(id) ON DELETE SET NULL,
    category_id TEXT NOT NULL,
    category_group TEXT NOT NULL,
    coverage_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('충분','부족','미가입','확인필요')),
    recommended_amount INTEGER NOT NULL CHECK (recommended_amount >= 0),
    current_amount INTEGER NOT NULL DEFAULT 0 CHECK (current_amount >= 0),
    gap_amount INTEGER NOT NULL DEFAULT 0 CHECK (gap_amount >= 0),
    coverage_ratio REAL NOT NULL DEFAULT 0 CHECK (coverage_ratio >= 0),
    gap_ratio REAL NOT NULL DEFAULT 0 CHECK (gap_ratio >= 0),
    priority_score INTEGER NOT NULL DEFAULT 0,
    priority TEXT NOT NULL CHECK (priority IN ('최우선','중요','선택','표시제외')),
    customer_display INTEGER NOT NULL DEFAULT 1 CHECK (customer_display IN (0, 1)),
    customer_summary TEXT,
    internal_memo TEXT,
    evidence_confidence TEXT NOT NULL DEFAULT 'none' CHECK (evidence_confidence IN ('high','medium','low','none','needs_review')),
    evidence_pages_json TEXT NOT NULL DEFAULT '[]',
    evidence_json TEXT NOT NULL DEFAULT '[]',
    matched_policy_ids_json TEXT NOT NULL DEFAULT '[]',
    manual_override INTEGER NOT NULL DEFAULT 0 CHECK (manual_override IN (0, 1)),
    override_reason TEXT,
    reviewed_by TEXT,
    reviewed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (run_id, category_group, coverage_name)
);
CREATE TABLE IF NOT EXISTS coverage_adjustment_factors (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES coverage_analysis_runs(id) ON DELETE CASCADE,
    coverage_id TEXT REFERENCES coverage_analysis(id) ON DELETE CASCADE,
    factor_type TEXT NOT NULL CHECK (factor_type IN ('age','sex','family','occupation','scope','manual')),
    factor_key TEXT NOT NULL,
    factor_value REAL NOT NULL,
    amount_delta INTEGER NOT NULL DEFAULT 0,
    score_delta INTEGER NOT NULL DEFAULT 0,
    note TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_coverage_catalog_category ON coverage_catalog(category_id, display_order);
CREATE INDEX IF NOT EXISTS idx_coverage_catalog_name ON coverage_catalog(category_group, coverage_name);
CREATE INDEX IF NOT EXISTS idx_coverage_runs_customer_time ON coverage_analysis_runs(customer_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_coverage_customer_run ON coverage_analysis(customer_id, run_id);
CREATE INDEX IF NOT EXISTS idx_coverage_customer_priority ON coverage_analysis(customer_id, customer_display, priority_score DESC);
CREATE INDEX IF NOT EXISTS idx_coverage_status ON coverage_analysis(status, priority);
CREATE INDEX IF NOT EXISTS idx_coverage_category ON coverage_analysis(category_group, coverage_name);
CREATE INDEX IF NOT EXISTS idx_coverage_factors_run ON coverage_adjustment_factors(run_id);
CREATE INDEX IF NOT EXISTS idx_coverage_factors_coverage ON coverage_adjustment_factors(coverage_id);
"""


def _load_json(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _base_weight(group: str, name: str) -> int:
    core = {
        "일반암 진단비", "뇌혈관 진단비", "허혈성심장질환 진단비",
        "질병입원", "질병통원", "상해입원", "상해통원", "질병 3% 이상 후유장해",
    }
    important_groups = {"수술비", "입원비 / 일당", "치료비"}
    important_names = {
        "유사암 진단비", "통합암 진단비", "특정암 진단비", "뇌졸중 진단비", "뇌출혈 진단비",
        "뇌산정특례대상 진단비", "급성심근경색증 진단비", "심장산정특례대상 진단비",
        "중증치매진단", "경증치매진단",
    }
    if name in core:
        return 100
    if group in important_groups or name in important_names:
        return 70
    return 40


def load_coverage_catalog(path: str | None = None) -> list[dict[str, Any]]:
    # PyInstaller 번들 경로 지원
    if getattr(sys, 'frozen', False):
        # PyInstaller로 패키징된 경우
        base_path = sys._MEIPASS
        default_rag = os.path.join(base_path, "database", "coverage-rag-queries.json")
        default_amounts = os.path.join(base_path, "database", "coverage-recommended-amounts.json")
    else:
        # 개발 환경
        base_path = os.path.dirname(os.path.dirname(__file__))
        default_rag = os.path.join(base_path, "database", "coverage-rag-queries.json")
        default_amounts = os.path.join(base_path, "database", "coverage-recommended-amounts.json")
    
    rag_path = path or os.environ.get("COVERAGE_RAG_QUERIES_PATH", default_rag)
    amounts_path = os.environ.get("COVERAGE_RECOMMENDED_AMOUNTS_PATH", default_amounts)
    rag = _load_json(rag_path)
    amounts = _load_json(amounts_path)["recommended_amounts"]
    out: list[dict[str, Any]] = []
    order = 0
    for cat in rag["categories"]:
        group = cat["category_name"]
        for sub in cat["subcoverages"]:
            if sub["name"] in _EXCLUDED_MVP_SUBCOVERAGES:
                continue
            order += 1
            name = sub["name"]
            out.append({
                "id": f"{cat['category_id']}:{order:02d}",
                "category_id": cat["category_id"],
                "category_group": group,
                "coverage_name": name,
                "display_order": order,
                "recommended_amount": int(amounts[group][name]),
                "base_weight": _base_weight(group, name),
                "primary_query": sub["primary_query"],
                "keywords": list(sub.get("keywords", [])),
                "exclude_keywords": list(sub.get("exclude_keywords", [])),
                "distinction_rule": cat.get("distinction_rule"),
            })
    return out


_COVERAGE_CATALOG = load_coverage_catalog()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA_SQL)
    for item in _COVERAGE_CATALOG:
        conn.execute(
            """
            INSERT INTO coverage_catalog
            (id, category_id, category_group, coverage_name, display_order, recommended_amount,
             base_weight, primary_query, keywords_json, exclude_keywords_json, distinction_rule,
             is_active, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(id) DO UPDATE SET
              category_id=excluded.category_id,
              category_group=excluded.category_group,
              coverage_name=excluded.coverage_name,
              display_order=excluded.display_order,
              recommended_amount=excluded.recommended_amount,
              base_weight=excluded.base_weight,
              primary_query=excluded.primary_query,
              keywords_json=excluded.keywords_json,
              exclude_keywords_json=excluded.exclude_keywords_json,
              distinction_rule=excluded.distinction_rule,
              is_active=1,
              updated_at=excluded.updated_at
            """,
            (
                item["id"], item["category_id"], item["category_group"], item["coverage_name"],
                item["display_order"], item["recommended_amount"], item["base_weight"], item["primary_query"],
                json.dumps(item["keywords"], ensure_ascii=False),
                json.dumps(item["exclude_keywords"], ensure_ascii=False),
                item.get("distinction_rule"), _now(),
            ),
        )
    conn.commit()


_AMOUNT_RE = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(억|천만|백만|만원|만 원|원)?")


def _parse_amounts(text: str) -> list[int]:
    amounts: list[int] = []
    for raw, unit in _AMOUNT_RE.findall(text or ""):
        value = float(raw.replace(",", ""))
        if unit == "억":
            amount = int(value * 100_000_000)
        elif unit == "천만":
            amount = int(value * 10_000_000)
        elif unit == "백만":
            amount = int(value * 1_000_000)
        elif unit in {"만원", "만 원"}:
            amount = int(value * 10_000)
        elif unit == "원":
            amount = int(value)
        else:
            continue
        if amount > 0:
            amounts.append(amount)
    return amounts


def _policy_text(policy: dict[str, Any]) -> str:
    parts = [
        policy.get("insurer"), policy.get("product_name"), policy.get("plan_type"),
        policy.get("policy_number"), policy.get("memo"), policy.get("insured_period"),
    ]
    return " ".join(str(p) for p in parts if p)


def _extract_current_amount(item: dict[str, Any], policies: list[dict[str, Any]]) -> tuple[int, list[str]]:
    best = 0
    matched: list[str] = []
    names = [item["coverage_name"], item["coverage_name"].replace(" ", "")]
    keywords = [kw for kw in item.get("keywords", []) if len(str(kw)) >= 2]
    excludes = item.get("exclude_keywords", [])
    for policy in policies:
        text = _policy_text(policy)
        compact = text.replace(" ", "")
        if any(ex and ex in text for ex in excludes):
            continue
        hit = any(name and name in text for name in names) or any(name and name in compact for name in names)
        if not hit:
            hit = any(str(kw) in text for kw in keywords[:4])
        if not hit:
            continue
        amounts = _parse_amounts(text)
        if amounts:
            best = max(best, max(amounts))
            matched.append(policy["id"])
        elif item["coverage_name"] in text:
            matched.append(policy["id"])
    return best, sorted(set(matched))


def _compute_status(recommended: int, current: int, has_evidence: bool, amount_failed: bool = False):
    if amount_failed and has_evidence:
        gap = max(recommended - current, 0)
        return "확인필요", gap, current / recommended if recommended else 0, gap / recommended if recommended else 0
    if current <= 0:
        status = "미가입"
    elif current < recommended:
        status = "부족"
    else:
        status = "충분"
    gap = max(recommended - current, 0)
    return status, gap, current / recommended if recommended else 0, gap / recommended if recommended else 0


def _core_diagnosis_has_gap(items: list[dict[str, Any]]) -> bool:
    core = {"일반암 진단비", "뇌혈관 진단비", "허혈성심장질환 진단비"}
    return any(i["coverage_name"] in core and i["status"] != "충분" for i in items)


def _calculate_priority(
    item: dict[str, Any], status: str, gap_ratio: float, profile: dict[str, Any], context: dict[str, Any]
) -> tuple[int, str]:
    if status == "충분":
        return max(int(item["base_weight"]) - 100, 0), "표시제외"
    score = int(item["base_weight"])
    score += {"미가입": 30, "부족": 20, "확인필요": 10}.get(status, 0)
    score += 20 if gap_ratio >= 0.8 else 15 if gap_ratio >= 0.5 else 8 if gap_ratio >= 0.2 else 3
    group, name = item["category_group"], item["coverage_name"]
    if name in {"일반암 진단비", "뇌혈관 진단비", "허혈성심장질환 진단비", "질병 3% 이상 후유장해"}:
        score += 10
    if group in {"수술비", "입원비 / 일당", "치료비"} and context.get("core_diagnosis_has_gap"):
        score -= 15
    age = profile.get("age")
    try:
        age = int(age) if age is not None else None
    except (TypeError, ValueError):
        age = None
    if age is not None and age <= 29 and group in {"사망", "치매"}:
        score -= 20
    if age is not None and 50 <= age <= 59 and group in {"암", "뇌혈관질환", "심장질환", "치매", "입원비 / 일당", "치료비"}:
        score += 10
    if age is not None and 60 <= age <= 69 and group in {"뇌혈관질환", "심장질환", "치매", "입원비 / 일당"}:
        score += 15
    if profile.get("sex") in {"female", "F", "여", "여성"} and name in {"유사암 진단비", "특정암 진단비"}:
        score += 5
    if profile.get("sex") in {"male", "M", "남", "남성"} and name in {"허혈성심장질환 진단비", "급성심근경색증 진단비"}:
        score += 5
    if profile.get("has_dependents") and group == "사망":
        score += 25
    elif group == "사망":
        score -= 30
    if profile.get("has_young_children") and name in {"질병 3% 이상 후유장해", "일반암 진단비", "뇌혈관 진단비", "허혈성심장질환 진단비"}:
        score += 10
    if profile.get("frequent_driving") and group == "운전자":
        score += 20
    elif group in {"운전자", "법률 / 배상책임", "치아 / 화상 / 골절"}:
        score -= 30
    priority = "최우선" if score >= 100 else "중요" if score >= 70 else "선택" if score >= 30 else "표시제외"
    return score, priority


def _gather_evidence_v2(doc_ids: list[str], catalog: list[dict[str, Any]] | None = None) -> dict[str, list[dict[str, Any]]]:
    if not doc_ids:
        return {}
    by_key: dict[str, list[dict[str, Any]]] = {}
    for item in catalog or _COVERAGE_CATALOG:
        try:
            hits = rag_search(item["primary_query"], top_k=TOP_K_PER_SUBCOVERAGE, doc_ids=doc_ids).get("hits", [])
        except Exception:
            hits = []
        filtered: list[dict[str, Any]] = []
        for h in hits:
            text = h.get("text") or ""
            score = float(h.get("score") or 0)
            if score < MIN_SCORE_KEEP:
                continue
            matched = [kw for kw in item.get("keywords", []) if kw in text]
            excluded = [kw for kw in item.get("exclude_keywords", []) if kw in text]
            adjusted = score + min(len(matched), 3) * 0.05 - len(excluded) * 0.20
            if adjusted < MIN_SCORE_KEEP:
                continue
            row = dict(h)
            row.update({
                "category_id": item["category_id"],
                "category_group": item["category_group"],
                "coverage_name": item["coverage_name"],
                "matched_keywords": matched,
                "excluded_keywords": excluded,
                "adjusted_score": round(adjusted, 4),
            })
            filtered.append(row)
        filtered.sort(key=lambda r: r.get("adjusted_score", 0), reverse=True)
        by_key[f"{item['category_group']}::{item['coverage_name']}"] = filtered[:TOP_K_PER_SUBCOVERAGE]
    return by_key


def _confidence(evidence: list[dict[str, Any]]) -> str:
    if not evidence:
        return "none"
    top = max(float(e.get("adjusted_score") or e.get("score") or 0) for e in evidence)
    if any(e.get("excluded_keywords") for e in evidence):
        return "needs_review"
    return "high" if top >= 0.75 else "medium" if top >= 0.4 else "low"


def _pages(evidence: list[dict[str, Any]]) -> list[int]:
    out: list[int] = []
    for e in evidence:
        try:
            page = int(e.get("page"))
        except (TypeError, ValueError):
            continue
        if page not in out:
            out.append(page)
    return out


def _customer_summary(name: str, status: str, recommended: int, current: int, gap: int) -> str:
    if status == "충분":
        return f"{name}는 권장금액 기준으로 충분히 준비되어 있습니다."
    if status == "부족":
        return f"{name}가 권장금액보다 {gap // 10000:,}만원 부족합니다."
    if status == "확인필요":
        return f"{name}는 보장 여부가 보이지만 가입금액 확인이 필요합니다."
    return f"{name}가 확인되지 않아 권장금액 {recommended // 10000:,}만원 보완을 검토해 보세요."


def _internal_memo(item: dict[str, Any], status: str, evidence_confidence: str) -> str:
    memo = item.get("distinction_rule") or f"{item['coverage_name']} 기준으로 독립 판정."
    if evidence_confidence == "none":
        memo += " 약관 RAG 근거가 없어 계약명/메모의 가입금액만 반영했습니다."
    return memo


def _build_items(policies: list[dict[str, Any]], doc_ids: list[str], profile: dict[str, Any]) -> list[dict[str, Any]]:
    evidence_by_key = _gather_evidence_v2(doc_ids, _COVERAGE_CATALOG)
    items: list[dict[str, Any]] = []
    for item in _COVERAGE_CATALOG:
        current, matched_policy_ids = _extract_current_amount(item, policies)
        key = f"{item['category_group']}::{item['coverage_name']}"
        evidence = evidence_by_key.get(key, [])
        amount_failed = bool(evidence and matched_policy_ids and current == 0)
        status, gap, coverage_ratio, gap_ratio = _compute_status(
            int(item["recommended_amount"]), current, bool(evidence or matched_policy_ids), amount_failed
        )
        items.append({
            **item,
            "status": status,
            "current_amount": current,
            "gap_amount": gap,
            "coverage_ratio": round(coverage_ratio, 4),
            "gap_ratio": round(gap_ratio, 4),
            "evidence_confidence": _confidence(evidence),
            "evidence_pages": _pages(evidence),
            "evidence": evidence,
            "matched_policy_ids": matched_policy_ids,
        })
    context = {"core_diagnosis_has_gap": _core_diagnosis_has_gap(items)}
    for row in items:
        score, priority = _calculate_priority(row, row["status"], row["gap_ratio"], profile, context)
        row["priority_score"] = score
        row["priority"] = priority
        row["customer_display"] = 0 if priority == "표시제외" else 1
        row["customer_summary"] = _customer_summary(row["coverage_name"], row["status"], row["recommended_amount"], row["current_amount"], row["gap_amount"])
        row["internal_memo"] = _internal_memo(row, row["status"], row["evidence_confidence"])
    return items


def _summary(items: list[dict[str, Any]], *, audience: str = "internal") -> tuple[str, str]:
    shown = [i for i in items if i.get("customer_display")]
    top = sorted(shown, key=lambda i: i["priority_score"], reverse=True)[:3]
    if top:
        names = ", ".join(i["coverage_name"] for i in top)
        customer = f"우선 확인할 보장은 {names}입니다. 예산에 맞춰 최우선 항목부터 보완을 검토하세요."
    else:
        customer = "주요 보장이 권장 기준에 대체로 부합합니다. 유지와 중복 여부를 점검하세요."
    internal = f"총 {len(items)}개 세부 담보 분석 완료. 고객 표시 {len(shown)}개, 표시제외 {len(items) - len(shown)}개."
    return customer, internal


def _persist_run(
    conn: sqlite3.Connection, customer_id: str, items: list[dict[str, Any]], profile: dict[str, Any], mode: str
) -> str:
    run_id = _new_id("run")
    started = _now()
    summary_customer, summary_internal = _summary(items)
    conn.execute(
        """
        INSERT INTO coverage_analysis_runs
        (id, customer_id, status, mode, analyzer_version, catalog_version, rag_strategy_json,
         profile_json, summary_customer, summary_internal, started_at, completed_at)
        VALUES (?, ?, 'completed', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id, customer_id, mode, ANALYZER_VERSION, CATALOG_VERSION,
            json.dumps({"top_k_per_subcoverage": TOP_K_PER_SUBCOVERAGE, "min_score_keep": MIN_SCORE_KEEP}, ensure_ascii=False),
            json.dumps(profile, ensure_ascii=False), summary_customer, summary_internal, started, _now(),
        ),
    )
    for row in items:
        coverage_id = _new_id("cov")
        conn.execute(
            """
            INSERT INTO coverage_analysis
            (id, run_id, customer_id, catalog_id, category_id, category_group, coverage_name, status,
             recommended_amount, current_amount, gap_amount, coverage_ratio, gap_ratio,
             priority_score, priority, customer_display, customer_summary, internal_memo,
             evidence_confidence, evidence_pages_json, evidence_json, matched_policy_ids_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                coverage_id, run_id, customer_id, row["id"], row["category_id"], row["category_group"],
                row["coverage_name"], row["status"], row["recommended_amount"], row["current_amount"],
                row["gap_amount"], row["coverage_ratio"], row["gap_ratio"], row["priority_score"],
                row["priority"], row["customer_display"], row["customer_summary"], row["internal_memo"],
                row["evidence_confidence"], json.dumps(row["evidence_pages"], ensure_ascii=False),
                json.dumps(row["evidence"], ensure_ascii=False), json.dumps(row["matched_policy_ids"], ensure_ascii=False),
            ),
        )
    conn.commit()
    return run_id


def analyze(
    conn: sqlite3.Connection,
    customer_id: str,
    model: Optional[str] = None,
    *,
    audience: str = "internal",
    persist: bool = True,
    force_recalculate: bool = False,
    catalog_path: str | None = None,
    profile: dict[str, Any] | None = None,
    mode: str = "full",
) -> Optional[dict[str, Any]]:
    if repo.get_customer(conn, customer_id) is None:
        return None
    ensure_schema(conn)
    policies = repo.list_policies(conn, customer_id)
    doc_ids = repo.customer_document_ids(conn, customer_id)
    profile = profile or {}
    items = _build_items(policies, doc_ids, profile)
    run_id = _persist_run(conn, customer_id, items, profile, mode) if persist else None
    summary_customer, summary_internal = _summary(items, audience=audience)
    return {
        "overall": summary_customer,
        "categories": _legacy_categories(items),
        "coverage_items": items,
        "gaps": [i["customer_summary"] for i in items if i["status"] in {"미가입", "부족", "확인필요"}],
        "overlaps": [],
        "recommendations": [i["customer_summary"] for i in sorted(items, key=lambda x: x["priority_score"], reverse=True)[:5]],
        "analyzed_policies": len(policies),
        "has_documents": bool(doc_ids),
        "sources": [e for i in items for e in i.get("evidence", [])],
        "model": model,
        "run_id": run_id,
        "status": "completed",
        "summary_customer": summary_customer,
        "summary_internal": summary_internal,
    }


def _legacy_categories(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order = {name: idx for idx, name in enumerate([i["category_group"] for i in _COVERAGE_CATALOG])}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        grouped.setdefault(item["category_group"], []).append(item)
    out = []
    for group, rows in sorted(grouped.items(), key=lambda kv: order.get(kv[0], 999)):
        worst = sorted(rows, key=lambda r: r["priority_score"], reverse=True)[0]
        out.append({
            "name": group,
            "status": worst["status"],
            "detail": "; ".join(r["customer_summary"] for r in rows[:3]),
            "policies": [],
            "evidence_pages": sorted({p for r in rows for p in r.get("evidence_pages", [])}),
        })
    return out


def _json_list(value: str | None) -> list[Any]:
    try:
        data = json.loads(value or "[]")
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def _row_to_item(row: sqlite3.Row, *, audience: str) -> dict[str, Any]:
    item = dict(row)
    item["customer_display"] = bool(item.get("customer_display"))
    item["manual_override"] = bool(item.get("manual_override"))
    item["evidence_pages"] = _json_list(item.pop("evidence_pages_json", "[]"))
    evidence = _json_list(item.pop("evidence_json", "[]"))
    matched_policy_ids = _json_list(item.pop("matched_policy_ids_json", "[]"))
    if audience == "internal":
        item["evidence"] = evidence
        item["matched_policy_ids"] = matched_policy_ids
    else:
        item.pop("internal_memo", None)
        item.pop("override_reason", None)
        item.pop("reviewed_by", None)
        item.pop("reviewed_at", None)
    return item


def get_analysis_response(
    conn: sqlite3.Connection,
    customer_id: str,
    *,
    audience: str = "internal",
    include_sufficient: bool = True,
    priority: str | None = None,
    run_id: str | None = None,
) -> Optional[dict[str, Any]]:
    ensure_schema(conn)
    if repo.get_customer(conn, customer_id) is None:
        return None
    if run_id:
        run = conn.execute(
            "SELECT * FROM coverage_analysis_runs WHERE id = ? AND customer_id = ?", (run_id, customer_id)
        ).fetchone()
    else:
        run = conn.execute(
            """
            SELECT * FROM coverage_analysis_runs
            WHERE customer_id = ? AND status = 'completed'
            ORDER BY completed_at DESC, created_at DESC LIMIT 1
            """,
            (customer_id,),
        ).fetchone()
    if run is None:
        return None
    rows = conn.execute(
        "SELECT * FROM coverage_analysis WHERE run_id = ? AND customer_id = ? ORDER BY priority_score DESC, category_group, coverage_name",
        (run["id"], customer_id),
    ).fetchall()
    all_rows = [_row_to_item(r, audience="internal") for r in rows]
    items = [_row_to_item(r, audience=audience) for r in rows]
    if audience == "customer":
        items = [i for i in items if i["customer_display"]]
    if not include_sufficient:
        items = [i for i in items if i["status"] != "충분" and i["priority"] != "표시제외"]
    if priority:
        items = [i for i in items if i["priority"] == priority]
    return {
        "customer_id": customer_id,
        "run_id": run["id"],
        "status": run["status"],
        "audience": audience,
        "catalog_version": run["catalog_version"],
        "summary": _response_summary(run, all_rows, items, audience),
        "categories": _group_response_items(items),
    }


def _response_summary(run: sqlite3.Row, all_rows: list[dict[str, Any]], items: list[dict[str, Any]], audience: str) -> dict[str, Any]:
    counts = {k: 0 for k in ["충분", "부족", "미가입", "확인필요"]}
    pcounts = {k: 0 for k in ["최우선", "중요", "선택", "표시제외"]}
    for row in all_rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
        pcounts[row["priority"]] = pcounts.get(row["priority"], 0) + 1
    return {
        "overall": run["summary_customer"] if audience == "customer" else (run["summary_internal"] or run["summary_customer"]),
        "total_items": len(all_rows),
        "displayed_items": len(items),
        "counts": counts,
        "priority_counts": pcounts,
    }


def _group_response_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order = {item["category_group"]: item["display_order"] for item in _COVERAGE_CATALOG}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        grouped.setdefault(item["category_group"], []).append(item)
    priority_rank = {"최우선": 4, "중요": 3, "선택": 2, "표시제외": 1}
    status_rank = {"미가입": 4, "확인필요": 3, "부족": 2, "충분": 1}
    out = []
    for group, rows in sorted(grouped.items(), key=lambda kv: order.get(kv[0], 999)):
        rows.sort(key=lambda r: r["priority_score"], reverse=True)
        out.append({
            "category_id": rows[0]["category_id"],
            "category_group": group,
            "status_summary": max((r["status"] for r in rows), key=lambda s: status_rank.get(s, 0)),
            "max_priority": max((r["priority"] for r in rows), key=lambda p: priority_rank.get(p, 0)),
            "items": rows,
        })
    return out


def recalculate(conn: sqlite3.Connection, customer_id: str, body: dict[str, Any] | None = None) -> Optional[dict[str, Any]]:
    body = body or {}
    result = analyze(
        conn,
        customer_id,
        audience=body.get("audience") or "internal",
        persist=bool(body.get("persist", True)),
        force_recalculate=bool(body.get("force", True)),
        profile=body.get("profile") or {},
        mode=body.get("mode") or "full",
    )
    if result is None:
        return None
    return {
        "run_id": result["run_id"],
        "customer_id": customer_id,
        "status": "completed",
        "mode": body.get("mode") or "full",
        "started_at": _now(),
        "completed_at": _now(),
        "items_count": len(result["coverage_items"]),
        "links": {"result": f"/customers/{customer_id}/coverage-analysis?run_id={result['run_id']}"},
    }


def patch_item(conn: sqlite3.Connection, customer_id: str, coverage_id: str, patch: dict[str, Any]) -> Optional[dict[str, Any]]:
    ensure_schema(conn)
    row = conn.execute(
        "SELECT * FROM coverage_analysis WHERE id = ? AND customer_id = ?", (coverage_id, customer_id)
    ).fetchone()
    if row is None:
        return None
    allowed = {
        "internal_memo", "recommended_amount", "current_amount", "customer_display",
        "override_reason", "customer_summary", "status", "priority", "reviewed_by",
    }
    changed = {k: v for k, v in (patch or {}).items() if k in allowed}
    if not changed:
        return _row_to_item(row, audience="internal")
    recommended = int(changed.get("recommended_amount", row["recommended_amount"]) or 0)
    current = int(changed.get("current_amount", row["current_amount"]) or 0)
    if "recommended_amount" in changed or "current_amount" in changed or "status" not in changed:
        status, gap, coverage_ratio, gap_ratio = _compute_status(recommended, current, False)
        changed.setdefault("status", status)
        changed["gap_amount"] = gap
        changed["coverage_ratio"] = round(coverage_ratio, 4)
        changed["gap_ratio"] = round(gap_ratio, 4)
    else:
        gap = max(recommended - current, 0)
        changed["gap_amount"] = gap
        changed["coverage_ratio"] = round(current / recommended, 4) if recommended else 0
        changed["gap_ratio"] = round(gap / recommended, 4) if recommended else 0
    if "priority" not in changed:
        item = {
            "base_weight": _base_weight(row["category_group"], row["coverage_name"]),
            "category_group": row["category_group"],
            "coverage_name": row["coverage_name"],
        }
        score, priority_value = _calculate_priority(item, changed["status"], changed["gap_ratio"], {}, {})
        changed["priority_score"] = score
        changed["priority"] = priority_value
    if "customer_display" in changed:
        changed["customer_display"] = 1 if changed["customer_display"] else 0
    changed["manual_override"] = 1
    changed["reviewed_at"] = _now()
    changed["updated_at"] = _now()
    sets = ", ".join(f"{k} = ?" for k in changed)
    conn.execute(f"UPDATE coverage_analysis SET {sets} WHERE id = ? AND customer_id = ?", [*changed.values(), coverage_id, customer_id])
    conn.commit()
    updated = conn.execute("SELECT * FROM coverage_analysis WHERE id = ?", (coverage_id,)).fetchone()
    return _row_to_item(updated, audience="internal")
