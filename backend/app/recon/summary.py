"""Tab Tổng quan (anh Hiệp chốt 04–05/11/2026) — số liệu từ tab ``FB-{YYYY}`` của FB.JT.

* Đã thu = tổng hoa hồng (cột K) của dòng Status U = Done / Done trả tiền.
* Còn pending = tổng K của dòng đã có K nhưng chưa Done.
* Nghiệm thu chưa làm (dòng có GMV, chưa có K) = GMV × 22% × 80%.
* Commission ETA cả năm = Đã thu + Pending + Nghiệm thu chưa làm.
* Cancel = 1 − ΣNMV / ΣGMV trên các dòng có cả NMV và GMV (= bình quân cột J theo trọng số GMV).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from app.core.calculation import normalize_text
from app.recon.fbjt import FbjtRow, parse_rows
from app.recon.rules import BusinessCalendar, days_late, parse_invoices, payment_due

ZERO = Decimal(0)
ESTIMATE_RATE = Decimal("0.22") * Decimal("0.8")       # nghiệm thu chưa làm: GMV × 22% × 80%


def _month_key(m: str) -> tuple[int, str]:
    return (int(m), m) if m.isdigit() else (99, m)


@dataclass
class Bucket:
    collected: Decimal = ZERO
    pending: Decimal = ZERO
    estimate: Decimal = ZERO              # nghiệm thu chưa làm (GMV × 22% × 80%)
    nmv: Decimal = ZERO
    gmv_settled: Decimal = ZERO           # GMV của dòng có NMV (mẫu số cancel)
    rows: int = 0
    last: dict[str, str] = field(default_factory=dict)   # collected / pending / eta -> tháng gần nhất

    def _seen(self, kind: str, month: str) -> None:
        if month and _month_key(month) > _month_key(self.last.get(kind, "0")):
            self.last[kind] = month

    def add(self, r: FbjtRow, done: bool) -> None:
        self.rows += 1
        k = r.commission or ZERO
        if done:
            self.collected += k
            self._seen("collected", r.month)
        elif k:
            self.pending += k
            self._seen("pending", r.month)
        elif r.gmv:
            self.estimate += r.gmv * ESTIMATE_RATE
        if k or r.gmv:
            self._seen("eta", r.month)
        if r.nmv and r.gmv:
            self.nmv += r.nmv
            self.gmv_settled += r.gmv

    @property
    def eta(self) -> Decimal:
        return self.collected + self.pending + self.estimate

    @property
    def cancel_rate(self) -> Decimal | None:
        return (1 - self.nmv / self.gmv_settled) if self.gmv_settled else None


@dataclass
class Summary:
    total: Bucket = field(default_factory=Bucket)
    by_month: dict[str, Bucket] = field(default_factory=dict)
    months: list[str] = field(default_factory=list)
    pics: list[str] = field(default_factory=list)
    by_group: dict[str, Bucket] = field(default_factory=dict)     # group brand (trống -> brand)
    overdue: dict[str, Late] = field(default_factory=dict)        # group brand (trống -> brand) -> nợ quá hạn


@dataclass
class Late:
    amount: Decimal = ZERO
    days: int = 0                 # số ngày trễ lâu nhất trong group


def overdue_days(r: FbjtRow, today: date, cal: BusinessCalendar) -> int:
    """Dòng chưa Done, đã có K + hoá đơn trong ghi chú: trễ = hôm nay − (ngày HĐ sớm nhất + 10 ngày làm việc, Q17)."""
    invoices = parse_invoices(r.invoice_note)
    if not r.commission or not invoices:
        return 0
    return days_late(payment_due(min(i.issued_on for i in invoices), cal), today)


def summarize(values: Sequence[Sequence[Any]], *, month: str | None = None,
              pic: str | None = None, today: date | None = None,
              cal: BusinessCalendar | None = None) -> Summary:
    # shortcut: lịch mặc định chỉ có lễ dương lịch cố định (không đọc bảng holiday đã duyệt) — sai 1–2 ngày quanh Tết
    today, cal = today or date.today(), cal or BusinessCalendar()
    parsed = parse_rows(values)
    rows = [(r, False) for r in parsed.rows] + [(r, True) for r in parsed.done]
    out = Summary(months=sorted({r.month for r, _ in rows if r.month}, key=_month_key),
                  pics=sorted({r.pic for r, _ in rows if r.pic}))
    for r, done in sorted(rows, key=lambda x: x[0].row_number):
        if (month and r.month != month) or (pic and normalize_text(r.pic) != normalize_text(pic)):
            continue
        out.total.add(r, done)
        out.by_month.setdefault(r.month or "?", Bucket()).add(r, done)
        out.by_group.setdefault(r.group or r.brand, Bucket()).add(r, done)
        late = 0 if done else overdue_days(r, today, cal)
        if late:
            b = out.overdue.setdefault(r.group or r.brand, Late())
            b.amount += r.commission or ZERO
            b.days = max(b.days, late)
    return out
