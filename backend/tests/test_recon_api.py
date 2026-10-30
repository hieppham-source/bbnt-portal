"""API /api/v1/recon (dashboard). Postgres thật (``RECON_TEST_DATABASE_URL``), dữ liệu do runner tạo."""
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

from . import test_recon_runner as tr
from . import test_recon_worker as tw
from .test_recon_migrations import URL, pytestmark  # noqa: F401  (pytestmark: cùng điều kiện skip)
from .test_recon_runner import go, one
from .test_recon_worker import MASTER_XLSX, ORG, row

run, conn = tw.run, tr.conn     # dùng lại fixture
BOSS = {"X-User-Email": "hiep.pham@hannaholala.com"}
KAM = {"X-User-Email": "kam@hannaholala.com"}


def client(db_url=URL):
    env = {"MASTER_FILE": str(MASTER_XLSX), "DEV_AUTH_HEADER": "true", "RECON_APPROVERS": "Hiep.Pham@hannaholala.com"}
    s = Settings.from_env({**env, **({"DATABASE_URL": db_url} if db_url else {})})
    return TestClient(create_app(s))


def test_view_cases_and_reviews_and_approver_flag(conn, run):
    go(conn, run, row(*ORG, nmv=1))
    c = client()
    assert c.get("/api/v1/recon/me", headers=BOSS).json() == {"email": "hiep.pham@hannaholala.com",
                                                              "can_approve": True}
    assert c.get("/api/v1/recon/me", headers=KAM).json()["can_approve"] is False
    [case] = c.get("/api/v1/recon/cases", headers=KAM).json()
    assert (case["state"], case["rows"], case["reasons"]) == ("NEEDS_REVIEW", [7], ["FIGURES_DIFFER_FBJT"])
    [rv] = c.get("/api/v1/recon/reviews", headers=KAM).json()
    assert (rv["row"], rv["column"], rv["current_value"]) == (7, "nmv", "1")
    assert c.get("/api/v1/recon/cases").status_code == 401


def test_only_approver_decides_once_and_it_is_audited(conn, run):
    go(conn, run, row(*ORG, nmv=1))
    c = client()
    rid = c.get("/api/v1/recon/reviews", headers=KAM).json()[0]["id"]
    assert c.post(f"/api/v1/recon/reviews/{rid}", json={"decision": "CHANGE"}, headers=KAM).status_code == 403
    assert c.post(f"/api/v1/recon/reviews/{rid}", json={"decision": "MAYBE"}, headers=BOSS).status_code == 422
    assert c.post(f"/api/v1/recon/reviews/{rid}", json={"decision": "CHANGE"}, headers=BOSS).status_code == 204
    assert c.post(f"/api/v1/recon/reviews/{rid}", json={"decision": "KEEP"}, headers=BOSS).status_code == 409
    assert one(conn, "SELECT decision, decided_by FROM cell_review") == ("CHANGE", "hiep.pham@hannaholala.com")
    assert one(conn, "SELECT count(*) FROM audit_log WHERE action = 'CELL_REVIEW'")[0] == 1
    assert c.get("/api/v1/recon/reviews", headers=KAM).json() == []


def test_hold_and_resume_case(conn, run):
    go(conn, run, row(*ORG))
    c = client()
    cid = c.get("/api/v1/recon/cases", headers=BOSS).json()[0]["id"]
    assert c.post(f"/api/v1/recon/cases/{cid}/hold", json={"reason": "Brand xin gia hạn"},
                  headers=KAM).status_code == 403
    assert c.post(f"/api/v1/recon/cases/{cid}/hold", json={"reason": "Brand xin gia hạn"},
                  headers=BOSS).status_code == 204
    assert one(conn, "SELECT state, held_from, hold_reason FROM recon_case") == ("ON_HOLD", "BBNT_DRAFTED",
                                                                                  "Brand xin gia hạn")
    assert c.post(f"/api/v1/recon/cases/{cid}/hold", json={"reason": "lần 2"}, headers=BOSS).status_code == 409
    assert c.post(f"/api/v1/recon/cases/{cid}/resume", headers=BOSS).status_code == 204
    assert one(conn, "SELECT state FROM recon_case") == ("BBNT_DRAFTED",)


def test_no_database_configured_returns_503():
    assert client(db_url=None).get("/api/v1/recon/cases", headers=KAM).status_code == 503


