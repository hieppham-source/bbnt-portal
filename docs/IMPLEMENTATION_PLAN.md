# Implementation plan

Thứ tự theo dependency (chi tiết nghiệp vụ: `ARCHITECTURE.md`). Mỗi bước: test → commit → PR → CI xanh.

1. **Core thuần** (xong): `app/recon/{rules,fbjt,state_machine,audit_gate}.py` + test.
2. **Schema** (xong): `backend/migrations/0001_*` + CI job `migrations`.
3. **Portal** (xong, PR #2): khoá gộp nhiều link / tách camp sau cờ `ALLOW_MERGED_BBNT=false` (ARCHITECTURE 14.1); sau 1–2 tháng xoá code.
4. **Repository DB** (xong): `app/recon/db.py` (psycopg), ghi transition + case trong 1 transaction, optimistic lock. Test: `tests/test_recon_db.py` (job `migrations`).
5. **Google bot client** (phần đọc xong: `app/recon/google_bot.py` + `dry-run --live` + workflow `recon-dry-run`; còn: writer I/K/M): refresh token → access token; tái dùng `GoogleSheetsReader.from_access_token`; đọc chip link (`spreadsheets.get` fields `chipRuns`); writer 3 ô I/K/M (`RAW`, đọc lại trước/sau khi ghi).
6. **BBNT adapter** (phần không cần token xong: `app/recon/bbnt_job.py` — dựng `BbntInput`, chọn hợp đồng `DATA CONTRACT`, clock cố định; còn: đọc DATA BRAND/CONTRACT thật, upload Drive): dựng `BbntInput` (brand/group từ DATA BRAND, hợp đồng từ DATA CONTRACT, đuôi từ `campaign_suffix`), clock cố định = `bbnt_issued_at`, upload Drive (`drive.file`), sha256.
7. **Gmail** (xong 30/10: Email 1 / 2 / 3A / 3B / 4, loopmail, thư brand trả lời dạng metadata; chưa: đọc REMINDER LOG, phân loại nội dung CONFIRM/REJECT — cần anh định nghĩa câu chữ): tìm loopmail theo TITLE MAIL + email brand, reply (`threadId` + `In-Reply-To`), đồng bộ `history.list`, phân loại reply, thư tự động/bounce.
8. **Worker** — dry-run xong: `python -m app.recon.worker dry-run --fixture f.json --master-file …` (nối dòng FB.JT ↔ link order, G+25, Non-OP, hợp đồng, tạo BBNT, kế hoạch ghi I/K/M; không ghi gì). Còn: `run-once` (DB + Google + Gmail) + `pg_try_advisory_lock` — chờ duyệt `RECON_STATE_PROPOSAL.md` + token.
9. **API** `/api/v1/recon/*` + **dashboard** `/reconcile` (Auth.js hiện có, quyền kiểm ở backend).
10. **Lịch** (xong 30/10, bật bằng `RECON_SCHEDULE=on`): workflow GitHub Actions (chỉ khi repo private), 30 phút/lần 8:00–20:00 T2–T6 giờ VN, `concurrency`.
11. **Hardening + dry-run** trên bản sao FB.JT, rồi bật theo phase (ARCHITECTURE 8).
