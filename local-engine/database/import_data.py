"""
CSV 고객 가져오기 (v1: 고객만. 계약 가져오기 없음).

- 인코딩 시도 순서: utf-8-sig → utf-8 → cp949. 감지 결과를 돌려준다.
- 매핑 마법사: {고객필드: CSV컬럼명}. name(이름) 매핑 필수 — 없으면 422.
- 중복 처리: merge(빈 칸만 채움) / new(항상 신규) / skip(건너뜀).
  중복 판정은 repo.match_customer (전화 완전일치 > 이름+생년월일).
- 행 단위 커밋 + 실패 리포트 failed:[{row, reason}] (전부-또는-전무 아님).
- 값 정규화: phone(숫자만 → 11자리면 010-xxxx-xxxx), birth_date(YYYY-MM-DD / YYYY.MM.DD /
  YYYYMMDD / YYMMDD → 정규화, 실패 시 빈칸 + 행 경고), tags(",;" 분리 → repo._norm_tags),
  빈 값 → None, 앞뒤 공백 제거.
- rrn 토글 OFF 인데 rrn 처럼 보이는 컬럼이 매핑되면 무시하고 "주민번호 컬럼 무시됨" 표기.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime
from typing import Optional

from openpyxl import load_workbook

from . import repo
from . import rrn as _rrn

_FIELDS = [
    "name", "phone", "birth_date", "gender", "email", "address",
    "occupation", "tags", "memo", "customer_status", "rrn",
]
_ENCODINGS = ["utf-8-sig", "utf-8", "cp949"]
_RRNISH = re.compile(r"주민|rrn", re.IGNORECASE)


class _ParsedRow(dict):
    def __init__(self, *args, numeric_columns=None, source_row=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.numeric_columns = numeric_columns or set()
        self.source_row = source_row


def _validate_headers(headers: list[str]) -> None:
    empty = [i + 1 for i, header in enumerate(headers) if not str(header).strip()]
    seen = set()
    duplicates = []
    for header in headers:
        if header in seen and header not in duplicates:
            duplicates.append(header)
        seen.add(header)
    if empty or duplicates:
        problems = []
        if empty:
            problems.append(f"빈 칸: {empty}")
        if duplicates:
            problems.append(f"중복: {duplicates}")
        raise _InvalidImport(
            "헤더(첫 행)에 빈 칸 또는 중복된 이름이 있습니다: " + ", ".join(problems)
        )


_HEADER_KEYWORDS = (
    "이름", "성함", "성명", "고객명", "name",
    "전화", "h.p", "hp", "휴대폰", "핸드폰", "mobile", "phone", "tel",
    "주민번호", "주민등록번호", "rrn",
    "주소", "address", "직업", "가입회사", "보험사", "가입상품", "상품", "고지사항", "비고", "메모",
)
_SUMMARY_ROW_RE = re.compile(r"^(합계|총계|소계|total)$", re.IGNORECASE)


def _header_score(values: list[str]) -> int:
    score = 0
    for value in values:
        header = str(value or "").strip().lower()
        if not header:
            continue
        if any(keyword in header for keyword in _HEADER_KEYWORDS):
            score += 1
    return score


def _unique_xlsx_headers(values: list[str]) -> tuple[list[str], list[int]]:
    headers: list[str] = []
    indexes: list[int] = []
    seen: dict[str, int] = {}
    for index, value in enumerate(values):
        header = str(value or "").strip()
        if not header:
            continue
        count = seen.get(header, 0) + 1
        seen[header] = count
        headers.append(header if count == 1 else f"{header}_{count}")
        indexes.append(index)
    return headers, indexes


def _classify_column(header: str) -> tuple[Optional[str], str]:
    text = str(header or "").strip().lower()
    if not text:
        return None, "review"
    if any(kw in text for kw in ["이름", "성함", "성명", "고객명", "name"]):
        return "name", "high"
    if any(kw in text for kw in ["전화", "h.p", "hp", "휴대폰", "핸드폰", "연락", "mobile", "phone", "tel"]):
        return "phone", "high"
    if any(kw in text for kw in ["생년월일", "생일", "birth", "dob"]):
        return "birth_date", "high"
    if any(kw in text for kw in ["성별", "gender", "sex"]):
        return "gender", "high"
    if any(kw in text for kw in ["주소", "address", "addr"]):
        return "address", "high"
    if any(kw in text for kw in ["직업", "occupation", "job"]):
        return "occupation", "high"
    if any(kw in text for kw in ["가입회사", "보험사", "보험회사", "회사"]):
        return "policy", "review"
    if any(kw in text for kw in ["가입상품", "상품", "보장", "보험명"]):
        return "policy", "review"
    if any(kw in text for kw in ["고지사항", "비고", "메모", "특이사항", "note", "memo"]):
        return "memo", "review"
    return None, "review"


def _column_mapping_analysis(cols: list[str]) -> dict:
    mapping: dict = {"policy_candidates": [], "memo_candidates": [], "needs_review": []}
    used_fields = set()
    for col in cols:
        kind, confidence = _classify_column(col)
        item = {"column": col, "confidence": confidence}
        if kind in {"name", "phone", "birth_date", "gender", "address", "occupation"}:
            if kind not in used_fields:
                mapping[kind] = item
                used_fields.add(kind)
            else:
                mapping["needs_review"].append({**item, "reason": f"{kind} 후보가 여러 개입니다."})
        elif kind == "policy":
            mapping["policy_candidates"].append(item)
        elif kind == "memo":
            mapping["memo_candidates"].append(item)
        else:
            mapping["needs_review"].append({**item, "reason": "자동 매핑 확신 낮음"})
    return mapping


def _row_values(row: dict) -> list[str]:
    return [str(v).strip() for v in row.values() if str(v or "").strip()]


def _excluded_row_reason(row: dict) -> Optional[str]:
    values = _row_values(row)
    if not values:
        return None
    first = values[0].strip()
    if _SUMMARY_ROW_RE.search(first):
        return f"{first} 행"
    return None


def _preview_analysis(rows: list[dict], cols: list[str], meta: dict) -> dict:
    column_mapping = _column_mapping_analysis(cols)
    name_col = (column_mapping.get("name") or {}).get("column")
    review_rows = []
    registrable_count = 0
    for index, row in enumerate(rows):
        rownum = getattr(row, "source_row", None) or index + 2
        name = str(row.get(name_col) or "").strip() if name_col else ""
        if name:
            registrable_count += 1
        else:
            review_rows.append({"row": rownum, "reason": "이름 후보가 비어 있어 확인 필요"})
    return {
        "header_row": meta.get("header_row"),
        "data_start_row": meta.get("data_start_row"),
        "registrable_count": registrable_count,
        "needs_review_count": len(review_rows),
        "excluded_count": len(meta.get("excluded_rows") or []),
        "excluded_rows": meta.get("excluded_rows") or [],
        "review_rows": review_rows[:50],
        "column_mapping": column_mapping,
    }


def decode(raw: bytes) -> tuple[str, str]:
    for enc in _ENCODINGS:
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    # 마지막 폴백: 손실 허용
    return raw.decode("utf-8", "replace"), "utf-8 (일부 손실)"


def _rows(text: str) -> tuple[list[dict], list[str]]:
    reader = csv.DictReader(io.StringIO(text))
    return list(reader), list(reader.fieldnames or [])


def _xlsx_rows(raw: bytes) -> tuple[list[dict], list[str], dict]:
    try:
        workbook = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception as e:  # noqa: BLE001 — openpyxl/ZIP/XML 형식 오류를 하나로 번역
        raise _XlsxReadError() from e
    try:
        if not workbook.worksheets:
            raise _InvalidImport("엑셀 파일에 시트가 없습니다.")
        sheet = workbook.worksheets[0]
        all_rows = list(sheet.iter_rows(values_only=True))
        header_row_index = None
        header_values = None
        best_score = 0
        first_non_empty_index = None
        first_non_empty_values = None
        for source_row, raw_values in enumerate(all_rows, start=1):
            numeric = {i for i, v in enumerate(raw_values) if isinstance(v, (int, float)) and not isinstance(v, bool)}
            values = [
                "" if v is None else v.isoformat()[:10] if isinstance(v, (date, datetime)) else str(v)
                for v in raw_values
            ]
            if not any(values):
                continue
            if first_non_empty_index is None:
                first_non_empty_index = source_row
                first_non_empty_values = values
            score = _header_score(values)
            if score > best_score:
                best_score = score
                header_row_index = source_row
                header_values = values
        if header_values is None:
            header_row_index = first_non_empty_index
            header_values = first_non_empty_values
        if header_values is None or header_row_index is None:
            raise _InvalidImport("엑셀 파일의 첫 시트에 헤더(첫 행)가 없습니다.")
        headers, header_indexes = _unique_xlsx_headers(header_values)
        if not headers:
            raise _InvalidImport("엑셀 파일의 첫 시트에 헤더(첫 행)가 없습니다.")
        rows = []
        excluded_rows = []
        for source_row, raw_values in enumerate(all_rows[header_row_index:], start=header_row_index + 1):
            values = [
                "" if v is None else v.isoformat()[:10] if isinstance(v, (date, datetime)) else str(v)
                for v in raw_values
            ]
            if not any(values):
                continue
            numeric = {i for i, v in enumerate(raw_values) if isinstance(v, (int, float)) and not isinstance(v, bool)}
            numeric_columns = {headers[pos] for pos, original_index in enumerate(header_indexes) if original_index in numeric}
            row = {header: values[original_index] if original_index < len(values) else "" for header, original_index in zip(headers, header_indexes)}
            excluded_reason = _excluded_row_reason(row)
            if excluded_reason:
                excluded_rows.append({"row": source_row, "reason": excluded_reason})
                continue
            rows.append(_ParsedRow(row, numeric_columns=numeric_columns, source_row=source_row))
        data_start_row = getattr(rows[0], "source_row", None) if rows else None
        return rows, headers, {
            "header_row": header_row_index,
            "data_start_row": data_start_row,
            "excluded_rows": excluded_rows[:50],
        }
    except _InvalidImport:
        raise
    except Exception as e:  # noqa: BLE001 — 스트리밍 중 발생한 ZIP/XML 오류 포함
        raise _XlsxReadError() from e
    finally:
        workbook.close()


def _parse(raw: bytes, file_format: str) -> tuple[list[dict], list[str], str, dict]:
    if file_format == "xlsx":
        rows, cols, meta = _xlsx_rows(raw)
        enc = "xlsx"
    else:
        text, enc = decode(raw)
        rows, cols = _rows(text)
        meta = {"header_row": 1, "data_start_row": 2 if rows else None, "excluded_rows": []}
        if not cols:
            raise _InvalidImport("파일에 헤더(첫 행)가 없습니다.")
        _validate_headers(list(cols))
    if not cols:
        raise _InvalidImport("파일에 헤더(첫 행)가 없습니다.")
    return rows, cols, enc, meta


def preview(raw: bytes, sample: int = 5, file_format: str = "csv") -> dict:
    rows, cols, enc, meta = _parse(raw, file_format)
    return {
        "encoding": enc,
        "columns": list(cols),
        "row_count": len(rows),
        "sample_rows": rows[:sample],
        "import_analysis": _preview_analysis(rows, list(cols), meta),
    }


def _norm_phone(v: Optional[str]) -> Optional[str]:
    if not v:
        return None
    digits = re.sub(r"\D", "", v)
    if not digits:
        return None
    if len(digits) == 11:
        return f"{digits[:3]}-{digits[3:7]}-{digits[7:]}"
    return v.strip()


_BD_RE = re.compile(r"^(\d{4})[-.](\d{1,2})[-.](\d{1,2})$")


def _norm_birth(v: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """(정규화된 YYYY-MM-DD 또는 None, 경고 또는 None)."""
    if not v:
        return None, None
    s = v.strip()
    two_digit_year = False
    m = _BD_RE.match(s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        digits = re.sub(r"\D", "", s)
        if len(digits) == 8:
            y, mo, d = int(digits[:4]), int(digits[4:6]), int(digits[6:8])
        elif len(digits) == 6:
            two_digit_year = True
            yy = int(digits[:2])
            this_yy = datetime.now().year % 100  # 두자리 연도 피벗 (올해 이하면 2000년대)
            y = 2000 + yy if yy <= this_yy else 1900 + yy
            mo, d = int(digits[2:4]), int(digits[4:6])
        else:
            return None, f"생년월일 형식을 알 수 없어 비웠습니다: {v!r}"
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return None, f"생년월일 값이 범위를 벗어나 비웠습니다: {v!r}"
    # 두자리 연도 피벗 추정이 미래로 튀면 100년 뺀다.
    if two_digit_year and y > datetime.now().year:
        y -= 100
    if not (1900 <= y <= 2100):
        return None, f"생년월일 값이 범위를 벗어나 비웠습니다: {v!r}"
    return f"{y:04d}-{mo:02d}-{d:02d}", None


def _clean(v):
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def commit(
    conn,
    raw: bytes,
    mapping: dict,
    dedupe: str = "merge",
    rrn_enabled: bool = False,
    file_format: str = "csv",
) -> dict:
    rows, cols, enc, _meta = _parse(raw, file_format)

    # 매핑 정리: {고객필드: CSV컬럼}. 값이 실제 컬럼에 있어야 유효.
    field_map = {
        f: mapping[f] for f in _FIELDS
        if f in mapping and mapping.get(f) and mapping[f] in cols
    }
    if not field_map.get("name"):
        raise _NameUnmapped()

    ignored_rrn = False
    if not rrn_enabled:
        # rrn 매핑 또는 rrn 처럼 보이는 컬럼이 매핑돼 있으면 무시.
        if "rrn" in field_map or any(_RRNISH.search(c or "") for c in field_map.values()):
            ignored_rrn = True
        field_map.pop("rrn", None)

    dedupe = dedupe if dedupe in ("merge", "new", "skip") else "merge"

    created = merged = skipped = 0
    failed: list[dict] = []
    warnings: list[str] = []
    if ignored_rrn:
        warnings.append("주민번호 컬럼 무시됨 (RRN 입력 기능 OFF)")

    for i, r in enumerate(rows):
        rownum = getattr(r, "source_row", None) or i + 2  # xlsx는 실제 워크시트 행
        try:
            vals = {f: _clean(r.get(col)) for f, col in field_map.items()}
            name = vals.get("name")
            if not name:
                failed.append({"row": rownum, "reason": "이름이 비어 있습니다."})
                continue

            if "phone" in vals:
                phone_col = field_map["phone"]
                if phone_col in getattr(r, "numeric_columns", set()) and vals["phone"]:
                    if re.fullmatch(r"10\d{8}", vals["phone"]):
                        vals["phone"] = "0" + vals["phone"]
                    elif not re.fullmatch(r"\d{11}", vals["phone"]):
                        raise ValueError(
                            "전화번호가 엑셀에서 숫자로 저장돼 앞자리 0이 사라졌을 수 있습니다. "
                            "텍스트로 다시 저장해주세요."
                        )
                vals["phone"] = _norm_phone(vals["phone"])
            if "birth_date" in vals:
                bd, warn = _norm_birth(vals["birth_date"])
                vals["birth_date"] = bd
                if warn:
                    warnings.append(f"{rownum}행: {warn}")
            if "tags" in vals and vals["tags"]:
                parts = re.split(r"[,;]", vals["tags"])
                vals["tags"] = repo._norm_tags([p.strip() for p in parts if p.strip()])

            rrn_val = None
            if rrn_enabled and vals.get("rrn"):
                rrn_col = field_map["rrn"]
                if (
                    rrn_col in getattr(r, "numeric_columns", set())
                    and re.fullmatch(r"\d{12}", vals["rrn"])
                ):
                    raise ValueError(
                        "주민번호가 엑셀에서 숫자로 저장돼 앞자리 0이 사라졌을 수 있습니다. "
                        "텍스트로 다시 저장해주세요."
                    )
                try:
                    rrn_val = _rrn.normalize(vals["rrn"])
                    # 주민번호 → 생년월일/성별 자동 채움 (별도 컬럼 없을 때)
                    if rrn_val:
                        bd_from_rrn, gd_from_rrn = _rrn.birth_and_gender(rrn_val)
                        if bd_from_rrn and not vals.get("birth_date"):
                            vals["birth_date"] = bd_from_rrn
                        if gd_from_rrn and not vals.get("gender"):
                            vals["gender"] = gd_from_rrn
                except ValueError as e:
                    warnings.append(f"{rownum}행: 주민번호 형식 오류로 제외 — {e}")
            vals.pop("rrn", None)

            match = repo.match_customer(
                conn, vals.get("phone"), name, vals.get("birth_date")
            )
            # 확실한 동일인만 기존 고객으로 취급: 전화 완전일치 또는 이름+생년월일.
            # "이름만 일치"는 동명이인 위험이 커서 병합/건너뜀 대상에서 제외한다.
            reason = match.get("match_reason")
            existing = match.get("match") if reason in ("phone", "name+birthdate") else None
            if existing is None and match.get("match"):
                cand_n = len(match.get("candidates") or [])
                if cand_n >= 2:
                    warnings.append(
                        f"{rownum}행: 이름만 일치하는 기존 고객이 {cand_n}명 있어 신규로 추가했습니다."
                    )
                else:
                    warnings.append(
                        f"{rownum}행: 이름만 일치하는 기존 고객이 있어 신규로 추가했습니다."
                    )

            if existing and dedupe == "skip":
                skipped += 1
                continue

            if existing and dedupe == "merge":
                patch = {}
                for k, v in vals.items():
                    if v and not (existing.get(k) or ""):
                        patch[k] = v
                if patch:
                    repo.update_customer(conn, existing["id"], patch)
                if rrn_val and not existing.get("has_rrn"):
                    _safe_rrn(conn, existing["id"], rrn_val, warnings, rownum)
                merged += 1
                continue

            # 신규 (확실한 동일인 없음, 또는 dedupe == "new")
            payload = {k: v for k, v in vals.items() if k != "rrn"}
            new_c = repo.create_customer(conn, payload)
            if rrn_val:
                _safe_rrn(conn, new_c["id"], rrn_val, warnings, rownum)
            created += 1
        except Exception as e:  # noqa: BLE001 — 행 단위 실패 격리
            failed.append({"row": rownum, "reason": str(e)})

    return {
        "encoding": enc,
        "created": created,
        "merged": merged,
        "skipped": skipped,
        "failed": failed,
        "warnings": warnings,
        "ignored_rrn": ignored_rrn,
        "total_rows": len(rows),
    }


def _safe_rrn(conn, cid, rrn_val, warnings, rownum):
    """rrn 은 항상 별도 PATCH (형식 검증 실패가 다른 필드 저장을 되돌리지 않게)."""
    try:
        repo.update_customer(conn, cid, {"rrn": rrn_val})
    except Exception as e:  # noqa: BLE001
        warnings.append(f"{rownum}행: 주민번호 저장 실패 — {e}")


class _NameUnmapped(Exception):
    pass


class _InvalidImport(Exception):
    pass


class _XlsxReadError(Exception):
    pass
