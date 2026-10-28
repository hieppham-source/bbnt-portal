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
| `run-once` | — | `DATABASE_URL`, `RECON_GOOGLE_*`, duyệt `RECON_STATE_PROPOSAL.md` | **Chưa có code** |

Credential nền: worker **không** dùng token Google của nhân viên đăng nhập portal. Chỉ dùng refresh token của hộp
thư gửi (`RECON_GOOGLE_*`) đặt trong GitHub Secrets của repo private, đọc qua biến môi trường, không in ra log.

Triển khai `run-once` (khi có code), theo thứ tự: (1) `dry-run` trên bản sao FB.JT, so với BBNT làm tay;
(2) áp migration bằng tay; (3) bật workflow lịch với `RECON_SHEET_WRITE=off`, `RECON_SEND_MODE=off`;
(4) bật từng cờ theo phase (ARCHITECTURE 8).

Rollback: tắt workflow lịch + đặt cả hai cờ `off` (xem `OPERATIONS_RUNBOOK.md`); revert PR trên `main`
(Render tự deploy lại khi CI xanh); frontend: `git checkout --detach <commit cũ>` trong worktree deploy rồi `deploy.cmd`.

## Migration

Áp dụng tay (không tự chạy migration production): `psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f backend/migrations/0001_recon_init.up.sql`. Rollback: xem `OPERATIONS_RUNBOOK.md`.
