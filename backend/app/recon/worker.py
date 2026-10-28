"""
Worker đối soát — chế độ **dry-run** (plan bước 8): đọc FB.JT + link order + DATA CONTRACT, quyết định từng case
sẽ làm gì, **không ghi gì cả** (không DB, không sheet, không email). Chạy thật (``run-once``) nối thêm DB + Google
khi có token bot.

Mỗi link order = 1 case (docs/ARCHITECTURE.md 14.1); 1 link có thể phủ nhiều dòng FB.JT (1 dòng/brand).
Định danh case = **spreadsheet id** của link (như ``recon_case.order_sheet_id`` UNIQUE), không phải chuỗi URL —
``/edit#gid=0`` và ``/edit?usp=sharing`` của cùng 1 file là 1 case.

    python -m app.recon.worker dry-run --fixture fixture.json --master-file deploy/MASTER_BBNT_TEMPLATE.xlsx

``fixture.json``: ``{"fbjt": [[...]], "links": [{"brand", "campaign", "url"}], "contracts": [{"contract_no",
"brand", "entity_b", "channel", "status"}], "orders": {"<url>": [[...]]}}`` — ``fbjt`` là giá trị tab FB-{YYYY}
(header ở dòng 6), ``orders`` là giá trị tab ORDER của từng link.
"""
from __future__ import annotations

import argparse
import enum
import json
import re
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.config import DEFAULT_TEMPLATES_DIR
from app.core.calculation import normalize_text
from app.master.models import MasterData
from app.recon.bbnt_job import CaseBbnt, ContractRow, make_bbnt, signed_contracts
from app.recon.fbjt import FbjtRow, brand_key, campaign_key, parse_rows, row_key
from app.recon.rules import CellAction, acceptance_send_date, allocate_total, entity_code, plan_cell
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


