"""
API tạo BBNT — không lưu gì trên server.

* ``POST /api/v1/bbnt/preview``        tính & kiểm tra, trả JSON (số BBNT, số liệu, bảng sản phẩm, cảnh báo)
* ``POST /api/v1/bbnt/preview-split``  ``tab_mode='split'`` — mỗi tab/camp trong file order xem trước thành
  1 BBNT riêng (xem docstring ``app.services.generator``)
* ``POST /api/v1/bbnt/generate``       cùng payload, trả thẳng file .docx để tải về
"""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from app.api.deps import MasterLoader, User, get_generator, get_master_loader, get_sheet_reader, get_user
from app.core.calculation import RowIssue
from app.docx.vn_text import amount_in_words
from app.errors import fail
from app.schemas.bbnt import (
    BbntRequest,
    BrandBreakdownOut,
    ContractOut,
    ErrorOut,
    FiguresOut,
    IssueOut,
    ItemOut,
    PreviewResponse,
    SplitPreviewResponse,
)
from app.services.generator import DOCX_MIME, BbntGenerator, BbntResult, OrderOpener
from app.services.sheets import (
    DriveScopeError,
    OrderWorkbook,
    SheetAccessError,
    SheetAuthError,
    SheetReader,
    parse_sheet_url,
)

router = APIRouter(prefix="/api/v1/bbnt", tags=["BBNT"])

MAX_WARNINGS_IN_RESPONSE = 500
VND = Decimal(1)

_ERRORS: dict[int | str, dict[str, Any]] = {
    422: {"model": ErrorOut, "description": "Payload / Sheet master / file order không hợp lệ"},
    502: {"model": ErrorOut, "description": "Không đọc được Google Sheet"},
}


def issue_out(i: RowIssue) -> IssueOut:
    return IssueOut(row_number=i.row_number, code=i.code.value, severity=i.severity.value,
                    message=i.message, order_code=i.order_code, amount=i.amount)


def download_filename(bbnt_number: str) -> str:
    """'06.26/BBNT-X-EZ-FB-CAMP-05,07.26' -> 'BBNT_06.26_BBNT-X-EZ-FB-CAMP-05,07.26.docx' (an toàn cho OS)."""
    return "BBNT_" + re.sub(r'[\\/:*?"<>|\s]+', "_", bbnt_number) + ".docx"


def order_opener(sheets: SheetReader, urls: list[str]) -> OrderOpener:
    """``urls``: link order chính + mọi ``extra_order_sheet_urls`` (nếu có) — mở RIÊNG mỗi link thành 1
    :class:`app.services.sheets.OrderWorkbook` (không gộp tab — xem docstring ``OrderOpener`` trong
    ``app.services.generator`` vì sao không gộp), để generator tự chọn tab của từng link rồi gộp dòng lại."""
    refs = [parse_sheet_url(u) for u in urls]

    def open_() -> list[OrderWorkbook]:
        return [sheets.open(ref) for ref in refs]
    return open_


def _run(payload: BbntRequest, load_master: MasterLoader, sheets: SheetReader, gen: BbntGenerator,
         render: bool, refresh: bool) -> BbntResult:
    master = load_master(refresh)
    opener = order_opener(sheets, [payload.order_sheet_url, *payload.extra_order_sheet_urls])
    try:
        return gen.run(master, payload.to_input(), opener, render=render)
    except SheetAuthError as exc:
        raise fail("GOOGLE_TOKEN_EXPIRED", str(exc), status=401) from exc
    except DriveScopeError as exc:
        raise fail("GOOGLE_DRIVE_SCOPE_MISSING", str(exc), status=403) from exc
    except SheetAccessError as exc:
        raise fail("ORDER_SHEET_UNREADABLE", str(exc), status=502) from exc


def _run_split(payload: BbntRequest, load_master: MasterLoader, sheets: SheetReader,
              gen: BbntGenerator, refresh: bool) -> list[BbntResult]:
    master = load_master(refresh)
    opener = order_opener(sheets, [payload.order_sheet_url, *payload.extra_order_sheet_urls])
    try:
        return gen.run_split(master, payload.to_input(), opener)
    except SheetAuthError as exc:
        raise fail("GOOGLE_TOKEN_EXPIRED", str(exc), status=401) from exc
    except DriveScopeError as exc:
        raise fail("GOOGLE_DRIVE_SCOPE_MISSING", str(exc), status=403) from exc
    except SheetAccessError as exc:
        raise fail("ORDER_SHEET_UNREADABLE", str(exc), status=502) from exc


