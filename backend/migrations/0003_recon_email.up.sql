-- Email nghiệm thu (30/10/2026): kết quả Audit Gate + loopmail ứng viên trên case; mẫu ACCEPTANCE_TEST cho chế độ test.
-- Mẫu ACCEPTANCE = EMAIL 2 trong Google Doc "Mẫu email nghiệm thu OM (Bot)" anh Hiệp gửi 30/10/2026; nội dung nằm
-- trong code (app/recon/emailer.py), bảng này ghi nguồn + người duyệt. Chạy trong 1 transaction. Không chứa credential.
BEGIN;
ALTER TABLE recon_case ADD COLUMN IF NOT EXISTS email_reasons text[] NOT NULL DEFAULT '{}';  -- Audit Gate chưa đạt
ALTER TABLE recon_case ADD COLUMN IF NOT EXISTS loopmail_candidates jsonb;      -- >1 thread khớp -> KAM chọn
INSERT INTO email_template (code, version) VALUES ('ACCEPTANCE_TEST', 1) ON CONFLICT DO NOTHING;
INSERT INTO email_template (code, version, status, subject, body_html, approved_by, approved_at) VALUES
  ('ACCEPTANCE', 1, 'APPROVED', '{{GROUP_BRAND}} ({{BRAND}}) x {{PHAP_NHAN}} - FB - {{TEN_CAMP}} {{THANG_NGHIEM_THU}}',
   'EMAIL 2 — Confirm số + gửi biên bản nghiệm thu: docs.google.com/document/d/1875T2F0_2SFpt1A2KtddzhpJtA6yDWma3I-xcoR89L8',
   'hiep.pham@hannaholala.com', '2026-10-30') ON CONFLICT DO NOTHING;
COMMIT;
