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
    signed_on: date | None = None   # cột "Ngày ký" (tuỳ chọn); trống -> BBNT dùng "Hiệu lực từ"
    #: Cột "Thuế suất riêng" (tuỳ chọn) — VAT/khấu trừ thuế áp dụng RIÊNG cho brand/hợp đồng này, ghi đè
    #: thuế suất mặc định của pháp nhân (tab PHAP_NHAN) khi khác nhau theo brand (vd mức giảm trừ theo
    #: NQ 204/2025/QH15 không giống nhau giữa các brand Sassy). None -> dùng thuế suất của pháp nhân như cũ.
    #: Không đổi tax_mode (Cộng VAT/Khấu trừ) của pháp nhân — chỉ đổi tỷ lệ %.
    tax_rate_override: Decimal | None = None
    #: Cột "Số phụ lục HĐ" / "Ngày ký phụ lục" (tuỳ chọn, đi cùng cặp) — hợp đồng có phụ lục (PLHD) bổ sung/
    #: điều chỉnh hợp đồng gốc. Khi có đủ cả 2, BBNT ghi thêm "và Phụ lục hợp đồng số ... ký ngày ..." sau câu
    #: "Căn cứ hợp đồng ... số {contract_no} ký ngày {signed_on}" (xem contract_signed_text trong
    #: app.services.bbnt_document). Thiếu 1 trong 2 -> coi như không có phụ lục (bỏ qua, không lỗi).
    appendix_no: str | None = None
    appendix_signed_on: date | None = None

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
