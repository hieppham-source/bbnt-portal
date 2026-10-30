-- Bot ghi Status U tới "Đã done BBNT + Chờ KT xuất HĐ nháp" khi KAM đánh dấu brand đã confirm (anh Hiệp 30/10/2026).
-- Chạy trong 1 transaction. Không chứa credential.
BEGIN;
ALTER TABLE recon_case ADD COLUMN IF NOT EXISTS brand_confirmed_by text;
ALTER TABLE recon_case ADD COLUMN IF NOT EXISTS brand_confirmed_at timestamptz;
COMMIT;
