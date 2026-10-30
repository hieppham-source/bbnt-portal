"""
``run-once`` (plan bước 8, D): lưu kết quả lập kế hoạch vào Postgres — idempotent theo
docs/RECON_STATE_PROPOSAL.md — rồi, chỉ khi được bật, ghi ô I/K/M lên FB.JT.

* Case định danh bằng ``order_sheet_id``; lần lập BBNT đầu lưu ``bbnt_number`` + ``bbnt_issued_at`` + số liệu.
  Chạy lại (kể cả tháng khác) dùng lại ``bbnt_issued_at`` -> cùng số BBNT.
* Plan không đổi (``last_plan_hash``) -> không ghi transition / audit mới.
* Số liệu đổi sau khi đã lập BBNT -> NEEDS_REVIEW ``FIGURES_CHANGED_AFTER_ISSUE`` (không tự lập bản sửa).
* Ô cần ghi -> ``sheet_write`` PLANNED (UNIQUE theo case/dòng/cột/giá trị: không bao giờ ghi 2 lần).
  Ô khác số BBNT -> ``cell_review`` mở; người duyệt chọn CHANGE -> lần chạy sau tạo ``sheet_write``.
* Bot chỉ chạm case ở WAITING / NEEDS_REVIEW / BBNT_DRAFTED / FIGURES_WRITTEN; trạng thái khác (Giữ, đã gửi…)
  do người / bước sau quyết định.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from app.recon.audit_gate import AcceptanceFacts, evaluate_acceptance
from app.recon.db import CaseState, NewCase, apply_transition, create_case
from app.recon.emailer import (
    KIND,
    LIVE_KIND,
    STATUS_REMINDER,
    STATUS_REQUEST,
    VN,
    BbntMail,
    EmailConfigError,
    EmailSettings,
    StatusMail,
    TestNotes,
    Thread,
    body,
    build_live_reply,
    build_test_message,
    deadline,
    envelope,
    find_loopmail,
    in_send_window,
    recipients,
    status_reminder_text,
    status_request_text,
)
from app.recon.fbjt import FbjtRow, brand_key, campaign_key
from app.recon.followup import (
    FOLLOW,
    FollowCase,
    load_cases,
    send_approved,
    send_followups,
    status_writes,
    sync_replies,
    track_stages,
)
from app.recon.rules import BusinessCalendar, CellAction, confirm_deadline, entity_code, reminder_due
from app.recon.sheet_writer import CellResult, CellWrite
from app.recon.state_machine import InvalidTransition, State, Trigger, transition
from app.recon.worker import Action, CasePlan, _brand_to

ACTOR = "worker"
LOCK_KEY = 0x5245434F4E  # "RECON" — pg_try_advisory_lock: 1 worker một lúc
BOT_STATES = frozenset({State.WAITING, State.NEEDS_REVIEW, State.BBNT_DRAFTED, State.FIGURES_WRITTEN})
_TARGET = {Action.WAIT: State.WAITING, Action.REVIEW: State.NEEDS_REVIEW, Action.DRAFT: State.BBNT_DRAFTED}


@dataclass
class RunStats:
    cases_new: int = 0
    transitions: int = 0
    unchanged: int = 0
    untouched: int = 0          # case ở trạng thái bot không được đổi
    writes_planned: int = 0
    reviews_opened: int = 0
    written: int = 0
    conflicts: int = 0
    failed: int = 0
    emails_sent: int = 0
    emails_failed: int = 0
    email_skipped: dict[str, int] = field(default_factory=dict)   # mã lý do -> số case
    stage_moves: int = 0          # case đổi giai đoạn theo Status U (HĐ nháp / đã xuất HĐ / Done)
    status_written: int = 0       # ô Status U bot đã ghi (tối đa "Đã done BBNT + Chờ KT xuất HĐ nháp")
    notes: list[str] = field(default_factory=list)


def figures(p: CasePlan) -> dict[str, str]:
    return {f"{c.row_number}:{c.column}": str(c.target) for c in p.cells}


def plan_hash(p: CasePlan, reasons: Sequence[str]) -> str:
    raw = json.dumps({"a": p.action.value, "r": sorted(reasons), "n": p.bbnt_number, "f": figures(p),
                      "c": sorted((c.row_number, c.column, c.action.value) for c in p.cells)}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def stored_issued_at(conn: psycopg.Connection) -> dict[str, datetime]:
    rows = conn.execute("SELECT order_sheet_id, bbnt_issued_at FROM recon_case WHERE bbnt_issued_at IS NOT NULL")
    return {r[0]: r[1] for r in rows.fetchall()}


def _move(conn: psycopg.Connection, case: CaseState, to: State, reason: str, at: datetime) -> CaseState:
    """Chuyển trạng thái; FIGURES_WRITTEN từ WAITING phải đi qua BBNT_DRAFTED. Không hợp lệ -> giữ nguyên."""
    path = [State.BBNT_DRAFTED, to] if to is State.FIGURES_WRITTEN and case.state is State.WAITING else [to]
    for step in path:
        if case.state is step:
            continue
        try:
            t = transition(case.state, step, trigger=Trigger.WORKER, actor=ACTOR, at=at, reason=reason)
        except InvalidTransition:
            return case
        case = apply_transition(conn, case, t)
    return case


def persist(conn: psycopg.Connection, plans: Sequence[CasePlan], rows: Mapping[int, FbjtRow], *, tab: str,
            now: datetime, run_id: uuid.UUID) -> RunStats:
    st = RunStats()
    for p in plans:
        if not p.order_sheet_id or not p.url:
            continue                                   # SKIP / dòng lỗi: chỉ có trong báo cáo
        cur = conn.execute("SELECT id, state, state_version, held_from, bbnt_number, figures, last_plan_hash "
                           "FROM recon_case WHERE order_sheet_id = %s", (p.order_sheet_id,)).fetchone()
        if cur is None:
            first = rows[p.rows[0]]
            create_case(conn, NewCase(p.order_sheet_id, p.url, p.brands, p.campaign, first.camp_end,
                                      entity_code(first.entity_b), first.group or None, first.pic or None),
                        actor=ACTOR, at=now)
            st.cases_new += 1
            cur = conn.execute("SELECT id, state, state_version, held_from, bbnt_number, figures, last_plan_hash "
                               "FROM recon_case WHERE order_sheet_id = %s", (p.order_sheet_id,)).fetchone()
        assert cur is not None
        case = CaseState(cur[0], State(cur[1]), cur[2], State(cur[3]) if cur[3] else None)
        stored_no, stored_fig, last_hash = cur[4], cur[5], cur[6]
        if case.state not in BOT_STATES:
            st.untouched += 1
            continue
        target, reasons = _TARGET[p.action], list(p.reasons)
        if p.action is Action.DRAFT and stored_no and stored_fig is not None and stored_fig != figures(p):
            target, reasons = State.NEEDS_REVIEW, ["FIGURES_CHANGED_AFTER_ISSUE"]
        elif p.action is Action.DRAFT and p.cells and all(c.action is CellAction.NOOP for c in p.cells):
            target = State.FIGURES_WRITTEN                # 3 ô trên FB.JT đã bằng số BBNT
        h = plan_hash(p, reasons)
        with conn.transaction():
            if case.state is not target:
                before = case.state
                case = _move(conn, case, target, ",".join(reasons), now)
                st.transitions += case.state is not before
            elif h == last_hash:
                st.unchanged += 1
            if p.action is Action.DRAFT and stored_no is None and target is not State.NEEDS_REVIEW:
                conn.execute("UPDATE recon_case SET bbnt_number = %s, bbnt_issued_at = %s, figures = %s "
                             "WHERE id = %s", (p.bbnt_number, now, Jsonb(figures(p)), case.id))
            conn.execute("UPDATE recon_case SET review_reasons = %s, last_plan_hash = %s, last_run_id = %s, "
                         "updated_at = %s WHERE id = %s", (reasons, h, run_id, now, case.id))
            row_ids = {n: _upsert_row(conn, rows[n], tab, case.id, now) for n in p.rows}
            if target is State.NEEDS_REVIEW and reasons == ["FIGURES_CHANGED_AFTER_ISSUE"]:
                continue
            for c in p.cells:
                if c.action is CellAction.WRITE and p.action is Action.DRAFT:
                    st.writes_planned += conn.execute(
                        "INSERT INTO sheet_write (case_id, sheet_row_id, column_name, seen_value, target_value, "
                        "status, run_id, created_at) VALUES (%s, %s, %s, %s, %s, 'PLANNED', %s, %s) "
                        "ON CONFLICT DO NOTHING",
                        (case.id, row_ids[c.row_number], c.column, c.current, c.target, run_id, now)).rowcount
                elif c.action is CellAction.REVIEW:
                    st.reviews_opened += conn.execute(
                        "INSERT INTO cell_review (case_id, sheet_row_id, column_name, current_value, bbnt_value, "
                        "created_at) VALUES (%s, %s, %s, %s, %s, %s) "
                        "ON CONFLICT (sheet_row_id, column_name) WHERE decision IS NULL DO NOTHING",
                        (case.id, row_ids[c.row_number], c.column, c.current, c.target, now)).rowcount
    return st


def _upsert_row(conn: psycopg.Connection, r: FbjtRow, tab: str, case_id: uuid.UUID, now: datetime) -> int:
    row = conn.execute(
        "INSERT INTO sheet_row (tab, brand_key, campaign_key, case_id, last_row_number, last_seen_at) "
        "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (tab, brand_key, campaign_key) DO UPDATE SET "
        "case_id = EXCLUDED.case_id, last_row_number = EXCLUDED.last_row_number, last_seen_at = EXCLUDED.last_seen_at "
        "RETURNING id", (tab, brand_key(r.brand), campaign_key(r.campaign), case_id, r.row_number, now)).fetchone()
    assert row is not None
    return int(row[0])


Writer = Callable[[Sequence[CellWrite]], list[CellResult]]


def write_back(conn: psycopg.Connection, write: Writer, letters: Mapping[str, str], *, now: datetime,
               run_id: uuid.UUID, st: RunStats) -> None:
    """Thực hiện ``sheet_write`` PLANNED (kể cả ô KAM đã duyệt CHANGE). Mỗi ô 1 dòng ``audit_log``."""
    with conn.transaction():
        conn.execute(
            "INSERT INTO sheet_write (case_id, sheet_row_id, column_name, seen_value, target_value, status, "
            "approved_by, run_id, created_at) SELECT case_id, sheet_row_id, column_name, current_value, bbnt_value, "
            "'PLANNED', decided_by, %s, %s FROM cell_review WHERE decision = 'CHANGE' ON CONFLICT DO NOTHING",
            (run_id, now))
    todo = conn.execute(
        "SELECT sw.id, sr.last_row_number, sw.column_name, sw.seen_value, sw.target_value FROM sheet_write sw "
        "JOIN sheet_row sr ON sr.id = sw.sheet_row_id JOIN recon_case c ON c.id = sw.case_id "
        "WHERE sw.status = 'PLANNED' AND c.state NOT IN ('ON_HOLD', 'DONE', 'ERROR') "
        "AND c.last_run_id = %s ORDER BY sw.id", (run_id,)).fetchall()   # chỉ case lần chạy này còn trong phạm vi
    if not todo:
        return
    writes = [CellWrite(r[1], letters[r[2]], r[3], r[4]) for r in todo]
    for (sw_id, *_), res in zip(todo, write(writes), strict=True):
        with conn.transaction():
            conn.execute("UPDATE sheet_write SET status = %s, run_id = %s, updated_at = %s WHERE id = %s",
                         (res.status, run_id, now, sw_id))
            conn.execute(
                "INSERT INTO audit_log (at, actor, action, entity, entity_id, before, after, run_id) "
                "VALUES (%s, %s, 'SHEET_WRITE', 'fbjt_cell', %s, %s, %s, %s)",
                (now, ACTOR, f"{res.write.column}{res.write.row}", Jsonb({"value": _jsonable(res.before)}),
                 Jsonb({"value": _jsonable(res.after), "target": str(res.write.target), "status": res.status,
                        "note": res.note, "sheet_write_id": sw_id}), run_id))
        st.written += res.status == "VERIFIED"
        st.conflicts += res.status == "CONFLICT"
        st.failed += res.status == "FAILED"


def _jsonable(v: Any) -> Any:
    return v if v is None or isinstance(v, (int, float, str, bool)) else str(v)


def _calendar(conn: psycopg.Connection) -> BusinessCalendar:
    return BusinessCalendar(frozenset(r[0] for r in conn.execute(
        "SELECT day FROM holiday WHERE status = 'APPROVED'").fetchall()))           # ngày lễ đã duyệt -> hạn brand


def _bump(st: RunStats, *codes: str) -> None:
    for c in codes:
        st.email_skipped[c] = st.email_skipped.get(c, 0) + 1


def _loopmail(conn: psycopg.Connection, case_id: uuid.UUID, thread_id: str | None, gmail: Any, title: str,
              brand_to: Sequence[str]) -> tuple[Thread | None, list[str]]:
    """(loopmail để reply, lý do chặn). ``None`` + không lý do = chưa có loopmail -> bot tạo luồng mới (Q15).
    Nhiều thread khớp -> ``loopmail_candidates``, KAM chọn trên portal."""
    try:
        if thread_id:
            return gmail.thread(thread_id), []
        threads = find_loopmail(gmail, title, brand_to)
    except RuntimeError:
        return None, ["GMAIL_LOOKUP_FAILED"]
    if len(threads) > 1:
        conn.execute("UPDATE recon_case SET loopmail_candidates = %s WHERE id = %s",
                     (Jsonb([t.summary() for t in threads]), case_id))
        return None, ["LOOPMAIL_NOT_UNIQUE"]
    if threads:
        conn.execute("UPDATE recon_case SET gmail_thread_id = %s, loopmail_rfc822_id = %s, loopmail_candidates = NULL "
                     "WHERE id = %s", (threads[0].id, next(iter(threads[0].message_ids), None), case_id))
        return threads[0], []
    return None, []


def _loop_note(thread: Thread | None, reasons: Sequence[str]) -> str:
    if thread is not None:
        return f"reply thread {thread.id} — {thread.subject}"
    return "chưa xác định" if reasons else "chưa có — bot tạo luồng mới"


def _outbox(conn: psycopg.Connection, case_id: uuid.UUID, kind: str, cycle: int, status: str, msg: EmailMessage,
            text: str, thread_id: str | None, now: datetime) -> uuid.UUID | None:
    """Ghi ``email_outbox`` (UNIQUE case + loại + lần). Đã có -> ``None`` (không gửi trùng)."""
    oid = uuid.uuid4()
    n = conn.execute(
        "INSERT INTO email_outbox (id, case_id, kind, cycle_no, idempotency_key, template_code, template_version, "
        "to_addrs, cc_addrs, subject, body_html, reply_to_thread_id, status, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, 1, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
        (oid, case_id, kind, cycle, f"{case_id}:{kind}:{cycle}", kind,
         [a.strip() for a in (msg["To"] or "").split(",") if a.strip()],
         [a.strip() for a in (msg["Cc"] or "").split(",") if a.strip()], msg["Subject"], text, thread_id, status,
         now)).rowcount
    return oid if n else None


def _deliver(conn: psycopg.Connection, oid: uuid.UUID, case_id: uuid.UUID, kind: str, msg: EmailMessage,
             thread_id: str | None, gmail: Any, *, live: bool, now: datetime, run_id: uuid.UUID, st: RunStats,
             on_sent: Callable[[str], None] | None = None) -> None:
    """Gửi 1 thư đã có dòng outbox. Thư FAILED không tự gửi lại. ``on_sent(thread_id)`` chạy cùng transaction."""
    conn.execute("UPDATE email_outbox SET status = 'SENDING', attempts = attempts + 1 WHERE id = %s", (oid,))
    try:
        (gmail_id, sent_thread), status, error = gmail.send(msg, thread_id), "SENT", None
    except Exception as exc:  # noqa: BLE001 — ghi lỗi, không dừng cả lần chạy
        gmail_id, sent_thread, status, error = None, None, "FAILED", type(exc).__name__
    with conn.transaction():
        conn.execute("UPDATE email_outbox SET status = %s, last_error = %s WHERE id = %s", (status, error, oid))
        if status == "SENT" and live:
            conn.execute("INSERT INTO email_message (gmail_message_id, gmail_thread_id, rfc822_message_id, mailbox, "
                         "case_id, direction, sent_at, outbox_id) VALUES (%s, %s, %s, 'me', %s, 'OUT', %s, %s) "
                         "ON CONFLICT DO NOTHING",
                         (gmail_id, sent_thread or thread_id, msg["Message-ID"], case_id, now, oid))
            if on_sent is not None:
                on_sent(sent_thread or thread_id or "")
        conn.execute(
            "INSERT INTO audit_log (at, actor, action, entity, entity_id, before, after, run_id) "
            "VALUES (%s, %s, 'EMAIL_SEND', 'email_outbox', %s, NULL, %s, %s)",
            (now, ACTOR, str(oid), Jsonb({"kind": kind, "status": status, "gmail_id": gmail_id,
                                          "thread_id": sent_thread, "case_id": str(case_id), "error": error}), run_id))
    st.emails_sent += status == "SENT"
    st.emails_failed += status == "FAILED"


def _guard_test(msg: EmailMessage, settings: EmailSettings) -> None:
    if settings.mode == "test" and recipients(msg) != [settings.test_to]:   # chốt chặn cuối: thư test không tới brand
        raise EmailConfigError("Thư test có người nhận ngoài RECON_EMAIL_TEST_TO")


def _full(st: RunStats, settings: EmailSettings) -> bool:
    if st.emails_sent + st.emails_failed >= settings.max_per_run:
        _bump(st, "MAX_PER_RUN")
        return True
    return False


def _store_new_thread(conn: psycopg.Connection, case_id: uuid.UUID, msg: EmailMessage) -> Callable[[str], None]:
    """Thư mở luồng mới (Q15): lưu thread + Message-ID làm loopmail của case."""
    def save(thread_id: str) -> None:
        conn.execute("UPDATE recon_case SET gmail_thread_id = COALESCE(gmail_thread_id, %s), "
                     "loopmail_rfc822_id = COALESCE(loopmail_rfc822_id, %s) WHERE id = %s",
                     (thread_id, msg["Message-ID"], case_id))
    return save


def send_status_requests(conn: psycopg.Connection, plans: Sequence[CasePlan],
                         build: Callable[[CasePlan], StatusMail | str], gmail: Any, settings: EmailSettings, *,
                         now: datetime, run_id: uuid.UUID, st: RunStats) -> None:
    """EMAIL 1 + nhắc lại cho case WAITING ``STATUS_INCOMPLETE`` (đã qua G + 25, file order còn dòng chưa có status).

    * Email 1 **tự gửi** 1 lần: reply loopmail tay có sẵn (tìm theo TITLE MAIL) hoặc tạo luồng mới (Q15).
    * Quá hạn (Email 1 + 3 ngày làm việc), sau đó 3 ngày/lần: tạo thư nhắc **chờ duyệt** (PENDING_REVIEW). KAM duyệt
      trên portal -> QUEUED -> lần chạy sau gửi. Brand đã điền đủ -> thư nhắc còn chờ bị huỷ."""
    if settings.mode == "off":
        return
    live = settings.mode == "live"
    req_kind, rem_kind = (STATUS_REQUEST, STATUS_REMINDER) if live else (STATUS_REQUEST + "_TEST",
                                                                         STATUS_REMINDER + "_TEST")
    cal, today = _calendar(conn), now.astimezone(VN).date()
    by_sid = {p.order_sheet_id: p for p in plans if "STATUS_INCOMPLETE" in p.reasons and p.order_sheet_id}
    rows = conn.execute(
        "SELECT id, order_sheet_id, gmail_thread_id FROM recon_case WHERE last_run_id = %s AND state = 'WAITING' "
        "AND 'STATUS_INCOMPLETE' = ANY(review_reasons) ORDER BY updated_at, id", (run_id,)).fetchall()
    mails: dict[uuid.UUID, tuple[StatusMail, str | None]] = {}
    for case_id, sid, thread_id in rows:
        p = by_sid.get(sid)
        if p is None:
            continue
        mail = build(p)
        if isinstance(mail, str):
            _bump(st, mail)
            conn.execute("UPDATE recon_case SET email_reasons = %s WHERE id = %s", ([mail], case_id))
            continue
        mails[case_id] = (mail, thread_id)
        sent = conn.execute("SELECT kind, status, cycle_no, created_at FROM email_outbox WHERE case_id = %s "
                            "AND kind IN (%s, %s)", (case_id, req_kind, rem_kind)).fetchall()
        request = next((r for r in sent if r[0] == req_kind), None)
        if request is None:                                             # ---- Email 1
            if _full(st, settings):
                continue
            thread, reasons = _loopmail(conn, case_id, thread_id, gmail, mail.title or mail.subject(), mail.brand_to)
            reasons += [r for ok, r in ((not live or settings.signer, "SIGNER_NOT_SET"),
                                        (in_send_window(now), "OUTSIDE_SEND_WINDOW")) if not ok]
            conn.execute("UPDATE recon_case SET email_reasons = %s WHERE id = %s", (reasons, case_id))
            if live and reasons:
                _bump(st, *reasons)
                continue
            msg = envelope(settings, mail.brand_to, mail.subject(),
                           status_request_text(mail, deadline(now, cal), settings.signer),
                           thread=thread if live else None,
                           notes=TestNotes(tuple(reasons), _loop_note(thread, reasons)))
            _guard_test(msg, settings)
            oid = _outbox(conn, case_id, req_kind, 1, "QUEUED", msg, status_request_text(mail, "{{DEADLINE}}", ""),
                          thread.id if live and thread else None, now)
            if oid is not None:
                _deliver(conn, oid, case_id, req_kind, msg, thread.id if live and thread else None, gmail, live=live,
                         now=now, run_id=run_id, st=st,
                         on_sent=None if thread is not None else _store_new_thread(conn, case_id, msg))
            continue
        if request[1] != "SENT" or any(r[1] in ("PENDING_REVIEW", "QUEUED") for r in sent if r[0] == rem_kind):
            continue
        last = max(r[3] for r in sent if r[1] == "SENT").astimezone(VN).date()
        reminded = any(r[0] == rem_kind and r[1] == "SENT" for r in sent)
        due = reminder_due(last, today) if reminded else today > confirm_deadline(last, cal)
        if due:                                                         # ---- nhắc lại: chờ KAM duyệt
            cycle = 1 + max((r[2] for r in sent if r[0] == rem_kind), default=0)
            preview = envelope(EmailSettings("live", signer=settings.signer), mail.brand_to, mail.subject(),
                               status_reminder_text(mail, "{{DEADLINE}}", settings.signer))
            _outbox(conn, case_id, rem_kind, cycle, "PENDING_REVIEW", preview,
                    status_reminder_text(mail, "{{DEADLINE}}", settings.signer), thread_id, now)
    # ---- gửi thư nhắc đã duyệt; brand đã điền đủ -> huỷ
    for oid, case_id in conn.execute("SELECT id, case_id FROM email_outbox WHERE kind = %s AND status = 'QUEUED' "
                                     "ORDER BY created_at", (rem_kind,)).fetchall():
        still = conn.execute("SELECT 1 FROM recon_case WHERE id = %s AND state = 'WAITING' "
                             "AND 'STATUS_INCOMPLETE' = ANY(review_reasons)", (case_id,)).fetchone()
        if still is None:
            conn.execute("UPDATE email_outbox SET status = 'CANCELLED', last_error = 'STATUS_COMPLETE' WHERE id = %s",
                         (oid,))
            continue
        if case_id not in mails or not in_send_window(now) or _full(st, settings):
            continue
        mail, thread_id = mails[case_id]
        thread = None
        if live:
            try:
                thread = gmail.thread(thread_id) if thread_id else None
            except RuntimeError:
                thread = None
            if thread is None:                                          # nhắc lại phải nằm trong loopmail
                conn.execute("UPDATE recon_case SET email_reasons = '{LOOPMAIL_UNKNOWN}' WHERE id = %s", (case_id,))
                continue
        msg = envelope(settings, mail.brand_to, mail.subject(),
                       status_reminder_text(mail, deadline(now, cal), settings.signer), thread=thread,
                       notes=TestNotes((), _loop_note(thread, ())))
        _guard_test(msg, settings)
        _deliver(conn, oid, case_id, rem_kind, msg, thread.id if thread else None, gmail, live=live, now=now,
                 run_id=run_id, st=st)


def send_acceptance_emails(conn: psycopg.Connection, plans: Sequence[CasePlan],
                           build: Callable[[CasePlan, datetime], BbntMail | str], gmail: Any,
                           settings: EmailSettings, *, now: datetime, run_id: uuid.UUID, st: RunStats) -> None:
    """EMAIL 2 (confirm số + BBNT) cho case BBNT_DRAFTED / FIGURES_WRITTEN của lần chạy này.

    * Loopmail: thread đã lưu (Email 1 / KAM chọn) -> reply; chưa có -> tìm theo TITLE MAIL; không có -> luồng mới.
      Lý do chưa gửi lưu ``recon_case.email_reasons`` (không đổi trạng thái case).
    * test: mỗi case ĐÚNG 1 thư về ``RECON_EMAIL_TEST_TO`` (kể cả chưa đủ điều kiện — thư ghi lý do).
    * live: chỉ khi Audit Gate đạt; case -> ACCEPTANCE_SENT; không bao giờ gửi 2 lần."""
    if settings.mode == "off":
        return
    live = settings.mode == "live"
    kind = LIVE_KIND if live else KIND
    cal = _calendar(conn)
    by_sid = {p.order_sheet_id: p for p in plans if p.action is Action.DRAFT and p.order_sheet_id}
    todo = conn.execute(
        "SELECT c.id, c.order_sheet_id, c.bbnt_issued_at, c.state, c.gmail_thread_id FROM recon_case c "
        "WHERE c.last_run_id = %s AND c.state IN ('BBNT_DRAFTED', 'FIGURES_WRITTEN') AND c.bbnt_issued_at IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM email_outbox o WHERE o.case_id = c.id AND o.kind = %s) "
        "ORDER BY c.updated_at, c.id", (run_id, kind)).fetchall()
    for case_id, sid, issued_at, state, thread_id in todo:
        p = by_sid.get(sid)
        if p is None or _full(st, settings):
            continue
        mail = build(p, issued_at)
        if isinstance(mail, str):
            _bump(st, mail)
            conn.execute("UPDATE recon_case SET email_reasons = %s WHERE id = %s", ([mail], case_id))
            continue
        thread, extra = _loopmail(conn, case_id, thread_id, gmail, mail.title or mail.subject(), mail.brand_to)
        if live and not settings.signer:
            extra.append("SIGNER_NOT_SET")                     # {{NGUOI_GUI}} của mẫu thư
        gate = evaluate_acceptance(AcceptanceFacts(
            non_op=True, order_links_found=1, order_link_readable=True, contracts_found=1,     # đã kiểm ở plan_cases
            contract_in_portal_master=True, generator_errors=0, figures_match_sheet=state == "FIGURES_WRITTEN",
            brand_emails=len(mail.brand_to), loopmail_threads_found=1, already_sent=False,     # loopmail: ``extra``
            template_configured=True, within_send_window=in_send_window(now),
            on_hold=False)).reasons + tuple(extra)
        conn.execute("UPDATE recon_case SET email_reasons = %s WHERE id = %s", (list(gate), case_id))
        if live and gate:
            _bump(st, *gate)
            continue
        if live:
            msg = build_live_reply(mail, thread, now=now, signer=settings.signer, cal=cal)
        else:
            msg = build_test_message(mail, settings, TestNotes(gate, _loop_note(thread, gate)), now=now, cal=cal)
            _guard_test(msg, settings)
        reply_to = thread.id if live and thread else None
        oid = _outbox(conn, case_id, kind, 1, "QUEUED", msg, body(mail), reply_to, now)
        if oid is None:
            continue

        def sent(new_thread: str, case_id: uuid.UUID = case_id, msg: EmailMessage = msg,
                 has_thread: bool = thread is not None) -> None:
            if not has_thread:
                _store_new_thread(conn, case_id, msg)(new_thread)
            cur = conn.execute("SELECT id, state, state_version FROM recon_case WHERE id = %s", (case_id,)).fetchone()
            assert cur is not None
            apply_transition(conn, CaseState(cur[0], State(cur[1]), cur[2], None), transition(
                State(cur[1]), State.ACCEPTANCE_SENT, trigger=Trigger.WORKER, actor=ACTOR, at=now,
                reason="email nghiệm thu"))
        _deliver(conn, oid, case_id, kind, msg, reply_to, gmail, live=live, now=now, run_id=run_id, st=st,
                 on_sent=sent)


def run_followups(conn: psycopg.Connection, rows: Sequence[FbjtRow], done: Sequence[FbjtRow], tab: str,
                  brand_emails: Mapping[str, str], gmail: Any, settings: EmailSettings, *, now: datetime,
                  run_id: uuid.UUID, st: RunStats) -> None:
    """Sau EMAIL 2: giai đoạn theo Status U (luôn chạy), đọc thư brand trả lời + nhắc 3A / 3B / 4 (khi bật email)."""
    cases = load_cases(conn, rows, done, tab)
    st.stage_moves += track_stages(conn, cases, now=now)
    if settings.mode == "off":
        return
    cal = _calendar(conn)
    threads = sync_replies(conn, [c for c in cases if c.state is not State.DONE], gmail)

    def brand_to(fc: FollowCase) -> list[str] | str:
        return _brand_to(fc.brands, brand_emails)

    def deliver(conn: psycopg.Connection, oid: uuid.UUID, case_id: uuid.UUID, kind: str, msg: EmailMessage,
                thread_id: str | None, gmail: Any, *, live: bool, now: datetime) -> None:
        _deliver(conn, oid, case_id, kind, msg, thread_id, gmail, live=live, now=now, run_id=run_id, st=st)

    active = [c for c in cases if c.state in FOLLOW]

    def guard(m: EmailMessage) -> None:
        _guard_test(m, settings)

    def full() -> bool:
        return _full(st, settings)

    send_approved(conn, active, threads, brand_to, gmail, settings, deliver, guard, full, now=now, cal=cal)
    send_followups(conn, active, threads, brand_to, gmail, settings, _outbox, deliver, guard, full,
                   lambda *c: _bump(st, *c), now=now, cal=cal)


def write_status(conn: psycopg.Connection, rows: Sequence[FbjtRow], write: Writer, letter: str, tab: str, *,
                 now: datetime, run_id: uuid.UUID, st: RunStats) -> None:
    """Ghi Status U theo việc bot đã làm (xem ``followup.status_writes``). Đọc lại ô trước khi ghi: người vừa đổi U
    -> CONFLICT, không ghi. Mỗi ô 1 dòng ``audit_log``."""
    todo = status_writes(conn, rows, tab)
    if not todo:
        return
    for w, res in zip(todo, write([CellWrite(w.row.row_number, letter, w.row.status, w.target) for w in todo]),
                      strict=True):
        conn.execute(
            "INSERT INTO audit_log (at, actor, action, entity, entity_id, before, after, run_id) "
            "VALUES (%s, %s, 'SHEET_WRITE', 'fbjt_cell', %s, %s, %s, %s)",
            (now, ACTOR, f"{letter}{w.row.row_number}", Jsonb({"value": _jsonable(res.before)}),
             Jsonb({"value": _jsonable(res.after), "target": w.target, "status": res.status, "note": res.note,
                    "case_id": str(w.case_id)}), run_id))
        st.status_written += res.status == "VERIFIED"
        st.conflicts += res.status == "CONFLICT"
        st.failed += res.status == "FAILED"
