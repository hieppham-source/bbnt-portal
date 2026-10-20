"""
Chọn tab trong file order.

Brand gửi file đủ kiểu: 1 file nhiều tab (mỗi brand / mỗi chương trình 1 tab, kèm tab tổng hợp), tên tab tuỳ ý,
header lệch vài dòng. Thứ tự quyết định:

1. Người dùng đã chọn tab (``order_tab``) -> dùng đúng tab đó.
2. Ứng viên = các tab có đủ cột bắt buộc theo mapping (dò header trong 15 dòng đầu).
3. Chỉ 1 ứng viên -> dùng luôn. Nhiều ứng viên -> chấm điểm theo tên tab:
   có tên/mã brand (+8), có tên chương trình (+4), là tab đang mở trong link ``gid`` (+2),
   có tháng nghiệm thu (+1 — ``months`` rỗng theo mặc định vì BBNT không còn thu thập kỳ nghiệm thu, chỉ
   còn dùng khi có truyền vào), trùng "Tên tab" của mapping (+1). Điểm cao nhất duy nhất -> dùng;
   hoà -> báo ``ORDER_TAB_AMBIGUOUS`` kèm danh sách để người dùng chọn trên web.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.core.calculation import MappingConfig, MappingError, locate_header, normalize_text
from app.core.numbering import YearMonth
from app.errors import BbntError, fail
from app.master.index import Scope
from app.services.sheets import OrderWorkbook


@dataclass(frozen=True)
class TabChoice:
    tab: str
    candidates: list[str]       # các tab đọc được theo mapping (để web cho đổi tab)
    reason: str                 # "chosen" | "only" | "best_match"


def _compact(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", normalize_text(s))


def _contains_term(title: str, term: str) -> bool:
    t, w = normalize_text(title), normalize_text(term)
    if len(w) < 2:
        return False
    if re.search(rf"(?:^|\W){re.escape(w)}(?:\W|$)", t):
        return True
    cw = _compact(term)
    return len(cw) >= 4 and cw in _compact(title)      # 'BIOCORNER' ~ 'BIO CORNER'


def _period_terms(months: Sequence[YearMonth]) -> list[str]:
    out: list[str] = []
    for m in months:
        out += [f"{m.month:02d}.{m.year % 100:02d}", f"{m.month:02d}/{m.year}", f"{m.month:02d}.{m.year}",
                f"{m.month:02d}/{m.year % 100:02d}", f"{m.month:02d}-{m.year}", f"{m.month:02d}-{m.year % 100:02d}"]
    return out


def score_tab(title: str, *, scope: Scope, campaign: str, months: Sequence[YearMonth] = (),
              gid_tab: str | None, mapping_tab: str | None) -> int:
    brand_terms = [x for b in scope.brands for x in (b.code, b.name)] + [scope.code, scope.display]
    score = 8 if any(_contains_term(title, t) for t in brand_terms if t) else 0
    words = [w for w in normalize_text(campaign).split() if len(w) >= 2]
    if words and all(_contains_term(title, w) for w in words):
        score += 4
    if gid_tab and title == gid_tab:
        score += 2
    if any(normalize_text(p) in normalize_text(title) for p in _period_terms(months)):
        score += 1
    if mapping_tab and normalize_text(title) == normalize_text(mapping_tab):
        score += 1
    return score


def detect_brand_tabs(book: OrderWorkbook, config: MappingConfig, *, scope: Scope) -> dict[str, list[str]] | None:
    """Phát hiện link order kiểu "1 (hoặc nhiều) tab riêng cho mỗi brand trong group" (vd group KOSE: tab
    ANESSA / ELIXIR / DPROGRAM; hay 1 brand tách nhiều tab theo loại sản phẩm — "BIO CORNER ORDER MIX" +
    "BIO CORNER ORDER FOOD" đều là brand BIO CORNER, gộp lại) — khác với file 1 tab dùng chung nhiều brand
    qua cột BRAND_NAME (đã xử lý ở :func:`app.services.generator._filter_brands`).

    Trả về map ``mã brand -> danh sách tên tab (gộp nếu brand đó khớp nhiều tab)`` CHỈ KHI khớp được RÕ RÀNG
    >= 2 brand khác nhau, mỗi tab khớp tên với ĐÚNG 1 brand (không có tab nào khớp tên > 1 brand — tab đó coi
    như mơ hồ, bỏ qua không tính candidate của brand nào) — để an toàn, mọi trường hợp mơ hồ (< 2 brand khớp
    được tab) trả về ``None`` và gọi lại :func:`choose_order_tab` kiểu cũ (1 tab chung cho cả scope).
    Brand trong group không khớp tab nào -> coi như không phát sinh đơn kỳ này (không lỗi, không có trong map).
    """
    if not scope.is_group or len(scope.brands) < 2:
        return None
    heads = book.heads(book.tabs)
    candidates: list[str] = []
    for tab in book.tabs:
        try:
            locate_header(heads.get(tab, []), config)
            candidates.append(tab)
        except MappingError:
            continue
    if len(candidates) < 2:
        return None
    by_brand: dict[str, list[str]] = {}
    for tab in candidates:
        matched = [b.code for b in scope.brands if any(_contains_term(tab, t) for t in (b.code, b.name) if t)]
        if len(matched) == 1:
            by_brand.setdefault(matched[0], []).append(tab)
    if len(by_brand) < 2:
        return None
    return by_brand


def _gather_candidates(book: OrderWorkbook, config: MappingConfig) -> tuple[list[str], dict[str, str]]:
    heads = book.heads(book.tabs)
    candidates: list[str] = []
    errors: dict[str, str] = {}
    for tab in book.tabs:
        try:
            locate_header(heads.get(tab, []), config)
            candidates.append(tab)
        except MappingError as exc:
            errors[tab] = str(exc)
    return candidates, errors


def _no_candidates_error(book: OrderWorkbook, config: MappingConfig, mapping_code: str,
                         errors: dict[str, str]) -> BbntError:
    first = book.gid_tab if book.gid_tab in errors else (
        config.sheet_name if config.sheet_name in errors else book.tabs[0] if book.tabs else None)
    detail = f" Tab '{first}': {errors[first]}" if first in errors else ""
    names = ", ".join(f"'{t}'" for t in book.tabs[:8])
    return fail("MAPPING_MISMATCH", f"File order không có tab nào khớp mapping {mapping_code} "
                                    f"(đã thử {len(book.tabs)} tab: {names}).{detail}",
               details={"tabs": book.tabs})


def list_candidate_tabs(book: OrderWorkbook, config: MappingConfig, *, mapping_code: str) -> list[str]:
    """Liệt kê MỌI tab khớp đủ cột mapping (không chấm điểm/chọn ra 1 tab như :func:`choose_order_tab`) — dùng
    khi muốn GỘP (``tab_mode='combine'`` — nhiều tab/nhiều camp cùng 1 brand/scope gộp chung 1 BBNT) hay TÁCH
    (``tab_mode='split'`` — mỗi tab 1 BBNT riêng) toàn bộ ứng viên thay vì để hệ thống tự chọn/báo mơ hồ 1 tab."""
    candidates, errors = _gather_candidates(book, config)
    if not candidates:
        raise _no_candidates_error(book, config, mapping_code, errors)
    return candidates


def choose_order_tab(book: OrderWorkbook, config: MappingConfig, *, mapping_code: str, scope: Scope,
                     campaign: str, months: Sequence[YearMonth] = (), requested: str | None = None) -> TabChoice:
    candidates, errors = _gather_candidates(book, config)

    if requested:
        requested = next((t for t in book.tabs if t.strip() == requested.strip()), requested)
        if requested not in book.tabs:
            raise fail("ORDER_TAB_NOT_FOUND", f"File order không có tab '{requested}'",
                       details={"tabs": candidates or book.tabs})
        return TabChoice(requested, candidates, "chosen")

    if not candidates:
        raise _no_candidates_error(book, config, mapping_code, errors)
    if len(candidates) == 1:
        return TabChoice(candidates[0], candidates, "only")

    scores = {t: score_tab(t, scope=scope, campaign=campaign, months=months, gid_tab=book.gid_tab,
                           mapping_tab=config.sheet_name) for t in candidates}
    best = max(scores.values())
    top = [t for t, s in scores.items() if s == best]
    if best > 0 and len(top) == 1:
        return TabChoice(top[0], candidates, "best_match")
    ranked = sorted(candidates, key=lambda t: -scores[t])
    raise fail("ORDER_TAB_AMBIGUOUS", f"File order có {len(candidates)} tab đơn hàng — chọn tab cần nghiệm thu",
               status=409, details={"tabs": ranked})
