"""Pydantic schemas cho API (không có id/DB — mọi thứ tham chiếu bằng mã trong Sheet master)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.numbering import NumberingError, YearMonth
from app.services.generator import BbntInput
from app.services.sheets import parse_sheet_url


class BbntRequest(BaseModel):
    """Payload chung cho ``/bbnt/preview`` và ``/bbnt/generate``.

    Chọn đúng 1 trong ``brand_codes`` (1 brand, hoặc nhiều brand cùng group -> số BBNT dùng group code)
    hoặc ``group_code`` (gộp cả group). ``contract_no`` chỉ cần khi có nhiều hợp đồng cùng hiệu lực.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    channel: str = Field(examples=["FB"], min_length=2, max_length=10)
    entity: str = Field(examples=["EZS"], min_length=2, max_length=20)
    brand_codes: list[str] | None = Field(default=None, min_length=1, max_length=50, examples=[["ORGANICA"]])
    group_code: str | None = Field(default=None, max_length=50)
    contract_no: str | None = Field(default=None, max_length=100)
    period_months: list[str] = Field(min_length=1, max_length=12, examples=[["05.26"], ["07.26", "08.26"]])
    campaign_name: str = Field(min_length=1, max_length=100, examples=["BDAY"])
    order_sheet_url: str = Field(examples=["https://docs.google.com/spreadsheets/d/1AbC.../edit#gid=0"])
    mapping_code: str | None = Field(default=None, max_length=50)
    collected_on_behalf: Decimal = Field(default=Decimal(0), ge=0, max_digits=16, decimal_places=2)
    consulting_fee: Decimal = Field(default=Decimal(0), ge=0, max_digits=16, decimal_places=2)
    strip_comments: bool = False
    order_tab: str | None = Field(default=None, max_length=200,
                                  description="Tab trong file order — bỏ trống để tự chọn")

    @field_validator("channel", "entity")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    @field_validator("group_code", "contract_no", "mapping_code", "order_tab")
    @classmethod
    def _blank_none(cls, v: str | None) -> str | None:
        return v or None

    @field_validator("brand_codes")
    @classmethod
    def _codes(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        codes = sorted({c.strip().upper() for c in v if c.strip()})
        if not codes:
            raise ValueError("brand_codes rỗng")
        return codes

    @field_validator("period_months")
    @classmethod
    def _months(cls, v: list[str]) -> list[str]:
        try:
            months = sorted({YearMonth.of(m) for m in v})
        except NumberingError as exc:
            raise ValueError(str(exc)) from exc
        return [m.label for m in months]

    @field_validator("order_sheet_url")
    @classmethod
    def _sheet(cls, v: str) -> str:
        parse_sheet_url(v)  # ValueError -> 422
        return v

    @model_validator(mode="after")
    def _scope(self) -> BbntRequest:
        if self.group_code:
            self.group_code = self.group_code.upper()
        if (self.brand_codes is None) == (self.group_code is None):
            raise ValueError("Chọn đúng 1 trong brand_codes hoặc group_code")
        return self

    def to_input(self) -> BbntInput:
        return BbntInput(channel=self.channel, entity=self.entity, period_months=self.period_months,
                         campaign_name=self.campaign_name, brand_codes=self.brand_codes, group_code=self.group_code,
                         contract_no=self.contract_no, mapping_code=self.mapping_code,
                         collected_on_behalf=self.collected_on_behalf, consulting_fee=self.consulting_fee,
                         strip_comments=self.strip_comments, order_tab=self.order_tab)


class IssueOut(BaseModel):
    row_number: int
    code: str
    severity: str
    message: str
    order_code: str | None = None
    amount: Decimal | None = None


class FiguresOut(BaseModel):
    gmv_estimated: Decimal
    nmv: Decimal
    cancellation_rate: Decimal | None
    commission_rate: Decimal
    commission_amount: Decimal
    tax_mode: str
    tax_rate: Decimal
    tax_label: str
    vat_amount: Decimal
    tax_deduction_amount: Decimal
    collected_on_behalf: Decimal
    consulting_fee: Decimal
    total_payable: Decimal
    total_payable_words: str


class ContractOut(BaseModel):
    contract_no: str
    scope: str
    channel: str | None
    model: str
    commission_rate: Decimal
    valid_from: date
    valid_to: date | None
    signed_on: date
    signed_on_source: str        # "Ngày ký" | "Hiệu lực từ" (HOP_DONG chưa có cột Ngày ký)
    party_a_name: str
    master_row: int


class ItemOut(BaseModel):
    brand_name: str | None
    sku: str | None
    product_name: str
    quantity: int
    revenue: Decimal


class BrandBreakdownOut(BaseModel):
    """NMV riêng 1 brand trong group — chỉ có khi link order kiểu "1 tab/brand" (xem
    ``detect_brand_tabs``); commission vẫn tính trên tổng (``figures``), không tính riêng theo brand."""

    index: int
    brand_code: str
    brand_name: str
    order_tab: str
    gmv_estimated: Decimal
    nmv: Decimal
    cancellation_rate: Decimal | None
    items: list[ItemOut]


class PreviewResponse(BaseModel):
    bbnt_number: str
    file_name: str
    issue_date: date
    period_label: str
    program_name: str
    scope_code: str
    brands: list[str]
    mapping_code: str
    contract: ContractOut
    figures: FiguresOut
    items: list[ItemOut]
    brand_breakdown: list[BrandBreakdownOut] = []
    order_line_count: int
    other_brand_line_count: int
    order_tab: str | None = None
    order_tabs: list[str] = []
    order_file_kind: str = "google_sheet"
    order_header_row: int = 1
    warning_count: int
    warnings: list[IssueOut]


class ErrorOut(BaseModel):
    code: str
    message: str
    details: list[IssueOut] | dict[str, object] | None = None


# ----------------------------------------------------------------- master
class EntityOut(BaseModel):
    code: str
    number_code: str
    name: str
    tax_today: str | None


class BrandOut(BaseModel):
    channel: str
    code: str
    name: str
    group_code: str | None
    group_name: str | None


class GroupOut(BaseModel):
    channel: str
    code: str
    name: str
    brand_codes: list[str]


class ContractBriefOut(BaseModel):
    contract_no: str
    entity: str
    channel: str | None
    brand_code: str | None
    group_code: str | None
    model: str
    commission_rate: Decimal
    valid_from: date
    valid_to: date | None


class MappingOut(BaseModel):
    code: str
    channel: str
    sheet_name: str | None
    is_default: bool


class MasterIssueOut(BaseModel):
    tab: str
    row: int
    severity: str
    message: str


class MasterOut(BaseModel):
    loaded_at: datetime | None
    channels: list[str]
    entities: list[EntityOut]
    brands: list[BrandOut]
    groups: list[GroupOut]
    contracts: list[ContractBriefOut]
    mappings: list[MappingOut]
    error_count: int
    warning_count: int


class MasterCheckOut(BaseModel):
    loaded_at: datetime | None
    ok: bool
    counts: dict[str, int]
    issues: list[MasterIssueOut]
