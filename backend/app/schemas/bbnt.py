"""Pydantic schemas cho API (không có id/DB — mọi thứ tham chiếu bằng mã trong Sheet master)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.numbering import NumberingError, YearMonth
from app.services.generator import BREAKDOWN_MODES, TAB_MODES, BbntInput, CampLabel
from app.services.sheets import parse_sheet_url

#: "auto" (mặc định, hành vi cũ) | "combine" (gộp mọi tab/camp ứng viên thành 1 BBNT) | "split" (mỗi tab 1 BBNT
#: riêng — CHỈ hợp lệ ở ``POST /bbnt/preview-split``, xem docstring đầu ``app.services.generator``).
TabMode = Literal["auto", "combine", "split"]

#: None (mặc định, hành vi cũ — tự nhận diện link "1 tab/brand" nếu có) | "brand" (tách bảng doanh thu/huỷ theo
#: brand, commission chung) | "camp" (tách theo camp/đợt chạy, bắt buộc kèm ``camp_names``) — CHỈ dùng khi
#: nghiệm thu group/nhiều brand cùng lúc, xem docstring đầu ``app.services.generator``.
BreakdownMode = Literal["brand", "camp"]


class CampLabelIn(BaseModel):
    """1 camp = 1 tab trong 1 link order cụ thể. ``link_index``: 0 = ``order_sheet_url`` chính, 1.. = vị trí
    (1-based) trong ``extra_order_sheet_urls``."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    link_index: int = Field(ge=0, le=19)
    tab: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=100)


