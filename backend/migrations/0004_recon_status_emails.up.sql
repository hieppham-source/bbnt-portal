-- Email 1 (nhắc brand điền status link order, tạo loopmail) + bản nhắc lại — mẫu "Mẫu email nghiệm thu OM (Bot)"
-- anh Hiệp gửi 30/10/2026; nội dung trong code (app/recon/emailer.py). Email 1 tự gửi; nhắc lại cần duyệt.
-- Chạy trong 1 transaction. Không chứa credential.
BEGIN;
INSERT INTO email_template (code, version, status, subject, body_html, approved_by, approved_at) VALUES
  ('STATUS_REQUEST', 1, 'APPROVED', '{{GROUP_BRAND}} ({{BRAND}}) x {{PHAP_NHAN}} - FB - {{TEN_CAMP}} {{THANG_NGHIEM_THU}}',
   'EMAIL 1 — Nhắc điền status vào link order: docs.google.com/document/d/1875T2F0_2SFpt1A2KtddzhpJtA6yDWma3I-xcoR89L8',
   'hiep.pham@hannaholala.com', '2026-10-30'),
  ('STATUS_REMINDER', 1, 'APPROVED', '(reply loopmail)',
   'EMAIL 1 — Bản nhắc lại: docs.google.com/document/d/1875T2F0_2SFpt1A2KtddzhpJtA6yDWma3I-xcoR89L8',
   'hiep.pham@hannaholala.com', '2026-10-30')
ON CONFLICT DO NOTHING;
INSERT INTO email_template (code, version) VALUES ('STATUS_REQUEST_TEST', 1), ('STATUS_REMINDER_TEST', 1)
ON CONFLICT DO NOTHING;
COMMIT;