#: Cảnh báo generator được đưa ra plan để KAM thấy (không chặn — xem docs/OPEN_QUESTIONS.md Q9).
WARNING_CODES = ("RETURNED", "CANCELLED", "UNKNOWN_STATUS")


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
    order_sheet_id: str | None
    rows: list[int]
    brands: list[str]
    campaign: str
    action: Action
    reasons: list[str] = field(default_factory=list)
    send_from: date | None = None
    bbnt_number: str | None = None
    cells: list[CellPlan] = field(default_factory=list)
    #: Gợi ý sửa dữ liệu — chỉ in trong báo cáo chi tiết (--out), không vào log CI.
    hints: list[str] = field(default_factory=list)
    #: Số dòng order bị loại khỏi NMV theo mã (Hoàn / Hủy / status lạ).
    warnings: dict[str, int] = field(default_factory=dict)


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
               today: date, now: datetime, issued_at: Mapping[str, datetime] | None = None,
               brand_op: Mapping[str, str] | None = None) -> list[CasePlan]:
    """``issued_at``: ``bbnt_issued_at`` đã lưu theo ``order_sheet_id`` — chạy lại (kể cả tháng khác) ra đúng
    số BBNT cũ. Case chưa có -> dùng ``now`` (lần lập đầu). ``brand_op``: DATA BRAND brand (chuẩn hoá) -> cột OP."""
    outcome = parse_rows(fbjt_values)
    plans: list[CasePlan] = [CasePlan(None, None, [n], [], "", Action.REVIEW, ["FBJT_ROW_INVALID"])
                             for n, _msg in outcome.issues]
    index: dict[tuple[str, str], dict[str, str]] = {}      # key -> {sheet_id: url}
    bad_links: set[tuple[str, str]] = set()
    for link in links:
        key = row_key(link.brand, link.campaign)
        try:
            sid = parse_sheet_url(link.url).spreadsheet_id
        except ValueError:
            bad_links.add(key)
            continue
        index.setdefault(key, {}).setdefault(sid, link.url.strip())

    camps_of: dict[str, set[str]] = {}      # brand_key -> CAMP trong NON OP (gợi ý khi không thấy link)
    for link in links:
        camps_of.setdefault(row_key(link.brand, link.campaign)[0], set()).add(link.campaign.strip())

    def data_brand_op(row: FbjtRow) -> bool:
        return normalize_text((brand_op or {}).get(brand_key(row.brand), "")) not in ("", "non op")

    seen: dict[tuple[str, str], int] = {}
    for row in outcome.rows:
        seen[row_key(row.brand, row.campaign)] = seen.get(row_key(row.brand, row.campaign), 0) + 1
    groups: dict[str, list[FbjtRow]] = {}
    for row in outcome.rows:
        key = row_key(row.brand, row.campaign)
        sheets = index.get(key, {})
        h = normalize_text(row.op)
        if h == "op":                               # phạm vi bot: chỉ cột H = "NON OP"
            plans.append(CasePlan(None, None, [row.row_number], [row.brand], row.campaign, Action.SKIP,
                                  ["NOT_FB_NON_OP"]))
        elif not row.is_non_op:                     # H trống / giá trị lạ -> không tự bỏ, chờ duyệt
            plans.append(CasePlan(None, None, [row.row_number], [row.brand], row.campaign, Action.REVIEW,
                                  ["OP_COLUMN_BLANK" if not h else "OP_COLUMN_UNKNOWN"],
                                  hints=[f"Cột H (OP) = {row.op!r} — cần 'NON OP' hoặc 'OP'"]))
        elif data_brand_op(row):                    # H = NON OP nhưng DATA BRAND ghi OP -> không đoán
            plans.append(CasePlan(None, None, [row.row_number], [row.brand], row.campaign, Action.REVIEW,
                                  ["OP_CONFLICT_DATA_BRAND"],
                                  hints=[f"Cột H NON OP, DATA BRAND.OP = {(brand_op or {})[brand_key(row.brand)]!r}"]))
        elif seen[key] > 1:
            plans.append(CasePlan(None, None, [row.row_number], [row.brand], row.campaign, Action.REVIEW,
                                  ["FBJT_ROW_DUPLICATE"]))
        elif key in bad_links or len(sheets) != 1:
            code = "LINK_INVALID" if key in bad_links else "LINK_NOT_FOUND" if not sheets else "LINK_AMBIGUOUS"
            pl = CasePlan(None, None, [row.row_number], [row.brand], row.campaign, Action.SKIP, [code])
            if code == "LINK_NOT_FOUND":
                month = key[1][-7:] if re.search(r"\d{2}/\d{4}$", key[1]) else ""
                near = sorted(c for c in camps_of.get(key[0], set()) if campaign_key(c).endswith(month))
                pl.hints.append("NON OP cùng brand, cùng tháng: " + (", ".join(near[:3]) if near
                                                                     else "không có (brand chưa có link tháng này)"))
            plans.append(pl)
        else:
            groups.setdefault(next(iter(sheets)), []).append(row)
    urls = {sid: url for d in index.values() for sid, url in d.items()}

    for sid, rows in groups.items():
        url = urls[sid]
        brands = sorted({r.brand for r in rows})
        campaigns = {r.campaign for r in rows}
        plan = CasePlan(url, sid, [r.row_number for r in rows], brands, rows[0].campaign, Action.REVIEW)
        plans.append(plan)
        ends = [r.camp_end for r in rows]
        if any(e is None for e in ends):
            plan.action, plan.reasons = Action.WAIT, ["NO_CAMP_END"]
            continue
        plan.send_from = acceptance_send_date(max(e for e in ends if e is not None))
        if today < plan.send_from:
            plan.action = Action.WAIT
            continue
        entities = {entity_code(r.entity_b) for r in rows}
        # Link phủ (BRAND, CAMP) nào thì FB.JT phải có đúng các dòng đó (dòng thiếu / Done / lỗi -> không tách số)
        if {k for k, d in index.items() if sid in d} != {row_key(r.brand, r.campaign) for r in rows}:
            plan.reasons.append("BRAND_ROWS_MISMATCH")
        if len(campaigns) != 1:
            plan.reasons.append("CAMPAIGN_MISMATCH")
        if None in entities:
            plan.reasons.append("ENTITY_UNKNOWN")
            plan.hints.append("BÊN B: " + ", ".join(sorted({repr(r.entity_b) for r in rows})))
        elif len(entities) != 1:
            plan.reasons.append("ENTITY_MISMATCH")
        if plan.reasons:
            continue
        entity = next(e for e in entities if e)
        groups_ = {r.group for r in rows if r.group}
        found = signed_contracts(contracts, brand_codes=brands, entity=entity,
                                 group_code=next(iter(groups_)) if len(groups_) == 1 else None)
        if len(found) != 1:
            plan.reasons.append("CONTRACT_NOT_UNIQUE")
            plan.hints.append(f"DATA CONTRACT 'Đã ký' khớp {'/'.join(brands)} + {entity}: "
                              + (", ".join(found) if found else "không có"))
            continue
        out = make_bbnt(templates, master, CaseBbnt(entity, brands, plan.campaign, found[0]), open_order(url),
                        issued_at=(issued_at or {}).get(sid, now), render=False)
        if out.result is None:
            plan.reasons.extend(out.reasons)
            continue
        plan.bbnt_number = str(out.result.number)
        for w in out.result.warnings:
            if w.code.value in WARNING_CODES:
                plan.warnings[w.code.value] = plan.warnings.get(w.code.value, 0) + 1
        if out.result.contract.model != "NON_OP":      # chỉ FB Non-OP (11.2-1); dry-run không render template
            plan.reasons.append("CONTRACT_MODEL_OP")
        c, issued_on = out.result.contract, out.result.number.issue_date  # Q12: hiệu lực theo ngày lập BBNT
        if issued_on is None or issued_on < c.valid_from or (c.valid_to is not None and issued_on > c.valid_to):
            plan.reasons.append("CONTRACT_NOT_VALID_ON_BBNT_DATE")
        plan.cells, problems = cell_plans(rows, out.result)
        plan.reasons.extend(problems)
        if any(c.action is CellAction.REVIEW for c in plan.cells):
            plan.reasons.append("FIGURES_DIFFER_FBJT")
        if not plan.reasons:
            plan.action = Action.DRAFT
    return plans