class BbntRequest(BaseModel):
    """Payload chung cho ``/bbnt/preview``, ``/bbnt/preview-split`` và ``/bbnt/generate``.

    Chọn đúng 1 trong ``brand_codes`` (1 brand, hoặc nhiều brand cùng group -> số BBNT dùng group code)
    hoặc ``group_code`` (gộp cả group). ``contract_no`` chỉ cần khi có nhiều hợp đồng cùng hiệu lực.

    ``extra_order_sheet_urls`` (tuỳ chọn): dán THÊM link order khác (ngoài ``order_sheet_url``) khi dữ liệu 1
    kỳ nghiệm thu nằm rải trên nhiều file/link — mọi dòng đơn hàng từ các link được gộp lại tính chung 1 BBNT.

    ``tab_mode`` xử lý khi file order có NHIỀU tab là nhiều CAMP/đợt chạy khác nhau của CÙNG 1 brand/scope
    (khác với link "1 tab/brand" — brand khác nhau luôn tự tách khối, không cần chọn): ``"auto"`` giữ hành vi
    cũ (1 tab khớp rõ thì dùng, nhiều tab mơ hồ thì báo lỗi ``ORDER_TAB_AMBIGUOUS`` để chọn qua ``order_tab``);
    ``"combine"`` gộp tất cả tab ứng viên vào 1 BBNT; ``"split"`` CHỈ dùng ở ``/bbnt/preview-split`` để xem
    trước MỖI tab thành 1 BBNT riêng (bắt buộc kèm ``tab_campaign_names``).

    ``breakdown_mode`` (CHỈ dùng khi nghiệm thu group hoặc nhiều brand cùng lúc, và ``tab_mode == "auto"``,
    không chọn sẵn ``order_tab``): gộp mọi tab/link thành 1 BBNT DUY NHẤT nhưng TÁCH RIÊNG bảng doanh thu ước
    tính/doanh thu thực nhận/tỷ lệ huỷ theo 1 trong 2 chiều (KHÔNG BAO GIỜ cả 2 cùng lúc) — ``"brand"`` tách
    theo brand (dùng tab riêng từng brand nếu có sẵn, không thì nhóm theo cột Brand); ``"camp"`` tách theo
    camp/đợt chạy (mỗi tab ở mỗi link là 1 camp, bắt buộc đặt tên riêng qua ``camp_names``).
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
    extra_order_sheet_urls: list[str] = Field(default_factory=list, max_length=19,
                                              description="Link order BỔ SUNG để gộp chung 1 BBNT")
    mapping_code: str | None = Field(default=None, max_length=50)
    collected_on_behalf: Decimal = Field(default=Decimal(0), ge=0, max_digits=16, decimal_places=2)
    consulting_fee: Decimal = Field(default=Decimal(0), ge=0, max_digits=16, decimal_places=2)
    strip_comments: bool = False
    order_tab: str | None = Field(default=None, max_length=200,
                                  description="Tab trong file order — bỏ trống để tự chọn")
    tab_mode: TabMode = Field(default="auto", description="auto | combine | split (xem docstring lớp này)")
    tab_campaign_names: dict[str, str] | None = Field(
        default=None, description="Bắt buộc khi tab_mode='split': {tên tab: tên chương trình riêng}")
    breakdown_mode: BreakdownMode | None = Field(
        default=None, description="brand | camp — chỉ dùng khi nghiệm thu group/nhiều brand (xem docstring lớp này)")
    camp_names: list[CampLabelIn] = Field(
        default_factory=list, max_length=20,
        description="Bắt buộc khi breakdown_mode='camp': tên camp riêng cho từng (link_index, tab)")

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

    @field_validator("extra_order_sheet_urls")
    @classmethod
    def _extra_sheets(cls, v: list[str]) -> list[str]:
        urls = [u.strip() for u in v if u.strip()]
        for u in urls:
            parse_sheet_url(u)  # ValueError -> 422
        return urls

    @field_validator("tab_campaign_names")
    @classmethod
    def _tab_names(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        if v is None:
            return None
        cleaned = {k.strip(): val.strip() for k, val in v.items() if k.strip() and val.strip()}
        return cleaned or None

    @model_validator(mode="after")
    def _scope(self) -> BbntRequest:
        if self.group_code:
            self.group_code = self.group_code.upper()
        if (self.brand_codes is None) == (self.group_code is None):
            raise ValueError("Chọn đúng 1 trong brand_codes hoặc group_code")
        if self.tab_mode not in TAB_MODES:
            raise ValueError(f"tab_mode phải là 1 trong {TAB_MODES}")
        if self.breakdown_mode is not None:
            if self.breakdown_mode not in BREAKDOWN_MODES:
                raise ValueError(f"breakdown_mode phải là 1 trong {BREAKDOWN_MODES}")
            if not self.group_code and len(self.brand_codes or ()) < 2:
                raise ValueError("breakdown_mode chỉ dùng khi nghiệm thu group hoặc từ 2 brand cùng lúc trở lên")
        return self

    def to_input(self) -> BbntInput:
        camp_names = [CampLabel(c.link_index, c.tab, c.name) for c in self.camp_names] or None
        return BbntInput(channel=self.channel, entity=self.entity, period_months=self.period_months,
                         campaign_name=self.campaign_name, brand_codes=self.brand_codes, group_code=self.group_code,
                         contract_no=self.contract_no, mapping_code=self.mapping_code,
                         collected_on_behalf=self.collected_on_behalf, consulting_fee=self.consulting_fee,
                         strip_comments=self.strip_comments, order_tab=self.order_tab, tab_mode=self.tab_mode,
                         tab_campaign_names=self.tab_campaign_names, breakdown_mode=self.breakdown_mode,
                         camp_names=camp_names)


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
    appendix_no: str | None = None
    appendix_signed_on: date | None = None
    party_a_name: str
    master_row: int


class ItemOut(BaseModel):
    brand_name: str | None
    sku: str | None
    product_name: str
    quantity: int
    revenue: Decimal


class BrandBreakdownOut(BaseModel):
    """NMV riêng 1 khối (brand hoặc camp, xem ``breakdown_kind`` ở ``PreviewResponse``) trong BBNT gộp —
    commission vẫn tính trên tổng (``figures``), không tính riêng theo khối. ``brand_code`` có giá trị khi
    ``breakdown_kind == "brand"``; ``None`` khi ``breakdown_kind == "camp"`` (``brand_name`` khi đó là tên camp
    người dùng đặt). ``order_tabs`` có thể rỗng khi tách theo brand qua cột Brand (không theo tab riêng)."""

    index: int
    brand_code: str | None = None
    brand_name: str
    order_tabs: list[str]
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
    campaign_name: str = ""
    scope_code: str
    brands: list[str]
    mapping_code: str
    contract: ContractOut
    figures: FiguresOut
    items: list[ItemOut]
    #: "none" (không tách bảng) | "brand" | "camp" — chiều tách của ``brand_breakdown`` (xem
    #: ``BbntRequest.breakdown_mode``; "brand" cũng dùng cho link tự nhận diện "1 tab/brand" như trước đây).
    breakdown_kind: Literal["none", "brand", "camp"] = "none"
    brand_breakdown: list[BrandBreakdownOut] = []
    order_line_count: int
    other_brand_line_count: int
    order_tab: str | None = None
    order_tabs: list[str] = []
    #: Tên các tab đã GỘP lại thành BBNT này (``tab_mode == "combine"``, >= 2 tab) — xem docstring
    #: ``app.services.generator``. Rỗng khi không gộp.
    order_tabs_combined: list[str] = []
    order_file_kind: str = "google_sheet"
    order_header_row: int = 1
    warning_count: int
    warnings: list[IssueOut]


class SplitPreviewResponse(BaseModel):
    """Kết quả ``POST /bbnt/preview-split`` (``tab_mode == "split"``) — mỗi tab ứng viên trong file order
    thành 1 BBNT riêng (xem :meth:`app.services.generator.BbntGenerator.run_split`)."""

    results: list[PreviewResponse]


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
