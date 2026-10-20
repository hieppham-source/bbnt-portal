"""CLI chạy offline: Sheet master + file order dạng .xlsx -> file .docx."""
from __future__ import annotations

import functools
from pathlib import Path

import pytest
from openpyxl import Workbook

from app import cli
from app.services.generator import BbntGenerator

from .conftest import MASTER_XLSX, ORGANICA_MAY, vn_clock


@pytest.fixture
def order_xlsx(tmp_path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "ORDER"
    for row in ORGANICA_MAY:
        ws.append(row)
    path = tmp_path / "order.xlsx"
    wb.save(path)
    return path


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    monkeypatch.setattr(cli, "BbntGenerator", functools.partial(BbntGenerator, clock=vn_clock(2026, 6, 3)))


def test_generate_offline(order_xlsx, tmp_path, capsys):
    out = tmp_path / "out"
    code = cli.main(["generate", "--master-file", str(MASTER_XLSX), "--order-file", str(order_xlsx),
                     "--entity", "EZS", "--channel", "FB", "--brand", "ORGANICA", "--suffix", "05",
                     "--contract", "01/2026/HĐDV/EZS-ORGANICA", "--campaign", "BDAY", "--out", str(out)])
    text = capsys.readouterr().out
    assert code == 0, text
    assert "06.26/BBNT-ORGANICA-EZ-FB-05" in text and "816,480" in text
    assert (out / "BBNT_06.26_BBNT-ORGANICA-EZ-FB-05.docx").stat().st_size > 10_000


def test_generate_reports_business_error(order_xlsx, capsys):
    code = cli.main(["generate", "--master-file", str(MASTER_XLSX), "--order-file", str(order_xlsx),
                     "--entity", "SASSY", "--channel", "FB", "--brand", "ORGANICA", "--suffix", "05",
                     "--contract", "01/2026/HĐDV/EZS-ORGANICA", "--campaign", "BDAY", "--dry-run"])
    assert code == 1 and "CONTRACT_NOT_FOUND" in capsys.readouterr().err


def test_check_with_master_file(monkeypatch, capsys):
    monkeypatch.setenv("MASTER_FILE", str(MASTER_XLSX))
    monkeypatch.delenv("MASTER_SHEET_URL", raising=False)
    cli.get_settings.cache_clear()
    try:
        code = cli.main(["check", "--master-file", str(MASTER_XLSX)])
    finally:
        cli.get_settings.cache_clear()
    out = capsys.readouterr().out
    assert "2 hợp đồng" in out and "Template bbnt_ezs_fb_nonop.v2.docx: ok" in out
    assert code == 0 and "OAuth" in out                          # không có service account: đọc bằng OAuth
