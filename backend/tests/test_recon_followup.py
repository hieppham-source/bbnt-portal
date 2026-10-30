"""Sau email nghiệm thu: giai đoạn theo Status U, thư brand trả lời, nhắc 3A / 3B (chờ duyệt), 4 (tự gửi)."""
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from app.recon.emailer import EmailSettings, Message, Thread, message_from_headers
from app.recon.fbjt import parse_rows
from app.recon.followup import invoices_due, load_cases, payment_text
from app.recon.rules import BusinessCalendar
from app.recon.runner import RunStats, run_followups

from . import test_recon_runner as tr
from . import test_recon_worker as tw
from .test_recon_email import LIVE, FakeGmail
from .test_recon_migrations import pytestmark  # noqa: F401  (pytestmark: cùng điều kiện skip)
from .test_recon_runner import NOW, _email, _figures_written, one
from .test_recon_worker import ORG, row, sheet

run, conn = tw.run, tr.conn
TAB = "FB-2026"
DRAFT_U, ISSUED_U = "Đã xuất hóa đơn nháp + Chờ Brand confirm", "Đã xuất hóa đơn + Chờ tiền"


def at(d, h=2):
    return datetime(2026, 11, d, h, 0, tzinfo=UTC) if d < 28 else datetime(2026, 10, d, h, 0, tzinfo=UTC)


def sent_case(conn, run, gmail):
    plans, st, run_id = _figures_written(conn, run)
    _email(conn, plans, run_id, st, gmail, LIVE)                       # T4 28/10: EMAIL 2 -> ACCEPTANCE_SENT
    assert one(conn, "SELECT state FROM recon_case") == ("ACCEPTANCE_SENT",)
    return {c.column: c.target for c in plans[0].cells}


def follow(conn, gmail, *, status="", note="", k=None, now=NOW, env=LIVE):
    from .test_recon_runner import TAB as RTAB
    r = row(*ORG, status=status, k=k)
    r[19] = note
    out = parse_rows(sheet(r))
    run_id = uuid.uuid4()
    conn.execute("INSERT INTO workflow_run (id, mode) VALUES (%s, 'RUN_ONCE')", (run_id,))
    st = RunStats()
    run_followups(conn, out.rows, out.done, RTAB, {"organica": "kt@brand.vn"}, gmail, EmailSettings.from_env(env),
                  now=now, run_id=run_id, st=st)
    return st


def test_stage_follows_status_u_until_done(conn, run):
    gmail = FakeGmail()
    sent_case(conn, run, gmail)
    assert follow(conn, gmail, status=DRAFT_U).stage_moves == 1
    assert one(conn, "SELECT state FROM recon_case") == ("AWAITING_INVOICE_CONFIRM",)
    follow(conn, gmail, status=ISSUED_U)
    assert one(conn, "SELECT state FROM recon_case") == ("AWAITING_PAYMENT",)
    follow(conn, gmail, status="Done trả tiền")
    assert one(conn, "SELECT state FROM recon_case") == ("DONE",)
    assert one(conn, "SELECT trigger FROM state_transition ORDER BY id DESC LIMIT 1") == ("SHEET",)


def test_confirm_bbnt_reminder_waits_3_business_days_and_needs_approval(conn, run):
    gmail = FakeGmail()
    sent_case(conn, run, gmail)
    follow(conn, gmail, now=at(2))                                    # hạn 02/11 chưa qua
    assert one(conn, "SELECT count(*) FROM email_outbox WHERE kind = 'CONFIRM_BBNT_REMINDER'")[0] == 0
    follow(conn, gmail, now=at(3))
    assert one(conn, "SELECT status, reply_to_thread_id FROM email_outbox WHERE kind = 'CONFIRM_BBNT_REMINDER'") == (
        "PENDING_REVIEW", "t1")
    n = len(gmail.sent)
    follow(conn, gmail, now=at(3))
    assert len(gmail.sent) == n                                       # chưa duyệt -> không gửi
    conn.execute("UPDATE email_outbox SET status = 'QUEUED' WHERE kind = 'CONFIRM_BBNT_REMINDER'")
    follow(conn, gmail, now=at(3))
    msg, thread = gmail.sent[-1]
    assert thread == "t1" and "biên bản nghiệm thu campaign BDAY 05/26" in msg.get_body(("plain",)).get_content()
    assert "trước ngày 06/11/2026" in msg.get_body(("plain",)).get_content()


