"""Tab Tổng quan (chốt 04/11/2026): đã thu / pending / estimate / cancel trung bình. Dữ liệu giả."""
from datetime import date
from decimal import Decimal

from app.recon.summary import summarize

from .test_master_data_brand import HEADER as DB_HEADER
from .test_master_data_brand import row as db_row
from .test_recon_worker import row, sheet

DATA_BRAND = [DB_HEADER, db_row("A", "ALPHA", "Cty EZS", "CÒN HIỆU LỰC", "1/A", rate="20%"),
              db_row("B", "BETA", "Cty EZS", "HẾT HIỆU LỰC", "2/B", rate="30%")]


def test_summary_buckets():
    values = sheet(
        row("ALPHA", "MIX - 09/2026", "", "EZS", nmv=900, k=180, gmv=1000, status="Done trả tiền"),   # đã thu
        row("ALPHA", "MIX - 10/2026", "", "EZS", nmv=800, k=160, gmv=1000, status="Đã xuất hóa đơn + Chờ tiền"),
        row("ALPHA", "MIX - 11/2026", "", "EZS", gmv=500),           # estimate 500 × 20%
        row("BETA", "MIX - 11/2026", "", "EZS", gmv=700),            # HĐ hết hiệu lực -> không có %
    )
    s = summarize(values, DATA_BRAND).total
    assert (s.collected, s.pending, s.estimate, s.no_rate) == (Decimal(180), Decimal(160), Decimal(100), 1)
    assert s.cancel_rate == Decimal("0.15")                       # 1 − 1700/2000
    assert summarize(values, DATA_BRAND, pic="nobody").total.rows == 0


def test_summary_route_registered():
    from app.api.v1.summary import router
    assert [r.path for r in router.routes] == ["/api/v1/summary"]


def test_overdue_by_brand():
    late = row("ALPHA", "MIX - 08/2026", "", "EZS", nmv=800, k=160, gmv=1000, status="Đã xuất hóa đơn + Chờ tiền")
    late[19] = "HĐ: 101 (01/10/2026)"          # hạn = 01/10 + 10 ngày làm việc = 15/10
    paid = row("BETA", "MIX - 08/2026", "", "EZS", nmv=800, k=99, gmv=1000, status="Done trả tiền")
    paid[19] = "HĐ: 102 (01/10/2026)"
    s = summarize(sheet(late, paid), DATA_BRAND, today=date(2026, 10, 25))
    assert {b: (x.amount, x.days) for b, x in s.overdue.items()} == {"ALPHA": (Decimal(160), 10)}
