"""
Primitive thao tác OOXML (lxml) dùng chung cho tagger và filler.

Nguyên tắc giữ định dạng:
* Không tạo/đổi ``w:pPr``, ``w:tblPr``, ``w:tcPr``, ``w:sectPr`` — chỉ đổi nội dung ``w:t``.
* Text thay thế kế thừa ``w:rPr`` của run chứa ký tự đầu tiên bị thay.
* Run rỗng sau khi thay vẫn được giữ (không xoá phần tử nào ngoài các dòng bảng sản phẩm).
* Ô/đoạn trống: run mới lấy rPr từ "paragraph mark" (``w:pPr/w:rPr``) — đúng như Word làm khi gõ vào ô đó
  (template xuất từ Google Docs để run rỗng chỉ có ``<w:rtl/>``, font thật nằm ở paragraph mark).
"""
from __future__ import annotations

import copy
import random
import re
from collections.abc import Iterator
from dataclasses import dataclass

from docx.document import Document as DocxDocument
from docx.oxml.ns import qn
from lxml import etree

W_P, W_R, W_T, W_TAB, W_BR, W_RPR, W_PPR = (qn(x) for x in ("w:p", "w:r", "w:t", "w:tab", "w:br", "w:rPr", "w:pPr"))
W_TR, W_TC, W_TBL = qn("w:tr"), qn("w:tc"), qn("w:tbl")
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
W14 = "http://schemas.microsoft.com/office/word/2010/wordml"

# Phần tử trong paragraph-mark rPr không hợp lệ trong run rPr
_PARA_MARK_ONLY = {qn("w:ins"), qn("w:del"), qn("w:moveFrom"), qn("w:moveTo"), qn("w:rPrChange")}


def iter_story_roots(doc: DocxDocument) -> Iterator[etree._Element]:
    """Body + mọi header/footer (mỗi part 1 lần, kể cả header 'linked')."""
    yield doc.element.body
    seen: set[int] = set()
    for section in doc.sections:
        for attr in ("header", "first_page_header", "even_page_header",
                     "footer", "first_page_footer", "even_page_footer"):
            hf = getattr(section, attr)
            if hf.is_linked_to_previous:
                continue
            el = hf._element
            if id(el) not in seen:
                seen.add(id(el))
                yield el


def iter_paragraphs(root: etree._Element) -> Iterator[etree._Element]:
    """Mọi ``w:p`` kể cả trong bảng lồng, textbox."""
    yield from root.iter(W_P)


def own_texts(p: etree._Element) -> list[etree._Element]:
    """``w:t`` thuộc chính paragraph này (bỏ qua text của textbox lồng bên trong)."""
    return [t for t in p.iter(W_T) if next(t.iterancestors(W_P)) is p]


def paragraph_text(p: etree._Element) -> str:
    return "".join(t.text or "" for t in own_texts(p))


def _set_text(t: etree._Element, text: str) -> None:
    t.text = text
    if text != text.strip() or "  " in text:
        t.set(XML_SPACE, "preserve")


@dataclass(frozen=True)
class _Span:
    t: etree._Element
    start: int  # offset trong text gộp của paragraph
    end: int


def _spans(p: etree._Element) -> list[_Span]:
    spans, pos = [], 0
    for t in own_texts(p):
        n = len(t.text or "")
        spans.append(_Span(t, pos, pos + n))
        pos += n
    return spans


def replace_span(p: etree._Element, start: int, end: int, new_text: str) -> None:
    """Thay ký tự [start, end) của text gộp paragraph bằng ``new_text`` — run-aware.

    Text mới đặt vào ``w:t`` chứa ký tự ``start`` (giữ rPr của run đó); phần bị thay ở các ``w:t`` sau bị cắt.
    ``\\n`` trong ``new_text`` -> ``<w:br/>`` cùng run.
    """
    spans = _spans(p)
    first = next((s for s in spans if s.start <= start < s.end), None)
    if first is None:
        raise ValueError("Vị trí thay thế không nằm trong text của paragraph")
    for s in spans:
        if s.end <= start or s.start >= end:
            continue
        text = s.t.text or ""
        lo, hi = max(start, s.start) - s.start, min(end, s.end) - s.start
        if s is first:
            _set_text(s.t, text[:lo] + new_text + text[hi:])
            if "\n" in new_text:
                _expand_line_breaks(s.t)
        else:
            _set_text(s.t, text[:lo] + text[hi:])


def _expand_line_breaks(t: etree._Element) -> None:
    parts = (t.text or "").split("\n")
    _set_text(t, parts[0])
    anchor = t
    for part in parts[1:]:
        br = etree.Element(W_BR)
        anchor.addnext(br)
        nt = etree.Element(W_T)
        _set_text(nt, part)
        br.addnext(nt)
        anchor = nt


def replace_literal(p: etree._Element, old: str, new: str, *, occurrence: int = 1) -> bool:
    """Thay lần xuất hiện thứ ``occurrence`` của ``old`` (có thể vắt qua nhiều run)."""
    text = paragraph_text(p)
    idx = -1
    for _ in range(occurrence):
        idx = text.find(old, idx + 1)
        if idx < 0:
            return False
    replace_span(p, idx, idx + len(old), new)
    return True


def run_props_from_paragraph_mark(p: etree._Element) -> etree._Element | None:
    ppr = p.find(W_PPR)
    mark = ppr.find(W_RPR) if ppr is not None else None
    if mark is None:
        return None
    rpr = etree.Element(W_RPR)
    for child in mark:
        if child.tag not in _PARA_MARK_ONLY:
            rpr.append(copy.deepcopy(child))
    return rpr


def append_text(p: etree._Element, text: str) -> None:
    """Nối text vào cuối paragraph.

    Có run chứa text -> nối vào ``w:t`` cuối (giữ format nhãn, vd 'Địa chỉ<tab>:  '{text}).
    Paragraph trống -> tạo run mới với rPr của paragraph mark.
    """
    ts = own_texts(p)
    if ts:
        last = ts[-1]
        _set_text(last, (last.text or "") + text)
        return
    r = etree.SubElement(p, W_R)
    rpr = run_props_from_paragraph_mark(p)
    if rpr is not None:
        r.append(rpr)
    t = etree.SubElement(r, W_T)
    _set_text(t, text)


def cell_paragraphs(tc: etree._Element) -> list[etree._Element]:
    return list(tc.findall(W_P))


def table_rows(tbl: etree._Element) -> list[etree._Element]:
    return list(tbl.findall(W_TR))


def row_cells(tr: etree._Element) -> list[etree._Element]:
    return list(tr.findall(W_TC))


def renew_paragraph_ids(el: etree._Element, rng: random.Random | None = None) -> None:
    """Sinh lại w14:paraId/textId cho phần tử clone — tránh trùng ID (Word có thể báo lỗi khi co-authoring)."""
    rng = rng or random.Random()
    for p in el.iter(W_P):
        for attr in ("paraId", "textId"):
            key = f"{{{W14}}}{attr}"
            if p.get(key) is not None:
                p.set(key, f"{rng.randrange(1, 0x7FFFFFFF):08X}")


_WS = re.compile(r"\s+")


def norm(s: str) -> str:
    return _WS.sub(" ", s.replace(" ", " ")).strip()
