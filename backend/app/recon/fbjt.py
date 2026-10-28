"""
Đọc tab FB-{YYYY} của FB.JT theo **tên header** (không hard-code chữ cột — sheet đã từng chèn cột).

* Header ở dòng 6 (1-based). Thiếu header bắt buộc / trùng header không được phép -> :class:`SchemaError`,
  worker dừng cả chu kỳ, không hành động trên dữ liệu đoán.
* Có 2 cột tên ``Status`` (U và Y): CHỈ dùng cột ``Status`` ĐẦU TIÊN (cột U — nguồn chuẩn do kế toán cập nhật).
* Dòng đã Done (``Done``, ``Done trả tiền``) bị bỏ qua — chỉ kế toán chuyển sang Done.
* Đầu vào là ``values`` của Sheets API (nên đọc ``UNFORMATTED_VALUE`` + ``SERIAL_NUMBER`` cho ngày/tiền).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.core.calculation import normalize_text, parse_money, parse_order_datetime

HEADER_ROW = 6

#: field -> các tên header chấp nhận (so khớp sau normalize_text: bỏ dấu, thường hoá, gộp khoảng trắng/xuống dòng).
REQUIRED: dict[str, tuple[str, ...]] = {
    "month": ("month",),
    "group": ("group brand",),
    "brand": ("brand",),
    "campaign": ("campaign (khop haravan)",),
    "camp_end": ("ngay ket thuc camp",),
    "entity_b": ("ben b",),
    "op": ("op",),
    "nmv": ("real rev (nmv)",),
    "commission": ("comission", "commission"),
    "gmv": ("hrv gmv",),
    "pic": ("pic",),
    "title_mail": ("title mail",),
    "invoice_note": ("oanh note tinh trang hd vat va thanh toan",),
    "status": ("status",),
}
#: Header được phép xuất hiện nhiều lần; lấy lần đầu.
FIRST_OF_DUPLICATES = frozenset({"status"})
#: Cột bot được phép ghi (docs/ARCHITECTURE.md 12.1).
WRITABLE = ("nmv", "commission", "gmv")

DONE_STATUSES = frozenset({normalize_text("Done"), normalize_text("Done trả tiền")})
_NON_OP = frozenset({"", "non op"})


class SchemaError(ValueError):
    """Header FB.JT thiếu / đổi tên / trùng ngoài dự kiến."""


def col_letter(index: int) -> str:
    """0 -> 'A', 25 -> 'Z', 26 -> 'AA'."""
    s, n = "", index + 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


@dataclass(frozen=True)
class Columns:
    index: dict[str, int]

    def letter(self, name: str) -> str:
        return col_letter(self.index[name])


def map_header(header: Sequence[Any]) -> Columns:
    norm = [normalize_text(h) for h in header]
    index: dict[str, int] = {}
    problems: list[str] = []
    for name, aliases in REQUIRED.items():
        hits = [i for i, h in enumerate(norm) if h in aliases]
        if not hits:
            problems.append(f"thiếu cột '{aliases[0]}'")
        elif len(hits) > 1 and name not in FIRST_OF_DUPLICATES:
            problems.append(f"cột '{aliases[0]}' xuất hiện {len(hits)} lần")
        else:
            index[name] = hits[0]
    if problems:
        raise SchemaError("FB.JT sai cấu trúc: " + "; ".join(problems))
    return Columns(index)


@dataclass(frozen=True)
class FbjtRow:
    row_number: int                 # số dòng như Google Sheets hiển thị
    month: str
    group: str
    brand: str
    campaign: str
    entity_b: str
    op: str
    camp_end: date | None
    nmv: Decimal | None
    commission: Decimal | None
    gmv: Decimal | None
    pic: str
    title_mail: str
    invoice_note: str
    status: str

    @property
    def is_done(self) -> bool:
        return normalize_text(self.status) in DONE_STATUSES

    @property
    def is_non_op(self) -> bool:
        return normalize_text(self.op) in _NON_OP


@dataclass
class ParseOutcome:
    columns: Columns
    rows: list[FbjtRow] = field(default_factory=list)
    issues: list[tuple[int, str]] = field(default_factory=list)   # (dòng, mô tả) — dòng lỗi bị bỏ, không đoán


def _cell(row: Sequence[Any], i: int) -> Any:
    return row[i] if i < len(row) else None


def _text(v: Any) -> str:
    return "" if v is None else " ".join(str(v).split())


def parse_date(v: Any) -> date | None:
    """Serial number, ``dd/mm/yyyy``, ``d/m/yy`` (vd '30/7/26')."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if isinstance(v, str):
        try:
            return datetime.strptime(v.strip(), "%d/%m/%y").date()
        except ValueError:
            pass
    dt = parse_order_datetime(v)
    return dt.date() if dt else None


def parse_rows(values: Sequence[Sequence[Any]], *, header_row: int = HEADER_ROW) -> ParseOutcome:
    if len(values) < header_row:
        raise SchemaError(f"FB.JT không có dòng header {header_row}")
    cols = map_header(values[header_row - 1])
    out = ParseOutcome(cols)
    c = cols.index
    for offset, row in enumerate(values[header_row:], start=header_row + 1):
        brand = _text(_cell(row, c["brand"]))
        if not brand:
            continue
        try:
            parsed = FbjtRow(
                row_number=offset, month=_text(_cell(row, c["month"])), group=_text(_cell(row, c["group"])),
                brand=brand, campaign=_text(_cell(row, c["campaign"])), entity_b=_text(_cell(row, c["entity_b"])),
                op=_text(_cell(row, c["op"])), camp_end=parse_date(_cell(row, c["camp_end"])),
                nmv=parse_money(_cell(row, c["nmv"])), commission=parse_money(_cell(row, c["commission"])),
                gmv=parse_money(_cell(row, c["gmv"])), pic=_text(_cell(row, c["pic"])),
                title_mail=_text(_cell(row, c["title_mail"])), invoice_note=_text(_cell(row, c["invoice_note"])),
                status=_text(_cell(row, c["status"])),
            )
        except ValueError as exc:
            out.issues.append((offset, str(exc)))
            continue
        if not parsed.is_done:
            out.rows.append(parsed)
    return out


def row_key(brand: str, campaign: str) -> tuple[str, str]:
    """Khoá nối FB.JT (BRAND, CAMPAIGN) <-> link order NON OP {YY} (BRAND, CAMP)."""
    return normalize_text(brand), normalize_text(campaign)
