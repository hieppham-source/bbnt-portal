"""run-once: lưu plan vào Postgres (idempotent) + ghi I/K/M có kiểm soát. Postgres thật (``RECON_TEST_DATABASE_URL``);
FB.JT giả (writer giả / session giả) — không gọi Google."""
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import psycopg
import pytest

from app.recon.fbjt import parse_rows
from app.recon.runner import persist, stored_issued_at, write_back
from app.recon.sheet_writer import CellResult, CellWrite
from app.recon.worker import Action

from . import test_recon_worker as tw
from .test_recon_migrations import URL, psql, pytestmark, run_file  # noqa: F401  (pytestmark: cùng điều kiện skip)
from .test_recon_worker import NOW, ORG, row, sheet

run = tw.run   # dùng lại fixture của worker

TAB = "FB-2026"
NOV = datetime(2026, 11, 20, 2, 0, tzinfo=UTC)
K = Decimal(816480)


@pytest.fixture()
def conn() -> Iterator[psycopg.Connection]:
    assert psql("DROP SCHEMA public CASCADE; CREATE SCHEMA public;").returncode == 0
    run_file("0001_recon_init.up.sql")
    run_file("0002_recon_idempotency.up.sql")
    run_file("0003_recon_email.up.sql")
    with psycopg.connect(URL or "", autocommit=True) as c:
        yield c


def go(conn, run, *rows_, now=NOW, **kw):
    plans = run(*rows_, now=now, issued_at=stored_issued_at(conn), **kw)
    rows = {r.row_number: r for r in parse_rows(sheet(*rows_)).rows}
    run_id = uuid.uuid4()
    conn.execute("INSERT INTO workflow_run (id, mode) VALUES (%s, 'RUN_ONCE')", (run_id,))
    return plans, persist(conn, plans, rows, tab=TAB, now=now, run_id=run_id), run_id


def one(conn, sql, *args):
    return conn.execute(sql, args).fetchone()


def test_first_run_drafts_and_plans_three_writes_then_rerun_is_noop(conn, run):
    plans, st, _ = go(conn, run, row(*ORG))
    assert plans[0].action is Action.DRAFT and (st.cases_new, st.transitions, st.writes_planned) == (1, 1, 3)
    assert one(conn, "SELECT state, bbnt_number FROM recon_case") == ("BBNT_DRAFTED", plans[0].bbnt_number)
    n_audit = one(conn, "SELECT count(*) FROM audit_log")[0]
    _, st2, _ = go(conn, run, row(*ORG))
    assert (st2.cases_new, st2.transitions, st2.unchanged, st2.writes_planned) == (0, 0, 1, 0)
    assert one(conn, "SELECT count(*) FROM sheet_write")[0] == 3
    assert one(conn, "SELECT count(*) FROM audit_log")[0] == n_audit            # không ghi audit thừa


def test_rerun_next_month_keeps_bbnt_number(conn, run):
    first, _, _ = go(conn, run, row(*ORG))
    again, st, _ = go(conn, run, row(*ORG), now=NOV, today=NOV.date())
    assert again[0].bbnt_number == first[0].bbnt_number == "10.26/BBNT-ORGANICA-EZ-FB-BDAY.0526"
    assert st.transitions == 0


def test_figures_changed_after_issue_goes_to_review_without_new_writes(conn, run):
    go(conn, run, row(*ORG))
    conn.execute("UPDATE recon_case SET figures = '{\"7:nmv\": \"1\"}'")       # giả lập số liệu đã lưu khác
    _, st, _ = go(conn, run, row(*ORG))
    assert one(conn, "SELECT state, review_reasons FROM recon_case") == ("NEEDS_REVIEW",
                                                                          ["FIGURES_CHANGED_AFTER_ISSUE"])
    assert st.writes_planned == 0 and one(conn, "SELECT count(*) FROM sheet_write")[0] == 3


