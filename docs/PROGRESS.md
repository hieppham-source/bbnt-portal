# Progress — Hannah Reconciliation Automation

Trạng thái: NOT_STARTED · IN_PROGRESS · BLOCKED · IMPLEMENTED · TESTED · DEPLOYED · VERIFIED

Baseline (27/10/2026, `main` @ `be77c42`): ruff OK · mypy strict OK (chạy bằng `python -m mypy`) · pytest 494 passed, 1 skipped.

| Task | Trạng thái | Bằng chứng / ghi chú |
|---|---|---|
| P0 Repo/CI baseline, branch `feat/reconciliation-core`, docs/ | VERIFIED | PR #1 merged, CI xanh |
| P1 Đọc FB.JT theo header (`app/recon/fbjt.py`) | TESTED | Header thật dòng 6 (U = Status đầu tiên), test thiếu/trùng header, dòng Done/lỗi |
| P1 Kết nối Google bằng token bot (đọc FB.JT, NON OP {YY} chip link, DATA BRAND/CONTRACT) | BLOCKED | Chưa có OAuth client Internal + refresh token hộp thư gửi |
| P2 Migration `0001_recon_init` (up/down) | TESTED | 5 test trên Postgres 16 local: idempotency outbox, audit_log append-only, ràng buộc, down→up, up lỗi rollback sạch |
| P2 CI job `migrations` (Postgres service) | VERIFIED | Xanh trên PR #1 |
| P2 Neon production DB | BLOCKED | Chưa có `DATABASE_URL` |
| P3 State machine (`app/recon/state_machine.py`) | TESTED | Không nhảy cóc, DONE chỉ từ SHEET, Giữ chỉ do USER, resume |
| P3/P6 Quy tắc + Audit Gate (`rules.py`, `audit_gate.py`) | TESTED | Đuôi BBNT (bảng 12.3), HĐ, lịch T2–T6 + lễ, G+25, hạn 10 ngày LV, phạt 0,05%, chia K group, ghi ô I/K/M, AUTO/REVIEW/BLOCKED |
| Repository DB `app/recon/db.py` | VERIFIED | PR #3 merged, CI xanh; create/move/resume, optimistic lock (`StaleCase`), transition + audit_log trong 1 transaction; 5 test Postgres |
| P4 BBNT adapter | IN_PROGRESS | PR #4 merged; `app/recon/bbnt_job.py`: dựng input từ case, lọc hợp đồng Đã ký, số BBNT cố định theo `bbnt_issued_at`, lỗi generator -> `reasons`. Còn: đọc DATA BRAND/CONTRACT thật, upload Drive (cần token) |
| P4 Portal bỏ gộp nhiều link / tách camp (cờ `ALLOW_MERGED_BBNT`) | DEPLOYED | Bước 1 (khoá): API 422 `MERGED_BBNT_DISABLED`; form ẩn link phụ, "Theo camp", nút gộp/tách tab. Bước 2 (xoá code) sau 1–2 tháng |
| P5 Gmail connector | NOT_STARTED | Cần token (BLOCKED để test thật) |
| P7 Worker | IN_PROGRESS | Dry-run `app/recon/worker.py` (PR #5, chưa merge): 23 test + fixture giả lập SKIP/WAIT/REVIEW/DRAFT. Còn `run-once`: chờ duyệt `RECON_STATE_PROPOSAL.md` + token |
| P8 API `/api/v1/recon/*` + dashboard `/reconcile` | NOT_STARTED | |
| CI/CD frontend (Cloudflare) | NOT_STARTED | Cần `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` trong GitHub Secrets |

## Lịch sử

- 27/10/2026 — commit local `feat/reconciliation-core` (xem `git log`). Test: 539 passed, 1 skipped (kèm migration test với `RECON_TEST_DATABASE_URL`). Chưa có PR: GitHub API trả 403 `Resource not accessible by integration` khi tạo branch.
- 27/10/2026 — repo đã private. Push vẫn **BLOCKED**: git proxy báo repo không thuộc tập repo được cấp cho phiên; GitHub MCP trả 404 cho repo.
- 28/10/2026 — PR #1 merged. Branch `feat/lock-merged-bbnt`: khoá gộp sau cờ. pytest 541 passed, 6 skipped; ruff, mypy strict, `tsc`, `next build` OK.
- 28/10/2026 — PR #2 merged + deploy frontend. Branch `feat/recon-db`: kho trạng thái Postgres. pytest 551 passed khi có Postgres (541 + 11 skipped khi không).
- 28/10/2026 — PR #3 merged. Branch `feat/recon-bbnt-adapter`: adapter BBNT cho bot.
- 28/10/2026 — PR #4 merged. Branch `feat/recon-worker-dry-run`: worker dry-run.
- 28/10/2026 — PR #5 thêm commit hardening + `GAP_ANALYSIS.md`, `RECON_STATE_PROPOSAL.md`. pytest 584 passed, 1 skipped (có Postgres 16 local); ruff, mypy strict OK.
- 28/10/2026 — Q9 (thêm status tính NMV, portal + bot) và Q12 (hợp đồng hiệu lực theo ngày lập BBNT, bot). pytest 596 passed, 1 skipped.
- 28/10/2026 — Q13 duyệt: migration `0002_recon_idempotency` (cột review/plan hash/run trên `recon_case`, bảng `sheet_write` idempotent) + 2 test. pytest 598 passed, 1 skipped.
- 28/10/2026 — Bước 5 (đọc): `google_bot.py` (token nền, DATA CONTRACT/BRAND, chip link NON OP), `dry-run --live`, workflow `recon-dry-run` (chạy tay). pytest 616 passed.
- 28/10/2026 — Dry-run thật đầu tiên: 619 dòng, DRAFT 2. Branch `feat/recon-dry-run-diagnostics`: nối tên chịu khác cách gõ, OP bị SKIP sớm, gợi ý sửa dữ liệu trong báo cáo. pytest 618 passed.
- 29/10/2026 — Bước D: `run-once` (lưu DB idempotent, ghi I/K/M có kiểm soát, `cell_review`), workflow `recon-run-once`. pytest 632 passed (Postgres local).
- 29/10/2026 — PR #11 merged: dashboard `/reconcile` (duyệt Đổi/Giữ, Giữ/Tiếp tục case). pytest 648 passed.
- 30/10/2026 — Branch `feat/recon-email-test`: email **chế độ test** (`emailer.py`, `send_test_emails`, migration `0003`): thư BBNT kèm .docx chỉ gửi `RECON_EMAIL_TEST_TO`, 1 thư/case, có audit; chế độ thật chưa có (chờ duyệt mẫu thư + loopmail). Kế hoạch test toàn bộ trước khi chạy thật: `RECON_E2E_TEST_PLAN.md`. pytest 657 passed, 1 skipped (Postgres local); ruff, mypy strict OK.
- 30/10/2026 — Cùng branch: email **live** (reply loopmail khi Audit Gate đạt + mẫu ACCEPTANCE đã duyệt), tìm loopmail theo TITLE MAIL + email brand, KAM chọn loopmail trên dashboard, lịch 30 phút/lần (`RECON_SCHEDULE=on`). Migration đổi tên `0003_recon_email`.
