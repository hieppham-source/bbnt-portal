"""
Quy tắc nghiệp vụ đã chốt (docs/ARCHITECTURE.md mục 11–16). Toàn bộ là hàm thuần — không I/O.

* Đuôi số BBNT + tên chương trình từ tên campaign (12.3)
* Đọc số + ngày hoá đơn chính thức trong ghi chú HĐ (11.3)
* Lịch ngày làm việc T2–T6 trừ ngày lễ đã duyệt (12.2, 13.3)
* Mốc gửi email nghiệm thu, hạn thanh toán, phạt chậm ước tính (12.2, 13.4)
* Chia K của BBNT group cho nhiều dòng FB.JT (15.1)
* Quyết định ghi ô I/K/M trên FB.JT (12.1)
"""
from __future__ import annotations

import enum
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

#: Email nghiệm thu đầu tiên: ngày kết thúc camp (FB.JT cột G) + 25 ngày lịch.
ACCEPTANCE_DELAY_DAYS = 25
#: Brand confirm BBNT / hoá đơn nháp: 3 ngày làm việc.
CONFIRM_BUSINESS_DAYS = 3
#: Hạn thanh toán: 10 ngày làm việc từ ngày hoá đơn chính thức.
PAYMENT_BUSINESS_DAYS = 10
#: Nhắc lại (thanh toán / confirm quá hạn): 3 ngày lịch một lần, tính riêng từng loopmail.
REMIND_EVERY_DAYS = 3
#: Phạt chậm thanh toán: 0,05% / ngày trên K.
LATE_FEE_RATE_PER_DAY = Decimal("0.0005")
VND = Decimal(1)


# =============================================================================
# Đuôi số BBNT
# =============================================================================
_CAMPAIGN_RE = re.compile(r"^\s*(?P<name>.+?)\s*-\s*(?P<month>\d{1,2})\s*/\s*(?P<year>\d{4})\s*$")


def _parse_campaign(campaign: str) -> tuple[list[str], str]:
    """``'<tên> - MM/YYYY'`` -> (các chữ của tên, ``'MMYY'``). Sai dạng -> ``ValueError``."""
    m = _CAMPAIGN_RE.match(campaign or "")
    if not m:
        raise ValueError(f"Tên campaign không đúng dạng '<tên> - MM/YYYY': {campaign!r}")
    month = int(m["month"])
    if not 1 <= month <= 12:
        raise ValueError(f"Tháng không hợp lệ trong campaign: {campaign!r}")
    return m["name"].upper().split(), f"{month:02d}{m['year'][2:]}"


def campaign_suffix(campaign: str) -> str:
    """``'<tên> - MM/YYYY'`` -> ``'<mã>.<MM><YY>'``. Tên 1 chữ giữ nguyên (in hoa); từ 2 chữ -> chữ cái đầu
    của 2 chữ đầu. Sai dạng -> ``ValueError`` (bot chuyển KAM, không đoán).

    >>> campaign_suffix("MIX - 07/2026")
    'MIX.0726'
    >>> campaign_suffix("Mix Derma - 07/2026")
    'MD.0726'
    """
    words, mmyy = _parse_campaign(campaign)
    code = words[0] if len(words) == 1 else words[0][0] + words[1][0]
    return f"{code}.{mmyy}"


def campaign_program_name(campaign: str) -> str:
    """Tên chương trình ghi trên BBNT: ``'<TÊN> <MMYY>'`` (anh Hiệp chốt 28/10/2026).

    >>> campaign_program_name("MIX - 07/2026")
    'MIX 0726'
    >>> campaign_program_name("Mix  Derma - 7/2026")
    'MIX DERMA 0726'
    """
    words, mmyy = _parse_campaign(campaign)
    return f"{' '.join(words)} {mmyy}"


# =============================================================================
# Hoá đơn chính thức trong ghi chú (vd "HĐ: 1358 (08/10/2026)", "HD: 1377 (15/10/2026)", "HĐ:179 (07/10/2026)")
# =============================================================================
_INVOICE_RE = re.compile(r"H[ĐD]\s*:?\s*(?P<no>\d+)\s*\(\s*(?P<d>\d{1,2})/(?P<m>\d{1,2})/(?P<y>\d{4})\s*\)",
                         re.IGNORECASE)


@dataclass(frozen=True)
class Invoice:
    number: str
    issued_on: date


def parse_invoices(note: str | None) -> list[Invoice]:
    """Mọi hoá đơn đọc được trong ô ghi chú; ngày sai -> bỏ qua hoá đơn đó (người xử lý).

    >>> parse_invoices("HĐ: 1343(05/10/2026)")
    [Invoice(number='1343', issued_on=datetime.date(2026, 10, 5))]
    """
    out: list[Invoice] = []
    for m in _INVOICE_RE.finditer(note or ""):
        try:
            out.append(Invoice(m["no"], date(int(m["y"]), int(m["m"]), int(m["d"]))))
        except ValueError:
            continue
    return out