def test_queued_confirm_reminder_cancelled_when_stage_moves(conn, run):
    gmail = FakeGmail()
    sent_case(conn, run, gmail)
    follow(conn, gmail, now=at(3))
    conn.execute("UPDATE email_outbox SET status = 'QUEUED' WHERE kind = 'CONFIRM_BBNT_REMINDER'")
    follow(conn, gmail, status="Đã có BBNT + Chờ Admin Check", now=at(3))
    assert one(conn, "SELECT status FROM email_outbox WHERE kind = 'CONFIRM_BBNT_REMINDER'") == ("CANCELLED",)


def test_invoice_draft_reminder_queued_after_kt_sends_draft(conn, run):
    gmail = FakeGmail()
    sent_case(conn, run, gmail)
    follow(conn, gmail, status=DRAFT_U, now=at(28))                   # thấy HĐ nháp T4 28/10
    follow(conn, gmail, status=DRAFT_U, now=at(3))
    assert one(conn, "SELECT status FROM email_outbox WHERE kind = 'INVOICE_DRAFT_REMINDER'") == ("PENDING_REVIEW",)


def test_payment_reminder_auto_with_penalty_then_stops_when_brand_replies(conn, run):
    gmail = FakeGmail()
    t = sent_case(conn, run, gmail)
    note, k = "HĐ: 1358 (08/10/2026)", t["commission"]               # hạn = 08/10 + 10 ngày làm việc = 22/10
    follow(conn, gmail, status=ISSUED_U, note=note, k=k, now=at(28))
    msg, thread = gmail.sent[-1]
    text = msg.get_body(("plain",)).get_content()
    assert thread == "t1" and "đã quá hạn 6 ngày (hạn 22/10/2026)" in text and "Phạt chậm thanh toán ước tính" in text
    assert "chuyển khoản" not in text.split("Phạt")[0] and "Thông tin chuyển khoản" not in text
    n = len(gmail.sent)
    follow(conn, gmail, status=ISSUED_U, note=note, k=k, now=at(30))
    assert len(gmail.sent) == n                                       # chưa đủ 3 ngày
    gmail.threads["t1"] = Thread("t1", "RE: BBNT BDAY T5", ["<a@x>", "<b@x>"], frozenset({"kt@brand.vn"}), "", (
        Message("in1", "<r@brand>", "kt@brand.vn", int(at(29, 5).timestamp() * 1000), "Re: đã chuyển khoản"),))
    follow(conn, gmail, status=ISSUED_U, note=note, k=k, now=at(2))      # T2 02/11
    assert len(gmail.sent) == n and one(conn, "SELECT email_reasons FROM recon_case") == (["BRAND_REPLIED"],)
    assert one(conn, "SELECT direction, from_addr, classification FROM email_message WHERE gmail_message_id = 'in1'"
               ) == ("IN", "kt@brand.vn", "UNKNOWN")


def test_payment_text_4a_on_due_date_and_invoice_grouping(conn, run):
    gmail = FakeGmail()
    sent_case(conn, run, gmail)
    r1, r2 = row(*ORG, status=ISSUED_U, k=100), row(*ORG, status=ISSUED_U, k=50)
    r1[19] = r2[19] = "HD: 7 (08/10/2026)"
    out = parse_rows(sheet(r1, r2))
    [fc] = load_cases(conn, out.rows[:1], [], "FB-2026")
    fc.rows = out.rows
    [inv] = invoices_due(fc, BusinessCalendar())
    assert (inv.number, inv.amount, inv.due.isoformat()) == ("7", Decimal(150), "2026-10-22")
    text = payment_text(fc, [inv], inv.due, "Hiệp")
    assert "đã đến hạn" in text and "Số tiền cần thanh toán: 150 VNĐ" in text and "Phạt" not in text


