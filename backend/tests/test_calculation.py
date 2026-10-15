"""Unit tests cho Calculation Engine."""
from __future__ import annotations

import unicodedata
from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.core.calculation import (
    DEFAULT_TAX_RULES,
    FieldSpec,
    IssueCode,
    MappingConfig,
    MappingError,
    OrderStatus,
    Severity,
    StatusClassifier,
    TaxMode,
    TaxPolicy,
    TaxRuleNotFoundError,
    calculate_commission,
    column_index,
    compute_nmv,
    normalize_text,
    parse_money,
    parse_order_datetime,
    parse_quantity,
    parse_sheet,
    reconcile,
    resolve_tax_policy,
    rows_from_xlsx,
)
from app.core.calculation import (
    TargetField as F,
)

D = Decimal
VN = ZoneInfo("Asia/Ho_Chi_Minh")
UTC = UTC
EZS = DEFAULT_TAX_RULES["EZS"][0]
SASSY = DEFAULT_TAX_RULES["SASSY"][0]

HEADER = ["Ngày đặt hàng", "Mã đơn hàng", "Hãng", "Mã sản phẩm *", "Tên sản phẩm", "Số lượng",
          "Giá sản phẩm", "Số tiền giảm", "Trạng thái", "Total GMV", "GMV SAU VOUCHER (IFANY)"]

CONFIG = MappingConfig(
    fields=(
        FieldSpec(F.ORDER_DATE, "Ngày đặt hàng"),
        FieldSpec(F.ORDER_CODE, "Mã đơn hàng", required=True),
        FieldSpec(F.BRAND_NAME, "Hãng"),
        FieldSpec(F.SKU, "Mã sản phẩm *"),
        FieldSpec(F.PRODUCT_NAME, "Tên sản phẩm"),
        FieldSpec(F.QUANTITY, "Số lượng"),
        FieldSpec(F.UNIT_PRICE, "Giá sản phẩm"),
        FieldSpec(F.DISCOUNT, "Số tiền giảm"),
        FieldSpec(F.ORDER_STATUS, "Trạng thái", required=True),
        FieldSpec(F.GMV, "Total GMV"),
        FieldSpec(F.GMV_AFTER_VOUCHER, "GMV SAU VOUCHER (IFANY)"),
    ),
    header_row=2, data_start_row=3,   # dòng 1 là dòng tổng như file ORGANICA
)


def row(date_, code, status, gmv, *, after=None, qty=1, price=None, disc=0, sku="SKU1", name="Táo đỏ",
        brand="ORGANICA"):
    return [date_, code, brand, sku, name, qty, price if price is not None else gmv, disc, status, gmv, after]


def sheet(*rows):
    return [[None] * 9 + [999, 999], HEADER, *rows]


# =========================================================================== text / status
class TestStatus:
    clf = StatusClassifier()

    @pytest.mark.parametrize("raw", ["Đã giao", "ĐÃ GIAO", "đã giao", "  Đã   giao ", "Đã giao",
                                     unicodedata.normalize("NFD", "Đã giao")])
    def test_delivered_variants(self, raw):
        assert self.clf.classify(raw) is OrderStatus.DELIVERED

    @pytest.mark.parametrize("raw", ["Hủy đơn", "Huỷ đơn", "HỦY ĐƠN", "Đã hủy", "Đã huỷ"])
    def test_cancel_tone_mark_position(self, raw):
        # 'Hủy' (dấu trên u) và 'Huỷ' (dấu trên y) là 2 chuỗi Unicode khác nhau
        assert self.clf.classify(raw) is OrderStatus.CANCELLED

    @pytest.mark.parametrize("raw", ["Hoàn hàng", "hoàn hàng", "Đã hoàn hàng", "Trả hàng"])
    def test_returned(self, raw):
        assert self.clf.classify(raw) is OrderStatus.RETURNED

    @pytest.mark.parametrize("raw", [None, "", "  ", "Đang giao", "Chờ lấy hàng", "Giao thất bại", "Đã giao?", 1])
    def test_unknown(self, raw):
        assert self.clf.classify(raw) is OrderStatus.UNKNOWN

    def test_custom_aliases_for_tiktok(self):
        clf = StatusClassifier({OrderStatus.DELIVERED: ["Đã giao", "Đã hoàn tất"],
                                OrderStatus.CANCELLED: ["Đã hủy"]})
        assert clf.classify("Đã hoàn tất") is OrderStatus.DELIVERED
        assert clf.classify("Hoàn hàng") is OrderStatus.UNKNOWN   # alias không khai báo -> unknown

    def test_conflicting_alias_rejected(self):
        with pytest.raises(ValueError):
            StatusClassifier({OrderStatus.DELIVERED: ["Xong"], OrderStatus.CANCELLED: ["xong"]})

    def test_normalize_text(self):
        assert normalize_text("  Mã  SẢN phẩm * ") == "ma san pham *"
        assert normalize_text("Gía") == normalize_text("Giá")       # typo dấu trong file brand


