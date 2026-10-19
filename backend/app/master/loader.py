"""
Đọc & kiểm tra Google Sheet master (4 tab). Mỗi dòng sai được ghi :class:`MasterIssue` (tab + số dòng) và bỏ qua,
các dòng đúng vẫn dùng được — người sửa Sheet xem danh sách lỗi ở ``GET /api/v1/master/check``.

Tab và cột (dòng 1 là tiêu đề; so khớp không phân biệt hoa/thường/dấu, thứ tự cột tuỳ ý):

* **PHAP_NHAN** — Mã pháp nhân · Mã số BBNT · Tên pháp nhân · Loại thuế · Thuế suất · Diễn giải thuế ·
  Hiệu lực từ · Hiệu lực đến. Mỗi giai đoạn thuế 1 dòng (EZS VAT 8% đến 31/12/2026, dòng sau VAT 10%...).
* **BRAND** — Kênh · Mã brand · Tên brand · Mã group · Tên group · Đang hợp tác
* **HOP_DONG** — Số hợp đồng · Pháp nhân · Kênh (trống = mọi kênh) · Mã brand | Mã group · Mô hình ·
  % hoa hồng · Hiệu lực từ · Hiệu lực đến · Tên Bên A · MST · Địa chỉ · Điện thoại · Số tài khoản · Ngân hàng ·
  Người đại diện · Chức vụ · Trạng thái · Thuế suất riêng (tuỳ chọn) · Số phụ lục HĐ (tuỳ chọn) · Ngày ký phụ lục
  (tuỳ chọn). Đổi % hoa hồng = thêm dòng mới với ngày hiệu lực mới. "Thuế suất riêng" (tuỳ chọn, vd ``5%``) ghi
  đè thuế suất mặc định của pháp nhân (tab PHAP_NHAN) CHO RIÊNG brand/hợp đồng này — dùng khi mức VAT/khấu trừ
  thuế (vd theo NQ 204/2025/QH15) khác nhau giữa các brand cùng pháp nhân; để trống thì dùng thuế suất chung
  của pháp nhân như cũ. "Số phụ lục HĐ" + "Ngày ký phụ lục" (tuỳ chọn, đi theo CẶP — điền thiếu 1 cột thì coi
  như không có, bỏ qua) dùng khi hợp đồng có phụ lục (PLHD) bổ sung/điều chỉnh: BBNT sẽ ghi thêm "và Phụ lục
  hợp đồng số ... ký ngày ..." sau câu "Căn cứ hợp đồng ... số {contract_no} ký ngày {Ngày ký}".
* **MAPPING** — Mã mapping · Kênh · Tên tab · Dòng tiêu đề · Dòng dữ liệu đầu · Mặc định · Trường · Tên cột ·
  Cột · Bắt buộc. Mỗi trường 1 dòng; dòng ``STATUS_DELIVERED`` / ``STATUS_RETURNED`` / ``STATUS_CANCELLED``
  ghi các giá trị trạng thái (phân cách dấu phẩy) ở cột "Tên cột".
"""
from __future__ import annotations

import functools
import re
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from app.core.calculation import (
    DEFAULT_STATUS_ALIASES,
    FieldSpec,
    MappingError,
    OrderStatus,
    TargetField,
    TaxMode,
    TaxPolicy,
    normalize_text,
    parse_money,
    parse_order_datetime,
)
from app.docx.vn_text import format_percent
from app.services.bbnt_document import PartyA

from .models import MODELS, Brand, Contract, Entity, MasterData, MasterIssue, OrderMapping

TABS: dict[str, tuple[str, ...]] = {
    "entities": ("PHAP_NHAN", "Pháp nhân"),
    "brands": ("BRAND", "Brands"),
    "contracts": ("HOP_DONG", "Hợp đồng"),
    "mappings": ("MAPPING",),
}

