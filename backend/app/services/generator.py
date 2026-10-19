"""
Tạo BBNT — KHÔNG lưu gì (stateless)::

    1. tra master   : pháp nhân, brand/group, hợp đồng hiệu lực trọn kỳ, thuế tại ngày lập, mapping, template
    2. đọc order    : link Google Sheet do người dùng dán -> parse theo mapping -> lọc brand
    3. tính         : NMV chỉ đơn "Đã giao" -> hoa hồng -> VAT (EZS) / khấu trừ (SASSY)
    4. số BBNT      : tất định theo brand/pháp nhân/kênh/kỳ + tháng lập (giờ VN) -> làm lại ra đúng số cũ
    5. render .docx : trả thẳng về trình duyệt, server không giữ bản nào

Cần làm lại -> dán lại link order. Cần sửa câu chữ -> người làm tự sửa trên file .docx đã tải.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from app.core.calculation import (
    CommissionResult,
    MappingConfig,
    MappingError,
    NmvResult,
    OrderLine,
    ParseResult,
    RowIssue,
    Severity,
    TargetField,
    TaxMode,
    TaxPolicy,
    calculate_commission,
    compute_nmv,
    normalize_text,
    parse_sheet,
)
from app.core.numbering import BbntNumber, BrandRef, NumberingError, YearMonth, generate_bbnt_number, normalize_months
from app.docx.filler import BrandBlock, LegalTextModifiedError, MissingValueError
from app.docx.vn_text import format_percent
from app.errors import fail
from app.master.index import Scope, find_contract, find_mapping, resolve_scope
from app.master.models import Contract, Entity, MasterData, OrderMapping
from app.services.bbnt_document import BrandBreakdown, build_render_context, program_name
from app.services.order_tabs import TabChoice, choose_order_tab, detect_brand_tabs
from app.services.sheets import OrderWorkbook
from app.services.templates import TemplateRegistry

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
VND = Decimal(1)
MAX_ISSUES_IN_ERROR = 200

#: Mở file order (Google Sheet hoặc .xlsx trên Drive) -> danh sách tab + cách đọc.
OrderOpener = Callable[[], OrderWorkbook]


@dataclass(frozen=True)
class BbntInput:
    channel: str
    entity: str
    period_months: Sequence[str]
    campaign_name: str
    brand_codes: Sequence[str] | None = None
    group_code: str | None = None
    contract_no: str | None = None
    mapping_code: str | None = None
    collected_on_behalf: Decimal = Decimal(0)
    consulting_fee: Decimal = Decimal(0)
    strip_comments: bool = False
    order_tab: str | None = None


@dataclass
class BbntResult:
    number: BbntNumber
    entity: Entity
    scope: Scope
    contract: Contract
    mapping: OrderMapping
    tax: TaxPolicy
    parse: ParseResult
    lines: list[OrderLine]
    other_brand_lines: int
    nmv: NmvResult
    gmv_vnd: Decimal
    nmv_vnd: Decimal
    commission: CommissionResult
    program: str
    order: TabChoice | None = None
    order_kind: str = "google_sheet"
    values: dict[str, str] = field(default_factory=dict)
    content: bytes | None = None
    brand_breakdown: list[BrandBreakdown] = field(default_factory=list)

    @property
    def warnings(self) -> list[RowIssue]:
        return sorted(self.parse.issues + self.nmv.issues, key=lambda i: i.row_number)


class BbntGenerator:
    def __init__(self, templates: TemplateRegistry,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.templates, self.clock = templates, clock

    def run(self, master: MasterData, inp: BbntInput, open_order: OrderOpener, *, render: bool = True) -> BbntResult:
        channel, entity_code = inp.channel.strip().upper(), inp.entity.strip().upper()
        issued_at = self.clock()
        try:
            months = normalize_months(inp.period_months)
        except NumberingError as exc:
            raise fail("INVALID_INPUT", str(exc)) from exc

        # 1. master
        entity = master.entities.get(entity_code)
        if entity is None:
            raise fail("ENTITY_NOT_FOUND", f"Pháp nhân {entity_code} không có trong tab PHAP_NHAN")
        scope = resolve_scope(master, channel, inp.brand_codes, inp.group_code)
        number = self._number(entity, scope, months, issued_at)
        assert number.issue_date is not None
        contract = find_contract(master, entity_code, scope, months, inp.contract_no)
        tax = entity.tax_on(number.issue_date)
        if tax is None:
            raise fail("TAX_RULE_MISSING", f"Tab PHAP_NHAN chưa có thuế của {entity_code} hiệu lực ngày "
                                           f"{number.issue_date:%d/%m/%Y}")
        if contract.tax_rate_override is not None:
            tax = _apply_contract_tax_override(tax, contract)
        mapping = find_mapping(master, channel, inp.mapping_code)
        renderer = self.templates.renderer(entity_code, contract.model, channel) if render else None

        # 2. order: mở file, chọn tab(s), parse
        config = mapping.to_config()
        book = open_order()
        brand_tabs = None if inp.order_tab else detect_brand_tabs(book, config, scope=scope)
        brand_breakdown: list[BrandBreakdown] = []
        if brand_tabs:
            parsed, lines, other, brand_breakdown = self._read_brand_tabs(book, config, mapping, scope,
                                                                          brand_tabs, months)
            choice = None
        else:
            choice = choose_order_tab(book, config, mapping_code=mapping.code, scope=scope,
                                      campaign=inp.campaign_name, months=months, requested=inp.order_tab)
            try:
                parsed = parse_sheet(book.read(choice.tab), config)
            except MappingError as exc:
                raise fail("MAPPING_MISMATCH", f"Tab '{choice.tab}' không khớp mapping {mapping.code}: {exc}",
                           details={"tabs": choice.candidates}) from exc
            lines, other = _filter_brands(parsed, mapping, scope)

        # 3. tính
        nmv = compute_nmv(lines, months)
        issues = sorted(parsed.issues + nmv.issues, key=lambda i: i.row_number)
        errors = [i for i in issues if i.severity is Severity.ERROR]
        if errors:
            raise fail("SHEET_DATA_ERRORS", f"File order có {len(errors)} dòng lỗi — sửa file rồi chạy lại",
                       details=errors[:MAX_ISSUES_IN_ERROR])
        gmv_vnd = nmv.gmv_estimated.quantize(VND, ROUND_HALF_UP)
        nmv_vnd = nmv.nmv.quantize(VND, ROUND_HALF_UP)
        if nmv_vnd <= 0:
            hint = f" ({other} dòng thuộc brand khác bị loại)" if other else ""
            if choice is not None and len(choice.candidates) > 1:
                hint += f" — đang đọc tab '{choice.tab}', chọn tab khác nếu sai"
            raise fail("NO_DELIVERED_ORDERS", f"Không có đơn 'Đã giao' nào trong kỳ nghiệm thu{hint}",
                       details=issues[:50])
        commission = calculate_commission(nmv_vnd, contract.commission_rate, tax,
                                          collected_on_behalf=inp.collected_on_behalf,
                                          consulting_fee=inp.consulting_fee, quantum=VND)

        # 4-5. số + render
        program = program_name(scope.display, inp.campaign_name, number)
        values, item_rows, brand_blocks = build_render_context(
            number=number, program=program, contract_no=contract.contract_no,
            contract_signed_on=contract.signed_on or contract.valid_from,
            appendix_no=contract.appendix_no, appendix_signed_on=contract.appendix_signed_on,
            party_a=contract.party_a, gmv_estimated=gmv_vnd, nmv=nmv_vnd, cancellation_rate=nmv.cancellation_rate,
            commission=commission, items=nmv.items, brand_breakdown=brand_breakdown)
        result = BbntResult(number, entity, scope, contract, mapping, tax, parsed, lines, other, nmv,
                            gmv_vnd, nmv_vnd, commission, program, choice, book.kind, values,
                            brand_breakdown=brand_breakdown)
        if renderer is not None:
            try:
                brands = [BrandBlock(b["values"], b["items"]) for b in brand_blocks]
                result.content = renderer.render(values, item_rows, brands=brands,
                                                 strip_comments=inp.strip_comments).content
            except (MissingValueError, LegalTextModifiedError) as exc:
                raise fail("RENDER_FAILED", f"Không điền được template: {exc}", status=500) from exc
        return result

    @staticmethod
    def _read_brand_tabs(book: OrderWorkbook, config: MappingConfig, mapping: OrderMapping, scope: Scope,
                         brand_tabs: dict[str, list[str]], months: tuple[YearMonth, ...],
                         ) -> tuple[ParseResult, list[OrderLine], int, list[BrandBreakdown]]:
        """Đọc RIÊNG (1 hoặc nhiều) tab cho mỗi brand (xem :func:`order_tabs.detect_brand_tabs` — 1 brand có
        thể tách nhiều tab theo loại sản phẩm, vd "BIO CORNER ORDER MIX"/"BIO CORNER ORDER FOOD" cùng 1 brand
        thì gộp dòng lại), gộp tất cả lại thành 1 ``ParseResult``/danh sách dòng chung (dùng tính NMV tổng +
        hoa hồng như cũ), đồng thời tính NMV RIÊNG từng brand (:class:`BrandBreakdown`, đã gộp mọi tab của
        brand đó) để hiển thị từng khối trong file .docx. Brand trong group không có tab nào trong file order
        kỳ này -> bỏ qua, không lỗi."""
        all_lines: list[OrderLine] = []
        all_issues: list[RowIssue] = []
        rows_read = 0
        header_row = 1
        other = 0
        breakdown: list[BrandBreakdown] = []
        tried_tabs: list[str] = []
        for idx, brand in enumerate(scope.brands, start=1):
            tabs = brand_tabs.get(brand.code)
            if not tabs:
                continue
            tried_tabs += tabs
            brand_lines: list[OrderLine] = []
            for tab in tabs:
                try:
                    parsed_b = parse_sheet(book.read(tab), config)
                except MappingError as exc:
                    raise fail("MAPPING_MISMATCH", f"Tab '{tab}' (brand {brand.code}) không khớp mapping "
                                                   f"{mapping.code}: {exc}", details={"tabs": [tab]}) from exc
                lines_b, other_b = _filter_brands(parsed_b, mapping, Scope(scope.channel, (brand,), None, None))
                brand_lines += lines_b
                all_issues += parsed_b.issues
                rows_read += parsed_b.rows_read
                header_row = parsed_b.header_row
                other += other_b
            if not brand_lines:
                continue
            tagged = [replace(ln, brand_name=ln.brand_name or brand.name) for ln in brand_lines]
            all_lines += tagged
            brand_nmv = compute_nmv(tagged, months)
            breakdown.append(BrandBreakdown(idx, brand.code, brand.name, tuple(tabs), brand_nmv))
        if not all_lines:
            raise fail("NO_DELIVERED_ORDERS", "Không brand nào trong group có đơn trong file order kỳ này "
                                              f"(đã thử tab: {', '.join(tried_tabs)})")
        parsed = ParseResult(tuple(all_lines), tuple(sorted(all_issues, key=lambda i: i.row_number)),
                             rows_read, header_row)
        return parsed, all_lines, other, breakdown

    @staticmethod
    def _number(entity: Entity, scope: Scope, months: tuple[YearMonth, ...], issued_at: datetime) -> BbntNumber:
        try:
            return generate_bbnt_number(brands=[BrandRef(scope.code)], entity=entity.number_code,
                                        channel=scope.channel, period_months=months, issued_at=issued_at)
        except NumberingError as exc:
            raise fail("INVALID_INPUT", str(exc)) from exc


def _apply_contract_tax_override(tax: TaxPolicy, contract: Contract) -> TaxPolicy:
    """Ghi đè % thuế (không đổi loại thuế) theo ``contract.tax_rate_override`` — xem cột "Thuế suất riêng"
    trong tab HOP_DONG. Dùng khi VAT/khấu trừ thuế (vd theo NQ 204/2025/QH15) khác nhau theo từng brand/hợp
    đồng ngay trong cùng 1 pháp nhân; KHÔNG được lấy 1 con số cố định dùng chung cho mọi brand."""
    assert contract.tax_rate_override is not None
    kind = "VAT" if tax.mode is TaxMode.VAT_ADD else "Khấu trừ thuế" if tax.mode is TaxMode.TAX_DEDUCT else tax.label
    label = f"{kind} {format_percent(contract.tax_rate_override)} (riêng theo HĐ {contract.contract_no})"
    return replace(tax, rate=contract.tax_rate_override, label=label)


def _filter_brands(parsed: ParseResult, mapping: OrderMapping, scope: Scope) -> tuple[list[OrderLine], int]:
    """File order dùng chung nhiều brand: nếu mapping có cột BRAND_NAME thì chỉ giữ dòng của brand đã chọn
    (so khớp với Mã brand hoặc Tên brand, không phân biệt hoa thường/dấu)."""
    if not any(f.target is TargetField.BRAND_NAME for f in mapping.fields):
        return list(parsed.lines), 0
    if not any(ln.brand_name for ln in parsed.lines):
        # cột brand (tuỳ chọn) không có trong file / trống hết -> coi là file của 1 brand, không lọc
        return list(parsed.lines), 0
    allowed = {normalize_text(x) for b in scope.brands for x in (b.code, b.name)}
    keep = [ln for ln in parsed.lines if normalize_text(ln.brand_name) in allowed]
    return keep, len(parsed.lines) - len(keep)
