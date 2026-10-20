"""Unit tests cho Numbering Engine (pure, không cần DB)."""
from __future__ import annotations

import itertools
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.core.numbering import (
    BbntNumber,
    BrandRef,
    NumberingError,
    format_period,
    generate_bbnt_number,
    issue_month_of,
    normalize_months,
    parse_bbnt_number,
    parse_period,
    resolve_channel_code,
    resolve_entity_code,
    resolve_scope_code,
)

UTC = UTC
ORGANICA = [BrandRef("ORGANICA")]


def gen(**kw):
    base = dict(brands=ORGANICA, entity="EZS", channel="FB", suffix="BDAY 05",
                issued_at=datetime(2026, 6, 10, 3, 0, tzinfo=UTC))
    base.update(kw)
    return generate_bbnt_number(**base)


# --------------------------------------------------------------------------- PERIOD
class TestFormatPeriod:
    @pytest.mark.parametrize("months, expected", [
        (["05.26"], "05.26"),
        ([(2026, 5)], "05.26"),
        ([date(2026, 5, 31)], "05.26"),
        (["2026-05"], "05.26"),
        (["5.26"], "05.26"),
        (["05/2026"], "05.26"),
        # liên tục
        (["07.26", "08.26", "09.26"], "07-09.26"),
        (["07.26", "08.26"], "07-08.26"),
        (["09.26", "07.26", "08.26"], "07-09.26"),                 # input lộn xộn
        (["01.26", "02.26", "03.26", "04.26", "05.26", "06.26",
          "07.26", "08.26", "09.26", "10.26", "11.26", "12.26"], "01-12.26"),
        # không liên tục
        (["05.26", "07.26"], "05,07.26"),
        (["05.26", "07.26", "09.26"], "05,07,09.26"),
        (["09.26", "05.26", "07.26", "05.26", "09.26"], "05,07,09.26"),  # trùng + lộn xộn
        (["05.26", "07.26", "08.26", "09.26"], "05,07,08,09.26"),        # có 1 lỗ -> liệt kê hết
        # trùng hết -> 1 tháng
        (["05.26", (2026, 5), date(2026, 5, 1)], "05.26"),
        # qua năm
        (["11.25", "12.25", "01.26"], "11.25-01.26"),
        (["12.25", "01.26"], "12.25-01.26"),
        (["10.25", "12.25", "01.26"], "10.25,12.25,01.26"),
    ])
    def test_formats(self, months, expected):
        assert format_period(months) == expected

    @pytest.mark.parametrize("bad", [[], ["13.26"], ["00.26"], ["2026"], ["abc"], [(2026, 0)], ["05.1999"]])
    def test_rejects_invalid(self, bad):
        with pytest.raises(NumberingError):
            format_period(bad)

    def test_same_month_different_years_not_merged(self):
        assert format_period(["05.25", "05.26"]) == "05.25,05.26"

    @pytest.mark.parametrize("months", [
        ["05.26"], ["07.26", "08.26", "09.26"], ["05.26", "07.26", "09.26"],
        ["11.25", "12.25", "01.26"], ["10.25", "12.25", "01.26"], ["05.26", "07.26", "08.26"],
    ])
    def test_parse_roundtrip(self, months):
        label = format_period(months)
        assert format_period(parse_period(label)) == label
        assert parse_period(label) == normalize_months(months)

    @pytest.mark.parametrize("bad", ["09-07.26", "07,05.26", "05,05.26", "01.26-11.25", "5.26", "05-.26"])
    def test_parse_rejects(self, bad):
        with pytest.raises(NumberingError):
            parse_period(bad)