COLUMNS: dict[str, dict[str, tuple[str, ...]]] = {
    "entities": {
        "code": ("Mã pháp nhân", "Pháp nhân", "Mã"),
        "number_code": ("Mã số BBNT", "Mã trong số BBNT"),
        "name": ("Tên pháp nhân", "Tên"),
        "tax_mode": ("Loại thuế",),
        "tax_rate": ("Thuế suất",),
        "tax_label": ("Diễn giải thuế",),
        "valid_from": ("Hiệu lực từ",),
        "valid_to": ("Hiệu lực đến",),
    },
    "brands": {
        "channel": ("Kênh",),
        "code": ("Mã brand",),
        "name": ("Tên brand",),
        "group_code": ("Mã group",),
        "group_name": ("Tên group",),
        "active": ("Đang hợp tác",),
    },
    "contracts": {
        "contract_no": ("Số hợp đồng",),
        "entity": ("Pháp nhân",),
        "channel": ("Kênh",),
        "brand_code": ("Mã brand",),
        "group_code": ("Mã group",),
        "model": ("Mô hình",),
        "rate": ("% hoa hồng", "Hoa hồng"),
        "valid_from": ("Hiệu lực từ",),
        "valid_to": ("Hiệu lực đến",),
        "signed_on": ("Ngày ký", "Ngày ký HĐ", "Ngày ký hợp đồng"),
        "a_name": ("Tên Bên A", "Bên A"),
        "a_tax_code": ("MST", "MST Bên A", "Mã số thuế"),
        "a_address": ("Địa chỉ",),
        "a_phone": ("Điện thoại",),
        "a_bank_account": ("Số tài khoản", "Số TK"),
        "a_bank_name": ("Ngân hàng",),
        "a_representative": ("Người đại diện", "Đại diện"),
        "a_title": ("Chức vụ",),
        "status": ("Trạng thái",),
        "tax_rate_override": ("Thuế suất riêng", "Thuế suất riêng (ghi đè)", "VAT/Khấu trừ riêng"),
        "appendix_no": ("Số phụ lục HĐ", "Số phụ lục", "Số PLHD", "Phụ lục hợp đồng"),
        "appendix_signed_on": ("Ngày ký phụ lục", "Ngày ký PLHD", "Ngày ký phụ lục HĐ", "Ngày ký phụ lục hợp đồng"),
    },
    "mappings": {
        "code": ("Mã mapping",),
        "channel": ("Kênh",),
        "tab": ("Tên tab",),
        "header_row": ("Dòng tiêu đề",),
        "data_row": ("Dòng dữ liệu đầu",),
        "default": ("Mặc định",),
        "field": ("Trường",),
        "header": ("Tên cột",),
        "column": ("Cột",),
        "required": ("Bắt buộc",),
    },
}

REQUIRED: dict[str, tuple[str, ...]] = {
    "entities": ("code", "number_code", "tax_mode", "tax_rate"),
    "brands": ("channel", "code", "name"),
    "contracts": ("contract_no", "entity", "rate", "valid_from", "a_name"),
    "mappings": ("code", "channel", "field"),
}

STATUS_FIELDS = {"STATUS_DELIVERED": OrderStatus.DELIVERED, "STATUS_RETURNED": OrderStatus.RETURNED,
                 "STATUS_CANCELLED": OrderStatus.CANCELLED}

_TRUE = {"true", "x", "co", "yes", "y", "1", "dang hop tac", "active", "dang hieu luc", "hieu luc", "mac dinh"}
_FALSE = {"false", "khong", "no", "n", "0", "ngung", "inactive", "het han", "terminated", "huy", "tam dung"}
_TAX_MODES = {
    "vat_add": TaxMode.VAT_ADD, "cong vat": TaxMode.VAT_ADD, "vat": TaxMode.VAT_ADD, "+vat": TaxMode.VAT_ADD,
    "tax_deduct": TaxMode.TAX_DEDUCT, "khau tru": TaxMode.TAX_DEDUCT, "khau tru thue": TaxMode.TAX_DEDUCT,
    "tru thue": TaxMode.TAX_DEDUCT, "none": TaxMode.NONE, "khong": TaxMode.NONE, "khong thue": TaxMode.NONE,
}
_MODEL = {"non_op": "NON_OP", "non-op": "NON_OP", "nonop": "NON_OP", "non op": "NON_OP", "op": "OP"}
_CODE_RE = re.compile(r"^[A-Z0-9_]+$")


class _RowError(ValueError):
    pass


# =============================================================================
# Cell parsers
# =============================================================================
def _blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _text(v: Any) -> str | None:
    if _blank(v):
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return " ".join(str(v).split())


