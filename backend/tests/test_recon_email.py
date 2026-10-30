"""Email chế độ test: chỉ gửi về hộp thư test, đúng 1 thư/case, không bao giờ tới brand."""
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
    build_test_message,
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
MAIL = BbntMail("ORGANICA", ["ORGANICA"], "EZS", "BBNT BDAY T5", "BDAY - 05/2026", "10.26/BBNT-X", "01/2026/HĐ",
                Decimal(1_000_000), Decimal(216_000), Decimal(1_200_000), ["kt@brand.vn"], "BBNT_x.docx", b"PK")


def test_settings_default_off_and_rejects_live_or_outside_address():
    assert EmailSettings.from_env({}).mode == "off"
    s = EmailSettings.from_env(TEST)
    assert (s.mode, s.test_to, s.max_per_run) == ("test", "hiep.pham@hannaholala.com", 5)
    for bad in ({"RECON_EMAIL_MODE": "live"}, {**TEST, "RECON_EMAIL_TEST_TO": "kt@brand.vn"},
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
    msg = build_test_message(MAIL, EmailSettings.from_env(TEST))
    assert recipients(msg) == ["hiep.pham@hannaholala.com"] and msg["Cc"] is None
    assert msg["Subject"] == "[TEST] ORGANICA x EZS - FB - BBNT BDAY T5"
    text = msg.get_body(("plain",)).get_content()
    assert "Khi chạy thật sẽ gửi To: kt@brand.vn" in text and INTERNAL_CC[0] in text and "216.000 đ" in text
    [att] = list(msg.iter_attachments())
    assert att.get_filename() == "BBNT_x.docx" and att.get_content() == b"PK"
    with pytest.raises(EmailConfigError):
        build_test_message(MAIL, EmailSettings("off"))


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
    assert worker.mail_for(p, tw.NOW, by_row, {}, render) == "NO_BRAND_EMAIL"
    assert worker.mail_for(p, tw.NOW, by_row, {"organica": "sai"}, render) == "BRAND_EMAIL_INVALID"
    assert worker.mail_for(p, tw.NOW, by_row, {"organica": "a@b.vn"}, lambda *_: None) == "RENDER_FAILED"