def test_cell_differs_opens_one_review_and_approved_change_is_written(conn, run):
    _, st, run_id = go(conn, run, row(*ORG, nmv=1))
    assert one(conn, "SELECT state FROM recon_case") == ("NEEDS_REVIEW",) and st.reviews_opened == 1
    _, st, run_id = go(conn, run, row(*ORG, nmv=1))
    assert st.reviews_opened == 0                                                # không mở trùng
    conn.execute("UPDATE cell_review SET decision = 'CHANGE', decided_by = 'kam@hannaholala.com', decided_at = now()")
    got: list[CellWrite] = []

    def writer(ws):
        got.extend(ws)
        return [CellResult(w, "VERIFIED", w.seen, w.target) for w in ws]
    write_back(conn, writer, {"nmv": "I", "commission": "K", "gmv": "M"}, now=NOW, run_id=run_id, st=st)
    assert [(w.row, w.column, w.seen) for w in got] == [(7, "I", Decimal(1))]
    assert one(conn, "SELECT status, approved_by FROM sheet_write") == ("VERIFIED", "kam@hannaholala.com")


def test_write_back_updates_status_audits_and_next_run_marks_figures_written(conn, run):
    plans, st, run_id = go(conn, run, row(*ORG))
    targets = {c.column: c.target for c in plans[0].cells}

    def writer(ws):
        return [CellResult(w, "CONFLICT" if w.column == "M" else "VERIFIED", None, w.target) for w in ws]
    write_back(conn, writer, {"nmv": "I", "commission": "K", "gmv": "M"}, now=NOW, run_id=run_id, st=st)
    assert (st.written, st.conflicts) == (2, 1)
    assert one(conn, "SELECT count(*) FROM audit_log WHERE action = 'SHEET_WRITE'")[0] == 3
    write_back(conn, writer, {"nmv": "I", "commission": "K", "gmv": "M"}, now=NOW, run_id=run_id, st=st)
    assert st.written == 2                                                       # không ghi lại ô đã xử lý
    _, _, _ = go(conn, run, row(*ORG, nmv=targets["nmv"], k=targets["commission"], gmv=targets["gmv"]))
    assert one(conn, "SELECT state FROM recon_case") == ("FIGURES_WRITTEN",)


def test_case_on_hold_is_not_touched(conn, run):
    go(conn, run, row(*ORG))
    conn.execute("UPDATE recon_case SET state = 'ON_HOLD', held_from = 'BBNT_DRAFTED'")
    _, st, _ = go(conn, run, row(*ORG, nmv=5))
    assert st.untouched == 1 and one(conn, "SELECT state FROM recon_case") == ("ON_HOLD",)
    assert one(conn, "SELECT count(*) FROM cell_review")[0] == 0


# --------------------------------------------------------------------------------------------- sheet_writer
class Resp:
    def __init__(self, status: int, body: dict[str, Any]) -> None:
        self.status_code, self._b = status, body

    def json(self) -> dict[str, Any]:
        return self._b


class FakeSheet:
    """Ô A1 -> giá trị; ``formulas`` -> công thức."""

    def __init__(self, cells: dict[str, Any], formulas: dict[str, str] | None = None, fail_post: bool = False,
                 sabotage: bool = False) -> None:
        self.cells, self.formulas, self.fail_post, self.sabotage, self.posts = cells, formulas or {}, fail_post, \
            sabotage, []

    def get(self, url: str, params: list[tuple[str, str]], timeout: int) -> Resp:
        render = dict(params)["valueRenderOption"]
        ranges = [v for k, v in params if k == "ranges"]
        cell = lambda a: self.formulas.get(a) if render == "FORMULA" and a in self.formulas else self.cells.get(a)  # noqa: E731
        return Resp(200, {"valueRanges": [{"values": [[cell(a)]]} if cell(a) is not None else {} for a in ranges]})

    def post(self, url: str, json: dict[str, Any], timeout: int) -> Resp:
        self.posts.append(json)
        if self.fail_post:
            return Resp(500, {})
        assert json["valueInputOption"] == "RAW"
        for d in json["data"]:
            self.cells[d["range"]] = "sai" if self.sabotage else d["values"][0][0]
        return Resp(200, {})


