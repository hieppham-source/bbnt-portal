"""
``run-once`` (plan bước 8, D): lưu kết quả lập kế hoạch vào Postgres — idempotent theo
docs/RECON_STATE_PROPOSAL.md — rồi, chỉ khi được bật, ghi ô I/K/M lên FB.JT.

* Case định danh bằng ``order_sheet_id``; lần lập BBNT đầu lưu ``bbnt_number`` + ``bbnt_issued_at`` + số liệu.
  Chạy lại (kể cả tháng khác) dùng lại ``bbnt_issued_at`` -> cùng số BBNT.
* Plan không đổi (``last_plan_hash``) -> không ghi transition / audit mới.
* Số liệu đổi sau khi đã lập BBNT -> NEEDS_REVIEW ``FIGURES_CHANGED_AFTER_ISSUE`` (không tự lập bản sửa).
* Ô cần ghi -> ``sheet_write`` PLANNED (UNIQUE theo case/dòng/cột/giá trị: không bao giờ ghi 2 lần).
  Ô khác số BBNT -> ``cell_review`` mở; người duyệt chọn CHANGE -> lần chạy sau tạo ``sheet_write``.
* Bot chỉ chạm case ở WAITING / NEEDS_REVIEW / BBNT_DRAFTED / FIGURES_WRITTEN; trạng thái khác (Giữ, đã gửi…)
  do người / bước sau quyết định.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from app.recon.db import CaseState, NewCase, apply_transition, create_case
from app.recon.fbjt import FbjtRow, brand_key, campaign_key
from app.recon.rules import CellAction, entity_code
from app.recon.sheet_writer import CellResult, CellWrite
from app.recon.state_machine import InvalidTransition, State, Trigger, transition
from app.recon.worker import Action, CasePlan

ACTOR = "worker"
LOCK_KEY = 0x5245434F4E  # "RECON" — pg_try_advisory_lock: 1 worker một lúc
BOT_STATES = frozenset({State.WAITING, State.NEEDS_REVIEW, State.BBNT_DRAFTED, State.FIGURES_WRITTEN})
_TARGET = {Action.WAIT: State.WAITING, Action.REVIEW: State.NEEDS_REVIEW, Action.DRAFT: State.BBNT_DRAFTED}


@dataclass
class RunStats:
    cases_new: int = 0
    transitions: int = 0
    unchanged: int = 0
    untouched: int = 0          # case ở trạng thái bot không được đổi
    writes_planned: int = 0
    reviews_opened: int = 0
    written: int = 0
    conflicts: int = 0
    failed: int = 0
    notes: list[str] = field(default_factory=list)


def figures(p: CasePlan) -> dict[str, str]:
    return {f"{c.row_number}:{c.column}": str(c.target) for c in p.cells}


def plan_hash(p: CasePlan, reasons: Sequence[str]) -> str:
    raw = json.dumps({"a": p.action.value, "r": sorted(reasons), "n": p.bbnt_number, "f": figures(p),
                      "c": sorted((c.row_number, c.column, c.action.value) for c in p.cells)}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def stored_issued_at(conn: psycopg.Connection) -> dict[str, datetime]:
    rows = conn.execute("SELECT order_sheet_id, bbnt_issued_at FROM recon_case WHERE bbnt_issued_at IS NOT NULL")
    return {r[0]: r[1] for r in rows.fetchall()}


def _move(conn: psycopg.Connection, case: CaseState, to: State, reason: str, at: datetime) -> CaseState:
    """Chuyển trạng thái; FIGURES_WRITTEN từ WAITING phải đi qua BBNT_DRAFTED. Không hợp lệ -> giữ nguyên."""
    path = [State.BBNT_DRAFTED, to] if to is State.FIGURES_WRITTEN and case.state is State.WAITING else [to]
    for step in path:
        if case.state is step:
            continue
        try:
            t = transition(case.state, step, trigger=Trigger.WORKER, actor=ACTOR, at=at, reason=reason)
        except InvalidTransition:
            return case
        case = apply_transition(conn, case, t)
    return case


def persist(conn: psycopg.Connection, plans: Sequence[CasePlan], rows: Mapping[int, FbjtRow], *, tab: str,
            now: datetime, run_id: uuid.UUID) -> RunStats:
    st = RunStats()
    for p in plans:
        if not p.order_sheet_id or not p.url:
            continue                                   # SKIP / dòng lỗi: chỉ có trong báo cáo
        cur = conn.execute("SELECT id, state, state_version, held_from, bbnt_number, figures, last_plan_hash "
                           "FROM recon_case WHERE order_sheet_id = %s", (p.order_sheet_id,)).fetchone()
        if cur is None:
            first = rows[p.rows[0]]
            create_case(conn, NewCase(p.order_sheet_id, p.url, p.brands, p.campaign, first.camp_end,
                                      entity_code(first.entity_b), first.group or None, first.pic or None),
                        actor=ACTOR, at=now)
            st.cases_new += 1
            cur = conn.execute("SELECT id, state, state_version, held_from, bbnt_number, figures, last_plan_hash "
                               "FROM recon_case WHERE order_sheet_id = %s", (p.order_sheet_id,)).fetchone()
        assert cur is not None
        case = CaseState(cur[0], State(cur[1]), cur[2], State(cur[3]) if cur[3] else None)
        stored_no, stored_fig, last_hash = cur[4], cur[5], cur[6]
        if case.state not in BOT_STATES:
            st.untouched += 1
            continue
        target, reasons = _TARGET[p.action], list(p.reasons)
        if p.action is Action.DRAFT and stored_no and stored_fig is not None and stored_fig != figures(p):
            target, reasons = State.NEEDS_REVIEW, ["FIGURES_CHANGED_AFTER_ISSUE"]
        elif p.action is Action.DRAFT and p.cells and all(c.action is CellAction.NOOP for c in p.cells):
            target = State.FIGURES_WRITTEN                # 3 ô trên FB.JT đã bằng số BBNT
        h = plan_hash(p, reasons)
        with conn.transaction():
            if case.state is not target:
                before = case.state
                case = _move(conn, case, target, ",".join(reasons), now)
                st.transitions += case.state is not before
            elif h == last_hash:
                st.unchanged += 1
            if p.action is Action.DRAFT and stored_no is None and target is not State.NEEDS_REVIEW:
                conn.execute("UPDATE recon_case SET bbnt_number = %s, bbnt_issued_at = %s, figures = %s "
                             "WHERE id = %s", (p.bbnt_number, now, Jsonb(figures(p)), case.id))
            conn.execute("UPDATE recon_case SET review_reasons = %s, last_plan_hash = %s, last_run_id = %s, "
                         "updated_at = %s WHERE id = %s", (reasons, h, run_id, now, case.id))
            row_ids = {n: _upsert_row(conn, rows[n], tab, case.id, now) for n in p.rows}
            if target is State.NEEDS_REVIEW and reasons == ["FIGURES_CHANGED_AFTER_ISSUE"]:
                continue
            for c in p.cells:
                if c.action is CellAction.WRITE and p.action is Action.DRAFT:
                    st.writes_planned += conn.execute(
                        "INSERT INTO sheet_write (case_id, sheet_row_id, column_name, seen_value, target_value, "
                        "status, run_id, created_at) VALUES (%s, %s, %s, %s, %s, 'PLANNED', %s, %s) "
                        "ON CONFLICT DO NOTHING",
                        (case.id, row_ids[c.row_number], c.column, c.current, c.target, run_id, now)).rowcount
                elif c.action is CellAction.REVIEW:
                    st.reviews_opened += conn.execute(
                        "INSERT INTO cell_review (case_id, sheet_row_id, column_name, current_value, bbnt_value, "
                        "created_at) VALUES (%s, %s, %s, %s, %s, %s) "
                        "ON CONFLICT (sheet_row_id, column_name) WHERE decision IS NULL DO NOTHING",
                        (case.id, row_ids[c.row_number], c.column, c.current, c.target, now)).rowcount
    return st


def _upsert_row(conn: psycopg.Connection, r: FbjtRow, tab: str, case_id: uuid.UUID, now: datetime) -> int:
    row = conn.execute(
        "INSERT INTO sheet_row (tab, brand_key, campaign_key, case_id, last_row_number, last_seen_at) "
        "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (tab, brand_key, campaign_key) DO UPDATE SET "
        "case_id = EXCLUDED.case_id, last_row_number = EXCLUDED.last_row_number, last_seen_at = EXCLUDED.last_seen_at "
        "RETURNING id", (tab, brand_key(r.brand), campaign_key(r.campaign), case_id, r.row_number, now)).fetchone()
    assert row is not None
    return int(row[0])


Writer = Callable[[Sequence[CellWrite]], list[CellResult]]


def write_back(conn: psycopg.Connection, write: Writer, letters: Mapping[str, str], *, now: datetime,
               run_id: uuid.UUID, st: RunStats) -> None:
    """Thực hiện ``sheet_write`` PLANNED (kể cả ô KAM đã duyệt CHANGE). Mỗi ô 1 dòng ``audit_log``."""
    with conn.transaction():
        conn.execute(
            "INSERT INTO sheet_write (case_id, sheet_row_id, column_name, seen_value, target_value, status, "
            "approved_by, run_id, created_at) SELECT case_id, sheet_row_id, column_name, current_value, bbnt_value, "
            "'PLANNED', decided_by, %s, %s FROM cell_review WHERE decision = 'CHANGE' ON CONFLICT DO NOTHING",
            (run_id, now))
    todo = conn.execute(
        "SELECT sw.id, sr.last_row_number, sw.column_name, sw.seen_value, sw.target_value FROM sheet_write sw "
        "JOIN sheet_row sr ON sr.id = sw.sheet_row_id JOIN recon_case c ON c.id = sw.case_id "
        "WHERE sw.status = 'PLANNED' AND c.state NOT IN ('ON_HOLD', 'DONE', 'ERROR') ORDER BY sw.id").fetchall()
    if not todo:
        return
    writes = [CellWrite(r[1], letters[r[2]], r[3], r[4]) for r in todo]
    for (sw_id, *_), res in zip(todo, write(writes), strict=True):
        with conn.transaction():
            conn.execute("UPDATE sheet_write SET status = %s, run_id = %s, updated_at = %s WHERE id = %s",
                         (res.status, run_id, now, sw_id))
            conn.execute(
                "INSERT INTO audit_log (at, actor, action, entity, entity_id, before, after, run_id) "
                "VALUES (%s, %s, 'SHEET_WRITE', 'fbjt_cell', %s, %s, %s, %s)",
                (now, ACTOR, f"{res.write.column}{res.write.row}", Jsonb({"value": _jsonable(res.before)}),
                 Jsonb({"value": _jsonable(res.after), "target": str(res.write.target), "status": res.status,
                        "note": res.note, "sheet_write_id": sw_id}), run_id))
        st.written += res.status == "VERIFIED"
        st.conflicts += res.status == "CONFLICT"
        st.failed += res.status == "FAILED"


def _jsonable(v: Any) -> Any:
    return v if v is None or isinstance(v, (int, float, str, bool)) else str(v)
