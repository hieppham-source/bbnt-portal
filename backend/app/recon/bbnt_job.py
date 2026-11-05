"""
BBNT Portal adapter cho bot (docs/ARCHITECTURE.md 11.2, 14.1; plan bước 6).

Dựng :class:`BbntInput` từ 1 case (1 link order = 1 BBNT; nhiều brand -> 1 BBNT group tách bảng theo brand),
chạy **generator có sẵn** với clock cố định = ``bbnt_issued_at`` đã lưu — tạo lại bao nhiêu lần cũng ra đúng số
BBNT cũ. Không đổi công thức / template. Không I/O ngoài ``open_order`` do worker truyền vào.
"""
from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.core.calculation import IssueCode, MappingConfig, MappingError, locate_header, normalize_text
from app.errors import BbntError
from app.master.data_brand import brand_code
from app.master.index import find_mapping
from app.master.models import MasterData
from app.recon.fbjt import brand_key
from app.recon.rules import campaign_program_name, campaign_suffix
from app.services.generator import BbntGenerator, BbntInput, BbntResult, OrderOpener
from app.services.sheets import OrderWorkbook, SheetAccessError
from app.services.templates import TemplateRegistry

CHANNEL = "FB"
_SIGNED = {normalize_text("Đã ký"), normalize_text("CÒN HIỆU LỰC")}   # DATA BRAND STATUS (04/11/2026)


@dataclass(frozen=True)
class ContractRow:
    """1 dòng tab ``DATA CONTRACT`` (FB.JT). ``brand`` có thể là tên brand hoặc tên group."""

    contract_no: str
    brand: str
    entity_b: str
    channel: str
    status: str
    level: str = "brand"         # "brand": dòng theo cột BRAND; "group": theo cột GROUP BRAND


def signed_contracts(rows: Iterable[ContractRow], *, brand_codes: Sequence[str], group_code: str | None,
                     entity: str) -> list[str]:
    """Số hợp đồng ``Đã ký`` + pháp nhân B + kênh FB. Từng brand: HĐ ghi theo BRAND nếu có, không có mới lấy HĐ
    theo GROUP (vd TEFAL có HĐ riêng dù group WELLHOME còn HĐ BOSCH). Gộp các brand; Audit Gate cần đúng 1."""
    ok = [r for r in rows if r.contract_no.strip() and normalize_text(r.status) in _SIGNED
          and normalize_text(r.entity_b) == normalize_text(entity) and normalize_text(r.channel) == "fb"]
    by_group = {r.contract_no.strip() for r in ok if group_code and brand_key(r.brand) == brand_key(group_code)}
    hits: set[str] = set()
    for b in brand_codes:
        own = {r.contract_no.strip() for r in ok if r.level == "brand" and brand_key(r.brand) == brand_key(b)}
        hits |= own or by_group
    return sorted(hits)


@dataclass(frozen=True)
class CaseBbnt:
    entity: str                  # pháp nhân Bên B (EZS / SASSY)
    brand_codes: Sequence[str]   # mọi brand trong link order
    campaign: str                # FB.JT 'CAMPAIGN (KHỚP HARAVAN)', dạng '<tên> - MM/YYYY'
    contract_no: str             # từ DATA CONTRACT (đúng 1)


def build_input(case: CaseBbnt) -> BbntInput:
    """Sai dạng campaign -> ``ValueError`` (chuyển KAM, không đoán)."""
    brands = sorted({brand_code(b) for b in case.brand_codes if brand_code(b)})   # = mã brand dựng từ DATA BRAND
    if not brands:
        raise ValueError("Case không có brand")
    program = campaign_program_name(case.campaign)
    return BbntInput(channel=CHANNEL, entity=case.entity, bbnt_suffix=campaign_suffix(case.campaign),
                     campaign_name=program, program_override=program, contract_no=case.contract_no,
                     brand_codes=brands, breakdown_mode="brand" if len(brands) > 1 else None)


