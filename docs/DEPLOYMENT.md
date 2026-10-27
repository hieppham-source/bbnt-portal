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

## Migration

Áp dụng tay (không tự chạy migration production): `psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f backend/migrations/0001_recon_init.up.sql`. Rollback: xem `OPERATIONS_RUNBOOK.md`.