def test_sheet_writer_checks_before_and_after():
    from app.recon.sheet_writer import write_cells
    s = FakeSheet({"'FB-2026'!K7": 5, "'FB-2026'!M7": 9}, formulas={"'FB-2026'!M7": "=I7*2"})
    res = write_cells(s, "sid", TAB, [CellWrite(7, "I", None, K), CellWrite(7, "K", None, K),
                                       CellWrite(7, "M", Decimal(9), K)])
    assert [r.status for r in res] == ["VERIFIED", "CONFLICT", "CONFLICT"]
    assert s.cells["'FB-2026'!I7"] == 816480 and len(s.posts) == 1 and len(s.posts[0]["data"]) == 1
    assert [r.status for r in write_cells(FakeSheet({}, fail_post=True), "sid", TAB, [CellWrite(7, "I", None, K)])] \
        == ["FAILED"]
    assert [r.status for r in write_cells(FakeSheet({}, sabotage=True), "sid", TAB, [CellWrite(7, "I", None, K)])] \
        == ["FAILED"]


def test_out_of_scope_case_keeps_planned_writes_unexecuted(conn, run):
    go(conn, run, row(*ORG))                                              # 3 ô PLANNED
    _, st, run_id = go(conn, run, row(*ORG, status="Đã done BBNT + Chờ KT xuất HĐ nháp"))
    write_back(conn, lambda ws: [CellResult(w, "VERIFIED") for w in ws], {"nmv": "I", "commission": "K", "gmv": "M"},
               now=NOW, run_id=run_id, st=st)
    assert st.written == 0 and one(conn, "SELECT count(*) FROM sheet_write WHERE status = 'PLANNED'")[0] == 3


# --------------------------------------------------------------------------------------------- email nghiệm thu
def _email(conn, plans, run_id, st, gmail, env, mail=None):
    from app.recon.emailer import EmailSettings
    from app.recon.runner import send_acceptance_emails

    from .test_recon_email import MAIL
    send_acceptance_emails(conn, plans, lambda p, at: mail or MAIL, gmail, EmailSettings.from_env(env), now=NOW,
                           run_id=run_id, st=st)


def _figures_written(conn, run):
    plans, _, _ = go(conn, run, row(*ORG))
    t = {c.column: c.target for c in plans[0].cells}
    plans, st, run_id = go(conn, run, row(*ORG, nmv=t["nmv"], k=t["commission"], gmv=t["gmv"]))
    assert one(conn, "SELECT state FROM recon_case") == ("FIGURES_WRITTEN",)
    return plans, st, run_id


def test_test_email_sent_once_per_case_with_gate_notes(conn, run):
    from .test_recon_email import TEST, FakeGmail
    gmail = FakeGmail()
    plans, st, run_id = go(conn, run, row(*ORG))
    _email(conn, plans, run_id, st, gmail, TEST)
    [(msg, thread)] = gmail.sent
    assert st.emails_sent == 1 and msg["To"] == "hiep.pham@hannaholala.com" and thread is None
    text = msg.get_body(("plain",)).get_content()
    assert "KHÔNG ĐẠT: FIGURES_MISMATCH_FBJT" in text and "thread t1" in text
    assert one(conn, "SELECT status, to_addrs, cc_addrs FROM email_outbox") == ("SENT", ["hiep.pham@hannaholala.com"],
                                                                               [])
    assert one(conn, "SELECT state, gmail_thread_id, email_reasons FROM recon_case") == (
        "BBNT_DRAFTED", "t1", ["FIGURES_MISMATCH_FBJT"])                     # test không đổi trạng thái
    assert one(conn, "SELECT count(*) FROM audit_log WHERE action = 'EMAIL_SEND'")[0] == 1
    plans, st, run_id = go(conn, run, row(*ORG))
    _email(conn, plans, run_id, st, gmail, TEST)
    assert st.emails_sent == 0 and len(gmail.sent) == 1                       # chạy lại không gửi trùng


def test_email_failure_recorded_not_retried_and_off_mode_sends_nothing(conn, run):
    from .test_recon_email import TEST, FakeGmail
    plans, st, run_id = go(conn, run, row(*ORG))
    _email(conn, plans, run_id, st, FakeGmail(), {})
    assert one(conn, "SELECT count(*) FROM email_outbox")[0] == 0
    _email(conn, plans, run_id, st, FakeGmail(fail=True), TEST)
    assert st.emails_failed == 1 and one(conn, "SELECT status, last_error FROM email_outbox") == ("FAILED",
                                                                                                   "RuntimeError")
    again = FakeGmail()
    _email(conn, plans, run_id, st, again, TEST)
    assert again.sent == []


