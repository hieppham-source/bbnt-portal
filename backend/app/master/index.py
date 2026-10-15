"""Tra cứu trên master data: brand/group -> phạm vi BBNT, chọn hợp đồng, chọn mapping file order."""
from __future__ import annotations

import calendar
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from app.core.numbering import YearMonth
from app.errors import fail

from .models import Brand, Contract, MasterData, OrderMapping


@dataclass(frozen=True)
class Scope:
    """Phạm vi 1 BBNT: 1 brand, hoặc nhiều brand cùng group (số BBNT dùng group code)."""

    channel: str
    brands: tuple[Brand, ...]
    group_code: str | None       # set khi BBNT gộp group
    group_name: str | None

    @property
    def is_group(self) -> bool:
        return self.group_code is not None

    @property
    def code(self) -> str:
        """Token BRAND trong số BBNT."""
        return self.group_code or self.brands[0].code

    @property
    def display(self) -> str:
        """Tên in trong tên chương trình (vd 'ORGANICA BDAY 05.26')."""
        return (self.group_name or self.group_code or "") if self.is_group else self.brands[0].name


def period_bounds(months: Sequence[YearMonth]) -> tuple[date, date]:
    last = months[-1]
    return months[0].first_day(), date(last.year, last.month, calendar.monthrange(last.year, last.month)[1])


def resolve_scope(master: MasterData, channel: str, brand_codes: Sequence[str] | None = None,
                  group_code: str | None = None) -> Scope:
    if channel not in master.channels():
        raise fail("CHANNEL_NOT_FOUND", f"Kênh {channel} chưa có brand nào trong tab BRAND")
    in_channel = {b.code: b for b in master.brands if b.channel == channel}
    if group_code:
        brands = tuple(b for b in in_channel.values() if b.group_code == group_code and b.active)
        if not brands:
            raise fail("GROUP_EMPTY", f"Group {group_code} không có brand đang hợp tác trên kênh {channel}")
        return Scope(channel, brands, group_code, brands[0].group_name)

    codes = sorted({c.strip().upper() for c in brand_codes or () if c.strip()})
    if not codes:
        raise fail("INVALID_INPUT", "Chọn ít nhất 1 brand hoặc 1 group")
    missing = [c for c in codes if c not in in_channel]
    if missing:
        raise fail("BRAND_NOT_FOUND", f"Brand không có trên kênh {channel} trong tab BRAND: {', '.join(missing)}")
    brands = tuple(in_channel[c] for c in codes)
    inactive = [b.code for b in brands if not b.active]
    if inactive:
        raise fail("BRAND_INACTIVE", f"Brand đang ngưng hợp tác: {', '.join(inactive)}")
    if len(brands) == 1:
        return Scope(channel, brands, None, None)
    groups = {b.group_code for b in brands}
    if None in groups or len(groups) != 1:
        raise fail("BRANDS_NOT_SAME_GROUP", "Gộp nhiều brand chỉ được khi tất cả cùng 1 Mã group (tab BRAND)")
    group = groups.pop()
    assert group is not None
    return Scope(channel, brands, group, brands[0].group_name)


def _scope_matches(c: Contract, scope: Scope) -> bool:
    if scope.is_group:
        return c.group_code == scope.group_code
    b = scope.brands[0]
    return c.brand_code == b.code or (b.group_code is not None and c.group_code == b.group_code)


def _summary(c: Contract) -> dict[str, str | None]:
    return {"contract_no": c.contract_no, "scope": c.scope_label, "channel": c.channel or "Mọi kênh",
            "valid_from": c.valid_from.isoformat(), "valid_to": c.valid_to.isoformat() if c.valid_to else None,
            "row": str(c.row)}


def find_contract(master: MasterData, entity: str, scope: Scope, months: Sequence[YearMonth],
                  contract_no: str | None = None) -> Contract:
    """Hợp đồng hiệu lực trọn kỳ nghiệm thu. Ưu tiên: hợp đồng riêng brand > hợp đồng group;
    rồi hợp đồng ghi đúng kênh > để trống kênh."""
    if entity not in master.entities:
        raise fail("ENTITY_NOT_FOUND", f"Pháp nhân {entity} không có trong tab PHAP_NHAN")
    start, end = period_bounds(months)
    cands = [c for c in master.contracts if c.active and c.entity_code == entity
             and c.channel in (None, scope.channel) and _scope_matches(c, scope)]
    if contract_no:
        key = contract_no.strip().upper()
        cands = [c for c in cands if c.contract_no.upper() == key]
        if not cands:
            raise fail("CONTRACT_NOT_FOUND", f"Hợp đồng {contract_no} không có / không áp dụng cho {entity} · "
                                             f"{scope.channel} · {scope.code} (tab HOP_DONG)")
    if not cands:
        raise fail("CONTRACT_NOT_FOUND", f"Chưa có hợp đồng {entity} · {scope.channel} · {scope.code} "
                                         "đang hiệu lực trong tab HOP_DONG")
    covering = [c for c in cands if c.covers(start, end)]
    if not covering:
        touching = [c for c in cands if c.overlaps(start, end)]
        if len(touching) > 1:
            raise fail("CONTRACT_SPLIT_PERIOD", "Kỳ nghiệm thu nằm vắt qua 2 hợp đồng/2 mức hoa hồng — "
                                                "hãy tách thành các BBNT theo từng giai đoạn",
                       details={"contracts": [_summary(c) for c in touching]})
        raise fail("CONTRACT_PERIOD_MISMATCH",
                   f"Không có hợp đồng hiệu lực trọn kỳ {start:%d/%m/%Y}–{end:%d/%m/%Y}",
                   details={"contracts": [_summary(c) for c in touching or cands]})

    def rank(c: Contract) -> tuple[int, int]:
        return (0 if c.brand_code else 1, 0 if c.channel else 1)

    best = min(rank(c) for c in covering)
    top = [c for c in covering if rank(c) == best]
    if len(top) > 1:
        raise fail("CONTRACT_AMBIGUOUS", "Có nhiều hợp đồng cùng hiệu lực — chọn Số hợp đồng",
                   details={"contracts": [_summary(c) for c in top]})
    return top[0]


def find_mapping(master: MasterData, channel: str, code: str | None = None) -> OrderMapping:
    cands = [m for m in master.mappings if m.channel == channel]
    if code:
        m = next((m for m in cands if m.code == code.strip().upper()), None)
        if m is None:
            raise fail("MAPPING_NOT_FOUND", f"Mapping {code} không có cho kênh {channel} (tab MAPPING)")
        return m
    if len(cands) == 1:
        return cands[0]
    defaults = [m for m in cands if m.is_default]
    if len(defaults) == 1:
        return defaults[0]
    if not cands:
        raise fail("MAPPING_NOT_FOUND", f"Chưa có mapping file order cho kênh {channel} (tab MAPPING)")
    raise fail("MAPPING_AMBIGUOUS", f"Kênh {channel} có {len(cands)} mapping — chọn 1 hoặc đánh dấu 1 cái "
                                    "'Mặc định' trong tab MAPPING", details={"mappings": [m.code for m in cands]})