def _code(v: Any, what: str) -> str | None:
    t = _text(v)
    if t is None:
        return None
    code = t.upper()
    if not _CODE_RE.fullmatch(code):
        raise _RowError(f"{what} '{t}' chỉ được gồm chữ không dấu, số, '_' (không dấu cách, không '-')")
    return code


def _percent(v: Any, what: str) -> Decimal:
    if _blank(v):
        raise _RowError(f"Thiếu {what}")
    raw = str(v).strip()
    try:
        if raw.endswith("%"):
            value = parse_money(raw[:-1])
            assert value is not None
            value = value / 100
        else:
            value = parse_money(v)
            assert value is not None
            if 1 < value <= 100:          # gõ 18 thay vì 18% -> hiểu là 18%
                value = value / 100
    except (ValueError, AssertionError) as exc:
        raise _RowError(f"{what} '{raw}' không hợp lệ (nhập dạng 18% hoặc 0,18)") from exc
    if not 0 <= value <= 1:
        raise _RowError(f"{what} '{raw}' phải trong khoảng 0–100%")
    return value.normalize() if value == value.to_integral_value() else value


def _date(v: Any, what: str) -> date | None:
    if _blank(v):
        return None
    try:
        dt = parse_order_datetime(v)
    except ValueError as exc:
        raise _RowError(f"{what} '{v}' không phải ngày (dd/mm/yyyy)") from exc
    assert dt is not None
    return dt.date()


def _int(v: Any, what: str, default: int) -> int:
    if _blank(v):
        return default
    try:
        n = int(Decimal(str(v).strip()))
    except Exception as exc:  # noqa: BLE001
        raise _RowError(f"{what} '{v}' phải là số nguyên") from exc
    if n < 1:
        raise _RowError(f"{what} phải >= 1")
    return n


def _bool(v: Any, default: bool) -> bool:
    if isinstance(v, bool):
        return v
    if _blank(v):
        return default
    key = normalize_text(v)
    if key in _TRUE:
        return True
    if key in _FALSE:
        return False
    raise _RowError(f"Giá trị '{v}' phải là Có/Không (hoặc TRUE/FALSE)")


