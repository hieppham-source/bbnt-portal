"""``app.services.bbnt_document`` — ghép câu "Căn cứ hợp đồng ... ký ngày ... [và Phụ lục hợp đồng số ... ký
ngày ...]" (PLHD = phụ lục hợp đồng). Xem test_master.py (parse cột master) và test_api.py (end-to-end .docx)
cho các mặt khác của tính năng này."""
from __future__ import annotations

from datetime import date

from app.services.bbnt_document import contract_signed_text


def test_contract_signed_text_without_appendix():
    assert contract_signed_text(date(2025, 8, 1)) == "ký ngày 01 tháng 08 năm 2025"


def test_contract_signed_text_with_appendix_appends_after_original_date():
    """Đúng thứ tự: hợp đồng GỐC trước (số + ngày ký riêng), rồi MỚI tới phụ lục (số + ngày ký riêng) — không
    được nhét số phụ lục trước số hợp đồng gốc (lỗi người dùng báo: "...PLHD .../PLHD/... (HĐ gốc ...)...")."""
    text = contract_signed_text(date(2026, 8, 1), appendix_no="PLHD/YANKEExSASSY-FB",
                                appendix_signed_on=date(2026, 7, 31))
    assert text == ("ký ngày 01 tháng 08 năm 2026 và Phụ lục hợp đồng số PLHD/YANKEExSASSY-FB ký ngày "
                     "31 tháng 07 năm 2026")


def test_contract_signed_text_ignores_appendix_when_only_one_side_given():
    """Chỉ có 1 trong 2 (số hoặc ngày ký phụ lục) -> coi như không có phụ lục (đã lọc ở bước đọc master, nhưng
    hàm này tự vệ thêm 1 lớp)."""
    assert contract_signed_text(date(2025, 8, 1), appendix_no="PLHD-01") == "ký ngày 01 tháng 08 năm 2025"
    assert (contract_signed_text(date(2025, 8, 1), appendix_signed_on=date(2025, 7, 1))
            == "ký ngày 01 tháng 08 năm 2025")