# =========================================================================== money / qty / date
class TestParseMoney:
    @pytest.mark.parametrize("raw, expected", [
        (463500, D("463500")), (463500.0, D("463500.0")), (1272997.44, D("1272997.44")),
        (D("10.5"), D("10.5")), ("463500", D("463500")),
        ("1.250.000", D("1250000")), ("1,250,000", D("1250000")), ("1.250.000 ₫", D("1250000")),
        ("1.250.000đ", D("1250000")), ("1 250 000 VND", D("1250000")), ("1 250 000", D("1250000")),
        ("1.250", D("1250")), ("1,250", D("1250")),
        ("1.234,56", D("1234.56")), ("1,234.56", D("1234.56")), ("1,5", D("1.5")), ("0.18", D("0.18")),
        ("-1.000", D("-1000")), ("(1.000)", D("-1000")),
        (None, None), ("", None), ("  ", None), ("-", None),
    ])
    def test_values(self, raw, expected):
        assert parse_money(raw) == expected

    @pytest.mark.parametrize("raw", ["abc", "1.2.3", "12.34.567", "1,23,456", True, float("nan"), "1..000", ","])
    def test_invalid(self, raw):
        with pytest.raises(ValueError):
            parse_money(raw)

    def test_float_has_no_binary_noise(self):
        assert parse_money(0.1) + parse_money(0.2) == D("0.3")

    def test_quantity(self):
        assert parse_quantity("2") == 2 and parse_quantity(3.0) == 3 and parse_quantity(None) is None
        with pytest.raises(ValueError):
            parse_quantity("1,5")


class TestOrderDatetime:
    def test_iso_utc_becomes_next_day_in_vn(self):
        dt = parse_order_datetime("2026-05-31T17:30:00Z")
        assert dt == datetime(2026, 6, 1, 0, 30, tzinfo=VN) and dt.month == 6

    def test_iso_with_offset(self):
        assert parse_order_datetime("2026-05-31T23:30:00+07:00").month == 5

    def test_naive_string_is_vn_by_default(self):
        assert parse_order_datetime("31/05/2026 23:59") == datetime(2026, 5, 31, 23, 59, tzinfo=VN)

    def test_naive_with_utc_export_setting(self):
        assert parse_order_datetime("31/05/2026 23:59", naive_tz=UTC).date() == date(2026, 6, 1)

    @pytest.mark.parametrize("raw, hour", [
        ("18/05/2026 21:53 CH", 21),     # gặp thật trong file ORGANICA BDAY05 (24h + hậu tố CH)
        ("18/05/2026 09:53 CH", 21),
        ("18/05/2026 09:53 SA", 9),
        ("18/05/2026 12:05 SA", 0),
        ("18/05/2026 12:05 CH", 12),
        ("18/05/2026 9:53 PM", 21),
        ("18/05/2026 09:53 sa", 9),
    ])
    def test_vietnamese_meridiem_suffix(self, raw, hour):
        assert parse_order_datetime(raw).hour == hour

    def test_day_first_vn_format(self):
        assert parse_order_datetime("05/06/2026").date() == date(2026, 6, 5)   # 5 tháng 6, không phải 6 tháng 5

    def test_excel_datetime_object(self):
        assert parse_order_datetime(datetime(2026, 5, 17, 18, 59)).tzinfo is not None

    def test_aware_datetime_converted(self):
        assert parse_order_datetime(datetime(2026, 5, 31, 18, 0, tzinfo=UTC)).date() == date(2026, 6, 1)

    def test_sheets_serial_number(self):
        # 46159 = 2026-05-17 ; .75 = 18:00
        assert parse_order_datetime(46159.75) == datetime(2026, 5, 17, 18, 0, tzinfo=VN)

    def test_date_only(self):
        assert parse_order_datetime(date(2026, 5, 1)) == datetime(2026, 5, 1, tzinfo=VN)

    @pytest.mark.parametrize("raw", [None, "", "   "])
    def test_blank(self, raw):
        assert parse_order_datetime(raw) is None

    def test_garbage(self):
        with pytest.raises(ValueError):
            parse_order_datetime("hôm qua")