def _digits_text(v: Any, what: str, issues: list[str]) -> str:
    """MST / số tài khoản: phải giữ nguyên số 0 đầu -> cột nên định dạng 'Văn bản thuần tuý'."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        issues.append(f"{what} đang là kiểu số — đổi cột sang định dạng văn bản để không mất số 0 ở đầu")
    return _text(v) or ""


# =============================================================================
# Table reader
# =============================================================================
def _header_index(header: Sequence[Any], spec: Mapping[str, tuple[str, ...]]) -> dict[str, int]:
    norm = [normalize_text(h) for h in header]
    found: dict[str, int] = {}
    for key, aliases in spec.items():
        for alias in aliases:
            a = normalize_text(alias)
            if a in norm:
                found[key] = norm.index(a)
                break
    return found


def _rows(kind: str, table: Sequence[Sequence[Any]], issues: list[MasterIssue], tab: str
          ) -> list[tuple[int, dict[str, Any]]]:
    if not table:
        issues.append(MasterIssue(tab, 1, "Tab trống — cần dòng tiêu đề ở dòng 1"))
        return []
    cols = _header_index(table[0], COLUMNS[kind])
    missing = [COLUMNS[kind][k][0] for k in REQUIRED[kind] if k not in cols]
    if missing:
        issues.append(MasterIssue(tab, 1, f"Thiếu cột bắt buộc: {', '.join(missing)}"))
        return []
    out = []
    for i, row in enumerate(table[1:], start=2):
        if all(_blank(c) for c in row):
            continue
        first = next((c for c in row if not _blank(c)), "")
        if str(first).strip().startswith("#"):     # dòng ghi chú
            continue
        out.append((i, {k: (row[idx] if idx < len(row) else None) for k, idx in cols.items()}))
    return out


# =============================================================================
# Parse each tab
# =============================================================================
def _parse_entities(table: Sequence[Sequence[Any]], tab: str, issues: list[MasterIssue]) -> dict[str, Entity]:
    by_code: dict[str, dict[str, Any]] = {}
    for rn, r in _rows("entities", table, issues, tab):
        try:
            code = _code(r.get("code"), "Mã pháp nhân")
            number_code = _code(r.get("number_code"), "Mã số BBNT")
            if not code or not number_code:
                raise _RowError("Thiếu Mã pháp nhân hoặc Mã số BBNT")
            mode = _TAX_MODES.get(normalize_text(r.get("tax_mode")))
            if mode is None:
                raise _RowError(f"Loại thuế '{r.get('tax_mode')}' phải là: Cộng VAT | Khấu trừ | Không")
            rate = Decimal(0) if mode is TaxMode.NONE else _percent(r.get("tax_rate"), "Thuế suất")
            if rate >= 1:
                raise _RowError("Thuế suất phải nhỏ hơn 100%")
            vf, vt = _date(r.get("valid_from"), "Hiệu lực từ"), _date(r.get("valid_to"), "Hiệu lực đến")
            if vf and vt and vt < vf:
                raise _RowError("Hiệu lực đến trước Hiệu lực từ")
            label = _text(r.get("tax_label")) or (
                f"VAT {format_percent(rate)}" if mode is TaxMode.VAT_ADD else
                f"Khấu trừ thuế {format_percent(rate)}" if mode is TaxMode.TAX_DEDUCT else "Không thuế")
            policy = TaxPolicy(mode, rate, label, vf, vt)
        except _RowError as exc:
            issues.append(MasterIssue(tab, rn, str(exc)))
            continue
        e = by_code.setdefault(code, {"number_code": number_code, "name": _text(r.get("name")) or code,
                                      "taxes": [], "rows": []})
        if e["number_code"] != number_code:
            issues.append(MasterIssue(tab, rn, f"Mã số BBNT của {code} khác dòng trước ({e['number_code']})"))
            continue
        clash = next((t for t, row in zip(e["taxes"], e["rows"], strict=True) if _periods_overlap(t, policy)), None)
        if clash is not None:
            issues.append(MasterIssue(tab, rn, f"Thuế của {code} chồng ngày hiệu lực với dòng "
                                               f"{e['rows'][e['taxes'].index(clash)]}"))
            continue
        e["taxes"].append(policy)
        e["rows"].append(rn)
    return {c: Entity(c, v["number_code"], v["name"], tuple(v["taxes"])) for c, v in by_code.items()}


def _periods_overlap(a: TaxPolicy, b: TaxPolicy) -> bool:
    a0, a1 = a.effective_from or date.min, a.effective_to or date.max
    b0, b1 = b.effective_from or date.min, b.effective_to or date.max
    return a0 <= b1 and b0 <= a1


def _parse_brands(table: Sequence[Sequence[Any]], tab: str, issues: list[MasterIssue]) -> list[Brand]:
    brands: list[Brand] = []
    seen: dict[tuple[str, str], int] = {}
    group_names: dict[str, str] = {}
    for rn, r in _rows("brands", table, issues, tab):
        try:
            channel = _code(r.get("channel"), "Kênh")
            code = _code(r.get("code"), "Mã brand")
            if not channel or not code:
                raise _RowError("Thiếu Kênh hoặc Mã brand")
            group = _code(r.get("group_code"), "Mã group")
            active = _bool(r.get("active"), True)
        except _RowError as exc:
            issues.append(MasterIssue(tab, rn, str(exc)))
            continue
        if (channel, code) in seen:
            issues.append(MasterIssue(tab, rn, f"Brand {code} kênh {channel} bị trùng với dòng "
                                               f"{seen[(channel, code)]}"))
            continue
        seen[(channel, code)] = rn
        gname = _text(r.get("group_name"))
        if group:
            if group in group_names and gname and gname != group_names[group]:
                issues.append(MasterIssue(tab, rn, f"Tên group {group} khác dòng trước ('{group_names[group]}')",
                                          "WARNING"))
            group_names.setdefault(group, gname or group)
        brands.append(Brand(channel, code, _text(r.get("name")) or code, group, None, active, rn))
    return [Brand(b.channel, b.code, b.name, b.group_code, group_names.get(b.group_code or ""), b.active, b.row)
            for b in brands]


def _parse_contracts(table: Sequence[Sequence[Any]], tab: str, issues: list[MasterIssue],
                     entities: Mapping[str, Entity], brands: Sequence[Brand]) -> list[Contract]:
    brand_keys = {(b.channel, b.code) for b in brands}
    brand_codes = {b.code for b in brands}
    groups = {b.group_code for b in brands if b.group_code}
    out: list[Contract] = []
    for rn, r in _rows("contracts", table, issues, tab):
        warns: list[str] = []
        try:
            no = _text(r.get("contract_no"))
            entity = _code(r.get("entity"), "Pháp nhân")
            if not no or not entity:
                raise _RowError("Thiếu Số hợp đồng hoặc Pháp nhân")
            if entity not in entities:
                raise _RowError(f"Pháp nhân '{entity}' không có trong tab PHAP_NHAN")
            channel = _code(r.get("channel"), "Kênh")
            brand, group = _code(r.get("brand_code"), "Mã brand"), _code(r.get("group_code"), "Mã group")
            if (brand is None) == (group is None):
                raise _RowError("Điền đúng 1 trong 2 cột: Mã brand hoặc Mã group")
            unknown = (channel, brand) not in brand_keys if channel else brand not in brand_codes
            if brand and unknown:
                raise _RowError(f"Brand {brand}{' kênh ' + channel if channel else ''} không có trong tab BRAND")
            if group and group not in groups:
                raise _RowError(f"Group {group} không có brand nào trong tab BRAND")
            model = _MODEL.get(normalize_text(r.get("model")) or "non_op")
            if model not in MODELS:
                raise _RowError(f"Mô hình '{r.get('model')}' phải là NON_OP hoặc OP")
            rate = _percent(r.get("rate"), "% hoa hồng")
            vf = _date(r.get("valid_from"), "Hiệu lực từ")
            vt = _date(r.get("valid_to"), "Hiệu lực đến")
            if vf is None:
                raise _RowError("Thiếu Hiệu lực từ")
            if vt and vt < vf:
                raise _RowError("Hiệu lực đến trước Hiệu lực từ")
            signed = _date(r.get("signed_on"), "Ngày ký")
            active = _bool(r.get("status"), True)
            raw_override = r.get("tax_rate_override")
            tax_rate_override = (_percent(raw_override, "Thuế suất riêng") if not _blank(raw_override) else None)
            if tax_rate_override is not None and tax_rate_override >= 1:
                raise _RowError("Thuế suất riêng phải nhỏ hơn 100%")
            appendix_no = _text(r.get("appendix_no"))
            appendix_signed = _date(r.get("appendix_signed_on"), "Ngày ký phụ lục")
            if bool(appendix_no) != bool(appendix_signed):
                warns.append("Có Số phụ lục HĐ hoặc Ngày ký phụ lục nhưng thiếu cột còn lại — bỏ qua phụ lục")
                appendix_no, appendix_signed = None, None
            party = PartyA(
                legal_name=_text(r.get("a_name")) or "",
                address=_text(r.get("a_address")) or "",
                phone=_digits_text(r.get("a_phone"), "Điện thoại", warns),
                tax_code=_digits_text(r.get("a_tax_code"), "MST", warns),
                bank_account_no=_digits_text(r.get("a_bank_account"), "Số tài khoản", warns),
                bank_name=_text(r.get("a_bank_name")) or "",
                representative_name=_text(r.get("a_representative")) or "",
                representative_title=_text(r.get("a_title")) or "",
            )
            if not party.legal_name:
                raise _RowError("Thiếu Tên Bên A")
        except _RowError as exc:
            issues.append(MasterIssue(tab, rn, str(exc)))
            continue
        issues.extend(MasterIssue(tab, rn, w, "WARNING") for w in warns)
        c = Contract(no, entity, channel, brand, group, model, rate, vf, vt, party, active, rn, signed,
                     tax_rate_override, appendix_no, appendix_signed)
        clash = next((o for o in out if o.active and c.active and o.entity_code == c.entity_code
                      and o.channel == c.channel and o.brand_code == c.brand_code and o.group_code == c.group_code
                      and o.model == c.model and o.overlaps(c.valid_from, c.valid_to or date.max)), None)
        if clash is not None:
            issues.append(MasterIssue(tab, rn, f"Chồng ngày hiệu lực với dòng {clash.row} (cùng pháp nhân, kênh, "
                                               f"{c.scope_label}) — khi tạo BBNT phải chọn số hợp đồng", "WARNING"))
        out.append(c)
    return out


def _parse_mappings(table: Sequence[Sequence[Any]], tab: str, issues: list[MasterIssue]) -> list[OrderMapping]:
    groups: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for rn, r in _rows("mappings", table, issues, tab):
        code = _text(r.get("code"))
        if not code:
            issues.append(MasterIssue(tab, rn, "Thiếu Mã mapping"))
            continue
        groups[code.upper()].append((rn, r))
    out: list[OrderMapping] = []
    for code, rows in groups.items():
        first_rn = rows[0][0]
        try:
            pick = functools.partial(_first_value, rows)
            channel = _code(pick("channel"), "Kênh")
            if not channel:
                raise _RowError("Thiếu Kênh")
            header_row = _int(pick("header_row"), "Dòng tiêu đề", 1)
            data_row = _int(pick("data_row"), "Dòng dữ liệu đầu", header_row + 1)
            fields: list[FieldSpec] = []
            aliases: dict[OrderStatus, frozenset[str]] = {}
            for rn, r in rows:
                name = (_text(r.get("field")) or "").upper()
                try:
                    if name in STATUS_FIELDS:
                        values = [x.strip() for x in re.split(r"[,;\n]", _text(r.get("header")) or "") if x.strip()]
                        if not values:
                            raise _RowError(f"{name}: ghi các giá trị trạng thái ở cột 'Tên cột'")
                        aliases[STATUS_FIELDS[name]] = frozenset(values)
                        continue
                    try:
                        target = TargetField(name)
                    except ValueError as exc:
                        raise _RowError(f"Trường '{name}' không hợp lệ") from exc
                    col = _text(r.get("column"))
                    fields.append(FieldSpec(target, header=_text(r.get("header")),
                                            column=col.upper() if col else None,
                                            required=_bool(r.get("required"), False)))
                except (_RowError, MappingError) as exc:
                    issues.append(MasterIssue(tab, rn, str(exc)))
            status_aliases = None
            if aliases:     # cộng thêm vào trạng thái mặc định; giá trị khai ở MAPPING thắng nếu trùng
                claimed = {normalize_text(v) for vs in aliases.values() for v in vs}
                status_aliases = {
                    st: frozenset(aliases.get(st, frozenset()))
                    | frozenset(v for v in DEFAULT_STATUS_ALIASES.get(st, ()) if normalize_text(v) not in claimed)
                    for st in {*DEFAULT_STATUS_ALIASES, *aliases}}
            mapping = OrderMapping(code, channel, _text(pick("tab")), header_row, data_row, tuple(fields),
                                   status_aliases, _bool(pick("default"), False), first_rn)
            mapping.to_config()   # kiểm tra đủ trường bắt buộc
        except (_RowError, MappingError) as exc:
            issues.append(MasterIssue(tab, first_rn, f"Mapping {code}: {exc}"))
            continue
        out.append(mapping)
    return out


def _first_value(rows: Sequence[tuple[int, dict[str, Any]]], key: str) -> Any:
    """Giá trị đầu tiên khác trống của cột ``key`` trong các dòng cùng 1 mapping."""
    return next((r[key] for _, r in rows if not _blank(r.get(key))), None)


# =============================================================================
# Entry point
# =============================================================================
TabReader = Callable[[str], Sequence[Sequence[Any]] | None]


def load_master(read_tab: TabReader, now: datetime | None = None) -> MasterData:
    """``read_tab(tên_tab)`` trả về ma trận ô hoặc ``None`` nếu tab không tồn tại (thử lần lượt các tên)."""
    issues: list[MasterIssue] = []
    tables: dict[str, tuple[str, Sequence[Sequence[Any]]]] = {}
    for kind, names in TABS.items():
        for name in names:
            t = read_tab(name)
            if t is not None:
                tables[kind] = (name, t)
                break
        else:
            issues.append(MasterIssue(names[0], 1, f"Không tìm thấy tab {names[0]}"))
            tables[kind] = (names[0], [])
    entities = _parse_entities(tables["entities"][1], tables["entities"][0], issues)
    brands = _parse_brands(tables["brands"][1], tables["brands"][0], issues)
    contracts = _parse_contracts(tables["contracts"][1], tables["contracts"][0], issues, entities, brands)
    mappings = _parse_mappings(tables["mappings"][1], tables["mappings"][0], issues)
    return MasterData(entities, tuple(brands), tuple(contracts), tuple(mappings), tuple(issues),
                      now or datetime.now(UTC))