def test_choose_loopmail_only_from_bot_candidates(conn, run):
    from psycopg.types.json import Jsonb
    go(conn, run, row(*ORG))
    conn.execute("UPDATE recon_case SET loopmail_candidates = %s, email_reasons = '{LOOPMAIL_NOT_UNIQUE}'",
                 (Jsonb([{"id": "t1", "subject": "S", "last_at": "1"}, {"id": "t2", "subject": "S", "last_at": "2"}]),))
    c = client()
    [case] = c.get("/api/v1/recon/cases", headers=KAM).json()
    assert case["email_reasons"] == ["LOOPMAIL_NOT_UNIQUE"] and len(case["loopmail_candidates"]) == 2
    url = f"/api/v1/recon/cases/{case['id']}/loopmail"
    assert c.post(url, json={"thread_id": "t1"}, headers=KAM).status_code == 403
    assert c.post(url, json={"thread_id": "t9999"}, headers=BOSS).status_code == 409
    assert c.post(url, json={"thread_id": "t2"}, headers=BOSS).status_code == 204
    assert one(conn, "SELECT gmail_thread_id, loopmail_candidates FROM recon_case") == ("t2", None)
    assert c.post(url, json={"thread_id": "t1"}, headers=BOSS).status_code == 409          # đã chọn rồi
    assert one(conn, "SELECT count(*) FROM audit_log WHERE action = 'LOOPMAIL_CHOSEN'")[0] == 1


def test_pending_email_approve_or_skip_by_approver_only(conn, run):
    import uuid
    go(conn, run, row(*ORG))
    cid = one(conn, "SELECT id FROM recon_case")[0]
    ids = [uuid.uuid4(), uuid.uuid4()]
    for n, i in enumerate(ids, 1):
        conn.execute("INSERT INTO email_outbox (id, case_id, kind, cycle_no, idempotency_key, template_code, "
                     "template_version, to_addrs, subject, body_html, status) VALUES (%s, %s, 'STATUS_REMINDER', %s, "
                     "%s, 'STATUS_REMINDER', 1, '{kt@brand.vn}', 'Re: x', 'nhắc', 'PENDING_REVIEW')",
                     (i, cid, n, f"k{n}"))
    c = client()
    got = c.get("/api/v1/recon/emails", headers=KAM).json()
    assert [(e["kind"], e["cycle_no"], e["to"]) for e in got] == [("STATUS_REMINDER", 1, ["kt@brand.vn"]),
                                                                  ("STATUS_REMINDER", 2, ["kt@brand.vn"])]
    assert c.post(f"/api/v1/recon/emails/{ids[0]}", json={"decision": "SEND"}, headers=KAM).status_code == 403
    assert c.post(f"/api/v1/recon/emails/{ids[0]}", json={"decision": "CHANGE"}, headers=BOSS).status_code == 422
    assert c.post(f"/api/v1/recon/emails/{ids[0]}", json={"decision": "SEND"}, headers=BOSS).status_code == 204
    assert c.post(f"/api/v1/recon/emails/{ids[1]}", json={"decision": "SKIP"}, headers=BOSS).status_code == 204
    assert c.post(f"/api/v1/recon/emails/{ids[1]}", json={"decision": "SEND"}, headers=BOSS).status_code == 409
    assert [r[0] for r in conn.execute("SELECT status FROM email_outbox ORDER BY cycle_no")] == ["QUEUED", "CANCELLED"]
    assert one(conn, "SELECT count(*) FROM audit_log WHERE action = 'EMAIL_REVIEW'")[0] == 2


def test_brand_replies_listed_until_seen(conn, run):
    go(conn, run, row(*ORG))
    cid = one(conn, "SELECT id FROM recon_case")[0]
    for mid, cls in (("m1", "UNKNOWN"), ("m2", "OOO"), ("m3", "BOUNCE")):
        conn.execute("INSERT INTO email_message (gmail_message_id, gmail_thread_id, mailbox, case_id, direction, "
                     "from_addr, sent_at, classification, subject) VALUES (%s, 't1', 'me', %s, 'IN', 'kt@brand.vn', "
                     "now(), %s, 'Re: BBNT')", (mid, cid, cls))
    c = client()
    assert [r["id"] for r in c.get("/api/v1/recon/replies", headers=KAM).json()] == ["m1", "m3"]   # bỏ OOO
    assert c.post("/api/v1/recon/replies/m1/seen", headers=KAM).status_code == 204
    assert c.post("/api/v1/recon/replies/m1/seen", headers=KAM).status_code == 409
    assert [r["id"] for r in c.get("/api/v1/recon/replies", headers=KAM).json()] == ["m3"]


def test_brand_confirmed_only_after_acceptance_sent(conn, run):
    go(conn, run, row(*ORG))
    c = client()
    cid = c.get("/api/v1/recon/cases", headers=KAM).json()[0]["id"]
    url = f"/api/v1/recon/cases/{cid}/brand-confirmed"
    assert c.post(url, headers=BOSS).status_code == 409                         # chưa gửi nghiệm thu
    conn.execute("UPDATE recon_case SET state = 'ACCEPTANCE_SENT'")
    assert c.post(url, headers=KAM).status_code == 403
    assert c.post(url, headers=BOSS).status_code == 204
    assert c.post(url, headers=BOSS).status_code == 409
    assert c.get("/api/v1/recon/cases", headers=KAM).json()[0]["brand_confirmed_at"] is not None
    assert one(conn, "SELECT count(*) FROM audit_log WHERE action = 'BRAND_CONFIRMED'")[0] == 1
