# Operations runbook

## Dừng khẩn cấp
1. Đặt `RECON_SEND_MODE=off` (ngừng mọi email) và `RECON_SHEET_WRITE=off` (ngừng ghi FB.JT).
2. Tắt workflow lịch chạy worker trên GitHub (Actions → workflow → Disable).

## Hoàn tác ghi FB.JT
Mỗi lần bot ghi I/K/M có dòng `audit_log` (`before`/`after`, số dòng, cột). Ghi lại giá trị `before` vào đúng ô.

## Migration
- Trước khi chạy: backup (`pg_dump "$DATABASE_URL" > backup.sql`, Neon cũng có point-in-time restore).
- Up lỗi giữa chừng: file chạy trong 1 transaction → không để lại bảng dở (có test).
- Rollback 0001: `psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f backend/migrations/0001_recon_init.down.sql` — **xoá toàn bộ dữ liệu workflow**, chỉ làm sau khi backup.

## Token Google bị thu hồi
Đổi mật khẩu hộp thư gửi làm token mất hiệu lực → worker báo lỗi quyền, không gửi gì. Lấy lại refresh token và cập nhật GitHub Secret.

## Rollback code
- Backend (Render): revert PR trên `main` → CI xanh → Render tự deploy; hoặc Render → Deploys → Rollback.
- Frontend (Cloudflare): trong worktree deploy `git checkout --detach <commit trước>` rồi `.\deploy.cmd`; hoặc `npx wrangler rollback`.
- Worker: chưa chạy production. Khi có: tắt workflow lịch trước, rồi mới revert code/migration.
