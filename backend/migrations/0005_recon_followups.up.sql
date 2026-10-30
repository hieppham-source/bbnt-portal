-- Theo dõi sau email nghiệm thu (30/10/2026): mẫu 3A / 3B / 4 ("Mẫu email nghiệm thu OM (Bot)"; nội dung trong code
-- app/recon/followup.py) + cột cho thư brand trả lời (chỉ metadata, không lưu nội dung thư). Không chứa credential.
BEGIN;
ALTER TABLE email_message ADD COLUMN IF NOT EXISTS subject text;
ALTER TABLE email_message ADD COLUMN IF NOT EXISTS reviewed_by text;          -- KAM đã xem trên dashboard
ALTER TABLE email_message ADD COLUMN IF NOT EXISTS reviewed_at timestamptz;
INSERT INTO email_template (code, version, status, subject, body_html, approved_by, approved_at) VALUES
  ('CONFIRM_BBNT_REMINDER', 1, 'APPROVED', '(reply loopmail)',
   'EMAIL 3A — docs.google.com/document/d/1875T2F0_2SFpt1A2KtddzhpJtA6yDWma3I-xcoR89L8', 'hiep.pham@hannaholala.com',
   '2026-10-30'),
  ('INVOICE_DRAFT_REMINDER', 1, 'APPROVED', '(reply loopmail)',
   'EMAIL 3B bản nhắc lại — docs.google.com/document/d/1875T2F0_2SFpt1A2KtddzhpJtA6yDWma3I-xcoR89L8',
   'hiep.pham@hannaholala.com', '2026-10-30'),
  ('PAYMENT_REMINDER', 1, 'APPROVED', '(reply loopmail)',
   'EMAIL 4A/4B (bỏ thông tin chuyển khoản, 4B có phạt ước tính) — docs.google.com/document/d/1875T2F0_2SFpt1A2KtddzhpJtA6yDWma3I-xcoR89L8',
   'hiep.pham@hannaholala.com', '2026-10-30')
ON CONFLICT DO NOTHING;
INSERT INTO email_template (code, version) VALUES ('CONFIRM_BBNT_REMINDER_TEST', 1), ('INVOICE_DRAFT_REMINDER_TEST', 1),
  ('PAYMENT_REMINDER_TEST', 1) ON CONFLICT DO NOTHING;
COMMIT;