# --------------------------------------------------------------------------------------------------------- CLI
def _fixture_opener(orders: dict[str, list[list[Any]]]) -> Callable[[str], OrderOpener]:
    by_id = {parse_sheet_url(u).spreadsheet_id: rows for u, rows in orders.items()}

    def for_url(url: str) -> OrderOpener:
        def open_() -> list[OrderWorkbook]:
            rows = by_id.get(parse_sheet_url(url).spreadsheet_id)
            if rows is None:
                raise SheetAccessError(f"Fixture không có dữ liệu order cho {url}")
            return [OrderWorkbook("google_sheet", ["ORDER"], lambda _t: rows)]
        parse_sheet_url(url)
        return open_
    return for_url


def _fmt(v: Decimal | None) -> str:
    return "-" if v is None else f"{v:,.0f}".replace(",", ".")


def _report(plans: Sequence[CasePlan]) -> list[str]:
    lines: list[str] = []
    for pl in plans:
        lines.append(f"[{pl.action.value}] dòng {','.join(map(str, pl.rows))} · {'/'.join(pl.brands)} · {pl.campaign}"
                     + (f" · gửi từ {pl.send_from:%d/%m/%Y}" if pl.send_from else "")
                     + (f" · {pl.bbnt_number}" if pl.bbnt_number else "")
                     + (f" · lý do: {', '.join(pl.reasons)}" if pl.reasons else "")
                     + (f" · loại khỏi NMV: {pl.warnings}" if pl.warnings else "")
                     + (f" · link: {pl.url}" if pl.url and pl.action is not Action.DRAFT else ""))
        lines += [f"    gợi ý: {h}" for h in pl.hints]
        for c in pl.cells:
            lines.append(f"    dòng {c.row_number} {c.column}: {_fmt(c.current)} -> {_fmt(c.target)} "
                         f"[{c.action.value}]")
    return lines


