from datetime import UTC, datetime

import pytest

from app.api.v1.bbnt import order_opener
from app.config import DEFAULT_TEMPLATES_DIR
from app.master.repository import MasterRepository
from app.recon.bbnt_job import CaseBbnt, ContractRow, build_input, make_bbnt, signed_contracts
from app.services.templates import TemplateRegistry

from .conftest import ORDER_URL_FB, ORDER_URL_SHISEIDO

ISSUED = datetime(2026, 6, 3, 2, 0, tzinfo=UTC)      # 09:00 giờ VN
ORGANICA = CaseBbnt(entity="EZS", brand_codes=["ORGANICA"], campaign="BDAY - 05/2026",
                    contract_no="01/2026/HĐDV/EZS-ORGANICA")


@pytest.fixture
def ctx(master_tables, fake_sheets):
    master = MasterRepository(lambda: master_tables, ttl_seconds=60).get()
    return TemplateRegistry(DEFAULT_TEMPLATES_DIR), master, fake_sheets


def test_signed_contracts_match_brand_or_group_entity_channel_and_status():
    rows = [
        ContractRow("01/HĐ-A", "aestura ", "EZS", "FB", "Đã ký"),
        ContractRow("02/HĐ-GRP", "AMORE", "EZS", "FB", "đã ký"),
        ContractRow("03/HĐ-DRAFT", "AESTURA", "EZS", "FB", "Đang soạn"),
        ContractRow("04/HĐ-SS", "AESTURA", "SASSY", "FB", "Đã ký"),
        ContractRow("05/HĐ-TTS", "AESTURA", "EZS", "TTS", "Đã ký"),
        ContractRow("", "AESTURA", "EZS", "FB", "Đã ký"),
    ]
    assert signed_contracts(rows, brand_codes=["AESTURA"], group_code=None, entity="EZS") == ["01/HĐ-A"]
    assert signed_contracts(rows, brand_codes=["AESTURA", "INNISFREE"], group_code="AMORE",
                            entity="ezs") == ["01/HĐ-A", "02/HĐ-GRP"]          # 2 hợp đồng -> Audit chặn


def test_build_input_single_brand_and_group():
    one = build_input(ORGANICA)
    assert (one.bbnt_suffix, one.campaign_name, one.brand_codes, one.breakdown_mode) == (
        "BDAY.0526", "BDAY 0526", ["ORGANICA"], None)
    group = build_input(CaseBbnt("SASSY", ["shiseido", "ANESSA"], "Mix Derma - 07/2026", "x"))
    assert (group.bbnt_suffix, group.brand_codes, group.breakdown_mode) == ("MD.0726", ["ANESSA", "SHISEIDO"],
                                                                             "brand")
    with pytest.raises(ValueError):
        build_input(CaseBbnt("EZS", ["ORGANICA"], "BDAY tháng 5", "x"))


def test_make_bbnt_is_reproducible_with_stored_issued_at(ctx):
    templates, master, sheets = ctx
    runs = [make_bbnt(templates, master, ORGANICA, order_opener(sheets, [ORDER_URL_FB]), issued_at=ISSUED,
                      render=True) for _ in range(2)]
    for out in runs:
        assert out.reasons == () and out.result is not None and out.contract_in_portal_master
        assert str(out.result.number) == "06.26/BBNT-ORGANICA-EZ-FB-BDAY.0526"
        assert out.result.program == "BDAY 0526"              # không ghép thêm đuôi BBNT như portal
        assert out.sha256 and len(out.sha256) == 64
    assert runs[0].result.commission.total_payable == runs[1].result.commission.total_payable


def test_make_bbnt_group_link_breaks_down_by_brand(ctx):
    templates, master, sheets = ctx
    case = CaseBbnt("SASSY", ["SHISEIDO", "ANESSA"], "Q3 - 09/2026", "05/2026/HĐDV/SS-SHISEIDO")
    out = make_bbnt(templates, master, case, order_opener(sheets, [ORDER_URL_SHISEIDO]), issued_at=ISSUED,
                    render=False)
    assert out.reasons == () and out.result is not None and out.sha256 is None
    assert out.result.breakdown_kind == "brand"
    assert {b.brand_code for b in out.result.brand_breakdown} == {"SHISEIDO", "ANESSA"}


def test_make_bbnt_returns_reasons_instead_of_raising(ctx):
    templates, master, sheets = ctx
    opener = order_opener(sheets, [ORDER_URL_FB])
    missing = make_bbnt(templates, master, CaseBbnt("EZS", ["ORGANICA"], "BDAY - 05/2026", "99/KHONG-CO"),
                        opener, issued_at=ISSUED, render=False)
    assert missing.result is None and missing.reasons == ("CONTRACT_NOT_FOUND",)
    assert not missing.contract_in_portal_master
    bad = make_bbnt(templates, master, CaseBbnt("EZS", ["ORGANICA"], "BDAY", "x"), opener, issued_at=ISSUED,
                    render=False)
    assert bad.reasons == ("CAMPAIGN_FORMAT",)