# =========================================================================== mapping
class TestMapping:
    def test_requires_status_column(self):
        with pytest.raises(MappingError, match="ORDER_STATUS"):
            MappingConfig(fields=(FieldSpec(F.ORDER_CODE, "Mã"), FieldSpec(F.GMV, "GMV")))

    def test_requires_amount_column(self):
        with pytest.raises(MappingError, match="GMV"):
            MappingConfig(fields=(FieldSpec(F.ORDER_CODE, "Mã"), FieldSpec(F.ORDER_STATUS, "TT")))

    def test_duplicate_target(self):
        with pytest.raises(MappingError, match="2 lần"):
            MappingConfig(fields=(FieldSpec(F.ORDER_CODE, "A"), FieldSpec(F.ORDER_CODE, "B"),
                                  FieldSpec(F.ORDER_STATUS, "C"), FieldSpec(F.GMV, "D")))

    def test_bad_rows(self):
        with pytest.raises(MappingError):
            MappingConfig(fields=CONFIG.fields, header_row=2, data_start_row=2)

    def test_header_not_found_in_file(self):
        rows = [["Mã đơn hàng", "Total GMV"], ["HO1", 100]]
        cfg = MappingConfig(fields=(FieldSpec(F.ORDER_CODE, "Mã đơn hàng"), FieldSpec(F.ORDER_STATUS, "Trạng thái"),
                                    FieldSpec(F.GMV, "Total GMV")))
        with pytest.raises(MappingError, match="ORDER_STATUS"):
            parse_sheet(rows, cfg)

    def test_column_letter_fallback_when_header_renamed(self):
        rows = [["Order ID", "Trạng thái (mới)", "Tiền"], ["HO1", "Đã giao", "100"]]
        cfg = MappingConfig(fields=(FieldSpec(F.ORDER_CODE, "Mã đơn hàng", column="A"),
                                    FieldSpec(F.ORDER_STATUS, "Trạng thái", column="B"),
                                    FieldSpec(F.GMV, "Total GMV", column="C")))
        res = parse_sheet(rows, cfg)
        assert res.lines[0].amount == D("100") and res.lines[0].status is OrderStatus.DELIVERED

    def test_header_match_is_accent_and_case_insensitive(self):
        rows = [["MA DON HANG", "trang thai", "total gmv"], ["HO1", "Đã giao", 5]]
        cfg = MappingConfig(fields=(FieldSpec(F.ORDER_CODE, "Mã đơn hàng"), FieldSpec(F.ORDER_STATUS, "Trạng thái"),
                                    FieldSpec(F.GMV, "Total GMV")))
        assert parse_sheet(rows, cfg).lines[0].amount == 5

    def test_optional_field_missing_is_ok(self):
        rows = [["Mã", "TT", "GMV"], ["HO1", "Đã giao", 5]]
        cfg = MappingConfig(fields=(FieldSpec(F.ORDER_CODE, "Mã"), FieldSpec(F.ORDER_STATUS, "TT"),
                                    FieldSpec(F.GMV, "GMV"), FieldSpec(F.SKU, "SKU")))
        assert parse_sheet(rows, cfg).lines[0].sku is None

    @pytest.mark.parametrize("letter, idx", [("A", 0), ("Z", 25), ("AA", 26), ("AZ", 51), ("BA", 52)])
    def test_column_index(self, letter, idx):
        assert column_index(letter) == idx

    def test_from_db(self):
        class Field:
            def __init__(self, t, h, c=None, r=False):
                self.target_field, self.source_header, self.source_column, self.is_required = t, h, c, r

        class M:
            header_row, data_start_row, sheet_name = 2, 3, "ORDER"
            options = {"status_aliases": {"DELIVERED": ["Giao thành công"]}, "naive_timezone": "UTC"}
            fields = [Field("ORDER_CODE", "Mã"), Field("ORDER_STATUS", "TT"), Field("GMV", "GMV")]

        cfg = MappingConfig.from_db(M)
        assert cfg.sheet_name == "ORDER" and cfg.naive_tz == ZoneInfo("UTC")
        assert StatusClassifier(cfg.status_aliases).classify("giao thanh cong") is OrderStatus.DELIVERED