def summary(plans: Sequence[CasePlan]) -> list[str]:
    """Chỉ số đếm theo action + mã lý do — không có brand, số tiền (an toàn cho log CI)."""
    actions = Counter(p.action.value for p in plans)
    reasons = Counter(r for p in plans for r in p.reasons)
    return ([f"{k}: {actions[k]}" for k in ("SKIP", "WAIT", "REVIEW", "DRAFT")]
            + [f"  {r}: {n}" for r, n in sorted(reasons.items())])


def _live(today: date, now: datetime) -> list[CasePlan]:
    from app.master.repository import build_master, google_sheet_source
    from app.recon.google_bot import (
        BotEnv,
        BotSheets,
        parse_brand_op,
        parse_contracts,
        parse_order_links,
        refresh_access_token,
    )
    from app.services.sheets import GoogleSheetsReader, SheetRef

    env = BotEnv.from_env()
    token = refresh_access_token(env)
    reader = GoogleSheetsReader(token)
    fbjt = SheetRef(env.fbjt_id)
    links = [OrderLink(*x) for x in parse_order_links(BotSheets(token).grid_rows(env.order_links_id,
                                                                                 f"NON OP {today:%y}"))]

    def open_order(url: str) -> OrderOpener:
        ref = parse_sheet_url(url)
        return lambda: [reader.open(ref)]

    return plan_cases(reader.read(fbjt, sheet_name=f"FB-{today.year}"), links,
                      parse_contracts(reader.read(fbjt, sheet_name="DATA CONTRACT")),
                      build_master(google_sheet_source(reader, parse_sheet_url(env.master_sheet_url))()),
                      TemplateRegistry(DEFAULT_TEMPLATES_DIR), open_order, today=today, now=now,
                      brand_op=parse_brand_op(reader.read(fbjt, sheet_name="DATA BRAND")))


def main(argv: Sequence[str] | None = None) -> int:
    from app.master.repository import build_master, xlsx_source

    p = argparse.ArgumentParser(prog="python -m app.recon.worker")
    p.add_argument("mode", choices=["dry-run"])
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--fixture", type=Path, help="JSON giả lập (cần --master-file)")
    src.add_argument("--live", action="store_true", help="Đọc Google thật bằng token nền (biến RECON_*), chỉ đọc")
    p.add_argument("--master-file")
    p.add_argument("--today", type=date.fromisoformat, default=None)
    p.add_argument("--summary", action="store_true", help="Chỉ in số đếm (cho log CI); chi tiết ghi vào --out")
    p.add_argument("--out", type=Path, help="Ghi báo cáo chi tiết vào file")
    a = p.parse_args(argv)
    now = datetime.now(UTC)
    today = a.today or now.date()
    if a.live:
        from app.recon.google_bot import BotConfigError

        try:
            plans = _live(today, now)
        except Exception as exc:  # noqa: BLE001 — log CI: không in chi tiết có thể chứa dữ liệu brand
            print(f"LỖI {type(exc).__name__}: {exc if isinstance(exc, BotConfigError) else '(chi tiết ẩn)'}",
                  file=sys.stderr)
            return 1
    else:
        if not a.master_file:
            p.error("--fixture cần --master-file")
        data = json.loads(a.fixture.read_text(encoding="utf-8"))
        plans = plan_cases(data["fbjt"], [OrderLink(**x) for x in data["links"]],
                           [ContractRow(**x) for x in data.get("contracts", [])],
                           build_master(xlsx_source(a.master_file)()), TemplateRegistry(DEFAULT_TEMPLATES_DIR),
                           _fixture_opener(data.get("orders", {})), today=today, now=now)
    detail = _report(plans)
    if a.out:
        a.out.write_text("\n".join(detail) + "\n", encoding="utf-8")
    print("\n".join(summary(plans) if a.summary else detail))
    return 0


if __name__ == "__main__":
    sys.exit(main())
