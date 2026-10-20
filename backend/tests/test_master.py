"""Sheet master: đọc/kiểm tra từng tab, tra brand/group -> hợp đồng -> mapping, cache."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from app.core.calculation import TaxMode
from app.errors import BbntError
from app.master.index import find_contract, find_mapping, resolve_scope
from app.master.repository import MasterRepository, build_master
from app.services.sheets import SheetAccessError

Tables = dict[str, list[list[Any]]]


def header(t: Tables, tab: str) -> list[Any]:
    return t[tab][0]


def add_row(t: Tables, tab: str, **cols: Any) -> None:
    h = header(t, tab)
    row: list[Any] = [None] * len(h)
    for k, v in cols.items():
        row[h.index(k)] = v
    t[tab].append(row)


def set_cell(t: Tables, tab: str, row: int, col: str, value: Any) -> None:
    """``row`` là số dòng như trên Google Sheets (1 = tiêu đề)."""
    r = t[tab][row - 1]
    i = header(t, tab).index(col)
    r.extend([None] * (i + 1 - len(r)))
    r[i] = value


def messages(m: Any, tab: str) -> list[tuple[int, str, str]]:
    return [(i.row, i.severity, i.message) for i in m.issues if i.tab == tab]


def contract_row(**over: Any) -> dict[str, Any]:
    base = {"Số hợp đồng": "HD-TEST", "Pháp nhân": "EZS", "Mã brand": "ORGANICA", "Mô hình": "NON_OP",
            "% hoa hồng": "20%", "Hiệu lực từ": "01/01/2026", "Tên Bên A": "CÔNG TY TEST", "MST": "0312345678"}
    base.update(over)
    return base


# =========================================================================== loader
class TestLoader:
    def test_template_master_is_clean(self, master_tables):
        m = build_master(master_tables)
        assert m.issues == ()
        assert set(m.entities) == {"EZS", "SASSY"}
        ezs = m.entities["EZS"]
        assert ezs.number_code == "EZ" and ezs.tax_on(date(2026, 6, 3)).rate == Decimal("0.08")
        assert ezs.tax_on(date(2027, 1, 1)) is None                       # hết NQ 204 -> phải cấu hình lại
        assert m.entities["SASSY"].taxes[0].mode is TaxMode.TAX_DEDUCT
        assert m.channels() == ["FB", "TTS"]
        c = m.contracts[0]
        assert c.commission_rate == Decimal("0.18") and c.channel is None and c.party_a.tax_code == "0312345678"
        assert c.party_a.bank_account_no == "0071000123456"               # giữ số 0 đầu
        fb = m.mappings[0]
        assert fb.code == "FB_ORDER" and fb.is_default and fb.header_row == 2 and fb.sheet_name == "ORDER"
        assert fb.to_config().status_aliases

    def test_missing_tab_and_column(self, master_tables):
        del master_tables["MAPPING"]
        master_tables["BRAND"][0] = ["Kênh", "Brand code", "Tên brand"]
        m = build_master(master_tables)
        assert (1, "ERROR", "Không tìm thấy tab MAPPING") in messages(m, "MAPPING")
        assert any("Thiếu cột bắt buộc: Mã brand" in msg for _, _, msg in messages(m, "BRAND"))
        # không có brand -> mọi hợp đồng bị báo lỗi, không crash
        assert m.brands == () and m.contracts == ()

    def test_tab_names_are_accent_and_case_insensitive(self, master_tables):
        master_tables["Hợp đồng"] = master_tables.pop("HOP_DONG")
        master_tables["phap_nhan"] = master_tables.pop("PHAP_NHAN")
        assert build_master(master_tables).issues == ()

    def test_bad_rows_reported_with_row_numbers_and_skipped(self, master_tables):
        t = master_tables
        add_row(t, "BRAND", **{"Kênh": "FB", "Mã brand": "ORGANICA", "Tên brand": "Trùng"})        # dòng 6
        add_row(t, "BRAND", **{"Kênh": "FB", "Mã brand": "LA ROCHE", "Tên brand": "La Roche"})     # dòng 7
        add_row(t, "HOP_DONG", **contract_row(**{"Mã group": "SHISEIDO_GRP"}))                    # dòng 4
        add_row(t, "HOP_DONG", **contract_row(**{"Pháp nhân": "ABC"}))                             # dòng 5
        add_row(t, "HOP_DONG", **contract_row(**{"% hoa hồng": "abc"}))                            # dòng 6
        add_row(t, "HOP_DONG", **contract_row(**{"Hiệu lực đến": "01/01/2025"}))                   # dòng 7
        add_row(t, "HOP_DONG", **contract_row(**{"Mã brand": "NOPE"}))                             # dòng 8
        add_row(t, "HOP_DONG", **contract_row(MST=312345678))                                     # dòng 9 (warning)
        add_row(t, "HOP_DONG", **contract_row(**{"Số hợp đồng": "# ghi chú — bỏ qua"}))
        m = build_master(t)
        brand = messages(m, "BRAND")
        assert brand[0][:2] == (6, "ERROR") and "trùng với dòng 2" in brand[0][2]
        assert brand[1][:2] == (7, "ERROR") and "không dấu cách" in brand[1][2]
        rows = {r: (sev, msg) for r, sev, msg in messages(m, "HOP_DONG")}
        assert "đúng 1 trong 2" in rows[4][1]
        assert "ABC" in rows[5][1]
        assert "% hoa hồng" in rows[6][1]
        assert "trước Hiệu lực từ" in rows[7][1]
        assert "NOPE" in rows[8][1]
        assert (9, "WARNING") in {(r, sev) for r, sev, msg in messages(m, "HOP_DONG") if "định dạng văn bản" in msg}
        assert any("Chồng ngày hiệu lực" in msg for r, sev, msg in messages(m, "HOP_DONG") if sev == "WARNING")
        assert {c.contract_no for c in m.contracts} == {"01/2026/HĐDV/EZS-ORGANICA", "05/2026/HĐDV/SS-SHISEIDO",
                                                        "HD-TEST"}

    def test_percent_formats(self, master_tables):
        for i, v in enumerate(["18%", 0.18, 18, "18", "0,18", "12,5%"]):
            add_row(master_tables, "HOP_DONG", **contract_row(**{"Số hợp đồng": f"HD{i}", "% hoa hồng": v,
                                                                  "Kênh": "TTS"}))
        m = build_master(master_tables)
        rates = {c.contract_no: c.commission_rate for c in m.contracts}
        assert [rates[f"HD{i}"] for i in range(5)] == [Decimal("0.18")] * 5
        assert rates["HD5"] == Decimal("0.125")

    def test_contract_tax_rate_override(self, master_tables):
        """Cột "Thuế suất riêng" (tuỳ chọn) ghi đè % thuế CHO RIÊNG 1 brand/hợp đồng — vd mức giảm trừ
        theo NQ 204/2025/QH15 không giống nhau giữa các brand Sassy, không được dùng chung 1 con số."""
        t = master_tables
        add_row(t, "HOP_DONG", **contract_row(**{"Số hợp đồng": "HD-OVERRIDE", "Thuế suất riêng": "5%"}))
        add_row(t, "HOP_DONG", **contract_row(**{"Số hợp đồng": "HD-BAD", "Thuế suất riêng": "abc"}))
        add_row(t, "HOP_DONG", **contract_row(**{"Số hợp đồng": "HD-100", "Thuế suất riêng": "100%"}))
        m = build_master(t)
        by_no = {c.contract_no: c for c in m.contracts}
        assert by_no["01/2026/HĐDV/EZS-ORGANICA"].tax_rate_override is None     # cột trống -> không đổi
        assert by_no["HD-OVERRIDE"].tax_rate_override == Decimal("0.05")
        msgs = [msg for _, _, msg in messages(m, "HOP_DONG")]
        assert any("Thuế suất riêng" in msg and "không hợp lệ" in msg for msg in msgs)
        assert any("Thuế suất riêng" in msg and "nhỏ hơn 100%" in msg for msg in msgs)
        assert "HD-BAD" not in by_no and "HD-100" not in by_no                  # 2 dòng lỗi bị bỏ qua

    def test_contract_appendix_plhd(self, master_tables):
        """Cột "Số phụ lục HĐ" + "Ngày ký phụ lục" (PLHD, tuỳ chọn, đi theo CẶP) — hợp đồng có phụ lục bổ sung/
        điều chỉnh hợp đồng gốc. Thiếu 1 trong 2 -> coi như không có phụ lục (warning, không lỗi dòng)."""
        t = master_tables
        header(t, "HOP_DONG").append("Số phụ lục HĐ")
        header(t, "HOP_DONG").append("Ngày ký phụ lục")
        add_row(t, "HOP_DONG", **contract_row(**{"Số hợp đồng": "HD-PLHD", "Số phụ lục HĐ": "PLHD-01",
                                                  "Ngày ký phụ lục": "31/07/2026"}))
        add_row(t, "HOP_DONG", **contract_row(**{"Số hợp đồng": "HD-NO-PLHD"}))
        add_row(t, "HOP_DONG", **contract_row(**{"Số hợp đồng": "HD-PARTIAL", "Số phụ lục HĐ": "PLHD-02"}))
        add_row(t, "HOP_DONG", **contract_row(**{"Số hợp đồng": "HD-PARTIAL-2", "Ngày ký phụ lục": "01/01/2026"}))
        m = build_master(t)
        by_no = {c.contract_no: c for c in m.contracts}
        assert by_no["HD-PLHD"].appendix_no == "PLHD-01"
        assert by_no["HD-PLHD"].appendix_signed_on == date(2026, 7, 31)
        assert by_no["HD-NO-PLHD"].appendix_no is None and by_no["HD-NO-PLHD"].appendix_signed_on is None
        assert by_no["HD-PARTIAL"].appendix_no is None and by_no["HD-PARTIAL"].appendix_signed_on is None
        assert by_no["HD-PARTIAL-2"].appendix_no is None and by_no["HD-PARTIAL-2"].appendix_signed_on is None
        msgs = [msg for _, _, msg in messages(m, "HOP_DONG")]
        assert sum("thiếu cột còn lại" in msg for msg in msgs) == 2

    def test_tax_periods_must_not_overlap(self, master_tables):
        add_row(master_tables, "PHAP_NHAN", **{"Mã pháp nhân": "EZS", "Mã số BBNT": "EZ", "Loại thuế": "Cộng VAT",
                                               "Thuế suất": "10%", "Hiệu lực từ": "01/12/2026"})
        add_row(master_tables, "PHAP_NHAN", **{"Mã pháp nhân": "EZS", "Mã số BBNT": "EZ", "Loại thuế": "Cộng VAT",
                                               "Thuế suất": "10%", "Hiệu lực từ": "01/01/2027"})
        m = build_master(master_tables)
        assert any(r == 5 and "chồng ngày" in msg for r, _, msg in messages(m, "PHAP_NHAN"))
        assert m.entities["EZS"].tax_on(date(2027, 3, 1)).rate == Decimal("0.10")

    def test_mapping_requires_status_column(self, master_tables):
        master_tables["MAPPING"] = [r for r in master_tables["MAPPING"] if r[6] != "ORDER_STATUS"]
        m = build_master(master_tables)
        assert m.mappings == ()
        assert any("ORDER_STATUS" in msg for _, _, msg in messages(m, "MAPPING"))


# =========================================================================== lookups
class TestScope:
    def test_single_brand(self, master_tables):
        s = resolve_scope(build_master(master_tables), "FB", ["organica"])
        assert s.code == "ORGANICA" and not s.is_group and s.display == "Organica"

    def test_brands_same_group_use_group_code(self, master_tables):
        s = resolve_scope(build_master(master_tables), "FB", ["ANESSA", "SHISEIDO"])
        assert s.code == "SHISEIDO_GRP" and s.is_group and s.display == "Shiseido"

    def test_whole_group(self, master_tables):
        s = resolve_scope(build_master(master_tables), "FB", group_code="SHISEIDO_GRP")
        assert [b.code for b in s.brands] == ["SHISEIDO", "ANESSA"]

    @pytest.mark.parametrize("channel, brands, group, code", [
        ("SHP", ["ORGANICA"], None, "CHANNEL_NOT_FOUND"),
        ("TTS", ["ANESSA"], None, "BRAND_NOT_FOUND"),
        ("FB", ["ORGANICA", "ANESSA"], None, "BRANDS_NOT_SAME_GROUP"),
        ("TTS", None, "SHISEIDO_GRP", "GROUP_EMPTY"),
    ])
    def test_errors(self, master_tables, channel, brands, group, code):
        with pytest.raises(BbntError) as e:
            resolve_scope(build_master(master_tables), channel, brands, group)
        assert e.value.code == code

    def test_inactive_brand(self, master_tables):
        set_cell(master_tables, "BRAND", 2, "Đang hợp tác", "Không")
        with pytest.raises(BbntError, match="ngưng"):
            resolve_scope(build_master(master_tables), "FB", ["ORGANICA"])


class TestFindContract:
    """``find_contract`` LUÔN cần ``contract_no`` tường minh — không còn tự suy luận theo kỳ nghiệm thu (đã bỏ
    hẳn khái niệm kỳ nghiệm thu); chỉ còn kiểm tra hợp đồng đó CÓ áp dụng cho pháp nhân/kênh/phạm vi hay không,
    hoàn toàn không xét ngày hiệu lực."""

    def test_brand_contract_matches_any_channel(self, master_tables):
        m = build_master(master_tables)
        for ch in ("FB", "TTS"):
            c = find_contract(m, "EZS", resolve_scope(m, ch, ["ORGANICA"]), "01/2026/HĐDV/EZS-ORGANICA")
            assert c.contract_no == "01/2026/HĐDV/EZS-ORGANICA"

    def test_single_brand_matches_group_contract(self, master_tables):
        m = build_master(master_tables)
        c = find_contract(m, "SASSY", resolve_scope(m, "FB", ["ANESSA"]), "05/2026/HĐDV/SS-SHISEIDO")
        assert c.group_code == "SHISEIDO_GRP"

    def test_contract_no_resolves_exact_match_regardless_of_other_candidates(self, master_tables):
        add_row(master_tables, "HOP_DONG", **contract_row(**{"Số hợp đồng": "ANESSA-RIENG", "Pháp nhân": "SASSY",
                                                              "Mã brand": "ANESSA"}))
        add_row(master_tables, "HOP_DONG", **contract_row(**{"Số hợp đồng": "ORG-FB", "Kênh": "FB"}))
        m = build_master(master_tables)
        assert find_contract(m, "SASSY", resolve_scope(m, "FB", ["ANESSA"]), "ANESSA-RIENG").contract_no \
            == "ANESSA-RIENG"
        assert find_contract(m, "SASSY", resolve_scope(m, "FB", ["ANESSA"]),
                             "05/2026/HĐDV/SS-SHISEIDO").group_code == "SHISEIDO_GRP"
        assert find_contract(m, "EZS", resolve_scope(m, "FB", ["ORGANICA"]), "org-fb").contract_no == "ORG-FB"
        assert find_contract(m, "EZS", resolve_scope(m, "TTS", ["ORGANICA"]),
                             "01/2026/HĐDV/EZS-ORGANICA").contract_no == "01/2026/HĐDV/EZS-ORGANICA"
        # ORG-FB chỉ ghi kênh FB -> không áp dụng cho scope TTS
        with pytest.raises(BbntError) as e:
            find_contract(m, "EZS", resolve_scope(m, "TTS", ["ORGANICA"]), "ORG-FB")
        assert e.value.code == "CONTRACT_NOT_FOUND"

    def test_unknown_or_mismatched_contract_no_rejected(self, master_tables):
        m = build_master(master_tables)
        scope = resolve_scope(m, "FB", ["ORGANICA"])
        with pytest.raises(BbntError, match="không có") as e:
            find_contract(m, "EZS", scope, "KHONG-TON-TAI")
        assert e.value.code == "CONTRACT_NOT_FOUND"
        with pytest.raises(BbntError, match="không có") as e:
            find_contract(m, "SASSY", scope, "01/2026/HĐDV/EZS-ORGANICA")   # đúng số nhưng sai pháp nhân/scope
        assert e.value.code == "CONTRACT_NOT_FOUND"

    def test_contract_no_lets_user_pick_rate_manually_no_date_check(self, master_tables):
        """Không còn tự suy luận theo ngày hiệu lực — 2 hợp đồng cùng scope, khác % hoa hồng, khác khoảng ngày
        hiệu lực (kể cả vắt qua nhau) đều tự chọn được thẳng qua ``contract_no``, không báo lỗi chồng kỳ."""
        set_cell(master_tables, "HOP_DONG", 2, "Hiệu lực đến", "30/06/2026")
        add_row(master_tables, "HOP_DONG", **contract_row(**{"Số hợp đồng": "PL01", "Hiệu lực từ": "01/07/2026",
                                                              "% hoa hồng": "20%"}))
        m = build_master(master_tables)
        scope = resolve_scope(m, "FB", ["ORGANICA"])
        assert find_contract(m, "EZS", scope, "01/2026/HĐDV/EZS-ORGANICA").commission_rate == Decimal("0.18")
        assert find_contract(m, "EZS", scope, "PL01").commission_rate == Decimal("0.20")

    def test_duplicate_contract_no_in_master_is_ambiguous(self, master_tables):
        add_row(master_tables, "HOP_DONG", **contract_row(**{"Số hợp đồng": "HD-DUP"}))
        add_row(master_tables, "HOP_DONG", **contract_row(**{"Số hợp đồng": "HD-DUP", "% hoa hồng": "25%"}))
        m = build_master(master_tables)
        with pytest.raises(BbntError) as e:
            find_contract(m, "EZS", resolve_scope(m, "FB", ["ORGANICA"]), "HD-DUP")
        assert e.value.code == "CONTRACT_AMBIGUOUS" and len(e.value.details["contracts"]) == 2

    def test_inactive_contract_ignored(self, master_tables):
        set_cell(master_tables, "HOP_DONG", 2, "Trạng thái", "Hết hạn")
        m = build_master(master_tables)
        with pytest.raises(BbntError, match="không có"):
            find_contract(m, "EZS", resolve_scope(m, "FB", ["ORGANICA"]), "01/2026/HĐDV/EZS-ORGANICA")


class TestFindMapping:
    def test_single_and_by_code(self, master_tables):
        m = build_master(master_tables)
        assert find_mapping(m, "FB").code == "FB_ORDER"
        assert find_mapping(m, "FB", "fb_order").code == "FB_ORDER"
        with pytest.raises(BbntError) as e:
            find_mapping(m, "TTS")
        assert e.value.code == "MAPPING_NOT_FOUND"

    def test_default_among_many(self, master_tables):
        extra = [["FB_OLD" if i == 0 else "Không" if i == 5 and v else v for i, v in enumerate(r)]
                 for r in master_tables["MAPPING"][1:]]
        master_tables["MAPPING"] += extra
        m = build_master(master_tables)
        assert find_mapping(m, "FB").code == "FB_ORDER"
        for r in master_tables["MAPPING"][1:]:
            if r[5] == "Có":
                r[5] = "Không"
        with pytest.raises(BbntError) as e:
            find_mapping(build_master(master_tables), "FB")
        assert e.value.code == "MAPPING_AMBIGUOUS"


# =========================================================================== cache
class TestRepository:
    def test_ttl_refresh_and_stale_fallback(self, master_tables):
        now = [0.0]
        calls = [0]
        state: dict[str, Any] = {"fail": False}

        def source() -> Any:
            calls[0] += 1
            if state["fail"]:
                raise SheetAccessError("Google 503")
            return master_tables

        repo = MasterRepository(source, ttl_seconds=60, monotonic=lambda: now[0])
        first = repo.get()
        assert repo.get() is first and calls[0] == 1                 # trong TTL: không đọc lại
        now[0] = 61
        master_tables["HOP_DONG"][1][master_tables["HOP_DONG"][0].index("% hoa hồng")] = 0.25
        assert repo.get().contracts[0].commission_rate == Decimal("0.25") and calls[0] == 2
        assert repo.get(refresh=True) is not first and calls[0] == 3  # bấm "Tải lại master"
        state["fail"] = True
        now[0] = 200
        assert repo.get().contracts[0].commission_rate == Decimal("0.25")   # Google lỗi -> dùng bản cũ
        with pytest.raises(BbntError) as e:
            repo.get(refresh=True)                                    # refresh tay thì báo lỗi thật
        assert e.value.code == "MASTER_UNREADABLE" and e.value.status_code == 502

    def test_no_cache_and_source_down(self):
        def down() -> Any:
            raise SheetAccessError("chưa share")

        with pytest.raises(BbntError, match="chưa share"):
            MasterRepository(down).get()