# --------------------------------------------------------------------------- TIMEZONE
class TestIssueMonthTimezone:
    @pytest.mark.parametrize("utc_moment, expected", [
        (datetime(2026, 5, 31, 16, 59, 59, tzinfo=UTC), "05.26"),   # 23:59:59 VN
        (datetime(2026, 5, 31, 17, 0, 0, tzinfo=UTC), "06.26"),     # 00:00:00 VN ngày 01/06
        (datetime(2026, 5, 31, 17, 30, tzinfo=UTC), "06.26"),
        (datetime(2025, 12, 31, 17, 0, tzinfo=UTC), "01.26"),       # sang năm mới theo giờ VN
        (datetime(2025, 12, 31, 16, 59, tzinfo=UTC), "12.25"),
    ])
    def test_utc_converted_to_vn(self, utc_moment, expected):
        assert issue_month_of(utc_moment).label == expected

    def test_other_timezones(self):
        # 31/05 10:30 New York = 31/05 21:30 VN
        assert issue_month_of(datetime(2026, 5, 31, 10, 30, tzinfo=ZoneInfo("America/New_York"))).label == "05.26"
        # 31/05 14:00 New York = 01/06 01:00 VN
        assert issue_month_of(datetime(2026, 5, 31, 14, 0, tzinfo=ZoneInfo("America/New_York"))).label == "06.26"

    def test_naive_datetime_rejected(self):
        with pytest.raises(NumberingError, match="timezone"):
            issue_month_of(datetime(2026, 5, 31, 23, 0))

    def test_clock_injection_used_when_issued_at_missing(self):
        n = generate_bbnt_number(brands=ORGANICA, entity="EZS", channel="FB", suffix="BDAY 05",
                                 clock=lambda: datetime(2026, 5, 31, 17, 1, tzinfo=UTC))
        assert str(n) == "06.26/BBNT-ORGANICA-EZ-FB-BDAY-05"
        assert n.issue_date == date(2026, 6, 1)   # ngày VN, không phải 31/05 UTC

    def test_issue_date_is_vn_date(self):
        n = gen(issued_at=datetime(2026, 6, 30, 18, 0, tzinfo=UTC))
        assert n.issue_date == date(2026, 7, 1) and n.issue_month.label == "07.26"


# --------------------------------------------------------------------------- GENERATE
class TestGenerate:
    def test_issue_month_independent_of_suffix(self):
        """Tháng lập chỉ phụ thuộc issued_at — suffix là chữ người làm tự gõ, không ràng buộc gì với tháng."""
        n = gen(suffix="BDAY 01", issued_at=datetime(2026, 7, 2, 2, 0, tzinfo=UTC))
        assert str(n) == "07.26/BBNT-ORGANICA-EZ-FB-BDAY-01"

    def test_full_examples(self):
        shiseido = [BrandRef("SHISEIDO", "SHISEIDO_GRP"), BrandRef("CLE_DE_PEAU", "SHISEIDO_GRP")]
        n = gen(brands=shiseido, entity="SASSY", channel="TTS", suffix="q3 camp",
                issued_at=datetime(2026, 10, 1, 1, 0, tzinfo=UTC))
        assert str(n) == "10.26/BBNT-SHISEIDO_GRP-SS-TTS-Q3-CAMP"
        assert n.business_key == "BBNT-SHISEIDO_GRP-SS-TTS-Q3-CAMP"
        assert n.scope_prefix == "BBNT-SHISEIDO_GRP-SS-TTS-"

    def test_suffix_is_normalized(self):
        n = gen(suffix="  bday   05  ")
        assert n.suffix == "BDAY-05"
        assert str(n) == "06.26/BBNT-ORGANICA-EZ-FB-BDAY-05"

    def test_blank_suffix_rejected(self):
        with pytest.raises(NumberingError):
            gen(suffix="   ")

    def test_suffix_too_long_rejected(self):
        with pytest.raises(NumberingError):
            gen(suffix="A" * 61)

    def test_revision_suffix(self):
        n = gen().with_revision(2)
        assert str(n) == "06.26/BBNT-ORGANICA-EZ-FB-BDAY-05-R2"
        assert n.business_key == "BBNT-ORGANICA-EZ-FB-BDAY-05"

    def test_negative_revision_rejected(self):
        with pytest.raises(NumberingError):
            gen(revision_no=-1)


