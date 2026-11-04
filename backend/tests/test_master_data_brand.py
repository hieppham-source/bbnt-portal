"""DATA BRAND (FB.JT) -> tab BRAND + HOP_DONG của master (chốt 04/11/2026). Dữ liệu giả, không có brand thật."""
from datetime import date
from decimal import Decimal

from app.master.data_brand import brand_code, with_data_brand
from app.master.repository import build_master

HEADER = ["GROUP", "BRAND", "PIC", "CMS", "OP", "PHÁP NHÂN B", "PHÁP NHÂN BÊN A", "EMAIL NHẬN HÓA ĐƠN", "STATUS",
          "SỐ HỢP ĐỒNG hoặc Phụ lục hợp đồng thêm", "NGÀY KÝ HỢP ĐỒNG\n(dd/mm/yy)", "NGÀY ĐẾN HẠN\n(dd/mm/yy)",
          "COMMISSION\n(chưa bao gồm VAT)", "FINAL COMMISSION (ĐÃ BAO GỒM VAT)", "HÌNH THỨC HỢP TÁC",
          "PHÁP NHÂN A\n(BRAND)", "PHÁP NHÂN B\n(EZ/SS)", "ĐỊA CHỈ BÊN A", "MÃ SỐ THUẾ", "TÀI KHOẢN NGÂN HÀNG",
          "SỐ ĐIỆN THOẠI BÊN A", "NGƯỜI KÝ", "CHỨC VỤ", "SỐ PHỤ LỤC", "NGÀY KÝ PHỤ LỤC"]


def row(group, brand, entity, status, no, signed="05/09/2025", rate="8%", op="NON OP", pl="", pl_date=""):
    return [group, brand, "PIC", "9%", op, entity, "", "a@b.vn", status, no, signed, "31/12/2020", rate, "", "",
            f"CÔNG TY {brand}", "", "Địa chỉ A", "0123456789", "", "", "Người Ký", "Giám đốc", pl, pl_date]


DATA = [HEADER,
        row("GRP", "ALPHA ONE", "Cty EZS", "CÒN HIỆU LỰC", "01/GRP-EZS-FB", pl="PL04", pl_date="14/01/2026"),
        row("GRP", "BETA", "Cty EZS", "CÒN HIỆU LỰC", "01/GRP-EZS-FB", pl="PL04", pl_date="14/01/2026"),
        row("SOLO", "SOLO", "HKD SASSY", "HẾT HIỆU LỰC", "02/SOLO-SS-FB", rate="22%"),
        ["NOCO", "NO CONTRACT"]]


def test_brand_code():
    assert brand_code("LA ROCHE POSAY") == "LA_ROCHE_POSAY" and brand_code("À ƠI CONCEPT") == "A_OI_CONCEPT"
    assert brand_code("ROGER&GALLET") == "ROGER_GALLET"


def test_data_brand_replaces_brand_and_contract_tabs(master_tables):
    m = build_master(with_data_brand(master_tables, DATA))
    assert {b.code for b in m.brands} == {"ALPHA_ONE", "BETA", "SOLO", "NO_CONTRACT"} and m.channels() == ["FB"]
    [grp] = [c for c in m.contracts if c.group_code == "GRP"]      # 2 brand chung 1 HĐ -> 1 dòng theo group
    assert (grp.contract_no, grp.entity_code, grp.commission_rate, grp.active) == (
        "01/GRP-EZS-FB", "EZS", Decimal("0.08"), True)
    assert grp.valid_to is None and grp.covers(date(2020, 1, 1), date(2030, 1, 1))   # chỉ theo STATUS
    assert (grp.signed_on, grp.appendix_no, grp.appendix_signed_on) == (date(2025, 9, 5), "PL04", date(2026, 1, 14))
    assert (grp.party_a.legal_name, grp.party_a.tax_code) == ("CÔNG TY ALPHA ONE", "0123456789")
    [solo] = [c for c in m.contracts if c.brand_code == "SOLO"]
    assert (solo.entity_code, solo.active, solo.commission_rate) == ("SASSY", False, Decimal("0.22"))
    assert not [i for i in m.issues if i.tab in ("BRAND", "HOP_DONG")]
