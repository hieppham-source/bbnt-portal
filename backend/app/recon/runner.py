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
    BbntMail,
    EmailConfigError,
    EmailSettings,
    TestNotes,
    Thread,
    body,
    build_live_reply,
    build_test_message,
    find_loopmail,
    in_send_window,
    recipients,
    render_template,
)
from app.recon.fbjt import FbjtRow, brand_key, campaign_key
from app.recon.rules import CellAction, entity_code
from app.recon.sheet_writer import CellResult, CellWrite
from app.recon.state_machine import InvalidTransition, State, Trigger, transition
from app.recon.worker import Action, CasePlan

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


def send_acceptance_emails(conn: psycopg.Connection, plans: Sequence[CasePlan],
                           build: Callable[[CasePlan, datetime], BbntMail | str], gmail: Any,
                           settings: EmailSettings, *, now: datetime, run_id: uuid.UUID, st: RunStats) -> None:
    """Email nghiệm thu cho case BBNT_DRAFTED / FIGURES_WRITTEN của lần chạy này.

    * Tìm loopmail (TITLE MAIL + email brand) -> lưu ``gmail_thread_id``; 0 / nhiều thread -> ``loopmail_candidates``
      để KAM chọn trên portal. Kết quả Audit Gate lưu ``recon_case.email_reasons`` (không đổi trạng thái case).
    * test: mỗi case ĐÚNG 1 thư về ``RECON_EMAIL_TEST_TO`` (kể cả Audit không đạt — thư ghi lý do).
    * live: chỉ khi Audit đạt; reply vào loopmail; case -> ACCEPTANCE_SENT. ``email_outbox`` UNIQUE theo case + loại:
      không bao giờ gửi 2 lần; thư FAILED không tự gửi lại (xem OPERATIONS_RUNBOOK)."""
    if settings.mode == "off":
        return
    live = settings.mode == "live"
    kind = LIVE_KIND if live else KIND
    template: str | None = None
    if live:
        row = conn.execute("SELECT body_html FROM email_template WHERE code = 'ACCEPTANCE' AND status = 'APPROVED' "
                           "ORDER BY version DESC LIMIT 1").fetchone()
        template = row[0] if row else None
    by_sid = {p.order_sheet_id: p for p in plans if p.action is Action.DRAFT and p.order_sheet_id}
    todo = conn.execute(
        "SELECT c.id, c.order_sheet_id, c.bbnt_issued_at, c.state, c.gmail_thread_id FROM recon_case c "
        "WHERE c.last_run_id = %s AND c.state IN ('BBNT_DRAFTED', 'FIGURES_WRITTEN') AND c.bbnt_issued_at IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM email_outbox o WHERE o.case_id = c.id AND o.kind = %s) "
        "ORDER BY c.updated_at, c.id", (run_id, kind)).fetchall()
    skip = st.email_skipped
    for case_id, sid, issued_at, state, thread_id in todo:
        p = by_sid.get(sid)
        if p is None:
            continue
        if st.emails_sent + st.emails_failed >= settings.max_per_run:
            skip["MAX_PER_RUN"] = skip.get("MAX_PER_RUN", 0) + 1
            continue
        mail = build(p, issued_at)
        if isinstance(mail, str):
            skip[mail] = skip.get(mail, 0) + 1
            conn.execute("UPDATE recon_case SET email_reasons = %s WHERE id = %s", ([mail], case_id))
            continue
        extra: list[str] = []
        threads: list[Thread] = []
        try:
            if not mail.title:
                extra.append("TITLE_MAIL_MISSING")
            elif thread_id:
                threads = [gmail.thread(thread_id)]                 # KAM đã chọn / đã tìm thấy lần trước
            else:
                threads = find_loopmail(gmail, mail.title, mail.brand_to)
        except RuntimeError:
            extra.append("GMAIL_LOOKUP_FAILED")
        if len(threads) == 1 and not thread_id:
            conn.execute("UPDATE recon_case SET gmail_thread_id = %s, loopmail_rfc822_id = %s, "
                         "loopmail_candidates = NULL WHERE id = %s",
                         (threads[0].id, next(iter(threads[0].message_ids), None), case_id))
        elif len(threads) > 1:
            conn.execute("UPDATE recon_case SET loopmail_candidates = %s WHERE id = %s",
                         (Jsonb([t.summary() for t in threads]), case_id))
        html_body = None
        if live and template is not None:
            try:
                html_body = render_template(template, mail)
            except (KeyError, ValueError, IndexError):
                extra.append("TEMPLATE_INVALID")
        gate = evaluate_acceptance(AcceptanceFacts(
            non_op=True, order_links_found=1, order_link_readable=True, contracts_found=1,     # đã kiểm ở plan_cases
            contract_in_portal_master=True, generator_errors=0, figures_match_sheet=state == "FIGURES_WRITTEN",
            brand_emails=len(mail.brand_to), loopmail_threads_found=len(threads), already_sent=False,
            template_configured=not live or template is not None, within_send_window=in_send_window(now),
            on_hold=False)).reasons + tuple(extra)
        conn.execute("UPDATE recon_case SET email_reasons = %s WHERE id = %s", (list(gate), case_id))
        if live and gate:
            for r in gate:
                skip[r] = skip.get(r, 0) + 1
            continue
        if live:
            assert html_body is not None
            msg, to_thread = build_live_reply(mail, threads[0], html_body), threads[0]
        else:
            loop = (f"{len(threads)} thread khớp" if len(threads) != 1 else f"thread {threads[0].id} — "
                    f"{threads[0].subject}")
            msg, to_thread = build_test_message(mail, settings, TestNotes(gate, loop)), None
            if recipients(msg) != [settings.test_to]:          # chốt chặn cuối: thư test không bao giờ tới brand
                raise EmailConfigError("Thư test có người nhận ngoài RECON_EMAIL_TEST_TO")
        _send_one(conn, case_id, kind, msg, to_thread, gmail, mail, now=now, run_id=run_id, st=st)


