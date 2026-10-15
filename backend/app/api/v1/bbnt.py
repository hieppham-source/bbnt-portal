"""
API tạo BBNT — không lưu gì trên server.

* ``POST /api/v1/bbnt/preview``   tính & kiểm tra, trả JSON (số BBNT, số liệu, bảng sản phẩm, cảnh báo)
* ``POST /api/v1/bbnt/generate``  cùng payload, trả thẳng file .docx để tải về
"""
from __future__ import annotations

import re
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from app.api.deps import User, get_generator, get_master_repo, get_sheet_reader, get_user
from app.core.calculation import RowIssue
from app.docx.vn_text import amount_in_words
from app.errors import fail
from app.master.repository import MasterRepository
from app.schemas.bbnt import (
    BbntRequest,
    ContractOut,
    ErrorOut,
    FiguresOut,
    IssueOut,
    ItemOut,
    PreviewResponse,
)
from app.services.generator import DOCX_MIME, BbntGenerator, BbntResult, OrderReader
from app.services.sheets import SheetAccessError, SheetReader, parse_sheet_url

router = APIRouter(prefix="/api/v1/bbnt", tags=["BBNT"])

MAX_WARNINGS_IN_RESPONSE = 500

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


def order_reader(sheets: SheetReader, url: str) -> OrderReader:
    ref = parse_sheet_url(url)

    def read(sheet_name: str | None) -> list[list[Any]]:
        try:
            return sheets.read(ref, sheet_name=sheet_name)
        except SheetAccessError as exc:
            raise fail("ORDER_SHEET_UNREADABLE", str(exc), status=502) from exc
    return read


def _run(payload: BbntRequest, repo: MasterRepository, sheets: SheetReader, gen: BbntGenerator,
         render: bool, refresh: bool) -> BbntResult:
    master = repo.get(refresh=refresh)
    return gen.run(master, payload.to_input(), order_reader(sheets, payload.order_sheet_url), render=render)


def to_preview(r: BbntResult) -> PreviewResponse:
    c, k = r.commission, r.contract
    warnings = r.warnings
    assert r.number.issue_date is not None
    return PreviewResponse(
        bbnt_number=str(r.number), file_name=download_filename(str(r.number)), issue_date=r.number.issue_date,
        period_label=r.number.period_label, program_name=r.program, scope_code=r.scope.code,
        brands=[b.code for b in r.scope.brands], mapping_code=r.mapping.code,
        contract=ContractOut(contract_no=k.contract_no, scope=k.scope_label, channel=k.channel, model=k.model,
                             commission_rate=k.commission_rate, valid_from=k.valid_from, valid_to=k.valid_to,
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
        order_line_count=len(r.lines), other_brand_line_count=r.other_brand_lines,
        warning_count=len(warnings), warnings=[issue_out(i) for i in warnings[:MAX_WARNINGS_IN_RESPONSE]],
    )


UserDep = Annotated[User, Depends(get_user)]
RepoDep = Annotated[MasterRepository, Depends(get_master_repo)]
SheetsDep = Annotated[SheetReader, Depends(get_sheet_reader)]
GenDep = Annotated[BbntGenerator, Depends(get_generator)]
RefreshQ = Annotated[bool, Query(description="Đọc lại Sheet master ngay, bỏ qua cache")]


@router.post("/preview", response_model=PreviewResponse, responses=_ERRORS,
             summary="Xem trước số BBNT + số liệu (không tạo file)")
def preview_bbnt(payload: BbntRequest, user: UserDep, repo: RepoDep, sheets: SheetsDep, gen: GenDep,
                 refresh: RefreshQ = False) -> PreviewResponse:
    return to_preview(_run(payload, repo, sheets, gen, render=False, refresh=refresh))


@router.post("/generate", responses={200: {"content": {DOCX_MIME: {}}}, **_ERRORS},
             response_class=Response, summary="Tạo & tải file BBNT .docx (server không lưu)")
def generate_bbnt(payload: BbntRequest, user: UserDep, repo: RepoDep, sheets: SheetsDep, gen: GenDep,
                  refresh: RefreshQ = False) -> Response:
    r = _run(payload, repo, sheets, gen, render=True, refresh=refresh)
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
