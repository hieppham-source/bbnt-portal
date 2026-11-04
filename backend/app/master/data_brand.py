"""Tab ``DATA BRAND`` của FB.JT -> tab ``BRAND`` + ``HOP_DONG`` của Sheet master (anh Hiệp chốt 04/11/2026).

Brand, hợp đồng, Bên A, email nhận thư của kênh FB lấy hết từ DATA BRAND; Sheet master chỉ còn giữ pháp nhân
Bên B (PHAP_NHAN) và mapping file order (MAPPING). Portal chỉ lập BBNT kênh FB.

Quy tắc (chốt 04/11/2026):
* Hợp đồng dùng được = cột STATUS ``CÒN HIỆU LỰC`` (hoặc ``Đã ký``), **không xét ngày** -> "Hiệu lực từ" để
  01/01/2000, "Hiệu lực đến" trống; NGÀY KÝ HỢP ĐỒNG chỉ để ghi vào BBNT.
* % hoa hồng = cột ``COMMISSION (chưa bao gồm VAT)`` (theo hợp đồng, không dùng cột CMS).
* Phụ lục: 2 cột ``SỐ PHỤ LỤC`` + ``NGÀY KÝ PHỤ LỤC`` (thiếu 1 trong 2 -> bỏ qua phụ lục, như Sheet master).
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from app.core.calculation import normalize_text

DATA_BRAND_TAB = "DATA BRAND"
CHANNEL = "FB"
_NO_DATE_CHECK = "01/01/2000"
_ACTIVE = {"con hieu luc", "da ky"}

# key -> cách nhận cột (header đã normalize_text): khớp đúng hoặc bắt đầu bằng
_EXACT = {"group": "group", "brand": "brand", "op": "op", "entity": "phap nhan b", "status": "status",
          "a_tax_code": "ma so thue", "a_representative": "nguoi ky", "a_title": "chuc vu",
          "email": "email nhan hoa don"}
_PREFIX = {"contract_no": "so hop dong", "signed_on": "ngay ky hop dong", "rate": "commission (chua",
           "a_name": "phap nhan a", "a_address": "dia chi ben a", "a_bank_account": "tai khoan ngan hang",
           "a_phone": "so dien thoai", "appendix_no": "so phu luc", "appendix_signed_on": "ngay ky phu luc"}

BRAND_HEADER = ["Kênh", "Mã brand", "Tên brand", "Mã group", "Tên group", "Đang hợp tác"]
CONTRACT_HEADER = ["Số hợp đồng", "Pháp nhân", "Kênh", "Mã brand", "Mã group", "Mô hình", "% hoa hồng", "Hiệu lực từ",
                   "Hiệu lực đến", "Ngày ký", "Tên Bên A", "MST", "Địa chỉ", "Điện thoại", "Số tài khoản",
                   "Người đại diện", "Chức vụ", "Trạng thái", "Số phụ lục HĐ", "Ngày ký phụ lục"]


def brand_code(name: Any) -> str:
    """'LA ROCHE POSAY' -> 'LA_ROCHE_POSAY', 'À ƠI CONCEPT' -> 'A_OI_CONCEPT' (mã brand của Sheet master)."""
    return re.sub(r"[^A-Z0-9]+", "_", normalize_text(name).upper()).strip("_")


def entity_of(text: Any) -> str:
    """'Cty EZS' -> EZS, 'HKD SASSY' -> SASSY; khác -> giữ nguyên (loader báo lỗi pháp nhân lạ)."""
    words = set(normalize_text(text).split())
    hits = {c for c, keys in (("EZS", {"ezs", "ez"}), ("SASSY", {"sassy", "ss"})) if words & keys}
    return hits.pop() if len(hits) == 1 else normalize_text(text).upper()


def data_brand_columns(values: Sequence[Sequence[Any]]) -> tuple[int, dict[str, int]]:
    for i, row in enumerate(values[:10]):
        norm = [normalize_text(c) for c in row]
        if "brand" in norm and any(h.startswith("so hop dong") for h in norm):
            col: dict[str, int] = {}
            for j, h in enumerate(norm):
                for k, name in _EXACT.items():
                    if h == name:
                        col.setdefault(k, j)
                for k, pre in _PREFIX.items():
                    if h.startswith(pre):
                        col.setdefault(k, j)
            return i, col
    raise ValueError("Tab DATA BRAND không có dòng tiêu đề chứa 'BRAND' và 'SỐ HỢP ĐỒNG'")


def data_brand_tables(values: Sequence[Sequence[Any]]) -> tuple[list[list[Any]], list[list[Any]]]:
    """Ma trận tab BRAND và HOP_DONG (đúng header của Sheet master) dựng từ DATA BRAND."""
    h, col = data_brand_columns(values)

    def cell(row: Sequence[Any], key: str) -> Any:
        j = col.get(key)
        return "" if j is None or j >= len(row) or row[j] is None else row[j]

    brands: list[list[Any]] = [BRAND_HEADER]
    seen: set[str] = set()
    rows: list[tuple[str, str, list[Any]]] = []        # (mã brand, mã group, dòng HOP_DONG chưa có Mã brand/group)
    for row in values[h + 1:]:
        name = " ".join(str(cell(row, "brand")).split())
        code = brand_code(name)
        if not code:
            continue
        group = " ".join(str(cell(row, "group")).split())
        if code not in seen:
            seen.add(code)
            brands.append([CHANNEL, code, name, brand_code(group), group, "x"])
        no = " ".join(str(cell(row, "contract_no")).split())
        if not no:
            continue
        active = normalize_text(cell(row, "status")) in _ACTIVE
        rows.append((code, brand_code(group), [
            no, entity_of(cell(row, "entity")), CHANNEL,
            "OP" if normalize_text(cell(row, "op")) == "op" else "NON_OP",
            cell(row, "rate"), _NO_DATE_CHECK, "", cell(row, "signed_on"), cell(row, "a_name"),
            cell(row, "a_tax_code"), cell(row, "a_address"), cell(row, "a_phone"), cell(row, "a_bank_account"),
            cell(row, "a_representative"), cell(row, "a_title"), "x" if active else "het han",
            cell(row, "appendix_no"), cell(row, "appendix_signed_on")]))
    # Nhiều brand cùng group dùng chung 1 hợp đồng (vd ABBOTT: ENSURE/GLUCERNA/PEDIASURE) -> 1 dòng theo GROUP,
    # để BBNT gộp group tìm được hợp đồng; còn lại 1 dòng theo brand.
    shared: dict[tuple[Any, ...], set[str]] = {}
    def key(group: str, r: list[Any]) -> tuple[Any, ...]:      # cùng group + số HĐ + pháp nhân + mô hình + % + status
        return (group, *map(str, r[:5]), r[15])
    for code, group, r in rows:
        if group:
            shared.setdefault(key(group, r), set()).add(code)
    contracts: list[list[Any]] = [CONTRACT_HEADER]
    done: set[tuple[Any, ...]] = set()
    for code, group, r in rows:
        k = key(group, r)
        if group and len(shared[k]) > 1:
            if k not in done:          # Bên A lấy theo dòng đầu tiên của group
                done.add(k)
                contracts.append([*r[:3], "", group, *r[3:]])
        else:
            contracts.append([*r[:3], code, "", *r[3:]])
    return brands, contracts


def with_data_brand(tables: Mapping[str, Any], values: Sequence[Sequence[Any]]) -> dict[str, Any]:
    """Tab của Sheet master, thay BRAND + HOP_DONG bằng bản dựng từ DATA BRAND."""
    drop = {"brand", "brands", "hop_dong", "hop dong"}
    out = {k: v for k, v in tables.items() if normalize_text(k) not in drop}
    out["BRAND"], out["HOP_DONG"] = data_brand_tables(values)
    return out


def with_data_brand_source(master: Callable[[], Mapping[str, Any]],
                           data_brand: Callable[[], Sequence[Sequence[Any]]]) -> Callable[[], dict[str, Any]]:
    return lambda: with_data_brand(master(), data_brand())