# =========================================================================== parse_sheet
class TestParseSheet:
    def test_amount_priority_after_voucher_then_gmv_then_qty_price(self):
        rows = sheet(
            row("17/05/2026 18:59", "HO1", "Đã giao", 500, after=450),
            row("17/05/2026 18:59", "HO2", "Đã giao", 500, after=None),
            [None, "HO3", "ORGANICA", "S", "N", 3, "100.000", "10.000", "Đã giao", None, None],
        )
        amounts = [ln.amount for ln in parse_sheet(rows, CONFIG).lines]
        assert amounts == [D(450), D(500), D(290000)]

    def test_blank_rows_skipped_and_short_rows_padded(self):
        rows = sheet(
            [],                                     # Google Sheets trả [] cho dòng trống
            [None, "", None],
            ["17/05/2026", "HO1", "X", "S", "N", 1, 100, 0, "Đã giao", 100],   # thiếu ô cuối
        )
        res = parse_sheet(rows, CONFIG)
        assert res.rows_read == 1 and res.lines[0].amount == D(100) and res.lines[0].row_number == 5

    def test_errors_reported_with_excel_row_numbers(self):
        rows = sheet(
            row("17/05/2026", None, "Đã giao", 100),          # thiếu mã đơn
            row("17/05/2026", "HO2", "Đã giao", "abc"),       # tiền sai
            row("17/05/2026", "HO3", "Đã giao", -5),          # âm
            row("không phải ngày", "HO4", "Đã giao", 1),      # ngày sai
            row("17/05/2026", "HO5", "Đã giao", None, price=None, qty=None) [:9] + [None, None],
            row("17/05/2026", "HO6", "Đã giao", 100),         # ok
        )
        res = parse_sheet(rows, CONFIG)
        assert [ln.order_code for ln in res.lines] == ["HO6"]
        assert [(i.row_number, i.code) for i in res.issues] == [
            (3, IssueCode.MISSING_REQUIRED), (4, IssueCode.INVALID_VALUE), (5, IssueCode.NEGATIVE_AMOUNT),
            (6, IssueCode.INVALID_VALUE), (7, IssueCode.MISSING_REQUIRED)]
        assert all(i.severity is Severity.ERROR for i in res.issues)

    def test_numeric_order_code_kept_as_string(self):
        res = parse_sheet(sheet(row("17/05/2026", 1658589, "Đã giao", 1)), CONFIG)
        assert res.lines[0].order_code == "1658589"

    def test_file_shorter_than_header_row(self):
        with pytest.raises(MappingError):
            parse_sheet([["x"]], CONFIG)


