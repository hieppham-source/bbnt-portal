"""
BBNT .docx renderer — fill ``{{token}}`` vào template Word, giữ nguyên định dạng gốc.

Cú pháp template (soạn thẳng trong Word được, kể cả khi Word cắt token qua nhiều run):

* ``{{bbnt_number}}``, ``{{nmv}}``, ``{{total_payable_words}}``... — giá trị scalar.
* Dòng bảng chứa ``{{item.name}}`` / ``{{item.quantity}}`` / ``{{item.revenue}}`` là **dòng mẫu**:
  được nhân bản cho từng SKU (giữ nguyên trPr/tcPr/borders/shading), rồi dòng mẫu bị xoá.

Bảo đảm:

1. Chỉ đổi nội dung ``w:t``; mọi pPr/rPr/tblPr/tcPr/sectPr, header/footer, ảnh, comment giữ nguyên.
2. Thiếu giá trị cho token -> :class:`MissingValueError` (không bao giờ xuất file còn ``{{...}}``).
3. **Chuỗi ngày tháng pháp lý** trong template (``ngày .. tháng .. năm ..``, ``dd/mm/yyyy``) được chụp
   trước khi fill và kiểm tra lại sau khi fill -> :class:`LegalTextModifiedError` nếu bị đổi.
   Template đặt token bên trong chuỗi ngày pháp lý bị từ chối ngay khi load (:class:`TemplateError`).
"""
from __future__ import annotations

import copy
import hashlib
import io
import random
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import docx
from docx.document import Document as DocxDocument
from lxml import etree

from .ooxml import (
    W_R,
    W_TBL,
    W_TR,
    iter_paragraphs,
    iter_story_roots,
    paragraph_text,
    renew_paragraph_ids,
    replace_span,
)

TOKEN_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)\s*\}\}")
ITEM_PREFIX = "item."

#: Chuỗi ngày tháng pháp lý — KHÔNG được thay đổi bởi quá trình fill.
LEGAL_DATE_RE = re.compile(
    r"ngày\s*[\w.…]{1,6}\s*tháng\s*[\w.…]{1,6}\s*năm\s*[\w.…]{2,6}(?:\s+\d{4})?"
    r"|\b\d{1,2}/\d{1,2}/\d{4}\b",
    re.IGNORECASE,
)


# Bản nới lỏng để phát hiện token đặt TRONG chuỗi ngày pháp lý khi kiểm tra template.
_LEGAL_DATE_WITH_TOKENS_RE = re.compile(
    r"ngày\s*(?:\{\{[^{}]*\}\}|[\w.…]{1,6})\s*tháng\s*(?:\{\{[^{}]*\}\}|[\w.…]{1,6})"
    r"\s*năm\s*(?:\{\{[^{}]*\}\}|[\w.…]{2,6})",
    re.IGNORECASE,
)


class TemplateError(ValueError):
    """Template không hợp lệ (token sai chỗ, nhiều dòng mẫu trong 1 bảng...)."""


class MissingValueError(KeyError):
    def __init__(self, missing: Sequence[str]) -> None:
        self.missing = sorted(set(missing))
        super().__init__(f"Thiếu giá trị cho token: {', '.join(self.missing)}")


class LegalTextModifiedError(RuntimeError):
    """Chuỗi ngày tháng pháp lý của template bị thay đổi sau khi fill."""


@dataclass(frozen=True)
class TemplateSpec:
    scalars: frozenset[str]
    item_fields: frozenset[str]


@dataclass(frozen=True)
class RenderResult:
    content: bytes
    sha256: str
    item_rows: int


def _row_of(el: etree._Element) -> etree._Element | None:
    return next(el.iterancestors(W_TR), None)


