"""
BBNT Portal adapter cho bot (docs/ARCHITECTURE.md 11.2, 14.1; plan bước 6).

Dựng :class:`BbntInput` từ 1 case (1 link order = 1 BBNT; nhiều brand -> 1 BBNT group tách bảng theo brand),
chạy **generator có sẵn** với clock cố định = ``bbnt_issued_at`` đã lưu — tạo lại bao nhiêu lần cũng ra đúng số
BBNT cũ. Không đổi công thức / template. Không I/O ngoài ``open_order`` do worker truyền vào.
"""
from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from app.core.calculation import normalize_text
from app.errors import BbntError
from app.master.models import MasterData
from app.recon.rules import campaign_program_name, campaign_suffix
from app.services.generator import BbntGenerator, BbntInput, BbntResult, OrderOpener
from app.services.templates import TemplateRegistry

CHANNEL = "FB"
_SIGNED = normalize_text("Đã ký")


@dataclass(frozen=True)
class ContractRow:
    """1 dòng tab ``DATA CONTRACT`` (FB.JT). ``brand`` có thể là tên brand hoặc tên group."""

    contract_no: str
    brand: str
    entity_b: str
    channel: str
    status: str


def signed_contracts(rows: Iterable[ContractRow], *, brand_codes: Sequence[str], group_code: str | None,
                     entity: str) -> list[str]:
    """Số hợp đồng ``Đã ký`` khớp (brand hoặc group) + pháp nhân B + kênh FB. Audit Gate cần đúng 1."""
    keys = {normalize_text(b) for b in brand_codes} | ({normalize_text(group_code)} if group_code else set())
    return sorted({
        r.contract_no.strip() for r in rows
        if r.contract_no.strip() and normalize_text(r.status) == _SIGNED and normalize_text(r.brand) in keys
        and normalize_text(r.entity_b) == normalize_text(entity) and normalize_text(r.channel) == "fb"
    })


@dataclass(frozen=True)
class CaseBbnt:
    entity: str                  # pháp nhân Bên B (EZS / SASSY)
    brand_codes: Sequence[str]   # mọi brand trong link order
    campaign: str                # FB.JT 'CAMPAIGN (KHỚP HARAVAN)', dạng '<tên> - MM/YYYY'
    contract_no: str             # từ DATA CONTRACT (đúng 1)


def build_input(case: CaseBbnt) -> BbntInput:
    """Sai dạng campaign -> ``ValueError`` (chuyển KAM, không đoán)."""
    brands = sorted({b.strip().upper() for b in case.brand_codes if b.strip()})
    if not brands:
        raise ValueError("Case không có brand")
    program = campaign_program_name(case.campaign)
    return BbntInput(channel=CHANNEL, entity=case.entity, bbnt_suffix=campaign_suffix(case.campaign),
                     campaign_name=program, program_override=program, contract_no=case.contract_no,
                     brand_codes=brands, breakdown_mode="brand" if len(brands) > 1 else None)


@dataclass(frozen=True)
class BbntOutcome:
    result: BbntResult | None
    reasons: tuple[str, ...]     # mã lỗi generator / adapter; rỗng = BBNT hợp lệ
    #: Của đúng file .docx vừa tạo (render=True) — bằng chứng file đã gửi. Hai lần render cùng số liệu vẫn khác
    #: byte (thời gian trong file zip), nên so sánh BBNT bằng số BBNT + số liệu, không bằng hash.
    sha256: str | None = None

    @property
    def contract_in_portal_master(self) -> bool:
        return not {"CONTRACT_NOT_FOUND", "CONTRACT_AMBIGUOUS"} & set(self.reasons)


def make_bbnt(templates: TemplateRegistry, master: MasterData, case: CaseBbnt, open_order: OrderOpener, *,
              issued_at: datetime, render: bool) -> BbntOutcome:
    """Chạy generator; lỗi nghiệp vụ trả về ``reasons`` (không raise) để worker chuyển NEEDS_REVIEW."""
    try:
        inp = build_input(case)
    except ValueError:
        return BbntOutcome(None, ("CAMPAIGN_FORMAT",))
    try:
        r = BbntGenerator(templates, clock=lambda: issued_at).run(master, inp, open_order, render=render)
    except BbntError as exc:
        return BbntOutcome(None, (exc.code,))
    return BbntOutcome(r, (), hashlib.sha256(r.content).hexdigest() if r.content is not None else None)
