-- Email chế độ test (anh Hiệp, 30/10/2026): thư BBNT chỉ gửi về hộp thư test, ghi email_outbox kind ACCEPTANCE_TEST.
-- Mẫu thư chưa duyệt -> status NOT_CONFIGURED (bản thật cần mẫu APPROVED). Không chứa credential.
BEGIN;
INSERT INTO email_template (code, version) VALUES ('ACCEPTANCE_TEST', 1) ON CONFLICT DO NOTHING;
COMMIT;