def to_preview(r: BbntResult) -> PreviewResponse:
    c, k = r.commission, r.contract
    warnings = r.warnings
    assert r.number.issue_date is not None
    return PreviewResponse(
        bbnt_number=str(r.number), file_name=download_filename(str(r.number)), issue_date=r.number.issue_date,
        bbnt_suffix=r.number.suffix, program_name=r.program, campaign_name=r.campaign_name,
        scope_code=r.scope.code, brands=[b.code for b in r.scope.brands], mapping_code=r.mapping.code,
        contract=ContractOut(contract_no=k.contract_no, scope=k.scope_label, channel=k.channel, model=k.model,
                             commission_rate=k.commission_rate, valid_from=k.valid_from, valid_to=k.valid_to,
                             signed_on=k.signed_on or k.valid_from,
                             signed_on_source="Ngày ký" if k.signed_on else "Hiệu lực từ",
                             appendix_no=k.appendix_no, appendix_signed_on=k.appendix_signed_on,
                             party_a_name=k.party_a.legal_name, master_row=k.row),
        figures=FiguresOut(
            gmv_estimated=r.gmv_vnd, nmv=r.nmv_vnd, cancellation_rate=r.nmv.cancellation_rate,
            commission_rate=c.commission_rate, commission_amount=c.revenue, tax_mode=c.tax_mode.value,
            tax_rate=c.tax_rate, tax_label=r.tax.label, vat_amount=c.vat_amount,
            tax_deduction_amount=c.tax_deduction_amount, collected_on_behalf=c.collected_on_behalf,
            consulting_fee=c.consulting_fee, total_payable=c.total_payable,
            total_payable_words=amount_in_words(c.total_payable)),
        items=[ItemOut(brand_name=i.brand_name, sku=i.sku, product_name=i.product_name, quantity=i.quantity,
                       revenue=i.revenue) for i in r.nmv.items],
        breakdown_kind=r.breakdown_kind, manual_contract=r.manual_contract,
        brand_breakdown=[
            BrandBreakdownOut(
                index=b.index, brand_code=b.brand_code, brand_name=b.brand_name, order_tabs=list(b.order_tabs),
                gmv_estimated=b.nmv.gmv_estimated.quantize(VND, ROUND_HALF_UP),
                nmv=b.nmv.nmv.quantize(VND, ROUND_HALF_UP), cancellation_rate=b.nmv.cancellation_rate,
                items=[ItemOut(brand_name=i.brand_name, sku=i.sku, product_name=i.product_name,
                               quantity=i.quantity, revenue=i.revenue) for i in b.nmv.items])
            for b in r.brand_breakdown
        ],
        order_line_count=len(r.lines), other_brand_line_count=r.other_brand_lines,
        order_tab=r.order.tab if r.order else None, order_tabs=r.order.candidates if r.order else [],
        order_tabs_combined=r.combined_tabs,
        order_file_kind=r.order_kind, order_header_row=r.parse.header_row,
        warning_count=len(warnings), warnings=[issue_out(i) for i in warnings[:MAX_WARNINGS_IN_RESPONSE]],
    )


UserDep = Annotated[User, Depends(get_user)]
MasterDep = Annotated[MasterLoader, Depends(get_master_loader)]
SheetsDep = Annotated[SheetReader, Depends(get_sheet_reader)]
GenDep = Annotated[BbntGenerator, Depends(get_generator)]
RefreshQ = Annotated[bool, Query(description="Đọc lại Sheet master ngay, bỏ qua cache")]


@router.post("/preview", response_model=PreviewResponse, responses=_ERRORS,
             summary="Xem trước số BBNT + số liệu (không tạo file)")
def preview_bbnt(payload: BbntRequest, user: UserDep, load_master: MasterDep, sheets: SheetsDep, gen: GenDep,
                 refresh: RefreshQ = False) -> PreviewResponse:
    return to_preview(_run(payload, load_master, sheets, gen, render=False, refresh=refresh))


@router.post("/preview-split", response_model=SplitPreviewResponse, responses=_ERRORS,
             summary="Xem trước TÁCH BBNT theo từng tab/camp (tab_mode='split')")
def preview_bbnt_split(payload: BbntRequest, user: UserDep, load_master: MasterDep, sheets: SheetsDep,
                       gen: GenDep, refresh: RefreshQ = False) -> SplitPreviewResponse:
    return SplitPreviewResponse(results=[to_preview(r) for r in _run_split(payload, load_master, sheets, gen,
                                                                           refresh)])


@router.post("/generate", responses={200: {"content": {DOCX_MIME: {}}}, **_ERRORS},
             response_class=Response, summary="Tạo & tải file BBNT .docx (server không lưu)")
def generate_bbnt(payload: BbntRequest, user: UserDep, load_master: MasterDep, sheets: SheetsDep, gen: GenDep,
                  refresh: RefreshQ = False) -> Response:
    r = _run(payload, load_master, sheets, gen, render=True, refresh=refresh)
    assert r.content is not None
    number = str(r.number)
    name = download_filename(number)
    return Response(content=r.content, media_type=DOCX_MIME, headers={
        "Content-Disposition": f"attachment; filename=\"{name.encode('ascii', 'ignore').decode()}\"; "
                               f"filename*=UTF-8''{quote(name)}",
        "X-BBNT-Number": quote(number, safe="/.,-"),
        "X-BBNT-Warnings": str(len(r.warnings)),
        "X-BBNT-Total-Payable": str(r.commission.total_payable),
        "Cache-Control": "no-store",
    })
