"""Dựng bộ giá trị (đã định dạng chuỗi) để fill template BBNT."""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.core.calculation import BbntItemRow, CommissionResult, NmvResult
from app.core.numbering import BbntNumber
from app.docx.vn_text import amount_in_words, format_int, format_percent, format_vnd

_VND = Decimal(1)

_HONORIFIC = re.compile(r"^(ông|bà|anh|chị|mr\.?|mrs\.?|ms\.?)\s+", re.IGNORECASE)


@dataclass(frozen=True)
class BrandBreakdown:
    """NMV riêng của 1 brand trong group (link order kiểu "1 (hoặc nhiều) tab/brand" — xem
    :func:`app.services.order_tabs.detect_brand_tabs`; ``order_tabs`` có > 1 phần tử khi brand đó tách
    nhiều tab theo loại sản phẩm, vd "BIO CORNER ORDER MIX" + "BIO CORNER ORDER FOOD"). Không giữ ``Brand``
    trực tiếp (tránh vòng import ``app.master.models`` <-> module này) — chỉ giữ mã/tên đã tra sẵn."""

    index: int
    brand_code: str
    brand_name: str
    order_tabs: tuple[str, ...]
    nmv: NmvResult


@dataclass(frozen=True)
class PartyA:
    legal_name: str
    address: str = ""
    phone: str = ""
    tax_code: str = ""
    bank_account_no: str = ""
    bank_name: str = ""
    representative_name: str = ""
    representative_title: str = ""

    @classmethod
    def from_snapshot(cls, snap: Mapping[str, Any]) -> PartyA:
        return cls(**{k: (snap.get(k) or "") for k in cls.__dataclass_fields__})


def program_name(scope_display: str, campaign_name: str, number: BbntNumber) -> str:
    """'ORGANICA BDAY 05.26' — tháng nghiệp vụ luôn ở dạng mm.yy (period label của số BBNT)."""
    return " ".join(x for x in (scope_display.strip().upper(), campaign_name.strip(), number.period_label) if x)


def signature_name(representative: str) -> str:
    """'Bà Nguyễn Thị A' -> 'NGUYỄN THỊ A' (bỏ danh xưng, in hoa)."""
    return _HONORIFIC.sub("", representative.strip()).upper()


def contract_signed_text(signed_on: date) -> str:
    """'ký ngày 01 tháng 08 năm 2025' — thay cụm 'ký ngày XX tháng XX năm 20XX' của template."""
    return f"ký ngày {signed_on:%d} tháng {signed_on:%m} năm {signed_on:%Y}"


def build_render_context(
    *,
    number: BbntNumber,
    program: str,
    contract_no: str,
    contract_signed_on: date,
    party_a: PartyA,
    gmv_estimated: Decimal,
    nmv: Decimal,
    cancellation_rate: Decimal | None,
    commission: CommissionResult,
    items: Sequence[BbntItemRow],
    brand_breakdown: Sequence[BrandBreakdown] = (),
) -> tuple[dict[str, str], list[dict[str, str]], list[dict[str, Any]]]:
    """Trả về ``(values, item_rows, brand_blocks)`` toàn chuỗi. Template nào dùng token nào thì lấy token đó;
    key thừa (vd ``vat_amount`` với template SASSY) được bỏ qua. ``brand_blocks`` rỗng nếu link order không
    phải kiểu "1 tab/brand" (xem :func:`app.services.order_tabs.detect_brand_tabs`) — template không có
    marker ``{{brand_section_start}}`` thì có truyền ``brands=()`` cũng không ảnh hưởng gì."""
    values = {
        "bbnt_number": str(number),
        "program_name": program,
        "contract_no": contract_no,
        "contract_signed": contract_signed_text(contract_signed_on),
        "period_label": number.period_label,                 # mm.yy
        "issue_month": number.issue_month.label,             # mm.yy — tháng nghiệp vụ, KHÔNG phải ngày lập pháp lý
        "party_a_name": party_a.legal_name,
        "party_a_address": party_a.address,
        "party_a_phone": party_a.phone,
        "party_a_tax_code": party_a.tax_code,
        "party_a_bank_account": party_a.bank_account_no,
        "party_a_bank_name": party_a.bank_name,
        "party_a_representative": party_a.representative_name,
        "party_a_representative_title": party_a.representative_title,
        "party_a_representative_upper": signature_name(party_a.representative_name),
        "gmv_estimated": format_vnd(gmv_estimated),
        "nmv": format_vnd(nmv),
        "nmv_words": amount_in_words(nmv),
        "cancellation_rate_pct": format_percent(cancellation_rate),
        "commission_rate_pct": format_percent(commission.commission_rate),
        "commission_amount": format_vnd(commission.revenue),
        "tax_rate_pct": format_percent(commission.tax_rate),
        "vat_amount": format_vnd(commission.vat_amount),
        "tax_deduction_amount": format_vnd(commission.tax_deduction_amount),
        "total_payable": format_vnd(commission.total_payable),
        "total_payable_words": amount_in_words(commission.total_payable),
        "total_quantity": format_int(sum(i.quantity for i in items)),
    }
    rows = [{"name": i.product_name or i.sku or "", "sku": i.sku or "", "brand": i.brand_name or "",
             "quantity": format_int(i.quantity), "revenue": format_vnd(i.revenue)} for i in items]
    brand_blocks = [_build_brand_block(b) for b in brand_breakdown]
    return values, rows, brand_blocks


def _build_brand_block(b: BrandBreakdown) -> dict[str, Any]:
    gmv_b = b.nmv.gmv_estimated.quantize(_VND, ROUND_HALF_UP)
    nmv_b = b.nmv.nmv.quantize(_VND, ROUND_HALF_UP)
    values = {
        "index": format_int(b.index),
        "name": b.brand_name,
        "gmv_estimated": format_vnd(gmv_b),
        "nmv": format_vnd(nmv_b),
        "cancellation_rate_pct": format_percent(b.nmv.cancellation_rate),
        "total_quantity": format_int(sum(i.quantity for i in b.nmv.items)),
    }
    items = [{"name": i.product_name or i.sku or "", "sku": i.sku or "", "brand": i.brand_name or "",
              "quantity": format_int(i.quantity), "revenue": format_vnd(i.revenue)} for i in b.nmv.items]
    return {"values": values, "items": items}