# =========================================================================== NMV
class TestNmv:
    def lines(self, *rows):
        return parse_sheet(sheet(*rows), CONFIG).lines

    def test_only_delivered_counts(self):
        res = compute_nmv(self.lines(
            row("17/05/2026", "HO1", "Đã giao", 1_000_000),
            row("17/05/2026", "HO2", "Hoàn hàng", 200_000),
            row("17/05/2026", "HO3", "Huỷ đơn", 300_000),
            row("17/05/2026", "HO4", "Đang giao", 400_000),
            row("17/05/2026", "HO5", "", 100_000),
        ))
        assert res.nmv == D(1_000_000)
        assert res.gmv_estimated == D(2_000_000)
        assert res.cancellation_rate == D("0.5")
        assert res.counts_by_status == {OrderStatus.DELIVERED: 1, OrderStatus.RETURNED: 1,
                                        OrderStatus.CANCELLED: 1, OrderStatus.UNKNOWN: 2}
        assert [(i.order_code, i.code) for i in res.issues] == [
            ("HO2", IssueCode.RETURNED), ("HO3", IssueCode.CANCELLED),
            ("HO4", IssueCode.UNKNOWN_STATUS), ("HO5", IssueCode.UNKNOWN_STATUS)]
        assert all(i.severity is Severity.WARNING for i in res.issues)
        assert "Đang giao" in res.issues[2].message

    def test_all_cancelled(self):
        res = compute_nmv(self.lines(row("17/05/2026", "HO1", "Hủy đơn", 100)))
        assert res.nmv == 0 and res.cancellation_rate == D(1) and res.items == ()

    def test_empty(self):
        res = compute_nmv([])
        assert res.gmv_estimated == res.nmv == 0 and res.cancellation_rate is None

    def test_period_filter_uses_vn_time(self):
        res = compute_nmv(self.lines(
            row("2026-05-31T16:59:00Z", "IN_MAY", "Đã giao", 100),      # 23:59 VN 31/05
            row("2026-05-31T17:00:00Z", "JUNE_VN", "Đã giao", 200),     # 00:00 VN 01/06
            row("01/05/2026 00:00", "FIRST", "Đã giao", 300),
            row("30/04/2026 23:59", "APRIL", "Đã giao", 400),
        ), period_months=["05.26"])
        assert res.nmv == D(400)
        assert {i.order_code for i in res.issues if i.code is IssueCode.OUT_OF_PERIOD} == {"JUNE_VN", "APRIL"}

    def test_non_contiguous_period_filter(self):
        res = compute_nmv(self.lines(
            row("10/05/2026", "MAY", "Đã giao", 1),
            row("10/06/2026", "JUN", "Đã giao", 10),
            row("10/07/2026", "JUL", "Đã giao", 100),
        ), period_months=["05.26", "07.26"])
        assert res.nmv == D(101)

    def test_missing_date_with_period_is_error(self):
        res = compute_nmv(self.lines(row(None, "HO1", "Đã giao", 1)), period_months=["05.26"])
        assert res.nmv == 0 and res.issues[0].severity is Severity.ERROR

    def test_items_aggregated_delivered_only(self):
        res = compute_nmv(self.lines(
            row("1/5/2026", "HO1", "Đã giao", 100, qty=1, sku="A", name="Táo"),
            row("1/5/2026", "HO2", "Đã giao", 300, qty=3, sku="A", name="Táo"),
            row("1/5/2026", "HO3", "Hủy đơn", 999, qty=9, sku="A", name="Táo"),
            row("1/5/2026", "HO4", "Đã giao", 50, qty=1, sku="B", name="Kỷ tử"),
        ))
        assert [(i.sku, i.quantity, i.revenue) for i in res.items] == [("A", 4, D(400)), ("B", 1, D(50))]
        assert sum(i.revenue for i in res.items) == res.nmv

    def test_group_items_keep_brand(self):
        res = compute_nmv(self.lines(
            row("1/5/2026", "HO1", "Đã giao", 100, sku="X", brand="SHISEIDO"),
            row("1/5/2026", "HO2", "Đã giao", 100, sku="X", brand="CLE_DE_PEAU"),
        ))
        assert len(res.items) == 2

    def test_duplicate_row_warned_but_counted(self):
        r = row("1/5/2026", "HO1", "Đã giao", 100)
        res = compute_nmv(self.lines(r, list(r)))
        assert res.nmv == D(200)
        assert [i.code for i in res.issues] == [IssueCode.DUPLICATE_ROW]


