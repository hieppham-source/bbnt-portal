"""
Chuyển template BBNT gốc (placeholder kiểu 'XXX', '……', ô trống) thành template gắn token ``{{...}}``.

Chạy 1 lần cho mỗi template gốc, kết quả upload thành ``template_versions`` mới::

    python -m app.docx.template_tagger ezs   "templates/source/TEMPLATE BBNT EZS X BRAND - FB NON-OP.docx" \\
        templates/bbnt_ezs_fb_nonop.v2.docx
    python -m app.docx.template_tagger sassy "templates/source/TEMPLATE BBNT HKD SASSY X BRAND - FB NON-OP.docx" \\
        templates/bbnt_sassy_fb_nonop.v2.docx

Tagger chỉ đổi đúng các placeholder; KHÔNG đụng ngày tháng pháp lý ("TPHCM, ngày .. tháng .. năm ..",
"ký ngày XX tháng XX năm 20XX"), thông tin Bên B, điều khoản, chữ ký Bên B. Thao tác cấu trúc duy nhất:
gom 15 (EZS) / 2 (SASSY) dòng trống của bảng sản phẩm thành 1 dòng mẫu ``{{item.*}}``.
Sau khi gắn token, file được nạp lại bằng :class:`BbntDocxRenderer` để kiểm tra đủ token kỳ vọng.
"""
from __future__ import annotations

import io
import re
import sys
from collections.abc import Callable
from pathlib import Path

import docx
from docx.document import Document as DocxDocument
from docx.oxml.ns import qn
from lxml import etree

from .filler import BbntDocxRenderer
from .ooxml import (
    W_P,
    W_RPR,
    W_T,
    W_TBL,
    append_text,
    cell_paragraphs,
    norm,
    paragraph_text,
    replace_literal,
    replace_span,
    row_cells,
    run_props_from_paragraph_mark,
    table_rows,
)

DOTS_RE = re.compile(r"[.…]{3,}\s*")


class TaggingError(RuntimeError):
    pass


class _Doc:
    def __init__(self, doc: DocxDocument) -> None:
        self.doc = doc
        self.body = doc.element.body
        self.paragraphs: list[etree._Element] = list(self.body.iter(W_P))

    # ---- locate
    def para(self, contains: str, *, after: etree._Element | None = None) -> etree._Element:
        start = self.paragraphs.index(after) + 1 if after is not None else 0
        for p in self.paragraphs[start:]:
            if contains in norm(paragraph_text(p)):
                return p
        raise TaggingError(f"Không tìm thấy đoạn chứa: {contains!r}")

    def tables(self, first_cell_startswith: str) -> list[etree._Element]:
        found = []
        for tbl in self.body.iter(W_TBL):
            first = row_cells(table_rows(tbl)[0])[0]
            if norm(" ".join(paragraph_text(p) for p in cell_paragraphs(first))).startswith(first_cell_startswith):
                found.append(tbl)
        if not found:
            raise TaggingError(f"Không tìm thấy bảng bắt đầu bằng {first_cell_startswith!r}")
        return found

    # ---- edit
    @staticmethod
    def replace(p: etree._Element, old: str, new: str) -> None:
        if not replace_literal(p, old, new):
            raise TaggingError(f"Không thấy {old!r} trong {paragraph_text(p)!r}")

    @staticmethod
    def replace_dots(p: etree._Element, new: str) -> None:
        text = paragraph_text(p)
        m = DOTS_RE.search(text)
        if not m:
            raise TaggingError(f"Không thấy dấu chấm điền trong {text!r}")
        if m.start() and not text[m.start() - 1].isspace():
            new = " " + new
        replace_span(p, m.start(), m.end(), new)

    @staticmethod
    def set_cell(tc: etree._Element, token: str) -> None:
        p = cell_paragraphs(tc)[0]
        if norm(paragraph_text(p)):
            raise TaggingError(f"Ô không trống: {paragraph_text(p)!r}")
        append_text(p, token)

    def party_a(self, labels: dict[str, str]) -> None:
        """Nối token vào các dòng nhãn giữa 'BÊN A:' và 'BÊN B:'."""
        start = self.para("BÊN A:")
        end = self.para("BÊN B:", after=start)
        block = self.paragraphs[self.paragraphs.index(start):self.paragraphs.index(end)]
        done = set()
        for p in block:
            label = norm(paragraph_text(p))
            for prefix, token in labels.items():
                if prefix not in done and label.startswith(prefix):
                    raw = paragraph_text(p)
                    append_text(p, token if raw[-1:].isspace() else " " + token)
                    done.add(prefix)
                    break
        missing = set(labels) - done
        if missing:
            raise TaggingError(f"Không thấy nhãn Bên A: {sorted(missing)}")

    def item_table(self) -> None:
        tbl = self.tables("TÊN SẢN PHẨM")[0]
        rows = table_rows(tbl)
        total = next(r for r in rows if norm(paragraph_text(cell_paragraphs(row_cells(r)[0])[0])) == "TỔNG CỘNG")
        blanks = rows[1:rows.index(total)]
        if not blanks:
            raise TaggingError("Bảng sản phẩm không có dòng trống làm dòng mẫu")
        proto, extra = blanks[0], blanks[1:]
        for tc, token in zip(row_cells(proto), ("{{item.name}}", "{{item.quantity}}", "{{item.revenue}}"),
                             strict=True):
            self.set_cell(tc, token)
        for r in extra:
            tbl.remove(r)
        cells = row_cells(total)
        self.set_cell(cells[1], "{{total_quantity}}")
        self.set_cell(cells[2], "{{nmv}}")

    def ab_tables(self) -> None:
        for tbl in self.tables("Doanh thu ước tính (A)"):
            for tc, token in zip(row_cells(table_rows(tbl)[1]),
                                 ("{{gmv_estimated}}", "{{nmv}}", "{{cancellation_rate_pct}}"), strict=True):
                self.set_cell(tc, token)


