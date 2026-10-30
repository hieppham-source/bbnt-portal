# Operations runbook

## Dừng khẩn cấp
1. Đặt Variable `RECON_EMAIL_MODE=off` (ngừng mọi email) và `RECON_SHEET_WRITE=off` (ngừng ghi FB.JT).
2. Tắt workflow lịch chạy worker trên GitHub (Actions → workflow → Disable).

## Hoàn tác ghi FB.JT
Mỗi lần bot ghi I/K/M có dòng `audit_log` (`before`/`after`, số dòng, cột). Ghi lại giá trị `before` vào đúng ô.

## Migration
- Trước khi chạy: backup (`pg_dump "$DATABASE_URL" > backup.sql`, Neon cũng có point-in-time restore).
- Up lỗi giữa chừng: file chạy trong 1 transaction → không để lại bảng dở (có test).
- Rollback 0004 (chạy trước 0003): `psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f backend/migrations/0004_recon_status_emails.down.sql`.
- Rollback 0003 (chạy trước 0002): `psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f backend/migrations/0003_recon_email.down.sql` — xoá thư test trong `email_outbox`, giữ `audit_log`.
- Rollback 0002 (chạy trước 0001): `psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f backend/migrations/0002_recon_idempotency.down.sql` — mất nhật ký `sheet_write`, giữ case.
- Rollback 0001: `psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f backend/migrations/0001_recon_init.down.sql` — **xoá toàn bộ dữ liệu workflow**, chỉ làm sau khi backup.

## Token Google bị thu hồi
Đổi mật khẩu hộp thư gửi làm token mất hiệu lực → worker báo lỗi quyền, không gửi gì. Lấy lại refresh token và cập nhật GitHub Secret.

## Rollback code
- Backend (Render): revert PR trên `main` → CI xanh → Render tự deploy; hoặc Render → Deploys → Rollback.
- Frontend (Cloudflare): trong worktree deploy `git checkout --detach <commit trước>` rồi `.\deploy.cmd`; hoặc `npx wrangler rollback`.
- Worker: chưa chạy production. Khi có: tắt workflow lịch trước, rồi mới revert code/migration.

## Hoàn tác ô I/K/M bot đã ghi
`SELECT entity_id, before, after, at FROM audit_log WHERE action = 'SHEET_WRITE' ORDER BY id DESC;` — `entity_id` là
ô (vd `I1548`), `before.value` là giá trị trước khi ghi. Gõ lại giá trị đó vào FB.JT. Ô ở trạng thái `CONFLICT` /
`FAILED` trong `sheet_write` là ô bot **không** ghi được (hoặc ghi rồi đọc lại không khớp) — kiểm tra tay.

## Duyệt ô khác số BBNT (chưa có dashboard)
`UPDATE cell_review SET decision = 'CHANGE' | 'KEEP', decided_by = '<email>', decided_at = now() WHERE id = ...;`
CHANGE → lần chạy `recon-run-once` sau sẽ ghi (vẫn đọc lại ô trước khi ghi).

## Email nghiệm thu
- Variables: `RECON_EMAIL_MODE` = `off` (mặc định) | `test` | `live`; `RECON_EMAIL_TEST_TO` (test, 1 địa chỉ @hannaholala.com); `RECON_EMAIL_SIGNER` (tên ký cuối thư `{{NGUOI_GUI}}`, bắt buộc với live); `RECON_EMAIL_MAX` (mặc định 5, tối đa 50 thư/lần chạy).
- Nội dung: **EMAIL 2** trong Google Doc "Mẫu email nghiệm thu OM (Bot)" (bảng confirm số theo pháp nhân, deadline = ngày gửi + 3 ngày làm việc, đính kèm `BBNT_<BRAND>_<CAMP>_<MM-YY>.docx`). Mẫu nằm trong code `app/recon/emailer.py`; sửa mẫu = sửa code qua PR (bảng `email_template` ghi nguồn + người duyệt).
- **test**: mỗi case BBNT_DRAFTED / FIGURES_WRITTEN nhận đúng 1 thư `[TEST] ...` kèm BBNT .docx, chỉ tới `RECON_EMAIL_TEST_TO`. Đầu thư ghi kết quả Audit Gate, loopmail tìm được, To/CC thật. Không đổi trạng thái case.
- **live**: chỉ khi Audit Gate đạt — case FIGURES_WRITTEN, có EMAIL BRAND, TITLE MAIL tìm được **đúng 1** loopmail (tiêu đề bỏ Re:/Fw: = TITLE MAIL và thread có email brand), có `RECON_EMAIL_SIGNER`, T2–T6 08:00–18:00 giờ VN. Reply vào loopmail (In-Reply-To/References), To brand, CC nội bộ. Case → ACCEPTANCE_SENT; bot không gửi lại.
- Lý do chưa gửi hiện ở dashboard ("Email chưa gửi: ..."). Nhiều thread khớp → dashboard hiện nút "Chọn loopmail".
- **Email 1** (G + 25, file order còn dòng chưa có status → case `STATUS_INCOMPLETE`): tự gửi 1 lần, mở loopmail mới nếu chưa có. Quá hạn → thư nhắc hiện ở dashboard mục **Thư chờ duyệt** (Gửi / Bỏ); Gửi → lần chạy sau gửi vào loopmail. Brand điền đủ → bot lập BBNT + Email 2, thư nhắc chưa gửi tự huỷ.
- Gửi lại 1 case (thư FAILED hoặc muốn thử lại thư test): `DELETE FROM email_outbox WHERE kind IN ('ACCEPTANCE_TEST', 'ACCEPTANCE', 'STATUS_REQUEST', 'STATUS_REQUEST_TEST') AND status NOT IN ('SENT', 'PENDING_REVIEW', 'QUEUED') AND case_id = '<id>';` rồi chạy lại.
- Xem: `SELECT o.kind, o.subject, o.status, o.last_error, c.bbnt_number FROM email_outbox o JOIN recon_case c ON c.id = o.case_id ORDER BY o.created_at DESC;`

## Lịch chạy
Workflow `recon-run-once` chạy 30 phút/lần 08:00–19:30 T2–T6 giờ VN **chỉ khi** Variable `RECON_SCHEDULE=on`. Dừng: đặt `off` (hoặc Disable workflow).
