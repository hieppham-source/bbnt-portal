-- Rollback 0006: bỏ đánh dấu brand confirm (U đã ghi trên FB.JT giữ nguyên; audit_log giữ nguyên).
BEGIN;
ALTER TABLE recon_case DROP COLUMN IF EXISTS brand_confirmed_at;
ALTER TABLE recon_case DROP COLUMN IF EXISTS brand_confirmed_by;
COMMIT;
