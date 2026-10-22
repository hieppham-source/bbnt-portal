"""
Tạo BBNT — KHÔNG lưu gì (stateless)::

    1. tra master   : pháp nhân, brand/group, hợp đồng do người làm TỰ CHỌN, thuế tại ngày lập, mapping, template
    2. đọc order    : link Google Sheet do người dùng dán -> parse theo mapping -> lọc brand -> TOÀN BỘ dòng
                      (không giới hạn theo tháng/kỳ nghiệm thu nào — xem ``BbntInput.bbnt_suffix``)
    3. tính         : NMV chỉ đơn "Đã giao" -> hoa hồng -> VAT (EZS) / khấu trừ (SASSY)
    4. số BBNT      : tất định theo brand/pháp nhân/kênh/đuôi + tháng lập (giờ VN) -> làm lại (dán lại link,
                      gõ lại đúng đuôi cũ) ra đúng số cũ
    5. render .docx : trả thẳng về trình duyệt, server không giữ bản nào

Cần làm lại -> dán lại link order. Cần sửa câu chữ -> người làm tự sửa trên file .docx đã tải.

``inp.bbnt_suffix``: "đuôi biên bản nghiệm thu" người làm TỰ GÕ (vd "BDAY 08"), ráp thẳng vào số BBNT (xem
``app.core.numbering``) — KHÔNG còn suy ra từ kỳ nghiệm thu, vì link order giờ mặc định tính TOÀN BỘ dòng đơn
có trong link (không lọc theo tháng nữa). ``inp.contract_no`` vì vậy cũng LUÔN bắt buộc — hợp đồng/% hoa hồng
áp dụng do người làm tự chọn (không còn tự suy luận theo kỳ nghiệm thu).

``inp.manual_contract`` (mặc định ``False``): hợp đồng CHƯA kịp cập nhật vào Sheet master — bỏ qua tra cứu,
dùng thẳng ``contract_no`` người làm tự gõ; Bên A/% hoa hồng/VAT/ngày ký đều để PLACEHOLDER (hoa hồng/VAT tính
ra = 0) — người làm PHẢI tự điền tay trên file .docx đã tải trước khi dùng để thanh toán (xem
:func:`_manual_contract`).

``inp.tab_mode`` (khi file order có NHIỀU tab/camp — vd nhiều đợt chạy campaign khác nhau cùng 1 brand/scope —
KHÁC với "1 tab/brand" của :func:`app.services.order_tabs.detect_brand_tabs`, vốn luôn tự gộp, không cần
chọn):

* ``"auto"`` (mặc định) — 1 tab khớp rõ -> dùng luôn; nhiều tab mơ hồ -> báo ``ORDER_TAB_AMBIGUOUS`` để người
  dùng tự chọn 1 tab (``order_tab``) hoặc gộp qua ``breakdown_mode`` (xem bên dưới).
* ``"split"`` — mỗi tab ứng viên 1 BBNT riêng (CHỈ dùng cho xem trước, gọi :meth:`BbntGenerator.run_split`) —
  bắt buộc có ``tab_campaign_names`` (tên chương trình RIÊNG cho từng tab, vì số BBNT không phân biệt theo tên
  chương trình — xem ``app.core.numbering``); các BBNT tách ra được đánh thêm hậu tố ``-R{n}`` để không trùng số.

``inp.breakdown_mode`` (không chọn sẵn ``order_tab``): gộp TOÀN BỘ dữ liệu (mọi tab, mọi link order) thành 1
BBNT DUY NHẤT (1 số, 1 commission) nhưng TÁCH RIÊNG bảng doanh thu ước tính/doanh thu thực nhận/tỷ lệ huỷ VÀ
danh sách sản phẩm (kèm tổng kết SL + doanh thu) theo 1 trong 2 chiều — KHÔNG BAO GIỜ tách cả 2 chiều cùng lúc.
Đây là lựa chọn THAY THẾ cho việc "gộp tất cả tab lại không tách gì" (không còn tồn tại riêng — gộp nhiều
tab/camp giờ LUÔN đi kèm 1 trong 2 cách tách bảng này):

* ``None`` (mặc định) — tự nhận diện link kiểu "1 tab/brand" (:func:`detect_brand_tabs`) nếu có (vẫn tách theo
  brand như trước), không thì không tách bảng gì cả (file 1 tab rõ ràng, không mơ hồ).
* ``"brand"`` — tách theo BRAND: CHỈ dùng khi nghiệm thu group hoặc từ 2 brand cùng lúc trở lên (1 brand không
  có chiều brand để tách). Dùng tab riêng từng brand nếu link order đã tách sẵn (giống hệt tự nhận diện cũ);
  nếu link KHÔNG tách sẵn theo tab (1 tab chung, nhiều brand lẫn trong cột Brand) thì đọc hết mọi tab ứng viên
  rồi nhóm lại theo cột Brand (xem :func:`_group_lines_by_brand`).
* ``"camp"`` — tách theo CAMP/đợt chạy: dùng được cho MỌI phạm vi (kể cả 1 brand — đây là lựa chọn khi 1 brand
  có file nhiều tab/camp mơ hồ). Mỗi tab (ở mỗi link order) là 1 camp — BẮT BUỘC đặt tên riêng cho TỪNG camp
  qua ``camp_names`` (không tự lấy theo tên tab/sheet nữa, xem :meth:`BbntGenerator._read_camp_breakdown`).

``breakdown_kind == "camp"`` cũng ẩn bảng TỔNG HỢP sản phẩm top-level (bảng gộp toàn bộ sản phẩm mọi camp) khỏi
file .docx xuất ra — CHỈ còn các bảng riêng từng camp (có tên camp làm tiêu đề, xem ``{{brand_section_start}}``
trong ``app.docx.filler``), tránh lặp lại 1 bảng tổng không cần thiết; bảng doanh thu ước tính (A)/thực nhận
(B)/tỷ lệ huỷ VÀ bảng hoa hồng ở cuối vẫn luôn hiển thị như cũ (tính trên TỔNG, không phải riêng từng camp).
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

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
from app.core.numbering import BbntNumber, BrandRef, NumberingError, generate_bbnt_number
from app.docx.filler import BrandBlock, LegalTextModifiedError, MissingValueError
from app.docx.vn_text import format_percent
from app.errors import fail
from app.master.index import Scope, find_contract, find_mapping, resolve_scope
from app.master.models import Contract, Entity, MasterData, OrderMapping
from app.services.bbnt_document import BrandBreakdown, PartyA, build_render_context, program_name
from app.services.order_tabs import TabChoice, choose_order_tab, detect_brand_tabs, list_candidate_tabs
from app.services.sheets import OrderWorkbook
from app.services.templates import TemplateRegistry

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
VND = Decimal(1)
MAX_ISSUES_IN_ERROR = 200

#: Mở (các) file order (Google Sheet hoặc .xlsx trên Drive) -> 1 :class:`OrderWorkbook` cho MỖI link (link
#: chính + mọi ``extra_order_sheet_urls``, KHÔNG gộp tab — mỗi book tự chọn tab riêng, xem
#: :meth:`BbntGenerator._resolve_book_tabs`, để tránh 1 tab cùng tên ở 2 link bị hiểu nhầm là trùng nhau).
OrderOpener = Callable[[], list[OrderWorkbook]]

#: "combine" đã bỏ — gộp nhiều tab/camp giờ LUÔN đi kèm ``breakdown_mode`` (xem docstring đầu module).
TAB_MODES = ("auto", "split")
BREAKDOWN_MODES = ("brand", "camp")


@dataclass(frozen=True)
class CampLabel:
    """1 camp = 1 tab trong 1 link order cụ thể — ``link_index`` 0 là ``order_sheet_url`` chính, 1.. là vị trí
    (1-based) trong ``extra_order_sheet_urls`` (vd 1 = phần tử đầu). Cần cả ``link_index`` lẫn ``tab`` vì tên
    tab có thể trùng giữa 2 link khác nhau (vd cả 2 link đều có tab 'ORDER')."""

    link_index: int
    tab: str
    name: str


@dataclass(frozen=True)
class BbntInput:
    channel: str
    entity: str
    #: "Đuôi biên bản nghiệm thu" người làm TỰ GÕ — ráp thẳng vào số BBNT, xem docstring đầu module +
    #: ``app.core.numbering.normalize_suffix``. KHÔNG còn là kỳ nghiệm thu — link order mặc định tính TOÀN BỘ
    #: dòng đơn, không lọc theo tháng.
    bbnt_suffix: str
    campaign_name: str
    #: LUÔN bắt buộc (không còn tự suy luận theo kỳ nghiệm thu) — xem ``app.master.index.find_contract``.
    contract_no: str
    brand_codes: Sequence[str] | None = None
    group_code: str | None = None
    mapping_code: str | None = None
    collected_on_behalf: Decimal = Decimal(0)
    consulting_fee: Decimal = Decimal(0)
    strip_comments: bool = False
    order_tab: str | None = None
    #: "auto" (mặc định) | "split" (mỗi tab ứng viên 1 BBNT riêng — chỉ hợp lệ qua
    #: :meth:`BbntGenerator.run_split`, xem docstring đầu module). "combine" đã bỏ.
    tab_mode: str = "auto"
    #: Bắt buộc khi ``tab_mode == "split"``: tên chương trình RIÊNG cho từng tab (key = tên tab).
    tab_campaign_names: Mapping[str, str] | None = None
    #: None (mặc định, hành vi cũ) | "brand" (CHỈ dùng khi ``scope.is_group``) | "camp" (mọi phạm vi) — không
    #: chọn sẵn ``order_tab``, xem docstring đầu module.
    breakdown_mode: str | None = None
    #: Bắt buộc khi ``breakdown_mode == "camp"``: tên camp riêng cho từng (link, tab).
    camp_names: Sequence[CampLabel] | None = None
    #: Hợp đồng CHƯA có trong Sheet master (chưa kịp cập nhật tab HOP_DONG) — bỏ qua tra cứu, dùng thẳng
    #: ``contract_no`` người làm tự gõ, mọi giá trị khác (Bên A, % hoa hồng, VAT, ngày ký) để PLACEHOLDER rõ
    #: ràng là chưa điền (hoa hồng/VAT tính ra = 0) — người làm PHẢI tự sửa tay trên file .docx đã tải trước khi
    #: dùng để thanh toán. Xem :func:`_manual_contract`.
    manual_contract: bool = False


#: ``BbntInput.manual_contract`` — mọi giá trị Bên A để placeholder RÕ RÀNG là chưa điền (không phải chuỗi
#: rỗng, để không bị nhầm là ô trống bình thường trong file .docx) — người làm tự gõ đè khi sửa tay.
_MANUAL_PLACEHOLDER = "(Tự điền sau khi tải BBNT)"
_MANUAL_PARTY_A = PartyA(
    legal_name=_MANUAL_PLACEHOLDER, address=_MANUAL_PLACEHOLDER, phone=_MANUAL_PLACEHOLDER,
    tax_code=_MANUAL_PLACEHOLDER, bank_account_no=_MANUAL_PLACEHOLDER, bank_name=_MANUAL_PLACEHOLDER,
    representative_name=_MANUAL_PLACEHOLDER, representative_title=_MANUAL_PLACEHOLDER)


def _manual_contract(entity_code: str, scope: Scope, contract_no: str, issue_date: date) -> Contract:
    """``BbntInput.manual_contract``: hợp đồng CHƯA có trong Sheet master — bỏ qua :func:`find_contract`, dùng
    thẳng ``contract_no`` người làm tự gõ. Model luôn ``"NON_OP"`` (template duy nhất có sẵn cho mỗi pháp nhân
    hiện nay — xem ``app.services.templates.DEFAULT_TEMPLATES``); % hoa hồng = 0 (hoa hồng/VAT tính ra = 0,
    KHÔNG dùng để thanh toán); Bên A = placeholder; ngày ký = ngày lập BBNT (chỉ để có 1 ngày hợp lệ điền vào
    câu "ký ngày ..." — người làm tự sửa lại cho đúng). Không ghi gì vào Sheet master."""
    return Contract(contract_no=contract_no, entity_code=entity_code, channel=scope.channel,
                     brand_code=None if scope.is_group else scope.brands[0].code, group_code=scope.group_code,
                     model="NON_OP", commission_rate=Decimal(0), valid_from=issue_date, valid_to=None,
                     party_a=_MANUAL_PARTY_A, active=True, row=0, signed_on=issue_date)


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
    #: Tên các tab đã GỘP lại (``tab_mode == "combine"``, >= 2 tab) — rỗng khi không gộp (1 tab bình thường,
    #: hoặc link kiểu "1 tab/brand" đã có ``brand_breakdown`` riêng).
    combined_tabs: list[str] = field(default_factory=list)
    #: Tên chương trình THỰC TẾ dùng cho BBNT này (giống ``BbntInput.campaign_name`` trừ khi tách theo
    #: ``tab_mode == "split"`` — mỗi BBNT tách ra có tên chương trình riêng, xem ``run_split``).
    campaign_name: str = ""
    #: "none" (không tách bảng) | "brand" | "camp" — chiều tách của ``brand_breakdown`` (xem
    #: ``BbntInput.breakdown_mode``; "brand" cũng áp dụng cho link tự nhận diện "1 tab/brand" như trước đây).
    breakdown_kind: Literal["none", "brand", "camp"] = "none"
    #: ``BbntInput.manual_contract`` — ``True`` thì ``contract`` ở trên là PLACEHOLDER (Bên A/hoa hồng/VAT/ngày
    #: ký đều chưa điền, hoa hồng/VAT = 0), KHÔNG dùng để thanh toán nếu chưa sửa tay trên file .docx đã tải.
    manual_contract: bool = False

    @property
    def warnings(self) -> list[RowIssue]:
        return sorted(self.parse.issues + self.nmv.issues, key=lambda i: i.row_number)


class BbntGenerator:
    def __init__(self, templates: TemplateRegistry,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.templates, self.clock = templates, clock

    def run(self, master: MasterData, inp: BbntInput, open_order: OrderOpener, *, render: bool = True) -> BbntResult:
        if inp.tab_mode == "split":
            raise fail("INVALID_INPUT", "tab_mode='split' chỉ dùng được qua xem trước tách BBNT theo tab "
                                        "(POST /bbnt/preview-split), không dùng để tạo file trực tiếp")
        issued_at = self.clock()
        books = open_order()
        return self._run_with_books(master, inp, books, issued_at, render=render)

    def run_split(self, master: MasterData, inp: BbntInput, open_order: OrderOpener) -> list[BbntResult]:
        """``tab_mode == 'split'``: liệt kê MỌI tab ứng viên của file order (chưa qua :func:`detect_brand_tabs`
        — link kiểu "1 tab/brand" không hỗ trợ tách theo camp, vì mỗi brand đã tự thành 1 khối riêng trong
        CÙNG 1 BBNT), mỗi tab tính thành 1 :class:`BbntResult` RIÊNG với tên chương trình lấy từ
        ``inp.tab_campaign_names[tab]`` (bắt buộc, phải khác nhau từng tab — số BBNT không phân biệt theo tên
        chương trình nên các BBNT tách ra được đánh thêm hậu tố ``-R{n}`` theo thứ tự tab để không trùng số).
        CHỈ dùng cho xem trước (không render .docx — tải file thì gọi lại :meth:`run` với ``order_tab`` đã chọn
        từ 1 trong các tab này, ``tab_mode`` để mặc định 'auto'). Chưa hỗ trợ cùng ``extra_order_sheet_urls``
        (nhiều link order) — chỉ 1 link."""
        if inp.tab_mode != "split":
            raise fail("INVALID_INPUT", "tab_mode phải là 'split' khi gọi preview tách BBNT theo tab")
        issued_at = self.clock()
        books = open_order()
        if len(books) != 1:
            raise fail("TAB_MODE_NOT_SUPPORTED", "Tách BBNT theo tab (tab_mode='split') chưa hỗ trợ cùng lúc "
                                                 "nhiều link order — chỉ dán 1 link")
        book = books[0]
        channel, entity_code = inp.channel.strip().upper(), inp.entity.strip().upper()
        entity = master.entities.get(entity_code)
        if entity is None:
            raise fail("ENTITY_NOT_FOUND", f"Pháp nhân {entity_code} không có trong tab PHAP_NHAN")
        scope = resolve_scope(master, channel, inp.brand_codes, inp.group_code)
        mapping = find_mapping(master, channel, inp.mapping_code)
        config = mapping.to_config()
        if not inp.order_tab and detect_brand_tabs(book, config, scope=scope):
            raise fail("TAB_MODE_NOT_SUPPORTED", "File order kiểu \"1 tab/brand\" không hỗ trợ tách theo "
                                                 "chương trình — mỗi brand đã tự tách khối riêng trong cùng "
                                                 "1 BBNT (xem lại ở chế độ xem trước thường)")
        candidates = list_candidate_tabs(book, config, mapping_code=mapping.code)
        names = dict(inp.tab_campaign_names or {})
        missing = [t for t in candidates if not (names.get(t) or "").strip()]
        if missing:
            raise fail("TAB_CAMPAIGN_NAMES_REQUIRED",
                       f"Tách BBNT theo tab cần đặt Tên chương trình riêng cho {len(missing)} tab chưa có tên "
                       "(mỗi tab 1 tên khác nhau)", status=409, details={"tabs": missing})
        seen: dict[str, str] = {}
        for t in candidates:
            key = normalize_text(names[t])
            if key in seen:
                raise fail("TAB_CAMPAIGN_NAMES_DUPLICATE",
                           f"Tên chương trình '{names[t].strip()}' trùng với tab '{seen[key]}' — mỗi tab tách "
                           "ra cần 1 Tên chương trình riêng để không bị trùng số BBNT",
                           details={"tabs": [seen[key], t]})
            seen[key] = t
        multi = len(candidates) > 1
        results: list[BbntResult] = []
        for rev, tab in enumerate(candidates, start=1):
            sub_inp = replace(inp, campaign_name=names[tab].strip(), order_tab=tab, tab_mode="auto",
                              tab_campaign_names=None)
            results.append(self._run_with_books(master, sub_inp, [book], issued_at, render=False,
                                                revision_no=rev if multi else 0))
        return results

    def _run_with_books(self, master: MasterData, inp: BbntInput, books: list[OrderWorkbook], issued_at: datetime,
                        *, render: bool, revision_no: int = 0) -> BbntResult:
        channel, entity_code = inp.channel.strip().upper(), inp.entity.strip().upper()
        if not (inp.contract_no or "").strip():
            raise fail("INVALID_INPUT", "Thiếu Số hợp đồng — hợp đồng/% hoa hồng áp dụng luôn do người làm "
                                        "tự chọn (không còn tự suy luận theo kỳ nghiệm thu)")

        # 1. master
        entity = master.entities.get(entity_code)
        if entity is None:
            raise fail("ENTITY_NOT_FOUND", f"Pháp nhân {entity_code} không có trong tab PHAP_NHAN")
        scope = resolve_scope(master, channel, inp.brand_codes, inp.group_code)
        number = self._number(entity, scope, inp.bbnt_suffix, issued_at, revision_no)
        assert number.issue_date is not None
        contract = (_manual_contract(entity_code, scope, inp.contract_no, number.issue_date)
                   if inp.manual_contract else find_contract(master, entity_code, scope, inp.contract_no))
        tax = entity.tax_on(number.issue_date)
        if tax is None:
            raise fail("TAX_RULE_MISSING", f"Tab PHAP_NHAN chưa có thuế của {entity_code} hiệu lực ngày "
                                           f"{number.issue_date:%d/%m/%Y}")
        if contract.tax_rate_override is not None:
            tax = _apply_contract_tax_override(tax, contract)
        mapping = find_mapping(master, channel, inp.mapping_code)
        renderer = self.templates.renderer(entity_code, contract.model, channel) if render else None

        # 2. order: mở (các) file, chọn tab(s), parse — xem docstring đầu module cho ý nghĩa tab_mode/breakdown_mode.
        config = mapping.to_config()
        brand_breakdown: list[BrandBreakdown] = []
        combined_tabs: list[str] = []
        choice: TabChoice | None = None
        breakdown_kind: Literal["none", "brand", "camp"] = "none"

        if inp.breakdown_mode is not None:
            if inp.breakdown_mode == "brand" and not scope.is_group:
                raise fail("BREAKDOWN_MODE_NOT_SUPPORTED", "Tách theo brand chỉ dùng khi nghiệm thu group hoặc "
                                                            "từ 2 brand cùng lúc trở lên — 1 brand hãy tách "
                                                            "theo camp")
            if inp.order_tab:
                raise fail("BREAKDOWN_MODE_NOT_SUPPORTED", "breakdown_mode không dùng cùng order_tab")
            if inp.breakdown_mode == "camp":
                parsed, lines, other, brand_breakdown, combined_tabs = self._read_camp_breakdown(
                    books, config, mapping, scope, inp)
                breakdown_kind = "camp"
            else:
                brand_tabs = detect_brand_tabs(books[0], config, scope=scope) if len(books) == 1 else None
                if brand_tabs:
                    parsed, lines, other, brand_breakdown = self._read_brand_tabs(books[0], config, mapping,
                                                                                  scope, brand_tabs)
                else:
                    parsed, lines, other, combined_tabs = self._read_all_candidate_tabs(books, config, mapping,
                                                                                        scope)
                    brand_breakdown = _group_lines_by_brand(lines, scope)
                breakdown_kind = "brand"
        else:
            brand_tabs = None if (inp.order_tab or len(books) > 1) else detect_brand_tabs(books[0], config, scope=scope)
            if brand_tabs:
                parsed, lines, other, brand_breakdown = self._read_brand_tabs(books[0], config, mapping, scope,
                                                                              brand_tabs)
                breakdown_kind = "brand"
            else:
                if len(books) > 1 and not inp.order_tab and any(detect_brand_tabs(b, config, scope=scope)
                                                                 for b in books):
                    raise fail("TAB_MODE_NOT_SUPPORTED", "Nhiều link order (extra_order_sheet_urls) chưa hỗ trợ "
                                                         "cùng file kiểu \"1 tab/brand\" — gộp các tab đó vào 1 "
                                                         "link duy nhất")
                all_lines: list[OrderLine] = []
                all_issues: list[RowIssue] = []
                rows_read = 0
                header_row = 1
                other = 0
                tabs_used: list[str] = []
                last_choice: TabChoice | None = None
                for book in books:
                    choice_b, tabs_b, parsed_b, lines_b, other_b = self._resolve_book_tabs(
                        book, config, mapping, scope, inp)
                    last_choice = choice_b
                    tabs_used += tabs_b
                    all_lines += lines_b
                    all_issues += parsed_b.issues
                    rows_read += parsed_b.rows_read
                    header_row = parsed_b.header_row
                    other += other_b
                parsed = ParseResult(tuple(all_lines), tuple(sorted(all_issues, key=lambda i: i.row_number)),
                                     rows_read, header_row)
                lines = all_lines
                if len(books) == 1 and last_choice is not None:
                    choice = last_choice           # 1 link, 1 tab (auto/chosen) -> hành vi hiển thị y hệt cũ
                elif len(tabs_used) > 1:
                    combined_tabs = tabs_used       # nhiều link order, mỗi link 1 tab -> gộp lại hiển thị

        # 3. tính — TOÀN BỘ dòng đơn trong (các) tab đã đọc, không lọc theo tháng/kỳ nghiệm thu nào
        nmv = compute_nmv(lines)
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
            raise fail("NO_DELIVERED_ORDERS", f"Không có đơn 'Đã giao' nào trong file order{hint}",
                       details=issues[:50])
        commission = calculate_commission(nmv_vnd, contract.commission_rate, tax,
                                          collected_on_behalf=inp.collected_on_behalf,
                                          consulting_fee=inp.consulting_fee, quantum=VND)

        # 4-5. số + render
        program = program_name(inp.campaign_name, number)
        values, item_rows, brand_blocks = build_render_context(
            number=number, program=program, contract_no=contract.contract_no,
            contract_signed_on=contract.signed_on or contract.valid_from,
            appendix_no=contract.appendix_no, appendix_signed_on=contract.appendix_signed_on,
            party_a=contract.party_a, gmv_estimated=gmv_vnd, nmv=nmv_vnd, cancellation_rate=nmv.cancellation_rate,
            commission=commission, items=nmv.items, brand_breakdown=brand_breakdown)
        result = BbntResult(number, entity, scope, contract, mapping, tax, parsed, lines, other, nmv,
                            gmv_vnd, nmv_vnd, commission, program, choice, books[0].kind, values,
                            brand_breakdown=brand_breakdown, combined_tabs=combined_tabs,
                            campaign_name=inp.campaign_name.strip(), breakdown_kind=breakdown_kind,
                            manual_contract=inp.manual_contract)
        if renderer is not None:
            try:
                brands = [BrandBlock(b["values"], b["items"]) for b in brand_blocks]
                # breakdown_kind == "camp" -> ẩn bảng TỔNG HỢP sản phẩm top-level (chỉ còn bảng riêng từng
                # camp), xem docstring đầu module + ``app.docx.filler`` (summary_section_start/end).
                result.content = renderer.render(values, item_rows, brands=brands,
                                                 show_summary=breakdown_kind != "camp",
                                                 strip_comments=inp.strip_comments).content
            except (MissingValueError, LegalTextModifiedError) as exc:
                raise fail("RENDER_FAILED", f"Không điền được template: {exc}", status=500) from exc
        return result

    @staticmethod
    def _resolve_book_tabs(book: OrderWorkbook, config: MappingConfig, mapping: OrderMapping, scope: Scope,
                           inp: BbntInput
                           ) -> tuple[TabChoice | None, list[str], ParseResult, list[OrderLine], int]:
        """1 book (KHÔNG phải kiểu "1 tab/brand" — xem ``detect_brand_tabs``) -> chọn ĐÚNG 1 tab cần đọc rồi
        parse (``order_tab`` nếu có, hoặc tự chấm điểm qua :func:`choose_order_tab`, có thể báo
        ``ORDER_TAB_AMBIGUOUS`` — khi đó gộp nhiều tab/camp lại dùng ``breakdown_mode``, xem docstring đầu
        module). Khi có NHIỀU link order (``extra_order_sheet_urls``), hàm này được gọi RIÊNG cho từng book để
        mỗi link tự chọn tab của mình, tránh trùng tên tab giữa 2 link bị hiểu nhầm."""
        choice = choose_order_tab(book, config, mapping_code=mapping.code, scope=scope,
                                  campaign=inp.campaign_name, requested=inp.order_tab)
        tabs = [choice.tab]
        parsed, lines, other = BbntGenerator._read_tabs(book, config, mapping, scope, tabs)
        return choice, tabs, parsed, lines, other

    @staticmethod
    def _read_tabs(book: OrderWorkbook, config: MappingConfig, mapping: OrderMapping, scope: Scope,
                   tabs: list[str]) -> tuple[ParseResult, list[OrderLine], int]:
        """Đọc 1 hoặc nhiều tab (``breakdown_mode``/``_read_all_candidate_tabs`` gộp > 1 tab; bình thường chỉ
        1 tab) thành 1 ``ParseResult``/danh sách dòng chung — gộp đơn giản (không tách theo brand, dùng cho
        link order kiểu "nhiều tab/camp CÙNG 1 brand/scope", khác với :meth:`_read_brand_tabs` tách theo
        brand)."""
        all_lines: list[OrderLine] = []
        all_issues: list[RowIssue] = []
        rows_read = 0
        header_row = 1
        other = 0
        for tab in tabs:
            try:
                parsed = parse_sheet(book.read(tab), config)
            except MappingError as exc:
                raise fail("MAPPING_MISMATCH", f"Tab '{tab}' không khớp mapping {mapping.code}: {exc}",
                           details={"tabs": tabs}) from exc
            lines, other_t = _filter_brands(parsed, mapping, scope)
            all_lines += lines
            all_issues += parsed.issues
            rows_read += parsed.rows_read
            header_row = parsed.header_row
            other += other_t
        parsed_all = ParseResult(tuple(all_lines), tuple(sorted(all_issues, key=lambda i: i.row_number)),
                                 rows_read, header_row)
        return parsed_all, all_lines, other

    @staticmethod
    def _read_brand_tabs(book: OrderWorkbook, config: MappingConfig, mapping: OrderMapping, scope: Scope,
                         brand_tabs: dict[str, list[str]],
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
            brand_nmv = compute_nmv(tagged)
            breakdown.append(BrandBreakdown(index=idx, brand_name=brand.name, order_tabs=tuple(tabs),
                                            nmv=brand_nmv, brand_code=brand.code))
        if not all_lines:
            raise fail("NO_DELIVERED_ORDERS", "Không brand nào trong group có đơn trong file order "
                                              f"(đã thử tab: {', '.join(tried_tabs)})")
        parsed = ParseResult(tuple(all_lines), tuple(sorted(all_issues, key=lambda i: i.row_number)),
                             rows_read, header_row)
        return parsed, all_lines, other, breakdown

    @staticmethod
    def _read_all_candidate_tabs(books: list[OrderWorkbook], config: MappingConfig, mapping: OrderMapping,
                                 scope: Scope) -> tuple[ParseResult, list[OrderLine], int, list[str]]:
        """Đọc TẤT CẢ tab ứng viên của MỌI book (không chọn ra 1 tab theo ``tab_mode``, không tách theo brand)
        — dùng khi ``breakdown_mode == "brand"`` nhưng link KHÔNG tách sẵn tab theo brand (file 1 tab chung,
        cần đọc hết rồi nhóm lại theo cột Brand qua :func:`_group_lines_by_brand`)."""
        all_lines: list[OrderLine] = []
        all_issues: list[RowIssue] = []
        rows_read = 0
        header_row = 1
        other = 0
        tabs_used: list[str] = []
        for book in books:
            tabs = list_candidate_tabs(book, config, mapping_code=mapping.code)
            parsed_b, lines_b, other_b = BbntGenerator._read_tabs(book, config, mapping, scope, tabs)
            tabs_used += tabs
            all_lines += lines_b
            all_issues += parsed_b.issues
            rows_read += parsed_b.rows_read
            header_row = parsed_b.header_row
            other += other_b
        parsed = ParseResult(tuple(all_lines), tuple(sorted(all_issues, key=lambda i: i.row_number)),
                             rows_read, header_row)
        return parsed, all_lines, other, tabs_used

    @staticmethod
    def _read_camp_breakdown(books: list[OrderWorkbook], config: MappingConfig, mapping: OrderMapping,
                             scope: Scope, inp: BbntInput
                             ) -> tuple[ParseResult, list[OrderLine], int, list[BrandBreakdown], list[str]]:
        """``breakdown_mode == "camp"`` (MỌI phạm vi — kể cả 1 brand): mỗi tab ở mỗi link order
        (``(link_index, tab)``) là 1 camp — BẮT BUỘC đặt tên riêng cho TỪNG camp qua ``inp.camp_names`` (không
        tự lấy theo tên tab/sheet — tên tab có thể trùng giữa 2 link khác nhau và không phản ánh đúng tên
        chương trình thật). Tính NMV RIÊNG từng camp (:class:`BrandBreakdown` với ``brand_code=None``), đồng
        thời gộp mọi dòng lại thành 1 danh sách chung (dùng tính NMV tổng + hoa hồng chung cho cả BBNT, như
        :meth:`_read_brand_tabs`)."""
        sources: list[tuple[int, str]] = []
        for bi, book in enumerate(books):
            tabs = list_candidate_tabs(book, config, mapping_code=mapping.code)
            sources += [(bi, t) for t in tabs]
        overrides = {(c.link_index, c.tab): c.name.strip() for c in (inp.camp_names or ())}
        missing = [s for s in sources if not overrides.get(s, "")]
        if missing:
            raise fail("TAB_CAMPAIGN_NAMES_REQUIRED",
                       f"Gộp nhiều tab/camp cần đặt Tên chương trình riêng cho {len(missing)} camp chưa có tên "
                       "(mỗi link order + tab 1 tên riêng, không tự lấy theo tên tab/sheet)", status=409,
                       details={"tabs": [{"link_index": bi, "tab": t, "link_label": _link_label(bi)}
                                         for bi, t in missing]})
        names = {s: overrides[s] for s in sources}
        seen: dict[str, tuple[int, str]] = {}
        for s in sources:
            key = normalize_text(names[s])
            if key in seen:
                other_bi, other_tab = seen[key]
                raise fail("TAB_CAMPAIGN_NAMES_DUPLICATE",
                           f"Tên camp '{names[s]}' trùng với {_link_label(other_bi)} tab '{other_tab}' — mỗi "
                           "camp cần 1 tên riêng để không bị gộp nhầm",
                           details={"tabs": [{"link_index": other_bi, "tab": other_tab},
                                             {"link_index": s[0], "tab": s[1]}]})
            seen[key] = s
        all_lines: list[OrderLine] = []
        all_issues: list[RowIssue] = []
        rows_read = 0
        header_row = 1
        other = 0
        breakdown: list[BrandBreakdown] = []
        for idx, (bi, tab) in enumerate(sources, start=1):
            parsed_t, lines_t, other_t = BbntGenerator._read_tabs(books[bi], config, mapping, scope, [tab])
            all_lines += lines_t
            all_issues += parsed_t.issues
            rows_read += parsed_t.rows_read
            header_row = parsed_t.header_row
            other += other_t
            camp_nmv = compute_nmv(lines_t)
            breakdown.append(BrandBreakdown(index=idx, brand_name=names[(bi, tab)], order_tabs=(tab,),
                                            nmv=camp_nmv, brand_code=None))
        parsed = ParseResult(tuple(all_lines), tuple(sorted(all_issues, key=lambda i: i.row_number)),
                             rows_read, header_row)
        return parsed, all_lines, other, breakdown, [t for _, t in sources]

    @staticmethod
    def _number(entity: Entity, scope: Scope, suffix: str, issued_at: datetime,
               revision_no: int = 0) -> BbntNumber:
        try:
            return generate_bbnt_number(brands=[BrandRef(scope.code)], entity=entity.number_code,
                                        channel=scope.channel, suffix=suffix, issued_at=issued_at,
                                        revision_no=revision_no)
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


def _link_label(link_index: int) -> str:
    return "Link chính" if link_index == 0 else f"Link phụ #{link_index}"


def _group_lines_by_brand(lines: Sequence[OrderLine], scope: Scope) -> list[BrandBreakdown]:
    """``breakdown_mode == "brand"`` khi link KHÔNG tách sẵn tab theo brand (file 1 tab chung) — nhóm lại danh
    sách dòng ĐÃ ĐỌC/ĐÃ LỌC (``_read_all_candidate_tabs``) theo cột Brand của từng dòng (so khớp mã/tên brand,
    không phân biệt hoa thường/dấu, giống :func:`_filter_brands`). ``order_tabs`` để trống (không theo dõi
    dòng nào đến từ tab nào trong đường này). Brand không có dòng nào -> bỏ qua, không lỗi."""
    out: list[BrandBreakdown] = []
    for idx, brand in enumerate(scope.brands, start=1):
        allowed = {normalize_text(x) for x in (brand.code, brand.name) if x}
        brand_lines = [ln for ln in lines if normalize_text(ln.brand_name) in allowed]
        if not brand_lines:
            continue
        out.append(BrandBreakdown(index=idx, brand_name=brand.name, order_tabs=(), nmv=compute_nmv(brand_lines),
                                  brand_code=brand.code))
    return out


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