def _common_party_a(d: _Doc, labels: dict[str, str]) -> None:
    d.party_a({"BÊN A:": "{{party_a_name}}", **labels})


def tag_ezs(d: _Doc) -> None:
    d.replace(d.para("Số: XX.202X/BBNT-"), " XX.202X/BBNT-BRAND-EZS-FB-XX.XX", " {{bbnt_number}}")
    d.replace(d.para("(Chương trình BRAND XXX"), "BRAND XXX", "{{program_name}}")
    d.replace(d.para("Căn cứ vào Hợp đồng số"), "XXX  ", "{{contract_no}} ")
    _common_party_a(d, {
        "Địa chỉ": "{{party_a_address}}", "Điện thoại": "{{party_a_phone}}", "Mã số thuế": "{{party_a_tax_code}}",
        "Tài khoản": "{{party_a_bank_account}}", "Ngân hàng": "{{party_a_bank_name}}",
        "Đại diện": "{{party_a_representative}}", "Chức vụ": "{{party_a_representative_title}}",
    })
    d.replace(d.para("ký Biên bản nghiệm thu chương trình BRAND XXX"), "BRAND XXX", "{{program_name}}")
    d.item_table()
    revenue = d.para("Tổng doanh thu của chương trình sau chiến dịch là:")
    d.replace(revenue, "XXX VND", "{{nmv}} VND")
    d.replace_dots(d.para("(Bằng chữ:", after=revenue), "{{nmv_words}}")
    d.ab_tables()
    commission = d.tables("Chi tiết")[0]
    r = table_rows(commission)
    d.replace(cell_paragraphs(row_cells(r[1])[0])[0], "xx%", "{{commission_rate_pct}}")
    d.set_cell(row_cells(r[1])[1], "{{commission_amount}}")
    d.replace(cell_paragraphs(row_cells(r[2])[0])[0], "x%", "{{tax_rate_pct}}")
    d.set_cell(row_cells(r[2])[1], "{{vat_amount}}")
    d.set_cell(row_cells(r[3])[1], "{{total_payable}}")
    d.replace_dots(d.para("(Bằng chữ:", after=d.para("Tương đương với kết quả đó")), "{{total_payable_words}}")
    d.replace(d.para("Biên bản nghiệm thu số"), "XX.202X/BBNT-BRAND-OM-FB-XX.XX", "{{bbnt_number}}")
    pay = d.para("Số tiền Bên A sẽ thanh toán thêm cho Bên B")
    d.replace(pay, "XXX ", "{{total_payable}} ")
    d.replace(pay, "….", "{{total_payable_words}}")
    _signature(d, title="xxxxxxxx")


