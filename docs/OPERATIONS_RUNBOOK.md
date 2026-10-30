# Operations runbook

## Dừng khẩn cấp
1. Đặt Variable `RECON_EMAIL_MODE=off` (ngừng mọi email) và `RECON_SHEET_WRITE=off` (ngừng ghi FB.JT).
2. Tắt workflow lịch chạy worker trên GitHub (Actions → workflow → Disable).

## Hoàn tác ghi FB.JT
Mỗi lần bot ghi I/K/M có dòng `audit_log` (`before`/`after`, số dòng, cột). Ghi lại giá trị `before` vào đúng ô.

## Migration
- Trước khi chạy: backup (`pg_dump "$DATABASE_URL" > backup.sql`, Neon cũng có point-in-time restore).
- Up lỗi giữa chừng: file chạy trong 1 transaction → không để lại bảng dở (có test).
- Rollback 0003 (chạy trước 0002): `psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f backend/migrations/0003_recon_email_test.down.sql` — xoá thư test trong `email_outbox`, giữ `audit_log`.
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

## Email chế độ test
- Bật: Variables `RECON_EMAIL_MODE=test`, `RECON_EMAIL_TEST_TO=<1 địa chỉ @hannaholala.com>`, tuỳ chọn `RECON_EMAIL_MAX` (mặc định 5, tối đa 50 thư/lần chạy).
- Mỗi case BBNT_DRAFTED / FIGURES_WRITTEN nhận đúng 1 thư test (tiêu đề `[TEST] ...`, đính kèm BBNT .docx); đầu thư ghi To/CC mà bản thật sẽ dùng. Bot không gửi tới các địa chỉ đó; mọi giá trị khác `off`/`test` bị từ chối.
- Gửi lại thư test của 1 case (vd sau khi sửa EMAIL BRAND): `DELETE FROM email_outbox WHERE kind = 'ACCEPTANCE_TEST' AND case_id = '<id>';` rồi chạy lại. Thư FAILED không tự gửi lại.
- Xem: `SELECT o.subject, o.status, o.last_error, c.bbnt_number FROM email_outbox o JOIN recon_case c ON c.id = o.case_id ORDER BY o.created_at DESC;`
