"""Tab Tổng quan (anh Hiệp chốt 04/11/2026) — số liệu từ tab ``FB-{YYYY}`` + % hoa hồng theo hợp đồng (DATA BRAND).

* Đã thu = tổng hoa hồng (cột K) của dòng Status U = Done / Done trả tiền.
* Còn pending = tổng K của dòng đã có K nhưng chưa Done.
* Commission estimate = GMV (M) × % hoa hồng theo hợp đồng (chưa VAT) cho dòng chưa có K.
* Cancel trung bình = 1 − ΣNMV / ΣGMV trên các dòng có cả NMV và GMV (= bình quân cột J theo trọng số GMV).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from app.core.calculation import normalize_text, parse_money
from app.master.data_brand import brand_code, data_brand_columns
from app.recon.fbjt import FbjtRow, parse_rows
from app.recon.rules import BusinessCalendar, days_late, parse_invoices, payment_due

ZERO = Decimal(0)


def contract_rates(data_brand: Sequence[Sequence[Any]]) -> dict[str, Decimal]:
    """Mã brand -> % hoa hồng (cột COMMISSION chưa VAT) của hợp đồng CÒN HIỆU LỰC; ô không phải số bị bỏ."""
    h, col = data_brand_columns(data_brand)
    out: dict[str, Decimal] = {}
    for row in data_brand[h + 1:]:
        def get(k: str, row: Sequence[Any] = row) -> Any:
            j = col.get(k)
            return row[j] if j is not None and j < len(row) else None
        if normalize_text(get("status")) not in {"con hieu luc", "da ky"}:
            continue
        raw = str(get("rate") or "").strip()
        try:
            rate = parse_money(raw.rstrip("%"))
        except ValueError:
            continue
        if rate is not None:
            out.setdefault(brand_code(get("brand")), rate / 100 if raw.endswith("%") or rate > 1 else rate)
    return out


@dataclass
class Bucket:
    collected: Decimal = ZERO
    pending: Decimal = ZERO
    estimate: Decimal = ZERO
    nmv: Decimal = ZERO
    gmv_settled: Decimal = ZERO           # GMV của dòng có NMV (mẫu số cancel)
    rows: int = 0
    no_rate: int = 0                      # dòng cần estimate nhưng brand chưa có % hợp đồng

    def add(self, r: FbjtRow, done: bool, rates: dict[str, Decimal]) -> None:
        self.rows += 1
        k = r.commission or ZERO
        if done:
            self.collected += k
        elif k:
            self.pending += k
        elif r.gmv:
            rate = rates.get(brand_code(r.brand))
            if rate is None:
                self.no_rate += 1
            else:
                self.estimate += r.gmv * rate
        if r.nmv and r.gmv:
            self.nmv += r.nmv
            self.gmv_settled += r.gmv

    @property
    def cancel_rate(self) -> Decimal | None:
        return (1 - self.nmv / self.gmv_settled) if self.gmv_settled else None


@dataclass
class Summary:
    total: Bucket = field(default_factory=Bucket)
    by_month: dict[str, Bucket] = field(default_factory=dict)
    months: list[str] = field(default_factory=list)
    pics: list[str] = field(default_factory=list)
    overdue: dict[str, Late] = field(default_factory=dict)        # brand -> nợ quá hạn


@dataclass
class Late:
    amount: Decimal = ZERO
    days: int = 0                 # số ngày trễ lâu nhất của brand


def overdue_days(r: FbjtRow, today: date, cal: BusinessCalendar) -> int:
    """Dòng chưa Done, đã có K + hoá đơn trong ghi chú: trễ = hôm nay − (ngày HĐ sớm nhất + 10 ngày làm việc, Q17)."""
    invoices = parse_invoices(r.invoice_note)
    if not r.commission or not invoices:
        return 0
    return days_late(payment_due(min(i.issued_on for i in invoices), cal), today)


def summarize(values: Sequence[Sequence[Any]], data_brand: Sequence[Sequence[Any]], *, month: str | None = None,
              pic: str | None = None, today: date | None = None,
              cal: BusinessCalendar | None = None) -> Summary:
    # shortcut: lịch mặc định chỉ có lễ dương lịch cố định (không đọc bảng holiday đã duyệt) — sai 1–2 ngày quanh Tết
    today, cal = today or date.today(), cal or BusinessCalendar()
    parsed = parse_rows(values)
    rates = contract_rates(data_brand)
    rows = [(r, False) for r in parsed.rows] + [(r, True) for r in parsed.done]
    out = Summary(months=sorted({r.month for r, _ in rows if r.month}, key=lambda m: (len(m), m)),
                  pics=sorted({r.pic for r, _ in rows if r.pic}))
    for r, done in sorted(rows, key=lambda x: x[0].row_number):
        if (month and r.month != month) or (pic and normalize_text(r.pic) != normalize_text(pic)):
            continue
        out.total.add(r, done, rates)
        out.by_month.setdefault(r.month or "?", Bucket()).add(r, done, rates)
        late = 0 if done else overdue_days(r, today, cal)
        if late:
            b = out.overdue.setdefault(r.brand, Late())
            b.amount += r.commission or ZERO
            b.days = max(b.days, late)
    return out
