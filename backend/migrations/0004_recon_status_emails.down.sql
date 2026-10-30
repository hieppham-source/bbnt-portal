-- Rollback 0004: xoá thư test Email 1 / nhắc + các mẫu chưa được dùng (thư thật đã gửi giữ nguyên, audit_log giữ nguyên).
BEGIN;
DELETE FROM email_outbox WHERE kind IN ('STATUS_REQUEST_TEST', 'STATUS_REMINDER_TEST');
DELETE FROM email_template t WHERE code IN ('STATUS_REQUEST', 'STATUS_REMINDER', 'STATUS_REQUEST_TEST',
  'STATUS_REMINDER_TEST') AND NOT EXISTS (SELECT 1 FROM email_outbox o WHERE o.template_code = t.code);
COMMIT;
