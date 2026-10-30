-- Rollback 0005: xoá thư test 3A/3B/4 + mẫu chưa dùng + cột xem thư (thư thật đã gửi và audit_log giữ nguyên).
BEGIN;
DELETE FROM email_outbox WHERE kind IN ('CONFIRM_BBNT_REMINDER_TEST', 'INVOICE_DRAFT_REMINDER_TEST', 'PAYMENT_REMINDER_TEST');
DELETE FROM email_template t WHERE code IN ('CONFIRM_BBNT_REMINDER', 'INVOICE_DRAFT_REMINDER', 'PAYMENT_REMINDER',
  'CONFIRM_BBNT_REMINDER_TEST', 'INVOICE_DRAFT_REMINDER_TEST', 'PAYMENT_REMINDER_TEST')
  AND NOT EXISTS (SELECT 1 FROM email_outbox o WHERE o.template_code = t.code);
ALTER TABLE email_message DROP COLUMN IF EXISTS reviewed_at;
ALTER TABLE email_message DROP COLUMN IF EXISTS reviewed_by;
ALTER TABLE email_message DROP COLUMN IF EXISTS subject;
COMMIT;
