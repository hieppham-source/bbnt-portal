"""Migration test trên Postgres thật. Chạy khi có ``RECON_TEST_DATABASE_URL`` (CI: service postgres) — DB này
bị xoá sạch schema public, KHÔNG BAO GIỜ trỏ vào database thật."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

URL = os.environ.get("RECON_TEST_DATABASE_URL")
MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"
pytestmark = pytest.mark.skipif(not URL or not shutil.which("psql"), reason="RECON_TEST_DATABASE_URL/psql chưa có")


def psql(sql: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["psql", URL or "", "-v", "ON_ERROR_STOP=1", "-qAt", "-c", sql],
                          capture_output=True, text=True, check=False)


def run_file(name: str) -> None:
    r = subprocess.run(["psql", URL or "", "-v", "ON_ERROR_STOP=1", "-q", "-f", str(MIGRATIONS / name)],
                       capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr


@pytest.fixture()
def fresh_db() -> None:
    assert psql("DROP SCHEMA public CASCADE; CREATE SCHEMA public;").returncode == 0
    run_file("0001_recon_init.up.sql")
    run_file("0002_recon_idempotency.up.sql")


CASE = ("INSERT INTO recon_case (id, order_sheet_id, order_sheet_url, brand_codes, campaign, state) VALUES "
        "('00000000-0000-0000-0000-000000000001', 'sheet1', 'https://x', '{AESTURA}', 'MIX - 07/2026', 'WAITING');")
TEMPLATE = "INSERT INTO email_template (code, version) VALUES ('ACCEPTANCE', 1);"
OUTBOX = ("INSERT INTO email_outbox (id, case_id, kind, cycle_no, idempotency_key, template_code, template_version, "
          "to_addrs, subject, body_html, status) VALUES (gen_random_uuid(), "
          "'00000000-0000-0000-0000-000000000001', 'ACCEPTANCE', 1, 'k1', 'ACCEPTANCE', 1, '{a@b.c}', 's', 'b', "
          "'PENDING_REVIEW');")


def test_outbox_idempotency_key_blocks_duplicate_send(fresh_db: None) -> None:
    assert psql(CASE + TEMPLATE + OUTBOX).returncode == 0
    dup = psql(OUTBOX)
    assert dup.returncode != 0 and "duplicate key" in dup.stderr


def test_audit_log_is_append_only(fresh_db: None) -> None:
    assert psql("INSERT INTO audit_log (actor, action) VALUES ('worker', 'x');").returncode == 0
    for sql in ("UPDATE audit_log SET action = 'y';", "DELETE FROM audit_log;"):
        r = psql(sql)
        assert r.returncode != 0 and "append-only" in r.stderr


def test_constraints_on_templates_holidays_and_bbnt_number(fresh_db: None) -> None:
    assert psql("INSERT INTO email_template (code, version, status) VALUES ('X', 1, 'APPROVED');").returncode != 0
    assert psql("INSERT INTO holiday (day, name, source_url, status, proposed_by) "
                "VALUES ('2027-02-06', 'Tết', 'https://chinhphu.vn', 'APPROVED', 'ai');").returncode != 0
    r = psql(CASE.replace("'WAITING');", "'WAITING');") + "UPDATE recon_case SET bbnt_number = '07.26/BBNT-X';")
    assert r.returncode != 0                                    # số BBNT phải đi kèm issued_at


def test_down_then_up_again(fresh_db: None) -> None:
    run_file("0001_recon_init.down.sql")
    assert psql("SELECT to_regclass('public.recon_case');").stdout.strip() == ""
    run_file("0001_recon_init.up.sql")
    assert psql("SELECT count(*) FROM recon_case;").stdout.strip() == "0"


def test_failed_up_leaves_nothing_behind() -> None:
    assert psql("DROP SCHEMA public CASCADE; CREATE SCHEMA public; CREATE TABLE holiday (x int);").returncode == 0
    r = subprocess.run(["psql", URL or "", "-v", "ON_ERROR_STOP=1", "-q", "-f",
                        str(MIGRATIONS / "0001_recon_init.up.sql")], capture_output=True, text=True, check=False)
    assert r.returncode != 0                                    # bảng holiday đã tồn tại -> lỗi giữa chừng
    assert psql("SELECT to_regclass('public.recon_case');").stdout.strip() == ""   # transaction đã rollback


SHEET_ROW = ("INSERT INTO sheet_row (id, tab, brand_key, campaign_key, case_id) VALUES "
             "(1, 'FB-2026', 'aestura', 'mix - 07/2026', '00000000-0000-0000-0000-000000000001');")
WRITE = ("INSERT INTO sheet_write (case_id, sheet_row_id, column_name, target_value, status) VALUES "
         "('00000000-0000-0000-0000-000000000001', 1, 'nmv', 1000, 'PLANNED');")


def test_0002_sheet_write_is_idempotent_per_target(fresh_db: None) -> None:
    assert psql(CASE + SHEET_ROW + WRITE).returncode == 0
    dup = psql(WRITE)
    assert dup.returncode != 0 and "duplicate key" in dup.stderr
    assert psql("SELECT review_reasons FROM recon_case;").stdout.strip() == "{}"


def test_0002_down_then_up(fresh_db: None) -> None:
    run_file("0002_recon_idempotency.down.sql")
    assert psql("SELECT to_regclass('sheet_write');").stdout.strip() == ""
    assert psql("SELECT 1 FROM recon_case;").returncode == 0      # 0001 còn nguyên
    run_file("0002_recon_idempotency.up.sql")
    assert psql("SELECT to_regclass('sheet_write');").stdout.strip() == "sheet_write"


def test_0003_seeds_test_template_and_down_up_is_clean(fresh_db: None) -> None:
    run_file("0003_recon_email_test.up.sql")
    run_file("0003_recon_email_test.up.sql")                    # chạy lại không lỗi
    assert psql("SELECT status FROM email_template WHERE code = 'ACCEPTANCE_TEST';").stdout.strip() \
        == "NOT_CONFIGURED"
    run_file("0003_recon_email_test.down.sql")
    assert psql("SELECT count(*) FROM email_template;").stdout.strip() == "0"
