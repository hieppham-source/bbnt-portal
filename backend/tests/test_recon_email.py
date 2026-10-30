"""Email chế độ test: chỉ gửi về hộp thư test, đúng 1 thư/case, không bao giờ tới brand."""
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.config import DEFAULT_TEMPLATES_DIR
from app.master.repository import MasterRepository
from app.recon import worker
from app.recon.bbnt_job import make_bbnt
from app.recon.emailer import (
    INTERNAL_CC,
    BbntMail,
    EmailConfigError,
    EmailSettings,
    Thread,
    acceptance_html,
    acceptance_text,
    build_live_reply,
    build_test_message,
    camp_parts,
    find_loopmail,
    in_send_window,
    recipients,
    split_addresses,
    subject,
)
from app.recon.fbjt import parse_rows
from app.recon.google_bot import parse_brand_emails
from app.recon.worker import Action
from app.services.templates import TemplateRegistry

from . import test_recon_worker as tw
from .test_recon_worker import ORDERS, ORG, row, sheet

run = tw.run
TEST = {"RECON_EMAIL_MODE": "test", "RECON_EMAIL_TEST_TO": "Hiep.Pham@hannaholala.com"}
LIVE = {"RECON_EMAIL_MODE": "live", "RECON_EMAIL_SIGNER": " Hiệp  Phạm "}
LOOP = Thread("t1", "RE: BBNT BDAY T5", ["<a@x>", "<b@x>"], frozenset({"kt@brand.vn", "hiep.pham@hannaholala.com"}))


class FakeGmail:
    """Gmail giả: thread t1 khớp loopmail (+ ``extra``), 1 thread lạc đề; ``send`` ghi lại thư."""

    def __init__(self, extra=(), fail=False):
        self.threads = {t.id: t for t in (LOOP, Thread("x9", "Khác", [], frozenset({"kt@brand.vn"})), *extra)}
        self.fail, self.sent = fail, []

    def search(self, title):
        return list(self.threads)

    def thread(self, thread_id):
        return self.threads[thread_id]

    def send(self, msg, thread_id=None):
        if self.fail:
            raise RuntimeError("Gmail HTTP 500")
        self.sent.append((msg, thread_id))
        return f"gm{len(self.sent)}", thread_id or "new"
D = Decimal
MAIL = BbntMail("ORGANICA", ["ORGANICA"], "EZS", "BBNT BDAY T5", "BDAY - 05/2026", "10.26/BBNT-X", "01/2026/HĐ",
                D(1_200_000), D("0.166667"), D(1_000_000), D("0.2"), D(200_000), D(16_000), D(0), D(0), D(0),
                D(216_000), D("0.08"), ["kt@brand.vn"], "BBNT_x.docx", b"PK")
SASSY = BbntMail("SHISEIDO_GRP", ["ANESSA", "SHISEIDO"], "SASSY", "T", "Q3 - 09/2026", "N", "HĐ", D(100), None,
                 D(100), D("0.15"), D(15), D(0), D("0.15"), D(0), D(0), D("14.85"), D("0.01"), ["a@b.vn"], "x", b"")
NOW = datetime(2026, 10, 28, 2, 0, tzinfo=UTC)


def test_settings_default_off_and_rejects_bad_mode_or_outside_address():
    assert EmailSettings.from_env({}).mode == "off"
    s = EmailSettings.from_env(TEST)
    assert (s.mode, s.test_to, s.max_per_run) == ("test", "hiep.pham@hannaholala.com", 5)
    assert EmailSettings.from_env(LIVE) == EmailSettings("live", "", 5, "Hiệp Phạm")
    for bad in ({"RECON_EMAIL_MODE": "send"}, {**TEST, "RECON_EMAIL_TEST_TO": "kt@brand.vn"},
                {**TEST, "RECON_EMAIL_TEST_TO": "a@hannaholala.com,b@hannaholala.com"},
                {"RECON_EMAIL_MODE": "test"}, {**TEST, "RECON_EMAIL_MAX": "x"}):
        with pytest.raises(EmailConfigError):
            EmailSettings.from_env(bad)
    assert EmailSettings.from_env({**TEST, "RECON_EMAIL_MAX": "999"}).max_per_run == 50


def test_addresses_and_subject():
    assert split_addresses("A@x.vn, b@y.com;\nA@x.vn") == ["a@x.vn", "b@y.com"]
    with pytest.raises(ValueError):
        split_addresses("ke toan@x")
    assert subject("ABBOTT", ["ENSURE"], "EZS", "BBNT CAMP MIX T6") == "ABBOTT (ENSURE) x EZS - FB - BBNT CAMP MIX T6"
    assert subject("ORGANICA", ["ORGANICA"], "EZS", "T") == "ORGANICA x EZS - FB - T"