class BbntDocxRenderer:
    """Render 1 template version. Instance immutable, dùng lại được cho nhiều lần render."""

    def __init__(self, template: bytes | Path | BinaryIO) -> None:
        if isinstance(template, Path):
            data = template.read_bytes()
        elif isinstance(template, bytes):
            data = template
        else:
            data = template.read()
        self._template = data
        self.spec = self._inspect(self._load())

    # ------------------------------------------------------------------ inspection
    def _load(self) -> DocxDocument:
        return docx.Document(io.BytesIO(self._template))

    @staticmethod
    def _inspect(doc: DocxDocument) -> TemplateSpec:
        scalars: set[str] = set()
        items: set[str] = set()
        proto_rows: dict[int, int] = {}
        for root in iter_story_roots(doc):
            for p in iter_paragraphs(root):
                text = paragraph_text(p)
                legal = [m.span() for rx in (LEGAL_DATE_RE, _LEGAL_DATE_WITH_TOKENS_RE) for m in rx.finditer(text)]
                for m in TOKEN_RE.finditer(text):
                    if any(a < m.end() and m.start() < b for a, b in legal):
                        raise TemplateError(f"Token {m.group(0)} nằm trong chuỗi ngày pháp lý: {text!r}")
                    name = m.group(1)
                    if name.startswith(ITEM_PREFIX):
                        row = _row_of(p)
                        if row is None:
                            raise TemplateError(f"{m.group(0)} phải nằm trong 1 dòng bảng")
                        tbl = row.getparent()
                        proto_rows.setdefault(id(tbl), id(row))
                        if proto_rows[id(tbl)] != id(row):
                            raise TemplateError("Mỗi bảng chỉ được có 1 dòng mẫu {{item.*}}")
                        items.add(name[len(ITEM_PREFIX):])
                    else:
                        scalars.add(name)
        return TemplateSpec(frozenset(scalars), frozenset(items))

    # ------------------------------------------------------------------ render
    def render(
        self,
        values: Mapping[str, str],
        items: Sequence[Mapping[str, str]] = (),
        *,
        strip_comments: bool = False,
        seed: int | None = None,
    ) -> RenderResult:
        """Fill template.

        Args:
            values: token -> chuỗi ĐÃ định dạng (số tiền '17.185.465', kỳ '05.26'...). Chỉ nhận ``str``.
            items: mỗi phần tử là 1 dòng bảng sản phẩm, key = phần sau ``item.`` (``name``, ``quantity``...).
            strip_comments: xoá marker comment hướng dẫn nội bộ khỏi bản xuất (mặc định giữ nguyên như template).
            seed: seed sinh paraId cho dòng clone (để output tái lập được trong test).
        """
        self._check_values(values, items)
        doc = self._load()
        legal_before = self._snapshot_legal_text(doc)
        rng = random.Random(seed)

        rows = self._expand_item_rows(doc, items, rng)
        for root in iter_story_roots(doc):
            for p in iter_paragraphs(root):
                self._fill_paragraph(p, values)

        self._verify_legal_text(legal_before)
        if strip_comments:
            _strip_comment_markers(doc)

        buf = io.BytesIO()
        doc.save(buf)
        content = buf.getvalue()
        return RenderResult(content, hashlib.sha256(content).hexdigest(), rows)

    def _check_values(self, values: Mapping[str, str], items: Sequence[Mapping[str, str]]) -> None:
        missing = [k for k in self.spec.scalars if k not in values]
        for i, item in enumerate(items):
            missing += [f"item[{i}].{k}" for k in self.spec.item_fields if k not in item]
        if missing:
            raise MissingValueError(missing)
        bad = [k for k, v in values.items() if not isinstance(v, str)]
        bad += [f"item.{k}" for it in items for k, v in it.items() if not isinstance(v, str)]
        if bad:
            raise TypeError(f"Giá trị phải là str đã định dạng: {', '.join(sorted(set(bad)))}")

    @staticmethod
    def _fill_paragraph(p: etree._Element, values: Mapping[str, str] | None,
                        item: Mapping[str, str] | None = None) -> None:
        """Thay token trong 1 paragraph. ``values=None`` -> chỉ thay token item (pass nhân bản dòng)."""
        text = paragraph_text(p)
        matches = list(TOKEN_RE.finditer(text))
        for m in reversed(matches):  # thay từ cuối để offset phía trước không đổi
            name = m.group(1)
            if name.startswith(ITEM_PREFIX):
                if item is None:
                    continue
                value = item[name[len(ITEM_PREFIX):]]
            elif values is None:
                continue
            else:
                value = values[name]
            replace_span(p, m.start(), m.end(), value)

    def _expand_item_rows(self, doc: DocxDocument, items: Sequence[Mapping[str, str]],
                          rng: random.Random) -> int:
        """Nhân bản dòng mẫu cho từng item, chèn ngay trước dòng mẫu rồi xoá dòng mẫu."""
        protos: list[etree._Element] = []
        for root in iter_story_roots(doc):
            for tbl in root.iter(W_TBL):
                for tr in tbl.findall(W_TR):
                    if any(m.group(1).startswith(ITEM_PREFIX)
                           for p in iter_paragraphs(tr) for m in TOKEN_RE.finditer(paragraph_text(p))):
                        protos.append(tr)
        for proto in protos:
            for item in items:
                clone = copy.deepcopy(proto)
                renew_paragraph_ids(clone, rng)
                for p in iter_paragraphs(clone):
                    self._fill_paragraph(p, None, item)
                proto.addprevious(clone)
            proto.getparent().remove(proto)
        return len(items) * len(protos)

    @staticmethod
    def _snapshot_legal_text(doc: DocxDocument) -> list[tuple[etree._Element, list[str]]]:
        snap = []
        for root in iter_story_roots(doc):
            for p in iter_paragraphs(root):
                found = [m.group(0) for m in LEGAL_DATE_RE.finditer(paragraph_text(p))]
                if found:
                    snap.append((p, found))
        return snap

    @staticmethod
    def _verify_legal_text(snapshot: list[tuple[etree._Element, list[str]]]) -> None:
        for p, expected in snapshot:
            text, pos = paragraph_text(p), 0
            for legal in expected:
                idx = text.find(legal, pos)
                if idx < 0:
                    raise LegalTextModifiedError(f"Chuỗi ngày pháp lý bị thay đổi: {legal!r} -> {text!r}")
                pos = idx + len(legal)


def _strip_comment_markers(doc: DocxDocument) -> None:
    from docx.oxml.ns import qn

    body = doc.element.body
    for tag in ("w:commentRangeStart", "w:commentRangeEnd"):
        for el in list(body.iter(qn(tag))):
            el.getparent().remove(el)
    for ref in list(body.iter(qn("w:commentReference"))):
        run = ref.getparent()
        if run.tag == W_R:
            run.getparent().remove(run)