# =========================================================================== commission & tax
class TestCommission:
    def test_ezs_matches_organica_reconciliation_sheet(self):
        r = calculate_commission(D("88402600"), D("0.18"), EZS)
        assert r.revenue == D("15912468.00")
        assert r.vat_amount == D("1272997.44") and r.tax_deduction_amount == 0
        assert r.total_payable == D("17185465.44")

    def test_sassy_deducts_one_percent(self):
        r = calculate_commission(D("100000000"), D("0.15"), SASSY)
        assert (r.revenue, r.tax_deduction_amount, r.vat_amount, r.total_payable) == \
            (D("15000000.00"), D("150000.00"), D("0.00"), D("14850000.00"))

    def test_rounding_half_up_not_bankers(self):
        r = calculate_commission(D("100.50"), D("1"), SASSY)      # 100.50 * 1% = 1.005
        assert r.tax_deduction_amount == D("1.01")

    def test_vnd_integer_quantum(self):
        r = calculate_commission(D("1234567"), D("0.18"), EZS, quantum=D("1"))
        assert r.revenue == D("222222") and r.vat_amount == D("17778") and r.total_payable == D("240000")

    def test_adjustments_e_equals_a_plus_b_minus_c_minus_d_plus_f(self):
        r = calculate_commission(D("1000000"), D("0.2"), EZS, collected_on_behalf=D("50000"), consulting_fee="10000")
        assert r.total_payable == D("200000") + D("16000") - 0 - D("50000") + D("10000")

    def test_zero_base(self):
        assert calculate_commission(0, D("0.18"), EZS).total_payable == 0

    def test_string_and_int_inputs(self):
        assert calculate_commission("1000", "0.1", EZS).revenue == D("100.00")

    @pytest.mark.parametrize("kw", [
        dict(commission_base=100.0, commission_rate=D("0.1")),
        dict(commission_base=D(100), commission_rate=0.1),
    ])
    def test_float_rejected(self, kw):
        with pytest.raises(TypeError, match="float"):
            calculate_commission(tax=EZS, **kw)

    @pytest.mark.parametrize("base, rate", [(D(-1), D("0.1")), (D(1), D("18")), (D(1), D("-0.1")), ("x", D(0))])
    def test_invalid(self, base, rate):
        with pytest.raises(ValueError):
            calculate_commission(base, rate, EZS)

    def test_none_tax(self):
        r = calculate_commission(D(1000), D("0.1"), TaxPolicy(TaxMode.NONE, D(0), "Không thuế"))
        assert r.total_payable == D("100.00")


class TestTaxRules:
    @pytest.mark.parametrize("entity", ["EZS", "ez", " EZ "])
    def test_ezs(self, entity):
        p = resolve_tax_policy(entity, date(2026, 10, 15))
        assert p.mode is TaxMode.VAT_ADD and p.rate == D("0.08")

    def test_sassy(self):
        p = resolve_tax_policy("SS", date(2026, 12, 31))   # ngày cuối hiệu lực (inclusive)
        assert p.mode is TaxMode.TAX_DEDUCT and p.rate == D("0.01")

    @pytest.mark.parametrize("d", [date(2027, 1, 1), date(2025, 6, 30)])
    def test_outside_effective_range_raises(self, d):
        with pytest.raises(TaxRuleNotFoundError):
            resolve_tax_policy("EZS", d)

    def test_unknown_entity(self):
        with pytest.raises(TaxRuleNotFoundError):
            resolve_tax_policy("OM", date(2026, 1, 1))

    def test_policy_validation(self):
        with pytest.raises(TypeError):
            TaxPolicy(TaxMode.VAT_ADD, 0.08, "x")        # type: ignore[arg-type]
        with pytest.raises(ValueError):
            TaxPolicy(TaxMode.NONE, D("0.1"), "x")
        with pytest.raises(ValueError):
            TaxPolicy(TaxMode.VAT_ADD, D("1"), "x")


