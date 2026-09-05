"""
고객/계약/상담 데이터를 CSV 3개 + README 로 묶어 zip 으로 내보낸다.

- stdlib 만 사용 (csv + zipfile + io). openpyxl/pandas 안 씀.
- 인코딩: utf-8-sig (BOM) — Windows Excel / Google Sheets 에서 한글 깨짐 방지.
- CSV 인젝션 가드: 셀 값이 = + - @ 로 시작하면 앞에 ' 를 붙인다.
- 필드 복호화 실패 → 빈 셀 + README 에 건수 기록, 절대 중단하지 않는다.
- rrn 컬럼은 rrn 토글이 ON 일 때만 포함 (파일럿은 OFF → 생략).
- 결과 zip 은 database/data/exports/ 에 저장하고 {path, filename, rows} 를 돌려준다.
"""
from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from . import settings as _settings
from .crypto import get_cipher
from .db import resolve_db_path

_CUSTOMER_COLS = [
    "id", "name", "phone", "birth_date", "gender", "email", "address",
    "occupation", "tags", "memo", "customer_status", "created_at", "updated_at",
]
_CUSTOMER_ENC = {
    "name", "phone", "birth_date", "gender", "email", "address",
    "occupation", "tags", "memo", "customer_status", "rrn",
}
_POLICY_COLS = [
    "id", "customer_id", "customer_name", "insurer", "product_name", "policy_number",
    "plan_type", "premium", "payment_cycle", "start_date", "end_date",
    "insured_period", "payment_period", "payment_end_date", "status", "memo",
]
_POLICY_ENC = {
    "insurer", "product_name", "policy_number", "plan_type", "payment_cycle",
    "start_date", "end_date", "memo", "insured_period", "payment_period",
    "payment_end_date",
}
_CONSULT_COLS = [
    "id", "customer_id", "customer_name", "consulted_at", "channel", "title",
    "content", "follow_up_at", "follow_up_done_at",
]
_CONSULT_ENC = {"channel", "title", "content"}


def exports_dir(db_path: Optional[str] = None) -> Path:
    d = resolve_db_path(db_path).parent / "exports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _guard(v) -> str:
    """CSV 인젝션 가드 + None → 빈칸."""
    if v is None:
        return ""
    s = str(v)
    if s and s[0] in ("=", "+", "-", "@"):
        return "'" + s
    return s


class _Decryptor:
    def __init__(self):
        self._c = get_cipher()
        self.failures = 0

    def dec(self, key: str, raw, enc_set: set):
        if key not in enc_set:
            return raw
        try:
            return self._c.decrypt(raw)
        except Exception:
            self.failures += 1
            return None


