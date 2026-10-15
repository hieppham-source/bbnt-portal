"""Fixture chung: Sheet master mẫu (deploy/MASTER_BBNT_TEMPLATE.xlsx) + file order giả lập."""
from __future__ import annotations

import copy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.master.repository import xlsx_source
from app.services.sheets import SheetAccessError, SheetRef

ROOT = Path(__file__).resolve().parents[1]
MASTER_XLSX = ROOT / "deploy" / "MASTER_BBNT_TEMPLATE.xlsx"

ORDER_URL_FB = "https://docs.google.com/spreadsheets/d/1OrderFbAbCdEfGhIjKlMnOpQr/edit#gid=0"
ORDER_URL_SHISEIDO = "https://docs.google.com/spreadsheets/d/1OrderShiseidoAbCdEfGhIjKl/edit"

ORDER_HEADER = ["Ngày đặt hàng", "Mã đơn hàng", "Trạng thái", "Brand", "SKU", "Tên sản phẩm", "Số lượng",
                "Doanh thu sau voucher"]


def order_rows(lines: list[list[Any]]) -> list[list[Any]]:
    """Tab ORDER: dòng 1 tiêu đề báo cáo, dòng 2 header (khớp mapping FB_ORDER: header 2, data 3)."""
    return [["BÁO CÁO ĐƠN HÀNG"], ORDER_HEADER, *lines]


ORGANICA_MAY = order_rows([
    ["03/05/2026 10:00", "FB001", "Đã giao", "Organica", "ORG-01", "Serum Organica", 2, 1_000_000],
    ["10/05/2026", "FB002", "Giao thành công", "ORGANICA", "ORG-02", "Kem dưỡng Organica", 1, 2_500_000],
    ["12/05/2026", "FB003", "Hoàn hàng", "Organica", "ORG-01", "Serum Organica", 1, 500_000],
    ["15/05/2026", "FB004", "Đã hủy", "Organica", "ORG-02", "Kem dưỡng Organica", 1, 2_500_000],
    ["20/05/2026", "FB005", "Đã giao", "Anessa", "ANS-01", "Kem chống nắng", 1, 9_999_000],   # brand khác
    ["02/06/2026", "FB006", "Đã giao", "Organica", "ORG-01", "Serum Organica", 1, 700_000],   # ngoài kỳ
])

SHISEIDO_Q3 = order_rows([
    ["05/07/2026", "S001", "Đã giao", "Shiseido", "SHI-01", "Ultimune", 2, 10_000_000],
    ["05/08/2026", "S002", "Đã giao", "Anessa", "ANS-01", "Anessa Gold", 5, 5_000_000],
    ["05/09/2026", "S003", "Trả hàng", "Anessa", "ANS-01", "Anessa Gold", 1, 1_000_000],
])


class FakeSheets:
    """SheetReader giả: map spreadsheet_id -> rows. Đếm số lần đọc để kiểm tra không cache file order."""

    def __init__(self, sheets: dict[str, list[list[Any]]]) -> None:
        self.sheets = copy.deepcopy(sheets)
        self.calls: list[tuple[str, str | None]] = []

    def read(self, ref: SheetRef, *, sheet_name: str | None = None) -> list[list[Any]]:
        self.calls.append((ref.spreadsheet_id, sheet_name))
        if ref.spreadsheet_id not in self.sheets:
            raise SheetAccessError("Tài khoản của bạn không mở được sheet — kiểm tra link và nhờ chủ file "
                                   "share quyền Người xem cho email của bạn")
        return copy.deepcopy(self.sheets[ref.spreadsheet_id])


def sheet_id(url: str) -> str:
    return url.split("/d/")[1].split("/")[0]


@pytest.fixture
def master_tables() -> dict[str, list[list[Any]]]:
    """Bản sao các tab master — test sửa thoải mái."""
    return {k: [list(r) for r in v] for k, v in xlsx_source(str(MASTER_XLSX))().items()}


@pytest.fixture
def fake_sheets() -> FakeSheets:
    return FakeSheets({sheet_id(ORDER_URL_FB): ORGANICA_MAY, sheet_id(ORDER_URL_SHISEIDO): SHISEIDO_Q3})


def vn_clock(y: int, m: int, d: int, hh: int = 9) -> Any:
    moment = datetime(y, m, d, hh, tzinfo=UTC)
    return lambda: moment