def test_test_message_goes_only_to_test_inbox():
    msg = build_test_message(MAIL, EmailSettings.from_env(TEST), now=NOW)
    assert recipients(msg) == ["hiep.pham@hannaholala.com"] and msg["Cc"] is None
    assert msg["Subject"] == "[TEST] ORGANICA x EZS - FB - BDAY 05/26"
    text = msg.get_body(("plain",)).get_content()
    assert "Khi chạy thật sẽ gửi To: kt@brand.vn" in text and INTERNAL_CC[0] in text
    assert "Tổng tiền brand cần thanh toán thêm | 216.000 | E=A+B-C-D+F" in text and "trước ngày 31/10/2026" in text
    [att] = list(msg.iter_attachments())
    assert att.get_filename() == "BBNT_x.docx" and att.get_content() == b"PK"
    with pytest.raises(EmailConfigError):
        build_test_message(MAIL, EmailSettings("off"), now=NOW)


def test_parse_brand_emails_merges_duplicate_brand_rows():
    got = parse_brand_emails([["GROUP", "BRAND", "OP", "EMAIL BRAND"], ["G", "Organica", "", "a@x.vn"],
                              ["G", "ORGANICA", "", "b@x.vn"], ["G", "NoMail", "", ""]])
    assert got == {"organica": "a@x.vn,b@x.vn"}


def test_mail_for_renders_docx_and_reports_missing_email(run, master_tables):
    rows_ = [row(*ORG)]
    [p] = run(*rows_)
    assert p.action is Action.DRAFT and p.job is not None
    by_row = {r.row_number: r for r in parse_rows(sheet(*rows_)).rows}
    master, templates = MasterRepository(lambda: master_tables).get(), TemplateRegistry(DEFAULT_TEMPLATES_DIR)

    def render(pl, at):
        return make_bbnt(templates, master, pl.job, worker._fixture_opener(ORDERS)(pl.url), issued_at=at,
                         render=True).result
    m = worker.mail_for(p, tw.NOW, by_row, {"organica": "KT@brand.vn"}, render)
    assert isinstance(m, BbntMail) and m.bbnt_number == p.bbnt_number and m.brand_to == ["kt@brand.vn"]
    assert m.attachment[:2] == b"PK" and m.attachment_name.endswith(".docx") and "/" not in m.attachment_name
    assert m.commission == next(c.target for c in p.cells if c.column == "commission")
    assert m.attachment_name == "BBNT_ORGANICA_BDAY_05-26.docx" and m.e == m.a + m.b - m.c - m.d + m.f
    assert worker.mail_for(p, tw.NOW, by_row, {}, render) == "NO_BRAND_EMAIL"
    assert worker.mail_for(p, tw.NOW, by_row, {"organica": "sai"}, render) == "BRAND_EMAIL_INVALID"
    assert worker.mail_for(p, tw.NOW, by_row, {"organica": "a@b.vn"}, lambda *_: None) == "RENDER_FAILED"


def test_loopmail_match_needs_title_and_brand_participant():
    assert [t.id for t in find_loopmail(FakeGmail(), "bbnt  bday t5", ["kt@brand.vn"])] == ["t1"]
    assert find_loopmail(FakeGmail(), "BBNT BDAY T5", ["khac@brand.vn"]) == []
    assert find_loopmail(FakeGmail(), "BBNT BDAY", ["kt@brand.vn"]) == []


def test_email2_tables_follow_entity_and_live_reply_threads():
    assert camp_parts("Mix Derma - 7/2026") == ("MIX DERMA", "07/26") and camp_parts("BDAY") == ("BDAY", "")
    ezs = {r[0]: r for r in MAIL.rows()}
    assert ezs["Tiền VAT"][1:3] == ("8%", "16.000") and ezs["Cấn trừ thuế"][1:3] == ("0%", "0")
    assert ezs["Cancellation"][2] == "16,67%" and ezs["Tổng Commission được hưởng (chưa VAT)"][1] == "20%"
    sassy = {r[0]: r for r in SASSY.rows()}
    assert sassy["Tiền VAT"][1] == "0%" and sassy["Cấn trừ thuế"][1] == "1%" and sassy["Cancellation"][2] == "-"
    assert SASSY.subject() == "SHISEIDO_GRP (ANESSA, SHISEIDO) x SASSY - FB - Q3 09/26"
    assert "Hiệp" in acceptance_text(MAIL, "31/10/2026", "Hiệp") and "<script" not in acceptance_html(
        BbntMail(**{**MAIL.__dict__, "brands": ["<script>"]}), "x", "y")
    msg = build_live_reply(MAIL, LOOP, now=NOW, signer="Hiệp Phạm")
    assert recipients(msg)[0] == "kt@brand.vn" and set(recipients(msg)[1:]) == set(INTERNAL_CC)
    assert msg["Subject"] == "Re: BBNT BDAY T5" and msg["In-Reply-To"] == "<b@x>"
    html = msg.get_body(("html",)).get_content()
    assert "<b>216.000</b>" in html and "trước ngày <b>31/10/2026</b>" in html and "Hiệp Phạm" in html
    assert len(list(msg.iter_attachments())) == 1


def test_send_window_is_weekday_office_hours_vn():
    assert in_send_window(datetime(2026, 10, 28, 2, 0, tzinfo=UTC))             # T4 09:00 VN
    assert not in_send_window(datetime(2026, 10, 28, 12, 0, tzinfo=UTC))        # T4 19:00 VN
    assert not in_send_window(datetime(2026, 10, 31, 3, 0, tzinfo=UTC))         # T7