class TestScopeEntityChannel:
    def test_single_brand_uses_brand_code_even_if_grouped(self):
        assert resolve_scope_code([BrandRef("shiseido", "SHISEIDO_GRP")]) == "SHISEIDO"

    def test_duplicate_brand_collapses_to_single(self):
        assert resolve_scope_code([BrandRef("ORGANICA"), BrandRef(" organica ")]) == "ORGANICA"

    def test_multi_brand_same_group(self):
        assert resolve_scope_code([BrandRef("A", "grp"), BrandRef("B", "GRP")]) == "GRP"

    def test_multi_brand_different_groups(self):
        with pytest.raises(NumberingError, match="nhiều group"):
            resolve_scope_code([BrandRef("A", "G1"), BrandRef("B", "G2")])

    def test_multi_brand_without_group(self):
        with pytest.raises(NumberingError, match="không thuộc group"):
            resolve_scope_code([BrandRef("A", "G1"), BrandRef("B")])

    @pytest.mark.parametrize("code", ["ORGA-NICA", "A/B", "A.B", "A,B", "", "  ", "CÀ PHÊ"])
    def test_code_with_separator_rejected(self, code):
        with pytest.raises(NumberingError):
            resolve_scope_code([BrandRef(code)])

    def test_empty_brands(self):
        with pytest.raises(NumberingError):
            resolve_scope_code([])

    @pytest.mark.parametrize("raw, code", [("EZS", "EZ"), ("ezs", "EZ"), ("EZ", "EZ"),
                                           ("SASSY", "SS"), ("Sassy", "SS"), ("SS", "SS")])
    def test_entity(self, raw, code):
        assert resolve_entity_code(raw) == code

    @pytest.mark.parametrize("raw", ["OM", "", "EZSS"])
    def test_entity_unknown(self, raw):
        with pytest.raises(NumberingError):
            resolve_entity_code(raw)

    @pytest.mark.parametrize("raw, code", [("FB", "FB"), ("fb", "FB"), (" tts ", "TTS")])
    def test_channel(self, raw, code):
        assert resolve_channel_code(raw) == code

    @pytest.mark.parametrize("raw", ["SHP", "IG", ""])
    def test_channel_unsupported(self, raw):
        with pytest.raises(NumberingError):
            resolve_channel_code(raw)


# --------------------------------------------------------------------------- PARSE
class TestParseNumber:
    @pytest.mark.parametrize("value", [
        "06.26/BBNT-ORGANICA-EZ-FB-BDAY-05",
        "06.26/BBNT-ORGANICA-EZ-FB-BDAY-05-R1",
        "10.26/BBNT-SHISEIDO_GRP-SS-TTS-Q3-CAMP",
        "10.26/BBNT-SHISEIDO_GRP-SS-TTS-Q3-CAMP-R12",
        "10.26/BBNT-X-EZ-FB-05-07-09",
        "02.26/BBNT-X-EZ-FB-A",
        "02.26/BBNT-X-EZ-FB-A-R3",
    ])
    def test_roundtrip(self, value):
        assert str(parse_bbnt_number(value)) == value

    @pytest.mark.parametrize("value", [
        "6.26/BBNT-ORGANICA-EZ-FB-BDAY-05",
        "06.26/BBNT-ORGANICA-EZ-FB-",
        "06.26/BBNT-ORGANICA-EZ-FB-bday-05",
        "06.26/BBNT-ORGANICA-EZ-FB-BDAY--05",
        "06.26/BNT-ORGANICA-EZ-FB-BDAY-05",
    ])
    def test_rejects(self, value):
        with pytest.raises(NumberingError):
            parse_bbnt_number(value)

    def test_generated_numbers_always_parse(self):
        """Property-style: mọi tổ hợp sinh ra đều parse ngược đúng."""
        suffix_pool = ["BDAY 01", "Q3 CAMP", "DOT-2", "MIX FOOD 09"]
        issued = datetime(2026, 5, 1, tzinfo=UTC)
        for suffix in suffix_pool:
            for entity, channel in itertools.product(["EZS", "SASSY"], ["FB", "TTS"]):
                n = gen(suffix=suffix, entity=entity, channel=channel, issued_at=issued)
                back = parse_bbnt_number(str(n))
                assert back == n and str(back) == str(n)


def test_bbnt_number_is_hashable_value_object():
    a, b = gen(), gen(issued_at=datetime(2026, 6, 20, tzinfo=UTC))
    assert a == b and hash(a) == hash(b)               # issue_date không tham gia so sánh
    assert isinstance(a, BbntNumber)


def test_month_boundary_sweep():
    """Quét mỗi 30 phút quanh nửa đêm VN 31/05 -> 01/06: tháng lập đổi đúng tại 17:00 UTC."""
    start = datetime(2026, 5, 31, 15, 0, tzinfo=UTC)
    for i in range(9):
        t = start + timedelta(minutes=30 * i)
        assert issue_month_of(t).label == ("06.26" if t >= datetime(2026, 5, 31, 17, tzinfo=UTC) else "05.26")
