"""
API trang /reconcile (dashboard đối soát). Đọc / ghi Postgres của worker (``DATABASE_URL``).

* Cả domain (người đăng nhập portal) được **xem**.
* Chỉ email trong ``RECON_APPROVERS`` được **duyệt** ô I/K/M (Đổi / Giữ), **Giữ / Tiếp tục** case,
  **chọn loopmail** khi bot tìm thấy nhiều thread và **duyệt thư nhắc** (Gửi / Bỏ).
  Quyền kiểm ở backend, không chỉ ẩn nút.
* Duyệt "Đổi" chỉ ghi quyết định; lần chạy worker sau mới ghi FB.JT (vẫn đọc lại ô trước khi ghi).
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from app.api.deps import User, get_user
from app.config import Settings, get_settings
from app.recon import db
from app.recon.state_machine import InvalidTransition, State, Trigger

router = APIRouter(prefix="/api/v1/recon", tags=["Reconcile"])


def get_conn(settings: Annotated[Settings, Depends(get_settings)]) -> Iterator[psycopg.Connection]:
    if not settings.database_url:
        raise HTTPException(503, detail={"code": "RECON_DB_NOT_CONFIGURED", "message": "Chưa cấu hình DATABASE_URL"})
    with psycopg.connect(settings.database_url, autocommit=True, connect_timeout=15) as conn:
        yield conn


Conn = Annotated[psycopg.Connection, Depends(get_conn)]
CurrentUser = Annotated[User, Depends(get_user)]


def _approver(user: User, settings: Settings) -> str:
    if user.email.lower() not in settings.recon_approvers:
        raise HTTPException(403, detail={"code": "NOT_APPROVER", "message": "Bạn chưa có quyền duyệt"})
    return user.email.lower()


class MeOut(BaseModel):
    email: str
    can_approve: bool


class CaseOut(BaseModel):
    id: uuid.UUID
    state: str
    brands: list[str]
    campaign: str
    rows: list[int]
    bbnt_number: str | None
    reasons: list[str]
    pic: str | None
    hold_reason: str | None
    updated_at: datetime
    email_reasons: list[str] = []
    loopmail_candidates: list[dict[str, str]] | None = None
    gmail_thread_id: str | None = None


class ReviewOut(BaseModel):
    id: int
    case_id: uuid.UUID
    brands: list[str]
    campaign: str
    row: int | None
    column: str
    current_value: Decimal | None
    bbnt_value: Decimal
    created_at: datetime


class PendingEmailOut(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID
    brands: list[str]
    campaign: str
    kind: str
    cycle_no: int
    to: list[str]
    cc: list[str]
    subject: str
    body: str
    created_at: datetime


class DecisionIn(BaseModel):
    decision: Literal["CHANGE", "KEEP"]


class EmailDecisionIn(BaseModel):
    decision: Literal["SEND", "SKIP"]


class LoopmailIn(BaseModel):
    thread_id: str = Field(min_length=1, max_length=64)


class HoldIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


@router.get("/me", response_model=MeOut)
def me(user: CurrentUser, settings: Annotated[Settings, Depends(get_settings)]) -> MeOut:
    return MeOut(email=user.email, can_approve=user.email.lower() in settings.recon_approvers)


@router.get("/cases", response_model=list[CaseOut])
def cases(user: CurrentUser, conn: Conn) -> list[CaseOut]:
    rows = conn.execute(
        "SELECT c.id, c.state, c.brand_codes, c.campaign, "
        "COALESCE(array_agg(r.last_row_number ORDER BY r.last_row_number) FILTER (WHERE r.id IS NOT NULL), '{}'), "
        "c.bbnt_number, c.review_reasons, c.pic, c.hold_reason, c.updated_at, c.email_reasons, "
        "c.loopmail_candidates, c.gmail_thread_id FROM recon_case c LEFT JOIN sheet_row r ON r.case_id = c.id "
        "GROUP BY c.id ORDER BY c.updated_at DESC LIMIT 1000").fetchall()
    return [CaseOut(id=r[0], state=r[1], brands=r[2], campaign=r[3], rows=r[4], bbnt_number=r[5], reasons=r[6],
                    pic=r[7], hold_reason=r[8], updated_at=r[9], email_reasons=r[10], loopmail_candidates=r[11],
                    gmail_thread_id=r[12]) for r in rows]


@router.get("/reviews", response_model=list[ReviewOut])
def reviews(user: CurrentUser, conn: Conn) -> list[ReviewOut]:
    rows = conn.execute(
        "SELECT cr.id, cr.case_id, c.brand_codes, c.campaign, r.last_row_number, cr.column_name, cr.current_value, "
        "cr.bbnt_value, cr.created_at FROM cell_review cr JOIN recon_case c ON c.id = cr.case_id "
        "JOIN sheet_row r ON r.id = cr.sheet_row_id WHERE cr.decision IS NULL ORDER BY cr.created_at").fetchall()
    return [ReviewOut(id=r[0], case_id=r[1], brands=r[2], campaign=r[3], row=r[4], column=r[5], current_value=r[6],
                      bbnt_value=r[7], created_at=r[8]) for r in rows]


@router.post("/reviews/{review_id}", status_code=204)
def decide(review_id: int, body: DecisionIn, user: CurrentUser, conn: Conn,
           settings: Annotated[Settings, Depends(get_settings)]) -> None:
    who = _approver(user, settings)
    now = datetime.now(UTC)
    with conn.transaction():
        row = conn.execute("UPDATE cell_review SET decision = %s, decided_by = %s, decided_at = %s "
                           "WHERE id = %s AND decision IS NULL RETURNING case_id, column_name, current_value, "
                           "bbnt_value", (body.decision, who, now, review_id)).fetchone()
        if row is None:
            raise HTTPException(409, detail={"code": "REVIEW_ALREADY_DECIDED", "message": "Ô này đã được duyệt"})
        conn.execute("INSERT INTO audit_log (at, actor, action, entity, entity_id, before, after) "
                     "VALUES (%s, %s, 'CELL_REVIEW', 'cell_review', %s, %s, %s)",
                     (now, who, str(review_id), Jsonb({"value": str(row[2]) if row[2] is not None else None}),
                      Jsonb({"decision": body.decision, "bbnt_value": str(row[3]), "column": row[1],
                             "case_id": str(row[0])})))


@router.get("/emails", response_model=list[PendingEmailOut])
def pending_emails(user: CurrentUser, conn: Conn) -> list[PendingEmailOut]:
    """Thư bot soạn sẵn chờ duyệt (nhắc brand điền status…). Duyệt -> lần chạy bot sau gửi (trong giờ cho phép)."""
    rows = conn.execute(
        "SELECT o.id, o.case_id, c.brand_codes, c.campaign, o.kind, o.cycle_no, o.to_addrs, o.cc_addrs, o.subject, "
        "o.body_html, o.created_at FROM email_outbox o JOIN recon_case c ON c.id = o.case_id "
        "WHERE o.status = 'PENDING_REVIEW' ORDER BY o.created_at").fetchall()
    return [PendingEmailOut(id=r[0], case_id=r[1], brands=r[2], campaign=r[3], kind=r[4], cycle_no=r[5], to=r[6],
                            cc=r[7], subject=r[8], body=r[9], created_at=r[10]) for r in rows]


@router.post("/emails/{email_id}", status_code=204)
def decide_email(email_id: uuid.UUID, body: EmailDecisionIn, user: CurrentUser, conn: Conn,
                 settings: Annotated[Settings, Depends(get_settings)]) -> None:
    """``SEND`` -> QUEUED (bot gửi lần chạy sau); ``SKIP`` -> CANCELLED."""
    who = _approver(user, settings)
    with conn.transaction():
        row = conn.execute("UPDATE email_outbox SET status = %s, approved_by = %s, approved_at = now() "
                           "WHERE id = %s AND status = 'PENDING_REVIEW' RETURNING case_id, kind",
                           ("QUEUED" if body.decision == "SEND" else "CANCELLED", who, email_id)).fetchone()
        if row is None:
            raise HTTPException(409, detail={"code": "EMAIL_ALREADY_DECIDED", "message": "Thư này đã được xử lý"})
        conn.execute("INSERT INTO audit_log (at, actor, action, entity, entity_id, after) "
                     "VALUES (now(), %s, 'EMAIL_REVIEW', 'email_outbox', %s, %s)",
                     (who, str(email_id), Jsonb({"decision": body.decision, "kind": row[1],
                                                 "case_id": str(row[0])})))


@router.post("/cases/{case_id}/loopmail", status_code=204)
def choose_loopmail(case_id: uuid.UUID, body: LoopmailIn, user: CurrentUser, conn: Conn,
                    settings: Annotated[Settings, Depends(get_settings)]) -> None:
    """KAM chọn 1 trong các thread bot tìm thấy (``loopmail_candidates``) — không nhận thread ngoài danh sách."""
    who = _approver(user, settings)
    with conn.transaction():
        row = conn.execute("UPDATE recon_case SET gmail_thread_id = %s, loopmail_candidates = NULL, updated_at = now() "
                           "WHERE id = %s AND gmail_thread_id IS NULL AND loopmail_candidates @> %s RETURNING id",
                           (body.thread_id, case_id, Jsonb([{"id": body.thread_id}]))).fetchone()
        if row is None:
            raise HTTPException(409, detail={"code": "LOOPMAIL_NOT_CANDIDATE",
                                             "message": "Thread không nằm trong danh sách bot tìm thấy"})
        conn.execute("INSERT INTO audit_log (at, actor, action, entity, entity_id, after) "
                     "VALUES (now(), %s, 'LOOPMAIL_CHOSEN', 'recon_case', %s, %s)",
                     (who, str(case_id), Jsonb({"thread_id": body.thread_id})))


@router.post("/cases/{case_id}/hold", status_code=204)
def hold(case_id: uuid.UUID, body: HoldIn, user: CurrentUser, conn: Conn,
         settings: Annotated[Settings, Depends(get_settings)]) -> None:
    who = _approver(user, settings)
    try:
        db.move(conn, case_id, State.ON_HOLD, trigger=Trigger.USER, actor=who, at=datetime.now(UTC),
                reason=body.reason.strip())
    except (db.StaleCase, InvalidTransition) as exc:
        raise HTTPException(409, detail={"code": "CASE_STATE_CONFLICT", "message": str(exc)}) from exc


@router.post("/cases/{case_id}/resume", status_code=204)
def resume(case_id: uuid.UUID, user: CurrentUser, conn: Conn,
           settings: Annotated[Settings, Depends(get_settings)]) -> None:
    who = _approver(user, settings)
    try:
        db.resume_case(conn, case_id, actor=who, at=datetime.now(UTC))
    except (db.StaleCase, InvalidTransition) as exc:
        raise HTTPException(409, detail={"code": "CASE_STATE_CONFLICT", "message": str(exc)}) from exc
