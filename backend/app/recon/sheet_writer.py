"""
Ghi số BBNT vào đúng 3 ô I/K/M của FB.JT (docs/ARCHITECTURE.md 12.1) và Status U (tối đa tới "Đã done BBNT +
Chờ KT xuất HĐ nháp", anh Hiệp chốt 30/10/2026) — có kiểm soát:

1. Đọc lại ô ngay trước khi ghi (kèm công thức). Khác giá trị bot thấy lúc quét, hoặc là công thức -> CONFLICT,
   không ghi (người vừa sửa / ô không phải số nhập tay).
2. Ghi ``RAW`` đúng từng ô (``values:batchUpdate``), không ghi cả dòng.
3. Đọc lại sau khi ghi -> VERIFIED, lệch -> FAILED.

Hàm thuần với ``session`` kiểu ``requests.Session`` (đã có header Bearer) — test bằng session giả.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.parse import quote

from app.core.calculation import normalize_text, parse_money

SHEETS = "https://sheets.googleapis.com/v4/spreadsheets"


@dataclass(frozen=True)
class CellWrite:
    row: int                  # số dòng như Sheets hiển thị
    column: str               # chữ cột, vd "I"
    seen: Decimal | str | None    # giá trị bot thấy lúc quét (None = ô trống)
    target: Decimal | str         # str = ô chữ (Status U), so sánh sau normalize_text


@dataclass(frozen=True)
class CellResult:
    write: CellWrite
    status: str               # VERIFIED | CONFLICT | FAILED
    before: Any = None
    after: Any = None
    note: str = ""


class SheetWriteError(RuntimeError):
    pass


def _a1(tab: str, w: CellWrite) -> str:
    return "'" + tab.replace("'", "''") + f"'!{w.column}{w.row}"


def _get(session: Any, sid: str, ranges: list[str], render: str) -> list[Any]:
    r = session.get(f"{SHEETS}/{sid}/values:batchGet", timeout=30,
                    params=[("ranges", a) for a in ranges] + [("valueRenderOption", render)])
    if r.status_code != 200:
        raise SheetWriteError(f"Đọc ô FB.JT lỗi HTTP {r.status_code}")
    out = []
    for vr in r.json().get("valueRanges", []):
        vals = vr.get("values", [])
        out.append(vals[0][0] if vals and vals[0] else None)
    return out


def _num(v: Any) -> Decimal | None:
    try:
        return parse_money(v) if v not in (None, "") else None
    except ValueError:
        return None


def _matches(v: Any, x: Decimal | str | None, text: bool) -> bool:
    return normalize_text(str(v or "")) == normalize_text(str(x or "")) if text else _num(v) == x


def _raw(t: Decimal | str) -> Any:
    return t if isinstance(t, str) else int(t) if t == t.to_integral() else str(t)


def write_cells(session: Any, spreadsheet_id: str, tab: str, writes: Sequence[CellWrite]) -> list[CellResult]:
    if not writes:
        return []
    ranges = [_a1(tab, w) for w in writes]
    formulas = _get(session, spreadsheet_id, ranges, "FORMULA")
    values = _get(session, spreadsheet_id, ranges, "UNFORMATTED_VALUE")
    results: dict[int, CellResult] = {}
    todo: list[int] = []
    for i, w in enumerate(writes):
        f = formulas[i] if i < len(formulas) else None
        v = values[i] if i < len(values) else None
        if isinstance(f, str) and f.startswith("="):
            results[i] = CellResult(w, "CONFLICT", v, note="ô là công thức")
        elif not _matches(v, w.seen, isinstance(w.target, str)):
            results[i] = CellResult(w, "CONFLICT", v, note="ô đã đổi so với lúc quét")
        else:
            todo.append(i)
    if todo:
        body = {"valueInputOption": "RAW",
                "data": [{"range": ranges[i], "values": [[_raw(writes[i].target)]]} for i in todo]}
        r = session.post(f"{SHEETS}/{quote(spreadsheet_id, safe='')}/values:batchUpdate", json=body, timeout=30)
        if r.status_code != 200:
            for i in todo:
                results[i] = CellResult(writes[i], "FAILED", values[i], note=f"HTTP {r.status_code}")
            todo = []
    if todo:
        after = _get(session, spreadsheet_id, [ranges[i] for i in todo], "UNFORMATTED_VALUE")
        for k, i in enumerate(todo):
            a = after[k] if k < len(after) else None
            ok = _matches(a, writes[i].target, isinstance(writes[i].target, str))
            results[i] = CellResult(writes[i], "VERIFIED" if ok else "FAILED", values[i], a,
                                    "" if ok else "đọc lại không khớp")
    return [results[i] for i in range(len(writes))]
