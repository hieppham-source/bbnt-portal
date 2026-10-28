-- Rollback 0002: xoá sheet_write + 3 cột mới của recon_case (mất nhật ký ghi Sheet — backup trước).
BEGIN;
DROP TABLE IF EXISTS sheet_write;
ALTER TABLE recon_case DROP COLUMN IF EXISTS last_run_id;
ALTER TABLE recon_case DROP COLUMN IF EXISTS last_plan_hash;
ALTER TABLE recon_case DROP COLUMN IF EXISTS review_reasons;
COMMIT;