def test_auto_reply_and_bounce_headers():
    auto = message_from_headers("m", 0, {"from": "Kt <kt@brand.vn>", "auto-submitted": "auto-replied"})
    ooo = message_from_headers("m", 0, {"from": "kt@brand.vn", "subject": "Automatic reply: BBNT"})
    bounce = message_from_headers("m", 0, {"from": "MAILER-DAEMON@googlemail.com"})
    human = message_from_headers("m", 0, {"from": "kt@brand.vn", "auto-submitted": "no", "subject": "Re: BBNT"})
    assert (auto.auto, ooo.auto, bounce.bounce, human.auto, human.bounce) == (True, True, True, False, False)
    assert message_from_headers("m", 0, {"from": "fb@hannaholala.com"}).internal


# ----------------------------------------------------------------------------------------------- bot ghi Status U
def _u_plan(conn, status):
    from app.recon.followup import status_writes
    return [(w.row.status, w.target) for w in status_writes(conn, parse_rows(sheet(row(*ORG, status=status))).rows,
                                                            "FB-2026")]


def test_status_u_after_email1(conn, run):
    from app.recon.followup import U_DATA
    gmail = FakeGmail()
    from .test_recon_runner import _status
    _status(conn, run, gmail, LIVE)                                   # Email 1 thật
    assert _u_plan(conn, "") == [("", U_DATA)]
    assert _u_plan(conn, U_DATA) == []                                # đã đúng -> không ghi lại


def test_status_u_after_email2_and_brand_confirm_stops_at_done_bbnt(conn, run):
    from app.recon.followup import U_CONFIRM, U_DATA, U_DONE_BBNT
    sent_case(conn, run, FakeGmail())                                 # Email 2 thật -> ACCEPTANCE_SENT
    assert _u_plan(conn, U_DATA) == [(U_DATA, U_CONFIRM)]
    conn.execute("UPDATE recon_case SET brand_confirmed_at = now(), brand_confirmed_by = 'kam@hannaholala.com'")
    assert _u_plan(conn, U_CONFIRM) == [(U_CONFIRM, U_DONE_BBNT)]
    assert _u_plan(conn, "Đã có BBNT + Chờ Admin Check") == [("Đã có BBNT + Chờ Admin Check", U_DONE_BBNT)]
    for later in (U_DONE_BBNT, DRAFT_U, ISSUED_U, "status lạ"):
        assert _u_plan(conn, later) == []                             # không ghi lùi / vượt quyền kế toán
    conn.execute("UPDATE recon_case SET state = 'ON_HOLD', held_from = 'ACCEPTANCE_SENT'")
    assert _u_plan(conn, U_CONFIRM) == []


def test_test_mode_emails_never_change_status_u(conn, run):
    from .test_recon_email import TEST
    from .test_recon_runner import _status
    _status(conn, run, FakeGmail(), TEST)
    assert _u_plan(conn, "") == []


def test_write_status_audits_and_counts(conn, run):
    from app.recon.followup import U_CONFIRM
    from app.recon.sheet_writer import CellResult
    gmail = FakeGmail()
    sent_case(conn, run, gmail)
    rows = parse_rows(sheet(row(*ORG, status=""))).rows
    got = []

    def writer(ws):
        got.extend(ws)
        return [CellResult(w, "VERIFIED", w.seen, w.target) for w in ws]
    st = RunStats()
    from app.recon.runner import write_status
    write_status(conn, rows, writer, "U", "FB-2026", now=NOW, run_id=uuid.uuid4(), st=st)
    assert [(w.row, w.column, w.seen, w.target) for w in got] == [(7, "U", "", U_CONFIRM)] and st.status_written == 1
    assert one(conn, "SELECT entity_id, after->>'target' FROM audit_log WHERE action = 'SHEET_WRITE'") == (
        "U7", U_CONFIRM)


def test_confirmed_case_gets_no_bbnt_reminder(conn, run):
    gmail = FakeGmail()
    sent_case(conn, run, gmail)
    conn.execute("UPDATE recon_case SET brand_confirmed_at = now()")
    follow(conn, gmail, now=at(3))
    assert one(conn, "SELECT count(*) FROM email_outbox WHERE kind = 'CONFIRM_BBNT_REMINDER'")[0] == 0
