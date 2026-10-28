-- Rollback 0001. XOÁ toàn bộ dữ liệu workflow — chỉ chạy tay sau khi đã backup (docs/OPERATIONS_RUNBOOK.md).
BEGIN;
DROP TABLE IF EXISTS audit_log, gmail_sync_state, workflow_run, holiday, payment_evidence, email_message,
  email_outbox, email_template, state_transition, cell_review, sheet_change_event, sheet_row, recon_case CASCADE;
DROP FUNCTION IF EXISTS audit_log_append_only();
COMMIT;