@dataclass(frozen=True)
class BbntOutcome:
    result: BbntResult | None
    reasons: tuple[str, ...]     # mã lỗi generator / adapter; rỗng = BBNT hợp lệ
    #: Của đúng file .docx vừa tạo (render=True) — bằng chứng file đã gửi. Hai lần render cùng số liệu vẫn khác
    #: byte (thời gian trong file zip), nên so sánh BBNT bằng số BBNT + số liệu, không bằng hash.
    sha256: str | None = None

    @property
    def contract_in_portal_master(self) -> bool:
        return not {"CONTRACT_NOT_FOUND", "CONTRACT_AMBIGUOUS"} & set(self.reasons)


def _has_pending_status(details: object) -> bool:
    """Dòng status trống hoặc lạ -> issue UNKNOWN_STATUS (xem ``compute_nmv``)."""
    return any(getattr(i, "code", None) is IssueCode.UNKNOWN_STATUS
               for i in (details if isinstance(details, list) else []))


def _with_missed_orders(open_order: OrderOpener, config: MappingConfig) -> OrderOpener | None:
    """File order có tab ``ORDER`` + các tab ``ĐƠN SÓT...`` (anh Hiệp chốt 05/11/2026: đơn sót cộng chung) ->
    1 tab ảo ``ORDER`` gồm dòng của tất cả, ghép theo tên cột; dòng đơn sót trùng y hệt 1 dòng đã có thì bỏ (tránh
    tính 2 lần). File có tab khác ngoài ORDER/ĐƠN SÓT -> None (giữ ORDER_TAB_AMBIGUOUS, người xử lý)."""
    books = open_order()
    if len(books) != 1:
        return None
    book = books[0]
    heads = book.heads(book.tabs)
    tabs = [t for t in book.tabs if _fits(heads.get(t, []), config)]
    main = [t for t in tabs if normalize_text(t) == "order"]
    extra = [t for t in tabs if normalize_text(t).startswith("don sot")]
    if len(main) != 1 or not extra or len(main) + len(extra) != len(tabs):
        return None
    rows = [list(r) for r in book.read(main[0])]
    h, cols = locate_header(rows, config)
    width = max(cols.values()) + 1
    seen = {tuple(r[cols[f]] if cols[f] < len(r) else None for f in cols) for r in rows[h:]}
    for t in extra:
        rows_t = book.read(t)
        h_t, cols_t = locate_header(rows_t, config)
        for r in rows_t[h_t:]:
            vals = {f: (r[cols_t[f]] if f in cols_t and cols_t[f] < len(r) else None) for f in cols}
            key = tuple(vals.values())
            if key in seen or not any(v not in (None, "") for v in key):
                continue
            seen.add(key)
            new: list[Any] = [None] * width
            for f, j in cols.items():
                new[j] = vals[f]
            rows.append(new)
    merged = OrderWorkbook(book.kind, [main[0]], lambda _t: rows, gid_tab=main[0])
    return lambda: [merged]


def _fits(head: Sequence[Sequence[Any]], config: MappingConfig) -> bool:
    try:
        locate_header(head, config)
        return True
    except MappingError:
        return False


def make_bbnt(templates: TemplateRegistry, master: MasterData, case: CaseBbnt, open_order: OrderOpener, *,
              issued_at: datetime, render: bool) -> BbntOutcome:
    """Chạy generator; lỗi nghiệp vụ trả về ``reasons`` (không raise) để worker chuyển NEEDS_REVIEW."""
    try:
        inp = build_input(case)
    except ValueError:
        return BbntOutcome(None, ("CAMPAIGN_FORMAT",))
    try:
        opener = _with_missed_orders(open_order, find_mapping(master, CHANNEL).to_config()) or open_order
        r = BbntGenerator(templates, clock=lambda: issued_at).run(master, inp, opener, render=render)
    except BbntError as exc:
        if exc.code == "NO_DELIVERED_ORDERS" and _has_pending_status(exc.details):
            return BbntOutcome(None, ("STATUS_INCOMPLETE",))   # brand chưa điền status, không phải 0 đơn giao
        return BbntOutcome(None, (exc.code,))
    except SheetAccessError:
        return BbntOutcome(None, ("ORDER_SHEET_UNREADABLE",))
    return BbntOutcome(r, (), hashlib.sha256(r.content).hexdigest() if r.content is not None else None)
