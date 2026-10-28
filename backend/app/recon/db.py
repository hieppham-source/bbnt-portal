"""
Kho trạng thái case trên Postgres (schema ``migrations/0001_recon_init``), dùng psycopg 3.

Mỗi lần đổi trạng thái: kiểm tra hợp lệ bằng :mod:`app.recon.state_machine`, rồi trong **1 transaction** cập nhật
``recon_case`` (optimistic lock ``state_version``), ghi ``state_transition`` và ``audit_log``. Hai tiến trình cùng
sửa 1 case -> tiến trình đến sau nhận :class:`StaleCase` và phải đọc lại, không ghi đè.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime

import psycopg
from psycopg.types.json import Jsonb

from app.recon.state_machine import State, Transition, Trigger, resume, transition


class StaleCase(RuntimeError):
    """Case đã bị tiến trình khác đổi (state_version không khớp) hoặc không tồn tại."""


@dataclass(frozen=True)
class NewCase:
    order_sheet_id: str
    order_sheet_url: str
    brand_codes: list[str]
    campaign: str
    camp_end: date | None = None
    entity_code: str | None = None
    group_code: str | None = None
    pic: str | None = None


@dataclass(frozen=True)
class CaseState:
    id: uuid.UUID
    state: State
    version: int
    held_from: State | None


def create_case(conn: psycopg.Connection, new: NewCase, *, actor: str, at: datetime) -> uuid.UUID:
    """Tạo case ở WAITING. ``order_sheet_id`` trùng -> ``psycopg.errors.UniqueViolation`` (1 link = 1 case)."""
    case_id = uuid.uuid4()
    with conn.transaction():
        conn.execute(
            "INSERT INTO recon_case (id, order_sheet_id, order_sheet_url, brand_codes, campaign, camp_end, "
            "entity_code, group_code, pic, state, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (case_id, new.order_sheet_id, new.order_sheet_url, new.brand_codes, new.campaign, new.camp_end,
             new.entity_code, new.group_code, new.pic, State.WAITING.value, at, at))
        _log(conn, case_id, None, State.WAITING, Trigger.WORKER, actor, at, "create")
    return case_id


def get_case(conn: psycopg.Connection, case_id: uuid.UUID) -> CaseState:
    row = conn.execute("SELECT state, state_version, held_from FROM recon_case WHERE id = %s",
                       (case_id,)).fetchone()
    if row is None:
        raise StaleCase(f"Không có case {case_id}")
    return CaseState(case_id, State(row[0]), row[1], State(row[2]) if row[2] else None)


def apply_transition(conn: psycopg.Connection, case: CaseState, t: Transition) -> CaseState:
    """Ghi 1 transition đã kiểm tra. ``case`` là bản đã đọc (state + version) — lệch -> :class:`StaleCase`."""
    if t.from_state is not case.state:
        raise StaleCase(f"Case {case.id} đang {case.state.value}, không phải {t.from_state.value}")
    # Chỉ case đang Giữ mới có held_from (để resume); rời ON_HOLD thì xoá.
    held, hold_reason = (case.state, t.reason or None) if t.to_state is State.ON_HOLD else (None, None)
    with conn.transaction():
        cur = conn.execute(
            "UPDATE recon_case SET state = %s, state_version = state_version + 1, held_from = %s, hold_reason = %s, "
            "updated_at = %s WHERE id = %s AND state_version = %s AND state = %s",
            (t.to_state.value, held.value if held else None, hold_reason, t.at, case.id, case.version,
             case.state.value))
        if cur.rowcount != 1:
            raise StaleCase(f"Case {case.id} đã bị đổi bởi tiến trình khác — đọc lại rồi thử lại")
        _log(conn, case.id, t.from_state, t.to_state, t.trigger, t.actor, t.at, t.reason, t.evidence_id)
    return CaseState(case.id, t.to_state, case.version + 1, held)


def move(conn: psycopg.Connection, case_id: uuid.UUID, to: State, *, trigger: Trigger, actor: str,
         at: datetime, reason: str = "", evidence_id: str | None = None) -> CaseState:
    """Đọc case, kiểm tra quy tắc chuyển (:func:`transition`), ghi. Tiếp tục case đang Giữ: :func:`resume_case`."""
    case = get_case(conn, case_id)
    t = transition(case.state, to, trigger=trigger, actor=actor, at=at, reason=reason, evidence_id=evidence_id)
    return apply_transition(conn, case, t)


def resume_case(conn: psycopg.Connection, case_id: uuid.UUID, *, actor: str, at: datetime) -> CaseState:
    case = get_case(conn, case_id)
    if case.state is not State.ON_HOLD or case.held_from is None:
        raise StaleCase(f"Case {case_id} không ở trạng thái Giữ")
    return apply_transition(conn, case, resume(case.held_from, actor=actor, at=at))


def _log(conn: psycopg.Connection, case_id: uuid.UUID, from_state: State | None, to_state: State,
         trigger: Trigger, actor: str, at: datetime, reason: str, evidence_id: str | None = None) -> None:
    conn.execute(
        "INSERT INTO state_transition (case_id, from_state, to_state, trigger, actor, reason, evidence_id, at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (case_id, from_state.value if from_state else None, to_state.value, trigger.value, actor, reason or None,
         evidence_id, at))
    conn.execute(
        "INSERT INTO audit_log (at, actor, action, entity, entity_id, before, after) "
        "VALUES (%s, %s, 'STATE_CHANGE', 'recon_case', %s, %s, %s)",
        (at, actor, str(case_id), Jsonb({"state": from_state.value}) if from_state else None,
         Jsonb({"state": to_state.value, "trigger": trigger.value, "reason": reason})))
