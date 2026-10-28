"""
Audit Gate — quyết định 1 email được TỰ GỬI hay vào hàng đợi duyệt (docs/ARCHITECTURE.md 11.2, 13.4, 13.5, 15.4).

Hàm thuần: worker thu thập ``*Facts`` từ FB.JT / DB / Gmail rồi gọi ``evaluate_*``. Bất kỳ lý do nào -> không tự
gửi. Template chưa cấu hình (``NOT_CONFIGURED``) chặn mọi email, kể cả loại được tự gửi.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.recon.rules import days_late, late_fee, reminder_due


class EmailKind(str, enum.Enum):
    ACCEPTANCE = "ACCEPTANCE"                       # nghiệm thu: BBNT + số liệu
    PAYMENT_REMINDER = "PAYMENT_REMINDER"           # nhắc thanh toán quá hạn + phạt ước tính
    CONFIRM_BBNT_REMINDER = "CONFIRM_BBNT_REMINDER"
    INVOICE_DRAFT_REMINDER = "INVOICE_DRAFT_REMINDER"
    ACCOUNTING_NOTICE = "ACCOUNTING_NOTICE"


#: Chỉ 2 loại được tự gửi khi Audit đạt; còn lại luôn cần duyệt.
AUTO_SEND = frozenset({EmailKind.ACCEPTANCE, EmailKind.PAYMENT_REMINDER})


class SendMode(str, enum.Enum):
    AUTO = "AUTO"
    REVIEW = "REVIEW"
    BLOCKED = "BLOCKED"        # thiếu template — không thể gửi, kể cả có duyệt


@dataclass(frozen=True)
class AcceptanceFacts:
    non_op: bool                      # FB.JT OP trống/NON OP và DATA BRAND.OP trống
    order_links_found: int            # số link order khớp (BRAND, CAMPAIGN) — phải đúng 1
    order_link_readable: bool
    contracts_found: int              # DATA CONTRACT 'Đã ký' khớp brand + pháp nhân + FB — phải đúng 1
    contract_in_portal_master: bool
    generator_errors: int
    figures_match_sheet: bool         # I/K/M trên FB.JT đã bằng số BBNT (sau khi ghi / duyệt)
    brand_emails: int
    loopmail_threads_found: int       # tìm theo TITLE MAIL + email brand — phải đúng 1
    already_sent: bool
    template_configured: bool
    within_send_window: bool
    on_hold: bool


@dataclass(frozen=True)
class PaymentReminderFacts:
    non_op: bool
    status_is_invoice_issued: bool    # U = 'Đã xuất hóa đơn + Chờ tiền'
    invoices_parsed: bool             # đọc được số + ngày HĐ chính thức
    due_date: date | None
    today: date
    commission_positive_and_matched: bool
    loopmail_known: bool
    last_reminder: date | None        # theo loopmail (DB + REMINDER LOG)
    brand_replied_since_last: bool    # UNC / đang xử lý / xin gia hạn / trả một phần -> chuyển KAM
    template_configured: bool
    on_hold: bool


@dataclass(frozen=True)
class AuditResult:
    kind: EmailKind
    reasons: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.reasons

    @property
    def mode(self) -> SendMode:
        if "TEMPLATE_NOT_CONFIGURED" in self.reasons:
            return SendMode.BLOCKED
        return SendMode.AUTO if self.passed and self.kind in AUTO_SEND else SendMode.REVIEW


def _check(pairs: list[tuple[bool, str]]) -> tuple[str, ...]:
    return tuple(code for ok, code in pairs if not ok)


def evaluate_acceptance(f: AcceptanceFacts) -> AuditResult:
    return AuditResult(EmailKind.ACCEPTANCE, _check([
        (f.non_op, "NOT_FB_NON_OP"),
        (f.order_links_found == 1, "ORDER_LINK_NOT_UNIQUE"),
        (f.order_link_readable, "ORDER_SHEET_UNREADABLE"),
        (f.contracts_found == 1, "CONTRACT_NOT_UNIQUE"),
        (f.contract_in_portal_master, "CONTRACT_MISSING_IN_PORTAL_MASTER"),
        (f.generator_errors == 0, "BBNT_GENERATOR_ERRORS"),
        (f.figures_match_sheet, "FIGURES_MISMATCH_FBJT"),
        (f.brand_emails > 0, "NO_BRAND_EMAIL"),
        (f.loopmail_threads_found == 1, "LOOPMAIL_NOT_UNIQUE"),
        (not f.already_sent, "ALREADY_SENT"),
        (f.template_configured, "TEMPLATE_NOT_CONFIGURED"),
        (f.within_send_window, "OUTSIDE_SEND_WINDOW"),
        (not f.on_hold, "ON_HOLD"),
    ]))


def evaluate_payment_reminder(f: PaymentReminderFacts) -> AuditResult:
    return AuditResult(EmailKind.PAYMENT_REMINDER, _check([
        (f.non_op, "NOT_FB_NON_OP"),
        (f.status_is_invoice_issued, "STATUS_NOT_INVOICE_ISSUED"),
        (f.invoices_parsed, "INVOICE_UNREADABLE"),
        (f.due_date is not None and f.today > f.due_date, "NOT_OVERDUE"),
        (f.commission_positive_and_matched, "COMMISSION_NOT_CONFIRMED"),
        (f.loopmail_known, "LOOPMAIL_UNKNOWN"),
        (reminder_due(f.last_reminder, f.today), "REMINDED_RECENTLY"),
        (not f.brand_replied_since_last, "BRAND_REPLIED"),
        (f.template_configured, "TEMPLATE_NOT_CONFIGURED"),
        (not f.on_hold, "ON_HOLD"),
    ]))


def review_only(kind: EmailKind, *, template_configured: bool) -> AuditResult:
    """Các loại luôn cần duyệt (nhắc confirm BBNT / HĐ nháp, báo kế toán)."""
    return AuditResult(kind, () if template_configured else ("TEMPLATE_NOT_CONFIGURED",))


def penalty_line(k: Decimal, due: date, today: date) -> tuple[int, Decimal]:
    """(số ngày chậm, phạt ước tính) cho 1 hoá đơn trong email nhắc thanh toán."""
    n = days_late(due, today)
    return n, late_fee(k, n)
