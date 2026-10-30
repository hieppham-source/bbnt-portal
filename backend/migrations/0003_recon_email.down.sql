-- Rollback 0003: xoá thư test + mẫu ACCEPTANCE_TEST + 2 cột email trên recon_case (audit_log giữ nguyên).
-- Thư ACCEPTANCE thật (nếu đã gửi) KHÔNG bị xoá.
BEGIN;
DELETE FROM email_message WHERE outbox_id IN (SELECT id FROM email_outbox WHERE kind = 'ACCEPTANCE_TEST');
DELETE FROM email_outbox WHERE kind = 'ACCEPTANCE_TEST';
DELETE FROM email_template WHERE code = 'ACCEPTANCE_TEST';
DELETE FROM email_template t WHERE code = 'ACCEPTANCE' AND version = 1
  AND NOT EXISTS (SELECT 1 FROM email_outbox o WHERE o.template_code = t.code AND o.template_version = t.version);
ALTER TABLE recon_case DROP COLUMN IF EXISTS loopmail_candidates;
ALTER TABLE recon_case DROP COLUMN IF EXISTS email_reasons;
COMMIT;
