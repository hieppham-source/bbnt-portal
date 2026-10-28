"""Kho trạng thái case trên Postgres thật. Chạy khi có ``RECON_TEST_DATABASE_URL`` (CI: job ``migrations``) —
DB này bị xoá sạch schema public, KHÔNG BAO GIỜ trỏ vào database thật."""
from collections.abc import Iterator
from datetime import UTC, datetime

import psycopg
import pytest

from app.recon import db
from app.recon.state_machine import InvalidTransition, State, Trigger, transition

from .test_recon_migrations import URL, psql, pytestmark, run_file  # noqa: F401  (pytestmark: cùng điều kiện skip)

AT = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)
NEW = db.NewCase(order_sheet_id="sheet1", order_sheet_url="https://docs.google.com/spreadsheets/d/sheet1",
                 brand_codes=["AESTURA", "INNISFREE"], campaign="MIX - 07/2026", group_code="AMORE")


@pytest.fixture()
def conn() -> Iterator[psycopg.Connection]:
    assert psql("DROP SCHEMA public CASCADE; CREATE SCHEMA public;").returncode == 0
    run_file("0001_recon_init.up.sql")
    run_file("0002_recon_idempotency.up.sql")
    with psycopg.connect(URL or "", autocommit=True) as c:
        yield c


def history(conn: psycopg.Connection) -> list[tuple[str | None, str, str]]:
    return [tuple(r) for r in conn.execute(
        "SELECT from_state, to_state, trigger FROM state_transition ORDER BY id").fetchall()]


def test_create_then_move_logs_transition_and_audit(conn: psycopg.Connection) -> None:
    cid = db.create_case(conn, NEW, actor="worker", at=AT)
    assert db.get_case(conn, cid) == db.CaseState(cid, State.WAITING, 0, None)
    case = db.move(conn, cid, State.BBNT_DRAFTED, trigger=Trigger.WORKER, actor="worker", at=AT, reason="G+25")
    assert (case.state, case.version) == (State.BBNT_DRAFTED, 1)
    assert db.get_case(conn, cid) == case
    assert history(conn) == [(None, "WAITING", "WORKER"), ("WAITING", "BBNT_DRAFTED", "WORKER")]
    assert conn.execute("SELECT count(*) FROM audit_log WHERE entity_id = %s", (str(cid),)).fetchone() == (2,)


def test_invalid_transition_writes_nothing(conn: psycopg.Connection) -> None:
    cid = db.create_case(conn, NEW, actor="worker", at=AT)
    with pytest.raises(InvalidTransition):
        db.move(conn, cid, State.ACCEPTANCE_SENT, trigger=Trigger.WORKER, actor="worker", at=AT)
    with pytest.raises(InvalidTransition):     # chỉ kế toán (SHEET) mới kết thúc case
        db.move(conn, cid, State.DONE, trigger=Trigger.WORKER, actor="worker", at=AT)
    assert db.get_case(conn, cid).version == 0
    assert len(history(conn)) == 1


def test_concurrent_update_raises_stale_and_keeps_first_write(conn: psycopg.Connection) -> None:
    cid = db.create_case(conn, NEW, actor="worker", at=AT)
    seen_by_a = db.get_case(conn, cid)
    seen_by_b = db.get_case(conn, cid)
    db.apply_transition(conn, seen_by_a, transition(State.WAITING, State.BBNT_DRAFTED, trigger=Trigger.WORKER,
                                                        actor="a", at=AT))
    with pytest.raises(db.StaleCase):
        db.apply_transition(conn, seen_by_b, transition(State.WAITING, State.NEEDS_REVIEW,
                                                            trigger=Trigger.WORKER, actor="b", at=AT))
    assert db.get_case(conn, cid).state is State.BBNT_DRAFTED
    assert len(history(conn)) == 2                                  # transition của b bị rollback


def test_hold_and_resume_returns_to_previous_state(conn: psycopg.Connection) -> None:
    cid = db.create_case(conn, NEW, actor="worker", at=AT)
    db.move(conn, cid, State.BBNT_DRAFTED, trigger=Trigger.WORKER, actor="worker", at=AT)
    with pytest.raises(InvalidTransition):     # chỉ người dùng mới Giữ
        db.move(conn, cid, State.ON_HOLD, trigger=Trigger.WORKER, actor="worker", at=AT)
    held = db.move(conn, cid, State.ON_HOLD, trigger=Trigger.USER, actor="kam@hannaholala.com", at=AT,
                   reason="brand xin gia hạn")
    assert held.held_from is State.BBNT_DRAFTED
    assert conn.execute("SELECT hold_reason FROM recon_case").fetchone() == ("brand xin gia hạn",)
    back = db.resume_case(conn, cid, actor="kam@hannaholala.com", at=AT)
    assert (back.state, back.held_from) == (State.BBNT_DRAFTED, None)
    assert conn.execute("SELECT held_from, hold_reason FROM recon_case").fetchone() == (None, None)
    with pytest.raises(db.StaleCase):
        db.resume_case(conn, cid, actor="kam@hannaholala.com", at=AT)


def test_one_order_link_is_one_case(conn: psycopg.Connection) -> None:
    db.create_case(conn, NEW, actor="worker", at=AT)
    with pytest.raises(psycopg.errors.UniqueViolation):
        db.create_case(conn, NEW, actor="worker", at=AT)
    assert conn.execute("SELECT count(*) FROM state_transition").fetchone() == (1,)