# =========================================================================== end-to-end
def test_reconcile_end_to_end_ezs_vs_sassy():
    rows = sheet(
        row("17/05/2026 18:59", "HO1", "Đã giao", 600_000, after=500_000),
        row("18/05/2026 10:00", "HO2", "Đã giao", 500_000),
        row("19/05/2026 10:00", "HO3", "Hoàn hàng", 300_000),
        row("20/05/2026 10:00", "HO4", "Hủy đơn", 200_000),
        row("01/06/2026 00:10", "HO5", "Đã giao", 999_999),     # ngoài kỳ
    )
    ezs = reconcile(rows, CONFIG, commission_rate=D("0.18"), tax=EZS, period_months=["05.26"])
    assert ezs.nmv.gmv_estimated == D(1_500_000) and ezs.nmv.nmv == D(1_000_000)
    assert ezs.commission.revenue == D("180000.00") and ezs.commission.total_payable == D("194400.00")
    assert not ezs.has_blocking_errors
    assert [i.code for i in ezs.issues] == [IssueCode.RETURNED, IssueCode.CANCELLED, IssueCode.OUT_OF_PERIOD]

    ss = reconcile(rows, CONFIG, commission_rate=D("0.18"), tax=SASSY, period_months=["05.26"])
    assert ss.commission.total_payable == D("178200.00")


def test_reconcile_flags_blocking_errors():
    res = reconcile(sheet(row("1/5/2026", "HO1", "Đã giao", "abc")), CONFIG, commission_rate=D("0.1"), tax=EZS)
    assert res.has_blocking_errors and res.commission.total_payable == 0


def test_rows_from_xlsx(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "ORDER"
    for r in sheet(row(datetime(2026, 5, 17, 18, 59), "HO1", "Đã giao", 463500)):
        ws.append(r)
    path = tmp_path / "doi_soat.xlsx"
    wb.save(path)
    res = reconcile(rows_from_xlsx(path, "ORDER"), CONFIG, commission_rate=D("0.18"), tax=EZS,
                    period_months=["05.26"])
    assert res.nmv.nmv == D(463500) and res.parse.lines[0].order_date.tzinfo is not None


@pytest.mark.skipif(not __import__("os").environ.get("BBNT_ORGANICA_XLSX"), reason="BBNT_ORGANICA_XLSX chưa set")
def test_golden_organica_bday05_file():
    """File thật 'ORGANICA X OM - DOI SOAT CAM BDAY05.2026.xlsx' (sheet ORDER, header dòng 2).

    File gốc không có cột status -> test gắn 'Đã giao' cho mọi đơn để đối chiếu GMV (A) với sheet SUM.
    """
    import os
    rows = rows_from_xlsx(os.environ["BBNT_ORGANICA_XLSX"], "ORDER")
    rows = [list(r) + ["Trạng thái" if i == 1 else ("Đã giao" if i > 1 and r[9] else None)]
            for i, r in enumerate(rows)]
    cfg = MappingConfig(fields=(
        FieldSpec(F.ORDER_DATE, "Ngày đặt hàng"), FieldSpec(F.ORDER_CODE, "Mã đơn hàng"),
        FieldSpec(F.ORDER_STATUS, "Trạng thái"), FieldSpec(F.SKU, "Mã sản phẩm *"),
        FieldSpec(F.PRODUCT_NAME, "Tên sản phẩm"), FieldSpec(F.QUANTITY, "Số lượng"),
        FieldSpec(F.GMV, "Total GMV"), FieldSpec(F.GMV_AFTER_VOUCHER, "GMV SAU VOUCHER (IFANY)"),
    ), header_row=2, data_start_row=3)
    res = reconcile(rows, cfg, commission_rate=D("0.18"), tax=EZS)
    assert res.nmv.gmv_estimated == D("93357100")                  # = ô 'GMV from HRV' sheet SUM
    assert len(res.parse.lines) == 190
    assert not res.has_blocking_errors
    assert [i.code for i in res.issues] == [IssueCode.DUPLICATE_ROW]   # HO1672671 lặp 2 dòng