def _send_one(conn: psycopg.Connection, case_id: uuid.UUID, kind: str, msg: EmailMessage, thread: Thread | None,
              gmail: Any, mail: BbntMail, *, now: datetime, run_id: uuid.UUID, st: RunStats) -> None:
    oid = uuid.uuid4()
    with conn.transaction():
        if conn.execute(
                "INSERT INTO email_outbox (id, case_id, kind, cycle_no, idempotency_key, template_code, "
                "template_version, to_addrs, cc_addrs, subject, body_html, reply_to_thread_id, status, attempts, "
                "created_at) VALUES (%s, %s, %s, 1, %s, %s, 1, %s, %s, %s, %s, %s, 'SENDING', 1, %s) "
                "ON CONFLICT DO NOTHING",
                (oid, case_id, kind, f"{case_id}:{kind}:1", kind,
                 [a.strip() for a in (msg["To"] or "").split(",") if a.strip()],
                 [a.strip() for a in (msg["Cc"] or "").split(",") if a.strip()], msg["Subject"], body(mail),
                 thread.id if thread else None, now)).rowcount == 0:
            return
    try:
        (gmail_id, thread_id), status, error = gmail.send(msg, thread.id if thread else None), "SENT", None
    except Exception as exc:  # noqa: BLE001 — ghi lỗi, không dừng cả lần chạy
        gmail_id, thread_id, status, error = None, None, "FAILED", type(exc).__name__
    with conn.transaction():
        conn.execute("UPDATE email_outbox SET status = %s, last_error = %s WHERE id = %s", (status, error, oid))
        if status == "SENT" and thread is not None:                 # live: thư thuộc loopmail + case đã gửi
            conn.execute("INSERT INTO email_message (gmail_message_id, gmail_thread_id, mailbox, case_id, direction, "
                         "sent_at, outbox_id) VALUES (%s, %s, 'me', %s, 'OUT', %s, %s) ON CONFLICT DO NOTHING",
                         (gmail_id, thread_id or thread.id, case_id, now, oid))
            cur = conn.execute("SELECT id, state, state_version, held_from FROM recon_case WHERE id = %s",
                               (case_id,)).fetchone()
            assert cur is not None
            apply_transition(conn, CaseState(cur[0], State(cur[1]), cur[2], None), transition(
                State(cur[1]), State.ACCEPTANCE_SENT, trigger=Trigger.WORKER, actor=ACTOR, at=now,
                reason="email nghiệm thu"))
        conn.execute(
            "INSERT INTO audit_log (at, actor, action, entity, entity_id, before, after, run_id) "
            "VALUES (%s, %s, 'EMAIL_SEND', 'email_outbox', %s, NULL, %s, %s)",
            (now, ACTOR, str(oid), Jsonb({"kind": kind, "status": status, "gmail_id": gmail_id, "thread_id": thread_id,
                                          "case_id": str(case_id), "error": error}), run_id))
    st.emails_sent += status == "SENT"
    st.emails_failed += status == "FAILED"
