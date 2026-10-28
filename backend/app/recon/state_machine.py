"""
State machine của 1 case (1 link order = 1 BBNT = 1 loopmail). Chuyển trạng thái tường minh: mỗi lần chuyển
phải hợp lệ theo :data:`TRANSITIONS`, có trigger + actor + bằng chứng; caller ghi :class:`Transition` vào DB
(bảng ``state_transition``) trong cùng transaction với cập nhật case (optimistic lock ``state_version``).

Tiến độ nghiệp vụ sau khi gửi nghiệm thu đi theo cột Status (U) do người cập nhật (:func:`stage_from_status`);
bot không bao giờ ghi U và không bao giờ tự chuyển sang DONE (chỉ kế toán đổi U = Done).
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime

from app.core.calculation import normalize_text


class State(str, enum.Enum):
    WAITING = "WAITING"                         # đã thấy ngày kết thúc camp, chưa tới G + 25
    BBNT_DRAFTED = "BBNT_DRAFTED"               # đã tạo BBNT (số + issued_at đã lưu)
    NEEDS_REVIEW = "NEEDS_REVIEW"               # Audit Gate không đạt / số liệu khác / thread mơ hồ
    FIGURES_WRITTEN = "FIGURES_WRITTEN"         # I/K/M trên FB.JT đã bằng số BBNT
    ACCEPTANCE_SENT = "ACCEPTANCE_SENT"         # đã reply loopmail email nghiệm thu
    AWAITING_INVOICE_CONFIRM = "AWAITING_INVOICE_CONFIRM"
    AWAITING_PAYMENT = "AWAITING_PAYMENT"
    ON_HOLD = "ON_HOLD"
    ERROR = "ERROR"
    DONE = "DONE"                               # chỉ khi kế toán đổi U = Done


_ANY_ACTIVE = frozenset(State) - {State.DONE}

TRANSITIONS: dict[State, frozenset[State]] = {
    State.WAITING: frozenset({State.BBNT_DRAFTED, State.NEEDS_REVIEW}),
    State.BBNT_DRAFTED: frozenset({State.FIGURES_WRITTEN, State.NEEDS_REVIEW}),
    State.NEEDS_REVIEW: frozenset({State.WAITING, State.BBNT_DRAFTED, State.FIGURES_WRITTEN,
                                   State.ACCEPTANCE_SENT}),
    State.FIGURES_WRITTEN: frozenset({State.ACCEPTANCE_SENT, State.NEEDS_REVIEW}),
    State.ACCEPTANCE_SENT: frozenset({State.AWAITING_INVOICE_CONFIRM, State.AWAITING_PAYMENT, State.NEEDS_REVIEW}),
    State.AWAITING_INVOICE_CONFIRM: frozenset({State.AWAITING_PAYMENT, State.NEEDS_REVIEW}),
    State.AWAITING_PAYMENT: frozenset({State.NEEDS_REVIEW}),
    State.ON_HOLD: frozenset(),      # resume -> trạng thái trước khi giữ (xem resume())
    State.ERROR: frozenset({State.WAITING, State.NEEDS_REVIEW}),
    State.DONE: frozenset(),
}
#: Trạng thái nào cũng có thể bị Giữ / lỗi / kết thúc do kế toán đổi U = Done.
UNIVERSAL = {State.ON_HOLD, State.ERROR, State.DONE}


class Trigger(str, enum.Enum):
    SHEET = "SHEET"      # thay đổi trên FB.JT
    EMAIL = "EMAIL"      # thư đến trong loopmail
    USER = "USER"        # thao tác trên portal
    WORKER = "WORKER"    # bot tự quyết theo quy tắc


class InvalidTransition(ValueError):
    pass


@dataclass(frozen=True)
class Transition:
    from_state: State
    to_state: State
    trigger: Trigger
    actor: str
    at: datetime
    reason: str = ""
    evidence_id: str | None = None


def transition(current: State, to: State, *, trigger: Trigger, actor: str, at: datetime, reason: str = "",
               evidence_id: str | None = None) -> Transition:
    if current is State.DONE:
        raise InvalidTransition("Case đã DONE")
    if to is State.DONE and trigger is not Trigger.SHEET:
        raise InvalidTransition("Chỉ kế toán (đổi Status U trên FB.JT) mới kết thúc case")
    if to not in TRANSITIONS[current] and to not in UNIVERSAL:
        raise InvalidTransition(f"{current.value} -> {to.value} không hợp lệ")
    if to is State.ON_HOLD and trigger is not Trigger.USER:
        raise InvalidTransition("Chỉ người dùng mới Giữ case")
    if not actor:
        raise InvalidTransition("Thiếu actor")
    return Transition(current, to, trigger, actor, at, reason, evidence_id)


def resume(held_from: State, *, actor: str, at: datetime) -> Transition:
    """Tiếp tục case đang Giữ về đúng trạng thái trước đó (lưu ở case khi Giữ)."""
    if held_from in (State.ON_HOLD, State.DONE):
        raise InvalidTransition("Trạng thái trước khi Giữ không hợp lệ")
    return Transition(State.ON_HOLD, held_from, Trigger.USER, actor, at, "resume")


class Stage(str, enum.Enum):
    """Giai đoạn nghiệp vụ đọc từ Status (U)."""

    PRE_INVOICE = "PRE_INVOICE"                 # chưa có hoá đơn (data / BBNT / chờ KT)
    INVOICE_DRAFT = "INVOICE_DRAFT"             # chờ brand confirm hoá đơn nháp
    INVOICE_ISSUED = "INVOICE_ISSUED"           # đã xuất HĐ chính thức, chờ tiền
    DONE = "DONE"


_STAGES: dict[str, Stage] = {
    normalize_text(k): v for k, v in {
        "Brand chưa gửi data": Stage.PRE_INVOICE,
        "Đã Confirm số + Chờ brand gửi BBNT": Stage.PRE_INVOICE,
        "Đã có BBNT + Chờ Admin Check": Stage.PRE_INVOICE,
        "Đã done BBNT + Chờ KT xuất HĐ nháp": Stage.PRE_INVOICE,
        "Đã done BBNT + chờ brand gửi bản cứng BBNT": Stage.PRE_INVOICE,
        "Đã xuất hóa đơn nháp + Chờ Brand confirm": Stage.INVOICE_DRAFT,
        "Đã xuất hóa đơn + Chờ tiền": Stage.INVOICE_ISSUED,
        "Done trả tiền": Stage.DONE,
        "Done": Stage.DONE,
    }.items()
}


def stage_from_status(status_u: str) -> Stage | None:
    """Status trống coi như PRE_INVOICE; giá trị lạ -> None (case chuyển người, không đoán)."""
    s = normalize_text(status_u)
    return Stage.PRE_INVOICE if not s else _STAGES.get(s)
