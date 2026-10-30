"""
Sau email nghiệm thu (EMAIL 2): theo dõi Status (U) + loopmail, nhắc theo mẫu "Mẫu email nghiệm thu OM (Bot)".

* **Giai đoạn** đọc từ U (``stage_from_status``), áp lên case (trigger SHEET): HĐ nháp -> AWAITING_INVOICE_CONFIRM,
  đã xuất HĐ -> AWAITING_PAYMENT, Done -> DONE.
* **Bot ghi U** (anh Hiệp chốt 30/10/2026), chỉ tiến lên, tối đa tới "Đã done BBNT + Chờ KT xuất HĐ nháp" (BBNT online,
  không chờ bản cứng): gửi Email 1 -> "Brand chưa gửi data"; gửi Email 2 -> "Đã Confirm số + Chờ brand gửi BBNT";
  KAM đánh dấu brand đã confirm -> "Đã done BBNT + Chờ KT xuất HĐ nháp". Từ HĐ nháp trở đi là của kế toán.
* **Thư brand trả lời**: đọc metadata loopmail (không lưu nội dung), ghi ``email_message`` IN; thư người thật
  hiện ở dashboard để KAM xem. Thư tự động / bounce được đánh dấu riêng.
* **3A** nhắc ký BBNT (U còn trước BBNT, quá Email 2 + 3 ngày làm việc, sau đó 3 ngày/lần) — chờ duyệt.
* **3B** nhắc confirm HĐ nháp (kế toán gửi HĐ nháp; quá lúc thấy U đổi + 3 ngày làm việc, sau đó 3 ngày/lần) — chờ
  duyệt (Q18).
* **4A / 4B** nhắc thanh toán: từ hạn = ngày HĐ + 10 ngày làm việc, 3 ngày/lần, **tự gửi** khi Audit đạt; 4B có
  dòng phạt ước tính K × 0,05% × ngày chậm (Q17). Brand đã trả lời từ lần nhắc trước -> dừng, KAM xử lý.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import psycopg

from app.core.calculation import normalize_text
from app.recon.audit_gate import PaymentReminderFacts, evaluate_payment_reminder
from app.recon.db import CaseState, apply_transition
from app.recon.emailer import VN, EmailSettings, TestNotes, Thread, camp_parts, deadline, envelope, in_send_window
from app.recon.fbjt import FbjtRow, brand_key, campaign_key
from app.recon.rules import (
    BusinessCalendar,
    confirm_deadline,
    days_late,
    late_fee,
    parse_invoices,
    payment_due,
    reminder_due,
)
from app.recon.state_machine import InvalidTransition, Stage, State, Trigger, stage_from_status, transition
from app.recon.worker import PRE_BBNT_STATUSES

ACTOR = "worker"
FOLLOW = (State.ACCEPTANCE_SENT, State.AWAITING_INVOICE_CONFIRM, State.AWAITING_PAYMENT)
CONFIRM_BBNT, INVOICE_DRAFT, PAYMENT = "CONFIRM_BBNT_REMINDER", "INVOICE_DRAFT_REMINDER", "PAYMENT_REMINDER"
_TARGET = {Stage.INVOICE_DRAFT: State.AWAITING_INVOICE_CONFIRM, Stage.INVOICE_ISSUED: State.AWAITING_PAYMENT,
           Stage.DONE: State.DONE}
_ORDER = [Stage.PRE_INVOICE, Stage.INVOICE_DRAFT, Stage.INVOICE_ISSUED, Stage.DONE]


def _vnd(v: Decimal) -> str:
    return f"{v:,.0f}".replace(",", ".")


@dataclass
class FollowCase:
    id: uuid.UUID
    state: State
    version: int
    thread_id: str | None
    brands: list[str]
    campaign: str
    rows: list[FbjtRow]
    done_rows: int
    since: datetime                    # lúc vào trạng thái hiện tại
    confirmed: bool = False            # KAM đã đánh dấu brand confirm BBNT

    @property
    def stage(self) -> Stage | None:
        """Giai đoạn chậm nhất trong các dòng (thận trọng); U lạ -> ``None``. Mọi dòng Done -> DONE."""
        if not self.rows:
            return Stage.DONE if self.done_rows else None
        stages = [stage_from_status(r.status) for r in self.rows]
        if any(s is None for s in stages):
            return None
        return min((s for s in stages if s is not None), key=_ORDER.index)

    @property
    def k_total(self) -> Decimal:
        return sum((r.commission or Decimal(0) for r in self.rows), Decimal(0))

    def camp(self) -> str:
        name, month = camp_parts(self.campaign)
        return f"{name} {month}".strip()

    def subject(self) -> str:
        """Tiêu đề thư test / xem trước khi chưa có loopmail (thư thật luôn reply loopmail)."""
        return f"{', '.join(self.brands)} - FB - {self.camp()}"


def load_cases(conn: psycopg.Connection, rows: Sequence[FbjtRow], done: Sequence[FbjtRow], tab: str
               ) -> list[FollowCase]:
    """Case đang theo dõi + các dòng FB.JT của nó (nối qua ``sheet_row``: brand + campaign)."""
    live = {(brand_key(r.brand), campaign_key(r.campaign)): r for r in rows}
    finished = {(brand_key(r.brand), campaign_key(r.campaign)) for r in done}
    out: dict[uuid.UUID, FollowCase] = {}
    for cid, state, ver, thread, brands, camp, bk, ck, since, confirmed in conn.execute(
            "SELECT c.id, c.state, c.state_version, c.gmail_thread_id, c.brand_codes, c.campaign, sr.brand_key, "
            "sr.campaign_key, (SELECT max(at) FROM state_transition t WHERE t.case_id = c.id "
            "AND t.to_state = c.state), c.brand_confirmed_at IS NOT NULL "
            "FROM recon_case c JOIN sheet_row sr ON sr.case_id = c.id AND sr.tab = %s WHERE c.state = ANY(%s) "
            "ORDER BY c.id", (tab, [s.value for s in FOLLOW])).fetchall():
        fc = out.setdefault(cid, FollowCase(cid, State(state), ver, thread, brands, camp, [], 0, since, confirmed))
        if (bk, ck) in live:
            fc.rows.append(live[(bk, ck)])
        elif (bk, ck) in finished:
            fc.done_rows += 1
    return list(out.values())


def track_stages(conn: psycopg.Connection, cases: Sequence[FollowCase], *, now: datetime) -> int:
    """Áp giai đoạn U lên case (trigger SHEET). Trả số case đổi trạng thái."""
    moved = 0
    for fc in cases:
        target = _TARGET.get(fc.stage) if fc.stage else None
        if target is None or target is fc.state:
            continue
        try:
            t = transition(fc.state, target, trigger=Trigger.SHEET, actor=ACTOR, at=now, reason="Status U")
        except InvalidTransition:
            continue                                               # vd U lùi giai đoạn: giữ nguyên, người xử lý
        new = apply_transition(conn, CaseState(fc.id, fc.state, fc.version, None), t)
        fc.state, fc.version, fc.since = new.state, new.version, now
        moved += 1
    return moved


def sync_replies(conn: psycopg.Connection, cases: Sequence[FollowCase], gmail: Any) -> dict[uuid.UUID, Thread]:
    """Ghi thư đến trong loopmail (metadata) vào ``email_message``. Lỗi Gmail 1 case -> bỏ qua case đó."""
    threads: dict[uuid.UUID, Thread] = {}
    for fc in cases:
        if not fc.thread_id:
            continue
        try:
            th = gmail.thread(fc.thread_id)
        except RuntimeError:
            continue
        threads[fc.id] = th
        for m in th.messages:
            if m.internal:
                continue
            conn.execute(
                "INSERT INTO email_message (gmail_message_id, gmail_thread_id, rfc822_message_id, mailbox, case_id, "
                "direction, from_addr, sent_at, is_auto_reply, classification, subject) "
                "VALUES (%s, %s, %s, 'me', %s, 'IN', %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (m.id, th.id, m.rfc822_id, fc.id, m.from_addr, datetime.fromtimestamp(m.at_ms / 1000, UTC), m.auto,
                 "BOUNCE" if m.bounce else "OOO" if m.auto else "UNKNOWN", m.subject))
    return threads


def brand_replied_after(th: Thread | None, after: datetime | None) -> bool:
    """Có thư người thật của brand (không tự động / bounce) sau ``after``."""
    if th is None or after is None:
        return False
    cut = after.timestamp() * 1000
    return any(not m.internal and not m.auto and not m.bounce and m.at_ms > cut for m in th.messages)


# ----------------------------------------------------------------------------------------------- nội dung thư
def confirm_bbnt_text(fc: FollowCase, due: str, signer: str) -> str:
    """EMAIL 3A — nhắc confirm biên bản nghiệm thu."""
    return "\n".join([
        f"Kính gửi anh/chị team {', '.join(fc.brands)},", "",
        f"Hannah xin nhắc lại: biên bản nghiệm thu campaign {fc.camp()} (tổng số tiền {_vnd(fc.k_total)} VNĐ) đã gửi "
        "ở mail trước hiện chưa nhận được xác nhận.",
        f"Anh/chị vui lòng kiểm tra, ký và gửi lại biên bản trước ngày {due} để Hannah kịp xuất hóa đơn theo đúng kỳ.",
        "", "Trân trọng,", signer or "(chưa cấu hình RECON_EMAIL_SIGNER)"])


def invoice_draft_text(fc: FollowCase, due: str, signer: str) -> str:
    """EMAIL 3B — bản nhắc lại (kế toán đã gửi HĐ nháp)."""
    return "\n".join([
        f"Kính gửi anh/chị team {', '.join(fc.brands)},", "",
        f"Hannah xin nhắc anh/chị xác nhận hóa đơn nháp campaign {fc.camp()} đã gửi ở mail trên. Vui lòng phản hồi "
        f"trước ngày {due} để Hannah xuất hóa đơn chính thức.", "",
        "Trân trọng,", signer or "(chưa cấu hình RECON_EMAIL_SIGNER)"])


@dataclass(frozen=True)
class InvoiceDue:
    number: str
    issued_on: date
    amount: Decimal                   # tổng K các dòng ghi số HĐ này
    due: date


def invoices_due(fc: FollowCase, cal: BusinessCalendar) -> list[InvoiceDue]:
    by_no: dict[str, InvoiceDue] = {}
    for r in fc.rows:
        for inv in parse_invoices(r.invoice_note):
            cur = by_no.get(inv.number)
            amount = (cur.amount if cur else Decimal(0)) + (r.commission or Decimal(0))
            by_no[inv.number] = InvoiceDue(inv.number, inv.issued_on, amount, payment_due(inv.issued_on, cal))
    return sorted(by_no.values(), key=lambda i: (i.due, i.number))


def payment_text(fc: FollowCase, invoices: Sequence[InvoiceDue], today: date, signer: str) -> str:
    """EMAIL 4A (đến hạn) / 4B (quá hạn, kèm phạt ước tính — Q17). Không có thông tin chuyển khoản (anh Hiệp bỏ)."""
    late = [i for i in invoices if days_late(i.due, today) > 0]
    head = [f"Kính gửi anh/chị team {', '.join(fc.brands)},", ""]
    tail = ["", "Trân trọng,", signer or "(chưa cấu hình RECON_EMAIL_SIGNER)"]
    if not late:
        lines = [f"Hannah xin thông tin khoản thanh toán campaign {fc.camp()} đã đến hạn:", ""]
        for i in invoices:
            lines += [f"Số hóa đơn: {i.number}", f"Ngày xuất hóa đơn: {i.issued_on:%d/%m/%Y}",
                      f"Số tiền cần thanh toán: {_vnd(i.amount)} VNĐ", f"Hạn thanh toán: {i.due:%d/%m/%Y}", ""]
        lines += ["Nhờ anh/chị sắp xếp thanh toán và phản hồi giúp Hannah lịch thanh toán dự kiến. Nếu đã chuyển "
                  "khoản, anh/chị vui lòng gửi ủy nhiệm chi để Hannah đối chiếu."]
        return "\n".join(head + lines + tail)
    lines = []
    for i in invoices:
        n = days_late(i.due, today)
        lines.append(f"Hóa đơn số {i.number} (ngày {i.issued_on:%d/%m/%Y}) campaign {fc.camp()}, số tiền "
                     f"{_vnd(i.amount)} VNĐ, " + (f"đã quá hạn {n} ngày (hạn {i.due:%d/%m/%Y}) và Hannah vẫn chưa nhận "
                                                  "được thanh toán." if n else f"đến hạn ngày {i.due:%d/%m/%Y}."))
        if n:
            lines.append(f"Phạt chậm thanh toán ước tính theo điều khoản hợp đồng (0,05%/ngày × {n} ngày): "
                         f"{_vnd(late_fee(i.amount, n))} VNĐ.")
    lines += ["", "Anh/chị vui lòng thanh toán hoặc phản hồi lịch thanh toán cụ thể trong thời gian sớm nhất. Nếu có "
              "vướng mắc về hóa đơn/chứng từ, anh/chị báo trong mail này để Hannah hỗ trợ xử lý ngay."]
    return "\n".join(head + lines + tail)


# ----------------------------------------------------------------------------------------------- gửi / chờ duyệt
Outbox = Callable[..., uuid.UUID | None]
Deliver = Callable[..., None]


def _history(conn: psycopg.Connection, case_id: uuid.UUID, kind: str) -> list[tuple[str, int, datetime]]:
    return [(r[0], r[1], r[2]) for r in conn.execute(
        "SELECT status, cycle_no, created_at FROM email_outbox WHERE case_id = %s AND kind = %s",
        (case_id, kind)).fetchall()]


def _last_sent(hist: Sequence[tuple[str, int, datetime]]) -> datetime | None:
    return max((h[2] for h in hist if h[0] == "SENT"), default=None)


def send_followups(conn: psycopg.Connection, cases: Sequence[FollowCase], threads: Mapping[uuid.UUID, Thread],
                   brand_to: Callable[[FollowCase], list[str] | str], gmail: Any, settings: EmailSettings,
                   outbox: Outbox, deliver: Deliver, guard: Callable[..., None], full: Callable[[], bool],
                   bump: Callable[..., None], *, now: datetime, cal: BusinessCalendar) -> None:
    """Nhắc 3A / 3B (chờ duyệt) và 4 (tự gửi). ``outbox`` / ``deliver`` / ``guard`` / ``full`` / ``bump`` là các
    hàm dùng chung của runner (cùng idempotency + audit + chốt chặn thư test)."""
    if settings.mode == "off":
        return
    live = settings.mode == "live"
    sfx = "" if live else "_TEST"
    today = now.astimezone(VN).date()
    for fc in cases:
        to = brand_to(fc)
        if isinstance(to, str):
            bump(to)
            continue
        th = threads.get(fc.id)
        if fc.state is State.ACCEPTANCE_SENT and not fc.confirmed and fc.rows and all(_pre_bbnt(r) for r in fc.rows):
            _queue_review(conn, fc, CONFIRM_BBNT + sfx, confirm_bbnt_text, to, th, settings, outbox, now=now, cal=cal,
                          start=_sent_at(conn, fc.id, "ACCEPTANCE" + ("" if live else "_TEST")) or fc.since)
        elif fc.state is State.AWAITING_INVOICE_CONFIRM:
            _queue_review(conn, fc, INVOICE_DRAFT + sfx, invoice_draft_text, to, th, settings, outbox, now=now, cal=cal,
                          start=fc.since)
        elif fc.state is State.AWAITING_PAYMENT:
            kind = PAYMENT + sfx
            hist = _history(conn, fc.id, kind)
            last = _last_sent(hist)
            invs = [i for i in invoices_due(fc, cal) if today >= i.due]
            gate = list(evaluate_payment_reminder(PaymentReminderFacts(
                non_op=True, status_is_invoice_issued=True, invoices_parsed=bool(invoices_due(fc, cal)),
                due_date=min((i.due for i in invs), default=None), today=today,
                commission_positive_and_matched=fc.k_total > 0, loopmail_known=th is not None,
                last_reminder=last.astimezone(VN).date() if last else None,
                brand_replied_since_last=brand_replied_after(th, last), template_configured=True,
                on_hold=False)).reasons)
            if not in_send_window(now):
                gate.append("OUTSIDE_SEND_WINDOW")
            if live and not settings.signer:
                gate.append("SIGNER_NOT_SET")
            if gate:
                if "NOT_DUE" not in gate and "REMINDED_RECENTLY" not in gate:      # chờ bình thường: không báo
                    bump(*gate)
                    conn.execute("UPDATE recon_case SET email_reasons = %s WHERE id = %s", (gate, fc.id))
                continue
            if full():
                continue
            text = payment_text(fc, invs, today, settings.signer)
            msg = envelope(settings, to, fc.subject(), text, thread=th,
                           notes=TestNotes((), f"reply thread {th.id}" if th else ""))
            guard(msg)
            oid = outbox(conn, fc.id, kind, 1 + max((h[1] for h in hist), default=0), "QUEUED", msg, text,
                         th.id if live and th else None, now)
            if oid is not None:
                deliver(conn, oid, fc.id, kind, msg, th.id if live and th else None, gmail, live=live, now=now)
                conn.execute("UPDATE recon_case SET email_reasons = '{}' WHERE id = %s", (fc.id,))


def _pre_bbnt(r: FbjtRow) -> bool:
    return normalize_text(r.status) in PRE_BBNT_STATUSES


def _sent_at(conn: psycopg.Connection, case_id: uuid.UUID, kind: str) -> datetime | None:
    row = conn.execute("SELECT max(created_at) FROM email_outbox WHERE case_id = %s AND kind = %s AND status = 'SENT'",
                       (case_id, kind)).fetchone()
    return row[0] if row else None


def _queue_review(conn: psycopg.Connection, fc: FollowCase, kind: str, text_of: Callable[[FollowCase, str, str], str],
                  to: Sequence[str], th: Thread | None, settings: EmailSettings, outbox: Outbox, *, now: datetime,
                  cal: BusinessCalendar, start: datetime) -> None:
    """Thư nhắc cần duyệt: quá ``start`` + 3 ngày làm việc, sau đó 3 ngày/lần kể từ lần nhắc gần nhất; đã có thư
    chờ duyệt / chờ gửi -> không tạo thêm. Thư được gửi bởi :func:`send_approved` sau khi KAM duyệt."""
    hist = _history(conn, fc.id, kind)
    if any(h[0] in ("PENDING_REVIEW", "QUEUED") for h in hist):
        return
    today, last = now.astimezone(VN).date(), _last_sent(hist)
    due = reminder_due(last.astimezone(VN).date(), today) if last else today > confirm_deadline(
        start.astimezone(VN).date(), cal)
    if not due:
        return
    text = text_of(fc, "{{DEADLINE}}", settings.signer)
    preview = envelope(EmailSettings("live", signer=settings.signer), to, fc.subject(), text, thread=th)
    outbox(conn, fc.id, kind, 1 + max((h[1] for h in hist), default=0), "PENDING_REVIEW", preview, text,
           th.id if th else None, now)


TEXTS: dict[str, Callable[[FollowCase, str, str], str]] = {CONFIRM_BBNT: confirm_bbnt_text,
                                                           INVOICE_DRAFT: invoice_draft_text}
STATE_OF = {CONFIRM_BBNT: State.ACCEPTANCE_SENT, INVOICE_DRAFT: State.AWAITING_INVOICE_CONFIRM}


def send_approved(conn: psycopg.Connection, cases: Sequence[FollowCase], threads: Mapping[uuid.UUID, Thread],
                  brand_to: Callable[[FollowCase], list[str] | str], gmail: Any, settings: EmailSettings,
                  deliver: Deliver, guard: Callable[..., None], full: Callable[[], bool], *, now: datetime,
                  cal: BusinessCalendar) -> None:
    """Gửi thư 3A / 3B KAM đã duyệt (QUEUED). Case đã sang giai đoạn khác -> huỷ thư."""
    if settings.mode == "off":
        return
    live = settings.mode == "live"
    sfx = "" if live else "_TEST"
    by_id = {fc.id: fc for fc in cases}
    for base, text_of in TEXTS.items():
        for oid, case_id in conn.execute("SELECT id, case_id FROM email_outbox WHERE kind = %s AND status = 'QUEUED' "
                                         "ORDER BY created_at", (base + sfx,)).fetchall():
            fc = by_id.get(case_id)
            if fc is None or fc.state is not STATE_OF[base] or (base == CONFIRM_BBNT and (fc.confirmed or not all(
                    _pre_bbnt(r) for r in fc.rows))):
                conn.execute("UPDATE email_outbox SET status = 'CANCELLED', last_error = 'STAGE_CHANGED' WHERE id = %s",
                             (oid,))
                continue
            th = threads.get(fc.id)
            to = brand_to(fc)
            if isinstance(to, str) or (live and th is None) or not in_send_window(now) or full():
                continue
            msg = envelope(settings, to, fc.subject(), text_of(fc, deadline(now, cal), settings.signer), thread=th,
                           notes=TestNotes((), f"reply thread {th.id}" if th else ""))
            guard(msg)
            deliver(conn, oid, fc.id, base + sfx, msg, th.id if live and th else None, gmail, live=live, now=now)



# ----------------------------------------------------------------------------------------------- bot ghi Status U
U_DATA, U_CONFIRM = "Brand chưa gửi data", "Đã Confirm số + Chờ brand gửi BBNT"
U_HAS_BBNT, U_DONE_BBNT = "Đã có BBNT + Chờ Admin Check", "Đã done BBNT + Chờ KT xuất HĐ nháp"
#: Thứ tự các giá trị U bot được ghi / được ghi đè. Giá trị khác (HĐ nháp, đã xuất HĐ, Done, lạ) -> không bao giờ ghi.
U_RANK = {normalize_text(v): i for i, v in enumerate(["", U_DATA, U_CONFIRM, U_HAS_BBNT, U_DONE_BBNT])}


@dataclass(frozen=True)
class StatusWrite:
    case_id: uuid.UUID
    row: FbjtRow
    target: str


def status_writes(conn: psycopg.Connection, rows: Sequence[FbjtRow], tab: str) -> list[StatusWrite]:
    """U cần ghi theo việc bot đã làm (chỉ thư THẬT đã gửi, không tính thư test). Chỉ tiến lên; case Giữ / Xong /
    Lỗi không ghi."""
    live = {(brand_key(r.brand), campaign_key(r.campaign)): r for r in rows}
    out: list[StatusWrite] = []
    for cid, confirmed, request, acceptance, bk, ck in conn.execute(
            "SELECT c.id, c.brand_confirmed_at IS NOT NULL, "
            "EXISTS (SELECT 1 FROM email_outbox o WHERE o.case_id = c.id AND o.kind = 'STATUS_REQUEST' "
            "AND o.status = 'SENT'), "
            "EXISTS (SELECT 1 FROM email_outbox o WHERE o.case_id = c.id AND o.kind = 'ACCEPTANCE' "
            "AND o.status = 'SENT'), "
            "sr.brand_key, sr.campaign_key FROM recon_case c JOIN sheet_row sr ON sr.case_id = c.id AND sr.tab = %s "
            "WHERE c.state NOT IN ('ON_HOLD', 'DONE', 'ERROR') ORDER BY c.id", (tab,)).fetchall():
        target = U_DONE_BBNT if confirmed else U_CONFIRM if acceptance else U_DATA if request else None
        r = live.get((bk, ck))
        if target is None or r is None:
            continue
        cur = U_RANK.get(normalize_text(r.status))
        if cur is not None and cur < U_RANK[normalize_text(target)]:
            out.append(StatusWrite(cid, r, target))
    return out
