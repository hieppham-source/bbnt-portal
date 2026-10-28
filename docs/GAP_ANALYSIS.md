# Gap analysis — BBNT Generator + Reconciliation (28/10/2026)

Nguồn: `main` @ `8d72306` (sau PR #4), PR #5 `feat/recon-worker-dry-run` (`09efc41` + commit hardening của tài liệu này),
`docs/ARCHITECTURE.md` (mục 10–16 là quyết định đã chốt). "Bằng chứng" = test trong repo đã chạy, hoặc CI trên GitHub.

## 1. Trạng thái theo nơi nằm

| Nhóm | Chức năng | Bằng chứng |
|---|---|---|
| **Đã merge vào `main`** | Portal tạo BBNT (FB Non-OP, EZS/Sassy), NMV chỉ đơn `Đã giao`, VAT 8% / khấu trừ 1% theo tab `PHAP_NHAN` | `test_calculation.py`, `test_api.py`; CI xanh |
| | Khoá gộp nhiều link / tách camp (`ALLOW_MERGED_BBNT=false`) | PR #2; frontend đã deploy Cloudflare 28/10 |
| | Lõi recon: đọc FB.JT theo header, quy tắc (đuôi số, lịch, phạt, chia K), state machine, Audit Gate | PR #1, `test_recon_rules.py` |
| | Schema `0001_recon_init` + repository Postgres (optimistic lock, transition + audit_log 1 transaction) | PR #1, #3; job CI `migrations` |
| | Adapter BBNT cho bot (`bbnt_job.py`), tên chương trình `<TÊN> <MMYY>` | PR #4, `test_recon_bbnt_job.py` |
| **Trong PR #5 (chưa merge)** | Worker `dry-run`: SKIP / WAIT / REVIEW / DRAFT + kế hoạch ghi I/K/M, không ghi gì | `test_recon_worker.py` (23 test), CLI trên fixture giả lập |
| | Hardening (commit mới): định danh case = spreadsheet id, dòng FB.JT lỗi/trùng → REVIEW, link sai dạng → SKIP, link group thiếu dòng brand → REVIEW, hợp đồng mô hình OP → REVIEW, `issued_at` đã lưu, cảnh báo Hoàn/Hủy/status lạ | như trên |
| **Chỉ có trong tài liệu** | Google bot client, đọc chip link `NON OP {YY}`, DATA BRAND/CONTRACT thật, ghi I/K/M, upload Drive | ARCHITECTURE 12.1, plan bước 5–6 — **chưa có code** |
| | Gmail (tìm loopmail, reply, đồng bộ, phân loại), outbox gửi thật | plan bước 7 — chưa có code |
| | `run-once` (DB + Google), advisory lock, lịch GitHub Actions | plan bước 8, 10 — chưa có code |
| | API `/api/v1/recon/*`, dashboard `/reconcile`, duyệt `RECON_APPROVERS` | plan bước 9 — chưa có code |
| | TikTok Shop, OP, SHP cho bot | ARCHITECTURE 8 phase 5 — chưa có code |

Không có chức năng recon nào đang **chạy** ở production: chưa có `DATABASE_URL`, chưa có token bot, chưa có workflow lịch.

## 2. Gap so với workflow mục tiêu

| Yêu cầu | Hiện có | Thiếu | Chặn bởi |
|---|---|---|---|
| NMV chỉ `Đã giao`; Hoàn/Hủy loại | Có (`calculation.py`) | — | — |
| Status lạ | Loại khỏi NMV + cảnh báo, không chặn; NMV tính thêm hoàn thành / đã xong / confirm (portal + bot) | — | Q9 đã chốt |
| EZS VAT 8%, Sassy khấu trừ 1% | Có, theo `PHAP_NHAN` hiệu lực tới 31/12/2026; test K của worker | Dòng thuế 2027 | Q10: giữ nguyên; từ 01/01/2027 BBNT mới → REVIEW |
| Facebook | Có (template EZS/Sassy FB Non-OP) | — | — |
| TikTok Shop | Số BBNT hỗ trợ `TTS` (portal) | Bot TTS | Q11: tạm bỏ |
| Brand / group | Có (1 link = 1 BBNT, group tách bảng brand, chia K dồn vào NMV lớn nhất) | Đọc `DATA BRAND` thật | Token |
| OP / non-OP | FB.JT `OP` + mô hình hợp đồng master → REVIEW | `DATA BRAND.OP` (ARCHITECTURE 11.2-1) | Token |
| Kỳ nghiệm thu / hiệu lực HĐ | Generator tính toàn bộ dòng của link; bot kiểm hợp đồng hiệu lực tại ngày lập BBNT | — | Q12 đã chốt |
| Hợp đồng | Đúng 1 dòng `Đã ký` + FB + pháp nhân + có trong master portal | — | — |
| Số BBNT | Ổn định khi truyền `issued_at` đã lưu; `bbnt_number` UNIQUE | Lưu `issued_at` (cần DB thật) | `DATABASE_URL` |
| Không phát hành khi lỗi | Mọi lý do → REVIEW; dry-run không ghi | Audit Gate email (template, loopmail…) chưa nối vào worker | Q1, token |
| Case ổn định, idempotency, audit | Schema + repository có; dry-run chưa dùng DB | Nối worker ↔ DB | Đã duyệt + migration 0002; còn chờ token để chạy thật |
| Credential nền | Thiết kế: refresh token hộp thư gửi trong GitHub Secrets | Code client + secret | Token |
| Dry-run trước khi ghi | Có, đã test | Chạy trên bản sao FB.JT thật | Token |
| Không lộ secret/dữ liệu | Fixture giả lập (`MẪU`), CI không có secret | — | — |

## 3. Mâu thuẫn tài liệu ↔ code (chưa sửa quy tắc — chờ anh quyết)

| # | Mâu thuẫn | Vị trí | Đề xuất |
|---|---|---|---|
| M1 | Kiến trúc yêu cầu `DATA BRAND.OP` trống; worker chỉ xét FB.JT `OP` (+ mô hình hợp đồng master) | `ARCHITECTURE.md:438`, `:546`; `backend/app/recon/worker.py:174` | Thêm khi đọc DATA BRAND thật (cần token); đến lúc đó run-once không được bật |
| M2 | ~~Status lạ không chặn~~ — **đã chốt Q9**: không chặn, thêm status tính NMV | `backend/app/core/calculation.py:124` | Đã làm |
| M3 | ~~Hiệu lực hợp đồng không được kiểm~~ — **đã chốt Q12** (bot, theo ngày lập BBNT; portal giữ nguyên) | `backend/app/recon/worker.py:196` | Đã làm |
| M4 | Tài liệu ghi `python -m app.worker`; code là `python -m app.recon.worker` | `ARCHITECTURE.md:123,136,187,515` | Đã ghi chú ở ARCHITECTURE 17 (giữ nguyên lịch sử các mục cũ) |
| M5 | Thuế hết hiệu lực 31/12/2026 — **Q10: giữ nguyên** | `backend/app/core/calculation.py:749-752` | Không đổi |
| M6 | "Ngày gửi nghiệm thu của link nhiều dòng = ngày kết thúc muộn nhất + 25" là giả định, chưa có trong ARCHITECTURE | `backend/app/recon/worker.py:162` | Xác nhận rồi ghi vào ARCHITECTURE |
| M7 | README "Không database" | `README.md:2` | Đã sửa: portal không DB; module recon dùng Postgres |
| M8 | ARCHITECTURE F8 (repo public) vs SECURITY (đã private) | `ARCHITECTURE.md:23`; `SECURITY.md:3` | F8 đã được thay bằng 10.1; phiên 28/10 clone được qua proxy của phiên nên **không kiểm chứng lại** được — anh xác nhận trên GitHub Settings |