# =============================================================================
# Lịch làm việc
# =============================================================================
def fixed_solar_holidays(year: int) -> frozenset[date]:
    """Ngày lễ dương lịch cố định (Bộ luật Lao động): 1/1, 30/4, 1/5, 2/9. Tết âm lịch, Giỗ Tổ, ngày liền kề
    2/9 và ngày nghỉ bù đổi theo năm -> lấy từ danh sách đã duyệt (docs/ARCHITECTURE.md 13.3), không tính ở đây."""
    return frozenset({date(year, 1, 1), date(year, 4, 30), date(year, 5, 1), date(year, 9, 2)})


@dataclass(frozen=True)
class BusinessCalendar:
    """T2–T6, trừ ``holidays`` (danh sách đã duyệt) và ngày lễ dương lịch cố định."""

    holidays: frozenset[date] = field(default_factory=frozenset)

    def is_business_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.holidays and d not in fixed_solar_holidays(d.year)

    def add_business_days(self, start: date, n: int) -> date:
        """Ngày làm việc thứ ``n`` SAU ``start`` (không tính chính ngày ``start``)."""
        if n < 0:
            raise ValueError("n phải >= 0")
        d = start
        while n:
            d += timedelta(days=1)
            if self.is_business_day(d):
                n -= 1
        return d


def acceptance_send_date(camp_end: date) -> date:
    return camp_end + timedelta(days=ACCEPTANCE_DELAY_DAYS)


def confirm_deadline(sent_on: date, cal: BusinessCalendar) -> date:
    return cal.add_business_days(sent_on, CONFIRM_BUSINESS_DAYS)


def payment_due(invoice_date: date, cal: BusinessCalendar) -> date:
    return cal.add_business_days(invoice_date, PAYMENT_BUSINESS_DAYS)


def days_late(due: date, today: date) -> int:
    return max(0, (today - due).days)


def late_fee(commission_k: Decimal, late_days: int) -> Decimal:
    """Phạt ước tính = K × 0,05% × số ngày chậm, làm tròn đến đồng.

    >>> late_fee(Decimal(17268768), 12)
    Decimal('103613')
    """
    if late_days <= 0 or commission_k <= 0:
        return Decimal(0)
    return (commission_k * LATE_FEE_RATE_PER_DAY * late_days).quantize(VND, ROUND_HALF_UP)


def reminder_due(last_sent: date | None, today: date) -> bool:
    """Chu kỳ nhắc 3 ngày lịch, tính riêng từng loopmail (ngày gửi gần nhất lấy từ DB + REMINDER LOG)."""
    return last_sent is None or (today - last_sent).days >= REMIND_EVERY_DAYS


# =============================================================================
# Chia K của BBNT group cho nhiều dòng FB.JT
# =============================================================================
def allocate_total(total: Decimal, raw: Mapping[str, Decimal], weight: Mapping[str, Decimal]) -> dict[str, Decimal]:
    """Làm tròn từng phần đến đồng; chênh lệch với ``total`` dồn vào phần có ``weight`` (NMV) lớn nhất ->
    tổng các dòng luôn bằng đúng ``total`` của BBNT."""
    if not raw:
        raise ValueError("Không có dòng nào để chia")
    out = {k: v.quantize(VND, ROUND_HALF_UP) for k, v in raw.items()}
    largest = max(raw, key=lambda k: (weight.get(k, Decimal(0)), k))
    out[largest] += total.quantize(VND, ROUND_HALF_UP) - sum(out.values(), Decimal(0))
    return out


# =============================================================================
# Ghi ô I/K/M trên FB.JT
# =============================================================================
class CellAction(str, enum.Enum):
    WRITE = "WRITE"      # ô trống -> ghi số BBNT
    NOOP = "NOOP"        # đã bằng số BBNT
    REVIEW = "REVIEW"    # khác số BBNT / là công thức -> KAM duyệt "Đổi / Giữ"


def plan_cell(current: Decimal | None, target: Decimal, *, is_formula: bool = False) -> CellAction:
    if is_formula:
        return CellAction.REVIEW
    if current is None:
        return CellAction.WRITE
    return CellAction.NOOP if current == target.quantize(VND, ROUND_HALF_UP) else CellAction.REVIEW


def invoices_total(rows: Iterable[tuple[str, Decimal]]) -> dict[str, Decimal]:
    """Gộp nhiều dòng cùng số HĐ thành 1 dòng HĐ, K = tổng (mục 16)."""
    out: dict[str, Decimal] = {}
    for number, k in rows:
        out[number] = out.get(number, Decimal(0)) + k
    return out
