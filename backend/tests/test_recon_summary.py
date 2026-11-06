"""Tab Tổng quan (chốt 04–05/11/2026): đã thu / pending / ETA / cancel, theo group. Dữ liệu giả."""
from datetime import date
from decimal import Decimal

from app.recon.summary import summarize

from .test_recon_worker import row, sheet


def at(month, r):
    r[0] = month
    return r


def test_summary_buckets_and_groups():
    values = sheet(
        at("9", row("ALPHA", "MIX - 09/2026", "", "EZS", group="G1", nmv=900, k=180, gmv=1000,
                    status="Done trả tiền")),                                                  # đã thu
        at("10", row("ALPHA", "MIX - 10/2026", "", "EZS", group="G1", nmv=800, k=160, gmv=1000,
                     status="Đã xuất hóa đơn + Chờ tiền")),                                    # pending
        at("11", row("BETA", "MIX - 11/2026", "", "EZS", gmv=500)),          # chưa nghiệm thu: 500 × 22% × 80%
        at("11", row("GAMMA", "MIX - 11/2026", "", "EZS", op="OP", nmv=1, k=999, gmv=5000,
                     status="Done trả tiền")),                                                 # OP -> không tính
    )
    s = summarize(values)
    t = s.total
    assert (t.collected, t.pending, t.estimate, t.eta) == (Decimal(180), Decimal(160), Decimal(88), Decimal(428))
    assert t.cancel_rate == Decimal("0.15")                       # 1 − 1700/2000
    assert s.by_group["G1"].last == {"collected": "9", "pending": "10", "eta": "10"}
    assert s.by_group["BETA"].estimate == Decimal(88)              # group trống -> brand
    assert summarize(values, pic="nobody").total.rows == 0
    assert "GAMMA" not in s.by_group and t.rows == 3


def test_summary_route_registered():
    from app.api.v1.summary import router
    assert [r.path for r in router.routes] == ["/api/v1/summary"]


def test_overdue_by_group_brand():
    late = row("ALPHA", "MIX - 08/2026", "", "EZS", group="GRP", nmv=800, k=160, gmv=1000,
               status="Đã xuất hóa đơn + Chờ tiền")
    late[19] = "HĐ: 101 (01/10/2026)"          # hạn = 01/10 + 10 ngày làm việc = 15/10
    paid = row("BETA", "MIX - 08/2026", "", "EZS", nmv=800, k=99, gmv=1000, status="Done trả tiền")
    paid[19] = "HĐ: 102 (01/10/2026)"
    s = summarize(sheet(late, paid), today=date(2026, 10, 25))
    assert {b: (x.amount, x.days) for b, x in s.overdue.items()} == {"GRP": (Decimal(160), 10)}   # theo GROUP BRAND


def test_fbjt_date_month_first_only_when_unambiguous():
    from app.recon.fbjt import parse_date
    assert parse_date("7/30/2026") == date(2026, 7, 30)       # 30 không thể là tháng -> tháng/ngày
    assert parse_date("7/8/2026") == date(2026, 8, 7)         # mơ hồ -> giữ ngày/tháng
    assert parse_date("30/07/2026") == date(2026, 7, 30)
