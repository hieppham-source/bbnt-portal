"""
Worker đối soát — chế độ **dry-run** (plan bước 8): đọc FB.JT + link order + DATA CONTRACT, quyết định từng case
sẽ làm gì, **không ghi gì cả** (không DB, không sheet, không email). Chạy thật (``run-once``) nối thêm DB + Google
khi có token bot.

Mỗi link order = 1 case (docs/ARCHITECTURE.md 14.1); 1 link có thể phủ nhiều dòng FB.JT (1 dòng/brand).

    python -m app.recon.worker dry-run --fixture fixture.json --master-file deploy/MASTER_BBNT_TEMPLATE.xlsx

``fixture.json``: ``{"fbjt": [[...]], "links": [{"brand", "campaign", "url"}], "contracts": [{"contract_no",
"brand", "entity_b", "channel", "status"}], "orders": {"<url>": [[...]]}}`` — ``fbjt`` là giá trị tab FB-{YYYY}
(header ở dòng 6), ``orders`` là giá trị tab ORDER của từng link.
"""
from __future__ import annotations

import argparse
import enum
import json
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.config import DEFAULT_TEMPLATES_DIR
from app.core.calculation import normalize_text
from app.master.models import MasterData
from app.recon.bbnt_job import CaseBbnt, ContractRow, make_bbnt, signed_contracts
from app.recon.fbjt import FbjtRow, parse_rows, row_key
from app.recon.rules import CellAction, acceptance_send_date, allocate_total, plan_cell
from app.services.generator import BbntResult, OrderOpener
from app.services.sheets import OrderWorkbook, SheetAccessError, parse_sheet_url
from app.services.templates import TemplateRegistry


@dataclass(frozen=True)
class OrderLink:
    """1 dòng tab ``NON OP {YY}``: (BRAND, CAMP) -> link order (chip link cột F)."""

    brand: str
    campaign: str
    url: str


class Action(str, enum.Enum):
    SKIP = "SKIP"        # không nối được dòng FB.JT với link order -> KAM
    WAIT = "WAIT"        # chưa tới G + 25 / chưa có ngày kết thúc camp
    REVIEW = "REVIEW"    # có lý do chặn -> hàng đợi KAM
    DRAFT = "DRAFT"      # tạo BBNT được; kèm kế hoạch ghi ô I/K/M


@dataclass(frozen=True)
class CellPlan:
    row_number: int
    column: str                  # nmv | commission | gmv
    current: Decimal | None
    target: Decimal
    action: CellAction


@dataclass
class CasePlan:
    url: str | None
    rows: list[int]
    brands: list[str]
    campaign: str
    action: Action
    reasons: list[str] = field(default_factory=list)
    send_from: date | None = None
    bbnt_number: str | None = None
    cells: list[CellPlan] = field(default_factory=list)


def _commission_k(r: BbntResult) -> Decimal:
    """Cột K FB.JT = hoa hồng sau VAT (EZS) / sau cấn trừ (Sassy); không cộng thu hộ, phí tư vấn (13.1)."""
    c = r.commission
    return c.revenue + c.vat_amount - c.tax_deduction_amount


def cell_plans(rows: Sequence[FbjtRow], r: BbntResult) -> tuple[list[CellPlan], list[str]]:
    """Số BBNT cho từng dòng FB.JT: 1 dòng = cả BBNT; BBNT group = bảng tách brand, K chia theo NMV (15.1)."""
    k_total = _commission_k(r)
    if len(rows) == 1:
        targets = {rows[0].row_number: (r.nmv_vnd, k_total, r.gmv_vnd)}
    else:
        by_brand = {normalize_text(b.brand_code or b.brand_name): b for b in r.brand_breakdown}
        missing = [x.brand for x in rows if normalize_text(x.brand) not in by_brand]
        if missing or len(by_brand) != len(rows):
            return [], ["BRAND_ROWS_MISMATCH"]
        nmv = {x.row_number: by_brand[normalize_text(x.brand)].nmv.nmv for x in rows}
        nmv_total = sum(nmv.values(), Decimal(0))
        raw_k = {str(n): (k_total * v / nmv_total if nmv_total else Decimal(0)) for n, v in nmv.items()}
        k = allocate_total(k_total, raw_k, {str(n): v for n, v in nmv.items()})
        targets = {x.row_number: (nmv[x.row_number].quantize(Decimal(1)), k[str(x.row_number)],
                                  by_brand[normalize_text(x.brand)].nmv.gmv_estimated.quantize(Decimal(1)))
                   for x in rows}
    plans: list[CellPlan] = []
    for x in rows:
        nmv_t, k_t, gmv_t = targets[x.row_number]
        for col, cur, tgt in (("nmv", x.nmv, nmv_t), ("commission", x.commission, k_t), ("gmv", x.gmv, gmv_t)):
            plans.append(CellPlan(x.row_number, col, cur, tgt, plan_cell(cur, tgt)))
    return plans, []


