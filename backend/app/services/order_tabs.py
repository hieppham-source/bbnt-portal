"""
Chọn tab trong file order.

Brand gửi file đủ kiểu: 1 file nhiều tab (mỗi brand / mỗi chương trình 1 tab, kèm tab tổng hợp), tên tab tuỳ ý,
header lệch vài dòng. Thứ tự quyết định:

1. Người dùng đã chọn tab (``order_tab``) -> dùng đúng tab đó.
2. Ứng viên = các tab có đủ cột bắt buộc theo mapping (dò header trong 15 dòng đầu).
3. Chỉ 1 ứng viên -> dùng luôn. Nhiều ứng viên -> chấm điểm theo tên tab:
   có tên/mã brand (+8), có tên chương trình (+4), là tab đang mở trong link ``gid`` (+2),
   có tháng nghiệm thu (+1), trùng "Tên tab" của mapping (+1). Điểm cao nhất duy nhất -> dùng;
   hoà -> báo ``ORDER_TAB_AMBIGUOUS`` kèm danh sách để người dùng chọn trên web.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.core.calculation import MappingConfig, MappingError, locate_header, normalize_text
from app.core.numbering import YearMonth
from app.errors import fail
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


def score_tab(title: str, *, scope: Scope, campaign: str, months: Sequence[YearMonth],
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


def choose_order_tab(book: OrderWorkbook, config: MappingConfig, *, mapping_code: str, scope: Scope,
                     campaign: str, months: Sequence[YearMonth], requested: str | None = None) -> TabChoice:
    heads = book.heads(book.tabs)
    candidates: list[str] = []
    errors: dict[str, str] = {}
    for tab in book.tabs:
        try:
            locate_header(heads.get(tab, []), config)
            candidates.append(tab)
        except MappingError as exc:
            errors[tab] = str(exc)

    if requested:
        requested = next((t for t in book.tabs if t.strip() == requested.strip()), requested)
        if requested not in book.tabs:
            raise fail("ORDER_TAB_NOT_FOUND", f"File order không có tab '{requested}'",
                       details={"tabs": candidates or book.tabs})
        return TabChoice(requested, candidates, "chosen")

    if not candidates:
        first = book.gid_tab if book.gid_tab in errors else (
            config.sheet_name if config.sheet_name in errors else book.tabs[0] if book.tabs else None)
        detail = f" Tab '{first}': {errors[first]}" if first in errors else ""
        names = ", ".join(f"'{t}'" for t in book.tabs[:8])
        raise fail("MAPPING_MISMATCH", f"File order không có tab nào khớp mapping {mapping_code} "
                                       f"(đã thử {len(book.tabs)} tab: {names}).{detail}",
                   details={"tabs": book.tabs})
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
