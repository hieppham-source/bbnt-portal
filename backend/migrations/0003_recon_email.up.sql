-- Email nghiệm thu (30/10/2026): kết quả Audit Gate + loopmail ứng viên trên case; mẫu ACCEPTANCE_TEST cho chế độ test.
-- Mẫu ACCEPTANCE thật do người duyệt thêm (status APPROVED). Chạy trong 1 transaction. Không chứa credential.
BEGIN;
ALTER TABLE recon_case ADD COLUMN IF NOT EXISTS email_reasons text[] NOT NULL DEFAULT '{}';  -- Audit Gate chưa đạt
ALTER TABLE recon_case ADD COLUMN IF NOT EXISTS loopmail_candidates jsonb;      -- >1 thread khớp -> KAM chọn
INSERT INTO email_template (code, version) VALUES ('ACCEPTANCE_TEST', 1) ON CONFLICT DO NOTHING;
COMMIT;
