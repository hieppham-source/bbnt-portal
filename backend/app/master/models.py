"""Dữ liệu master (đọc từ Google Sheet master) — chỉ đọc, bất biến."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from app.core.calculation import FieldSpec, MappingConfig, OrderStatus, TaxPolicy
from app.services.bbnt_document import PartyA

MODELS = ("NON_OP", "OP")


@dataclass(frozen=True)
class MasterIssue:
    """Lỗi/cảnh báo ở 1 dòng của Sheet master — hiển thị cho người sửa Sheet."""

    tab: str
    row: int          # số dòng như Google Sheets hiển thị (1-based)
    message: str
    severity: str = "ERROR"   # ERROR: dòng bị bỏ qua · WARNING: vẫn dùng


@dataclass(frozen=True)
class Entity:
    code: str            # EZS | SASSY
    number_code: str     # EZ | SS — token ENTITY trong số BBNT
    name: str
    taxes: tuple[TaxPolicy, ...]

    def tax_on(self, d: date) -> TaxPolicy | None:
        return next((t for t in self.taxes if t.applies_on(d)), None)


@dataclass(frozen=True)
class Brand:
    channel: str
    code: str
    name: str
    group_code: str | None
    group_name: str | None
    active: bool
    row: int


@dataclass(frozen=True)
class Contract:
    contract_no: str
    entity_code: str
    channel: str | None          # None = áp dụng mọi kênh
    brand_code: str | None
    group_code: str | None
    model: str                   # NON_OP | OP
    commission_rate: Decimal
    valid_from: date
    valid_to: date | None        # None = chưa hết hạn
    party_a: PartyA
    active: bool
    row: int

    def covers(self, start: date, end: date) -> bool:
        return self.valid_from <= start and (self.valid_to is None or end <= self.valid_to)

    def overlaps(self, start: date, end: date) -> bool:
        return self.valid_from <= end and (self.valid_to is None or start <= self.valid_to)

    @property
    def scope_label(self) -> str:
        return self.brand_code or f"group {self.group_code}"


@dataclass(frozen=True)
class OrderMapping:
    code: str
    channel: str
    sheet_name: str | None
    header_row: int
    data_start_row: int
    fields: tuple[FieldSpec, ...]
    status_aliases: Mapping[OrderStatus, frozenset[str]] | None = None
    is_default: bool = False
    row: int = 0

    def to_config(self) -> MappingConfig:
        kwargs = {"status_aliases": self.status_aliases} if self.status_aliases else {}
        return MappingConfig(fields=self.fields, header_row=self.header_row, data_start_row=self.data_start_row,
                             sheet_name=self.sheet_name, **kwargs)  # type: ignore[arg-type]


@dataclass(frozen=True)
class MasterData:
    entities: Mapping[str, Entity]
    brands: Sequence[Brand]
    contracts: Sequence[Contract]
    mappings: Sequence[OrderMapping]
    issues: Sequence[MasterIssue] = field(default_factory=tuple)
    loaded_at: datetime | None = None

    @property
    def errors(self) -> list[MasterIssue]:
        return [i for i in self.issues if i.severity == "ERROR"]

    def channels(self) -> list[str]:
        return sorted({b.channel for b in self.brands})
