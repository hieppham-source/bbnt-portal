"""GET /api/v1/summary — tab Tổng quan: đọc FB.JT bằng token Google của người xem (chỉ đọc)."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import get_google_access_token
from app.config import Settings, get_settings
from app.errors import fail
from app.recon.summary import Bucket, summarize
from app.services.sheets import GoogleSheetsReader, SheetRef, parse_sheet_url

router = APIRouter(prefix="/api/v1", tags=["Summary"])


class BucketOut(BaseModel):
    collected: Decimal
    pending: Decimal
    estimate: Decimal               # nghiệm thu chưa làm: GMV × 22% × 80%
    eta: Decimal                    # đã thu + pending + nghiệm thu chưa làm
    cancel_rate: Decimal | None
    gmv: Decimal                    # GMV của dòng đã có NMV (mẫu số cancel)
    rows: int
    last_month: dict[str, str]      # collected / pending / eta -> tháng gần nhất có số

    @classmethod
    def of(cls, b: Bucket) -> BucketOut:
        return cls(collected=b.collected, pending=b.pending, estimate=b.estimate.quantize(Decimal(1)),
                   eta=b.eta.quantize(Decimal(1)),
                   cancel_rate=b.cancel_rate.quantize(Decimal("0.0001")) if b.cancel_rate is not None else None,
                   gmv=b.gmv_settled, rows=b.rows, last_month=b.last)


class MonthOut(BucketOut):
    month: str


class GroupOut(BucketOut):
    group: str


class LateOut(BaseModel):
    brand: str
    amount: Decimal
    days_late: int


class SummaryOut(BaseModel):
    year: int
    total: BucketOut
    months: list[MonthOut]
    groups: list[GroupOut]          # theo group brand — dùng cho bảng chi tiết khi bấm từng ô
    month_options: list[str]
    pic_options: list[str]
    overdue: list[LateOut]          # group brand chậm trả, trễ lâu nhất trước


@router.get("/summary", response_model=SummaryOut)
def summary(settings: Annotated[Settings, Depends(get_settings)],
            token: Annotated[str, Depends(get_google_access_token)],
            year: int | None = None, month: str | None = None, pic: str | None = None) -> SummaryOut:
    if not settings.data_brand_sheet:
        raise fail("SUMMARY_NOT_CONFIGURED", "Chưa cấu hình RECON_FBJT_ID trên máy chủ", status=503)
    y = year or datetime.now(ZoneInfo(settings.timezone)).year
    sheet = settings.data_brand_sheet
    ref = parse_sheet_url(sheet) if "/" in sheet else SheetRef(sheet)
    reader = GoogleSheetsReader.from_access_token(token)
    s = summarize(reader.read(ref, sheet_name=f"FB-{y}"), month=month or None, pic=pic or None,
                  today=datetime.now(ZoneInfo(settings.timezone)).date())
    return SummaryOut(year=y, total=BucketOut.of(s.total),
                      months=[MonthOut(month=m, **BucketOut.of(b).model_dump()) for m, b in s.by_month.items()],
                      groups=[GroupOut(group=g, **BucketOut.of(b).model_dump()) for g, b in s.by_group.items()],
                      month_options=s.months, pic_options=s.pics,
                      overdue=sorted((LateOut(brand=b, amount=x.amount, days_late=x.days)
                                      for b, x in s.overdue.items()),
                                     key=lambda x: (-x.days_late, -x.amount)))