def _write_csv(rows: list[dict], cols: list[str]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        w.writerow([_guard(r.get(c)) for c in cols])
    return buf.getvalue().encode("utf-8-sig")


def build_export(
    conn,
    include_rrn: Optional[bool] = None,
    db_path: Optional[str] = None,
    rrn_audit: Optional[Callable[[list[str]], None]] = None,
) -> dict:
    if include_rrn is None:
        include_rrn = _settings.rrn_enabled(conn)
    dcr = _Decryptor()

    # --- 고객 ---
    cust_cols = list(_CUSTOMER_COLS)
    if include_rrn:
        cust_cols = cust_cols[:-2] + ["rrn"] + cust_cols[-2:]
    names: dict[str, str] = {}
    cust_rows: list[dict] = []
    rrn_customer_ids: list[str] = []
    for r in conn.execute("SELECT * FROM customers").fetchall():
        d = dict(r)
        row = {c: dcr.dec(c, d.get(c), _CUSTOMER_ENC) for c in cust_cols}
        row["id"] = d.get("id")
        if include_rrn and row.get("rrn"):
            rrn_customer_ids.append(row["id"])
        names[d.get("id")] = row.get("name") or ""
        cust_rows.append(row)

    # --- 계약 ---
    pol_rows: list[dict] = []
    for r in conn.execute("SELECT * FROM policies").fetchall():
        d = dict(r)
        row = {c: dcr.dec(c, d.get(c), _POLICY_ENC) for c in _POLICY_COLS}
        row["id"] = d.get("id")
        row["customer_id"] = d.get("customer_id")
        row["premium"] = d.get("premium")
        row["status"] = d.get("status")
        row["customer_name"] = names.get(d.get("customer_id"), "")
        pol_rows.append(row)

    # --- 상담 ---
    con_rows: list[dict] = []
    for r in conn.execute("SELECT * FROM consultations").fetchall():
        d = dict(r)
        row = {c: dcr.dec(c, d.get(c), _CONSULT_ENC) for c in _CONSULT_COLS}
        row["id"] = d.get("id")
        row["customer_id"] = d.get("customer_id")
        for k in ("consulted_at", "follow_up_at", "follow_up_done_at"):
            row[k] = d.get(k)
        row["customer_name"] = names.get(d.get("customer_id"), "")
        con_rows.append(row)

    created_at = datetime.now().isoformat(timespec="seconds")
    readme = _readme(created_at, include_rrn, dcr.failures, len(cust_rows), len(pol_rows), len(con_rows))

    if rrn_customer_ids and rrn_audit:
        rrn_audit(rrn_customer_ids)

    d = exports_dir(db_path)
    base = f"export-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    path = d / f"{base}.zip"
    n = 1
    while path.exists():
        path = d / f"{base}_{n:03d}.zip"
        n += 1
    fname = path.name
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("고객.csv", _write_csv(cust_rows, cust_cols))
        z.writestr("계약.csv", _write_csv(pol_rows, _POLICY_COLS))
        z.writestr("상담.csv", _write_csv(con_rows, _CONSULT_COLS))
        z.writestr("README.txt", readme.encode("utf-8-sig"))

    return {
        "path": str(path),
        "filename": fname,
        "rows": len(cust_rows) + len(pol_rows) + len(con_rows),
        "customers": len(cust_rows),
        "policies": len(pol_rows),
        "consultations": len(con_rows),
        "include_rrn": include_rrn,
        "decrypt_failures": dcr.failures,
        "created_at": created_at,
    }


def list_exports(db_path: Optional[str] = None) -> list[dict]:
    d = exports_dir(db_path)
    out = []
    for p in d.glob("export-*.zip"):
        if not p.is_file():
            continue
        st = p.stat()
        out.append({
            "filename": p.name,
            "path": str(p),
            "size": st.st_size,
            "created_at": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
        })
    out.sort(key=lambda x: x["filename"], reverse=True)
    return out


def _readme(created_at, include_rrn, fails, nc, npo, nk) -> str:
    lines = [
        "보험 AI — 데이터 내보내기",
        f"생성일시: {created_at}",
        "",
        f"고객.csv   : {nc}행",
        f"계약.csv   : {npo}행",
        f"상담.csv   : {nk}행",
        "",
        "[컬럼 설명]",
        "고객.csv : id, name(이름), phone(전화), birth_date(생년월일), gender(성별 M/F),",
        "           email, address(주소), occupation(직업), tags(태그, 콤마 구분),",
        "           memo(메모), customer_status(가입/미가입/가망/해지), created_at, updated_at"
        + (", rrn(주민등록번호)" if include_rrn else ""),
        "계약.csv : id, customer_id, customer_name(고객명), insurer(보험사), product_name(상품명),",
        "           policy_number(증권번호), plan_type(보장구분), premium(보험료, 원), payment_cycle(납입주기),",
        "           start_date(개시일), end_date(만기일), insured_period(보험기간 원문),",
        "           payment_period(납입기간 원문), payment_end_date(납입종료일), status, memo",
        "상담.csv : id, customer_id, customer_name, consulted_at(상담일시), channel(채널),",
        "           title(제목), content(내용), follow_up_at(후속연락 예정), follow_up_done_at(후속연락 완료)",
        "           ※ 녹취 전사(transcript) 와 보장현황(coverage_json) 은 용량·민감도 때문에 제외했습니다.",
        "",
        f"주민등록번호 포함 여부: {'포함' if include_rrn else '제외 (RRN 입력 기능 OFF)'}",
        f"복호화 실패 셀 수: {fails} (해당 셀은 비어 있습니다)",
        "",
        "인코딩: UTF-8 (BOM). Excel/Google Sheets 에서 바로 열립니다.",
        "일부 셀 앞의 작은따옴표(') 는 스프레드시트 수식 오작동 방지용입니다.",
    ]
    return "\n".join(lines) + "\n"