def plan_cases(fbjt_values: Sequence[Sequence[Any]], links: Iterable[OrderLink], contracts: Sequence[ContractRow],
               master: MasterData, templates: TemplateRegistry, open_order: Callable[[str], OrderOpener], *,
               today: date, now: datetime) -> list[CasePlan]:
    outcome = parse_rows(fbjt_values)
    index: dict[tuple[str, str], set[str]] = {}
    for link in links:
        index.setdefault(row_key(link.brand, link.campaign), set()).add(link.url.strip())

    plans: list[CasePlan] = []
    groups: dict[str, list[FbjtRow]] = {}
    for row in outcome.rows:
        urls = index.get(row_key(row.brand, row.campaign), set())
        if len(urls) != 1:
            plans.append(CasePlan(None, [row.row_number], [row.brand], row.campaign, Action.SKIP,
                                  ["LINK_NOT_FOUND" if not urls else "LINK_AMBIGUOUS"]))
            continue
        groups.setdefault(next(iter(urls)), []).append(row)

    for url, rows in groups.items():
        brands = sorted({r.brand for r in rows})
        campaigns = {r.campaign for r in rows}
        plan = CasePlan(url, [r.row_number for r in rows], brands, rows[0].campaign, Action.REVIEW)
        plans.append(plan)
        ends = [r.camp_end for r in rows]
        if any(e is None for e in ends):
            plan.action, plan.reasons = Action.WAIT, ["NO_CAMP_END"]
            continue
        plan.send_from = acceptance_send_date(max(e for e in ends if e is not None))
        if today < plan.send_from:
            plan.action = Action.WAIT
            continue
        entities = {normalize_text(r.entity_b) for r in rows}
        if len(campaigns) != 1:
            plan.reasons.append("CAMPAIGN_MISMATCH")
        if len(entities) != 1:
            plan.reasons.append("ENTITY_MISMATCH")
        if not all(r.is_non_op for r in rows):
            plan.reasons.append("NOT_FB_NON_OP")
        if plan.reasons:
            continue
        entity = rows[0].entity_b.upper()
        groups_ = {r.group for r in rows if r.group}
        found = signed_contracts(contracts, brand_codes=brands, entity=entity,
                                 group_code=next(iter(groups_)) if len(groups_) == 1 else None)
        if len(found) != 1:
            plan.reasons.append("CONTRACT_NOT_UNIQUE")
            continue
        out = make_bbnt(templates, master, CaseBbnt(entity, brands, plan.campaign, found[0]), open_order(url),
                        issued_at=now, render=False)
        if out.result is None:
            plan.reasons.extend(out.reasons)
            continue
        plan.bbnt_number = str(out.result.number)
        plan.cells, problems = cell_plans(rows, out.result)
        plan.reasons.extend(problems)
        if any(c.action is CellAction.REVIEW for c in plan.cells):
            plan.reasons.append("FIGURES_DIFFER_FBJT")
        if not plan.reasons:
            plan.action = Action.DRAFT
    return plans


# --------------------------------------------------------------------------------------------------------- CLI
def _fixture_opener(orders: dict[str, list[list[Any]]]) -> Callable[[str], OrderOpener]:
    def for_url(url: str) -> OrderOpener:
        def open_() -> list[OrderWorkbook]:
            rows = orders.get(url)
            if rows is None:
                raise SheetAccessError(f"Fixture không có dữ liệu order cho {url}")
            return [OrderWorkbook("google_sheet", ["ORDER"], lambda _t: rows)]
        parse_sheet_url(url)
        return open_
    return for_url


def _fmt(v: Decimal | None) -> str:
    return "-" if v is None else f"{v:,.0f}".replace(",", ".")


def main(argv: Sequence[str] | None = None) -> int:
    from app.master.repository import build_master, xlsx_source

    p = argparse.ArgumentParser(prog="python -m app.recon.worker")
    p.add_argument("mode", choices=["dry-run"])
    p.add_argument("--fixture", required=True, type=Path)
    p.add_argument("--master-file", required=True)
    p.add_argument("--today", type=date.fromisoformat, default=None)
    a = p.parse_args(argv)
    data = json.loads(a.fixture.read_text(encoding="utf-8"))
    master = build_master(xlsx_source(a.master_file)())
    now = datetime.now(UTC)
    plans = plan_cases(data["fbjt"], [OrderLink(**x) for x in data["links"]],
                       [ContractRow(**x) for x in data.get("contracts", [])], master,
                       TemplateRegistry(DEFAULT_TEMPLATES_DIR), _fixture_opener(data.get("orders", {})),
                       today=a.today or now.date(), now=now)
    for pl in plans:
        print(f"[{pl.action.value}] dòng {','.join(map(str, pl.rows))} · {'/'.join(pl.brands)} · {pl.campaign}"
              + (f" · gửi từ {pl.send_from:%d/%m/%Y}" if pl.send_from else "")
              + (f" · {pl.bbnt_number}" if pl.bbnt_number else "")
              + (f" · lý do: {', '.join(pl.reasons)}" if pl.reasons else ""))
        for c in pl.cells:
            print(f"    dòng {c.row_number} {c.column}: {_fmt(c.current)} -> {_fmt(c.target)} [{c.action.value}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
