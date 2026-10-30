-- Rollback 0003: xoá thư test đã ghi + mẫu ACCEPTANCE_TEST (audit_log giữ nguyên).
BEGIN;
DELETE FROM email_message WHERE outbox_id IN (SELECT id FROM email_outbox WHERE kind = 'ACCEPTANCE_TEST');
DELETE FROM email_outbox WHERE kind = 'ACCEPTANCE_TEST';
DELETE FROM email_template WHERE code = 'ACCEPTANCE_TEST';
COMMIT;