def test_email_skip_reason_and_limit(conn, run):
    from .test_recon_email import TEST, FakeGmail
    plans, st, run_id = go(conn, run, row(*ORG))
    _email(conn, plans, run_id, st, FakeGmail(), TEST, mail="NO_BRAND_EMAIL")
    assert st.email_skipped == {"NO_BRAND_EMAIL": 1} and one(conn, "SELECT count(*) FROM email_outbox")[0] == 0
    assert one(conn, "SELECT email_reasons FROM recon_case") == (["NO_BRAND_EMAIL"],)
    st.email_skipped.clear()
    _email(conn, plans, run_id, st, FakeGmail(), {**TEST, "RECON_EMAIL_MAX": "0"})
    assert st.email_skipped == {"MAX_PER_RUN": 1}


def test_live_needs_figures_written_and_signer(conn, run):
    from .test_recon_email import LIVE, FakeGmail
    gmail = FakeGmail()
    plans, st, run_id = go(conn, run, row(*ORG))
    _email(conn, plans, run_id, st, gmail, {**LIVE, "RECON_EMAIL_SIGNER": ""})
    assert gmail.sent == [] and one(conn, "SELECT email_reasons FROM recon_case") == (
        ["FIGURES_MISMATCH_FBJT", "SIGNER_NOT_SET"],)
    assert one(conn, "SELECT count(*) FROM email_outbox")[0] == 0


def test_live_replies_into_loopmail_once_and_moves_case(conn, run):
    from .test_recon_email import LIVE, FakeGmail
    plans, st, run_id = _figures_written(conn, run)
    gmail = FakeGmail()
    _email(conn, plans, run_id, st, gmail, LIVE)
    [(msg, thread)] = gmail.sent
    assert thread == "t1" and msg["To"] == "kt@brand.vn" and "fb@hannaholala.com" in msg["Cc"]
    assert msg["In-Reply-To"] == "<b@x>" and msg["References"] == "<a@x> <b@x>"
    assert msg["Subject"] == "Re: BBNT BDAY T5"
    assert "<b>216.000</b>" in msg.get_body(("html",)).get_content()
    assert one(conn, "SELECT state, email_reasons FROM recon_case") == ("ACCEPTANCE_SENT", [])
    assert one(conn, "SELECT direction, gmail_thread_id FROM email_message") == ("OUT", "t1")
    plans, st, run_id = go(conn, run, row(*ORG))
    _email(conn, plans, run_id, st, gmail, LIVE)
    assert len(gmail.sent) == 1 and st.untouched == 1                         # đã gửi: bot không chạm nữa


def test_live_ambiguous_loopmail_waits_for_kam_then_sends(conn, run):
    from app.recon.emailer import Thread

    from .test_recon_email import LIVE, FakeGmail
    plans, st, run_id = _figures_written(conn, run)
    gmail = FakeGmail(extra=[Thread("t2", "BBNT BDAY T5", ["<c@x>"], frozenset({"kt@brand.vn"}))])
    _email(conn, plans, run_id, st, gmail, LIVE)
    assert gmail.sent == [] and one(conn, "SELECT email_reasons FROM recon_case") == (["LOOPMAIL_NOT_UNIQUE"],)
    assert [c["id"] for c in one(conn, "SELECT loopmail_candidates FROM recon_case")[0]] == ["t1", "t2"]
    conn.execute("UPDATE recon_case SET gmail_thread_id = 't2', loopmail_candidates = NULL")   # KAM chọn (API)
    _email(conn, plans, run_id, st, gmail, LIVE)
    assert [t for _, t in gmail.sent] == ["t2"]


def test_live_outside_send_window_waits(conn, run):
    from app.recon.emailer import EmailSettings
    from app.recon.runner import send_acceptance_emails

    from .test_recon_email import LIVE, MAIL, FakeGmail
    plans, st, run_id = _figures_written(conn, run)
    gmail = FakeGmail()
    send_acceptance_emails(conn, plans, lambda p, at: MAIL, gmail, EmailSettings.from_env(LIVE),
                           now=datetime(2026, 10, 31, 3, 0, tzinfo=UTC), run_id=run_id, st=st)   # thứ Bảy
    assert gmail.sent == [] and one(conn, "SELECT email_reasons FROM recon_case") == (["OUTSIDE_SEND_WINDOW"],)
