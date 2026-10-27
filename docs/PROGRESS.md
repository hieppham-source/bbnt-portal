# Progress — Hannah Reconciliation Automation

Trạng thái: NOT_STARTED · IN_PROGRESS · BLOCKED · IMPLEMENTED · TESTED · DEPLOYED · VERIFIED

Baseline (27/10/2026, `main` @ `be77c42`): ruff OK · mypy strict OK (chạy bằng `python -m mypy`) · pytest 494 passed, 1 skipped.

| Task | Trạng thái | Bằng chứng / ghi chú |
|---|---|---|
| P0 Repo/CI baseline, branch `feat/reconciliation-core`, docs/ | TESTED | Branch local; push **BLOCKED** (GitHub integration 403, xem PR/push bên dưới) |
| P1 Đọc FB.JT theo header (`app/recon/fbjt.py`) | TESTED | Header thật dòng 6 (U = Status đầu tiên), test thiếu/trùng header, dòng Done/lỗi |
| P1 Kết nối Google bằng token bot (đọc FB.JT, NON OP {YY} chip link, DATA BRAND/CONTRACT) | BLOCKED | Chưa có OAuth client Internal + refresh token hộp thư gửi |
| P2 Migration `0001_recon_init` (up/down) | TESTED | 5 test trên Postgres 16 local: idempotency outbox, audit_log append-only, ràng buộc, down→up, up lỗi rollback sạch |
| P2 CI job `migrations` (Postgres service) | IMPLEMENTED | Chưa chạy trên GitHub (chưa push) |
| P2 Neon production DB | BLOCKED | Chưa có `DATABASE_URL` |
| P3 State machine (`app/recon/state_machine.py`) | TESTED | Không nhảy cóc, DONE chỉ từ SHEET, Giữ chỉ do USER, resume |
| P3/P6 Quy tắc + Audit Gate (`rules.py`, `audit_gate.py`) | TESTED | Đuôi BBNT (bảng 12.3), HĐ, lịch T2–T6 + lễ, G+25, hạn 10 ngày LV, phạt 0,05%, chia K group, ghi ô I/K/M, AUTO/REVIEW/BLOCKED |
| P4 BBNT adapter | NOT_STARTED | |
| P4 Portal bỏ gộp nhiều link / tách camp (cờ `ALLOW_MERGED_BBNT`) | NOT_STARTED | |
| P5 Gmail connector | NOT_STARTED | Cần token (BLOCKED để test thật) |
| P7 Worker `python -m app.worker` | NOT_STARTED | |
| P8 API `/api/v1/recon/*` + dashboard `/reconcile` | NOT_STARTED | |
| CI/CD frontend (Cloudflare) | NOT_STARTED | Cần `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` trong GitHub Secrets |

## Lịch sử

- 27/10/2026 — commit local `feat/reconciliation-core` (xem `git log`). Test: 539 passed, 1 skipped (kèm migration test với `RECON_TEST_DATABASE_URL`). Chưa có PR: GitHub API trả 403 `Resource not accessible by integration` khi tạo branch.