def tag_sassy(d: _Doc) -> None:
    d.replace(d.para("Số: XXXXX/"), " XXXXX/BRAND-SASSY-FB-XXXXX", " {{bbnt_number}}")
    d.replace(d.para("(Chương trình BRAND XXX"), "BRAND XXX", "{{program_name}}")
    d.replace(d.para("Căn cứ vào Hợp đồng dịch vụ số"), "XXXX ", "{{contract_no}} ")
    _common_party_a(d, {
        "Địa chỉ": "{{party_a_address}}", "Số điện thoại": "{{party_a_phone}}", "Mã số thuế": "{{party_a_tax_code}}",
        "Người đại diện": "{{party_a_representative}}", "Chức vụ": "{{party_a_representative_title}}",
        "Số TK": "{{party_a_bank_account}}",
    })
    d.replace(d.para("ký Biên bản nghiệm thu chương trình BRAND XXX"), "BRAND XXX", "{{program_name}}")
    d.item_table()
    items_tbl = d.tables("TÊN SẢN PHẨM")[0]
    first_words = next(p for p in d.paragraphs
                       if "(Bằng chữ:" in paragraph_text(p) and _after(items_tbl, p))
    d.replace_dots(first_words, "{{nmv_words}}")
    d.ab_tables()
    commission = d.tables("Chi tiết")[0]
    r = table_rows(commission)
    d.replace(cell_paragraphs(row_cells(r[1])[0])[0], "XX%", "{{commission_rate_pct}}")
    d.set_cell(row_cells(r[1])[1], "{{commission_amount}}")
    d.set_cell(row_cells(r[2])[1], "{{tax_deduction_amount}}")
    d.set_cell(row_cells(r[3])[1], "{{total_payable}}")
    d.replace_dots(d.para("(Bằng chữ:", after=first_words), "{{total_payable_words}}")
    d.replace(d.para("Biên bản nghiệm thu số"), "XXXXX/BRAND-SASSY-FB-XXXXX", "{{bbnt_number}}")
    pay = d.para("Số tiền Bên A sẽ thanh toán thêm cho Bên B")
    d.replace(pay, "…………", "{{total_payable}} ")
    d.replace_dots(pay, "{{total_payable_words}}")
    _signature(d, title=None)


def _after(anchor: etree._Element, p: etree._Element) -> bool:
    """p nằm sau anchor trong thứ tự tài liệu."""
    for el in anchor.itersiblings():
        if el is p or p in el.iter(W_P):
            return True
    return False


def _signature(d: _Doc, *, title: str | None) -> None:
    for p in d.paragraphs:
        if norm(paragraph_text(p)) == "XXXXXXXXXXXXXXXX":
            d.replace(p, "XXXXXXXXXXXXXXXX", "{{party_a_representative_upper}}")
            _inherit_paragraph_font(p, "{{party_a_representative_upper}}")
            break
    else:
        raise TaggingError("Không thấy ô tên đại diện Bên A ở phần ký")
    if title:
        for p in d.paragraphs:
            if norm(paragraph_text(p)) == title:
                d.replace(p, title, "{{party_a_representative_title}}")
                break


def _inherit_paragraph_font(p: etree._Element, token: str) -> None:
    """Run chứa token không khai báo font (template SASSY: placeholder chữ ký để font mặc định Calibri,
    lệch với 'BÙI DŨNG ANH TÚ' Times New Roman) -> dùng rPr của paragraph mark, giống khi gõ mới vào ô."""
    for t in p.iter(W_T):
        if token in (t.text or ""):
            run = t.getparent()
            rpr = run.find(W_RPR)
            if rpr is not None and rpr.find(qn("w:rFonts")) is not None:
                return
            mark = run_props_from_paragraph_mark(p)
            if mark is None:
                return
            if rpr is not None:
                run.remove(rpr)
            run.insert(0, mark)
            return


TAGGERS: dict[str, Callable[[_Doc], None]] = {"ezs": tag_ezs, "sassy": tag_sassy}

EXPECTED_ITEM_FIELDS = frozenset({"name", "quantity", "revenue"})
EXPECTED_COMMON = frozenset({
    "bbnt_number", "program_name", "contract_no", "party_a_name", "party_a_address", "party_a_phone",
    "party_a_tax_code", "party_a_bank_account", "party_a_representative", "party_a_representative_title",
    "party_a_representative_upper", "total_quantity", "nmv", "nmv_words", "gmv_estimated", "cancellation_rate_pct",
    "commission_rate_pct", "commission_amount", "total_payable", "total_payable_words",
})
EXPECTED: dict[str, frozenset[str]] = {
    "ezs": EXPECTED_COMMON | {"party_a_bank_name", "tax_rate_pct", "vat_amount"},
    "sassy": EXPECTED_COMMON | {"tax_deduction_amount"},
}


def tag_template(kind: str, source: bytes) -> bytes:
    """Gắn token cho template gốc ``kind`` ('ezs' | 'sassy'); trả về bytes .docx đã kiểm tra."""
    doc = docx.Document(io.BytesIO(source))
    TAGGERS[kind](_Doc(doc))
    buf = io.BytesIO()
    doc.save(buf)
    out = buf.getvalue()
    spec = BbntDocxRenderer(out).spec
    if spec.scalars != EXPECTED[kind] or spec.item_fields != EXPECTED_ITEM_FIELDS:
        raise TaggingError(f"Token không khớp kỳ vọng: thiếu {sorted(EXPECTED[kind] - spec.scalars)}, "
                           f"thừa {sorted(spec.scalars - EXPECTED[kind])}")
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[0] not in TAGGERS:
        print(__doc__)
        return 2
    kind, src, dst = argv
    Path(dst).write_bytes(tag_template(kind, Path(src).read_bytes()))
    print(f"OK -> {dst}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
