# Deployment

## Hiện tại

- Backend: Render Blueprint (`render.yaml`), Docker, plan free, `autoDeployTrigger: checksPass` → chỉ deploy khi `backend-ci` xanh trên `main`.
- Frontend: Cloudflare Workers qua OpenNext, deploy tay `cd frontend && npm run cf:deploy` (Windows: `chcp 65001` trước khi build).
- CI: `.github/workflows/backend-ci.yml` — job `test` (ruff, mypy strict, pytest) + job `migrations` (Postgres 16 service, không secret).

## Biến môi trường mới (chưa cấu hình — chỉ ghi tên, không ghi giá trị)

| Tên | Ở đâu | Dùng cho |
|---|---|---|
| `DATABASE_URL` | Render env + GitHub Secrets (worker) | Neon Postgres |
| `RECON_GOOGLE_CLIENT_ID`, `RECON_GOOGLE_CLIENT_SECRET`, `RECON_GOOGLE_REFRESH_TOKEN` | GitHub Secrets (worker), Render env (API preview) | Token hộp thư gửi |
| `RECON_APPROVERS` | Render env | Email được duyệt trên portal |
| `RECON_SEND_MODE` (`off`/`review`/`auto`), `RECON_SHEET_WRITE` (`off`/`on`) | Worker env | Bật theo phase; mặc định `off` |
| `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` | GitHub Secrets | Deploy frontend từ CI |

## Worker đối soát

| Chế độ | Lệnh | Cần | Trạng thái |
|---|---|---|---|
| `dry-run` | `python -m app.recon.worker dry-run --fixture <json> --master-file <xlsx>` | Không cần DB/token; không ghi gì | Có (PR #5) |
| `dry-run --live` | Workflow `recon-dry-run` (chạy tay trên GitHub Actions) | Secrets `RECON_GOOGLE_*`; Variables `RECON_FBJT_ID`, `RECON_ORDER_LINKS_ID`, `MASTER_SHEET_URL`. Chỉ đọc Google | Có |
| `run-once` | Workflow `recon-run-once` (chạy tay) | Secret `DATABASE_URL` + như dry-run; ghi FB.JT chỉ khi Variables `RECON_SHEET_WRITE=on` **và** `RECON_SHEET_WRITE_ID` = `RECON_FBJT_ID` | Có (chưa chạy thật) |

Credential nền: worker **không** dùng token Google của nhân viên đăng nhập portal. Chỉ dùng refresh token của hộp
thư gửi (`RECON_GOOGLE_*`) đặt trong GitHub Secrets của repo private, đọc qua biến môi trường, không in ra log.

Triển khai `run-once` (khi có code), theo thứ tự: (1) `dry-run` trên bản sao FB.JT, so với BBNT làm tay;
(2) áp migration bằng tay; (3) bật workflow lịch với `RECON_SHEET_WRITE=off`, `RECON_SEND_MODE=off`;
(4) bật từng cờ theo phase (ARCHITECTURE 8).

Rollback: tắt workflow lịch + đặt cả hai cờ `off` (xem `OPERATIONS_RUNBOOK.md`); revert PR trên `main`
(Render tự deploy lại khi CI xanh); frontend: `git checkout --detach <commit cũ>` trong worktree deploy rồi `deploy.cmd`.

### GitHub Variables cho `recon-dry-run` (không phải secret — chỉ là ID file)

| Tên | Giá trị |
|---|---|
| `RECON_FBJT_ID` | ID file FB.JT (đoạn giữa `/d/` và `/edit`) |
| `RECON_ORDER_LINKS_ID` | ID file `[NEW] Các order link gửi brand` |
| `MASTER_SHEET_URL` | Link Sheet master portal (giống env Render) |

Hộp thư của token phải xem được 3 file này và các file order. Log chỉ in số đếm; báo cáo chi tiết là artifact giữ 1 ngày
khi chọn `report=true`. Lỗi bất ngờ chỉ in tên lỗi (ẩn chi tiết).

## Migration

Áp dụng tay theo thứ tự (không tự chạy migration production): `psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f backend/migrations/0001_recon_init.up.sql`, rồi `0002_recon_idempotency.up.sql`, rồi `0003_recon_email.up.sql`, rồi `0004_recon_status_emails.up.sql`, rồi `0005_recon_followups.up.sql`, rồi `0006_recon_brand_confirmed.up.sql`. CI job `migrations` chạy hết. Rollback: xem `OPERATIONS_RUNBOOK.md`.

## Bật ghi I/K/M (thứ tự bắt buộc)

1. Neon: tạo DB, chạy `0001` → `0006`, lưu `DATABASE_URL` vào GitHub Secrets.
2. Chạy `recon-run-once` với `RECON_SHEET_WRITE` chưa đặt (= off): chỉ lưu trạng thái, không ghi Sheet.
3. Tạo **bản sao FB.JT**, share Editor cho hộp thư bot. Đặt tạm `RECON_FBJT_ID` = ID bản sao, `RECON_SHEET_WRITE=on`,
   `RECON_SHEET_WRITE_ID` = ID bản sao → chạy, kiểm tra ô I/K/M trên bản sao + bảng `sheet_write`, `audit_log`.
   (Dùng DB riêng cho lần thử này, hoặc xoá dữ liệu thử trước khi chuyển sang FB.JT thật.)
4. Chỉ khi bước 3 đúng: đặt lại `RECON_FBJT_ID` = FB.JT thật và `RECON_SHEET_WRITE_ID` = cùng ID đó.

Tắt ghi ngay: xoá Variable `RECON_SHEET_WRITE` (hoặc đặt `off`).

## Trang /reconcile (dashboard duyệt)

- Backend Render cần thêm env: `DATABASE_URL` (cùng chuỗi Neon **main**, direct connection) và `RECON_APPROVERS`
  (email được Đổi/Giữ ô và Giữ/Tiếp tục case, cách nhau dấu phẩy). Thiếu `DATABASE_URL` → trang báo 503.
- Frontend: deploy lại Cloudflare (`deploy.cmd`) để có menu "Đối soát".
- Mọi người đăng nhập @hannaholala.com xem được; quyền duyệt kiểm ở backend.
