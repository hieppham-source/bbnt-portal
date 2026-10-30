# Kế hoạch test toàn bộ trước khi chạy hệ thống thật

Chạy hết trên **bản sao FB.JT** + **Neon branch `test-write`**. Chưa qua hết bảng dưới thì chưa đổi sang FB.JT thật.
Mỗi dòng: cách dựng trên bản sao → kết quả phải thấy. Đánh dấu ✅ / ❌ vào cột cuối, ❌ thì gửi log + số dòng.

## 0. Chuẩn bị (1 lần)

| Việc | Cách làm |
|---|---|
| Biến GitHub | `RECON_FBJT_ID` = `RECON_SHEET_WRITE_ID` = ID bản sao; `RECON_SHEET_WRITE=on`; `RECON_EMAIL_MODE=test`; `RECON_EMAIL_TEST_TO=hiep.pham@hannaholala.com`; `RECON_EMAIL_MAX=5` |
| Secret | `DATABASE_URL` = branch `test-write` (đã đổi mật khẩu sau lần lộ) |
| Migration | Neon SQL Editor (branch `test-write`): chạy nội dung `backend/migrations/0003_recon_email.up.sql` |
| Làm sạch DB test | `TRUNCATE recon_case, sheet_row, sheet_write, cell_review, email_outbox, state_transition, workflow_run, audit_log CASCADE;` (CHỈ branch test) |
| Render | Tạm trỏ `DATABASE_URL` của backend về `test-write` để dashboard đọc cùng DB |

## 1. Lọc dòng (dry-run hoặc run-once, xem báo cáo `--out`)

| # | Dựng trên bản sao | Kết quả mong đợi | |
|---|---|---|---|
| 1 | Campaign tháng 07/2026 | SKIP `BEFORE_SCOPE` | |
| 2 | Cột H = `OP` | SKIP `NOT_FB_NON_OP` | |
| 3 | Cột H trống | REVIEW `OP_COLUMN_BLANK` | |
| 4 | Cột H = `abc` | REVIEW `OP_COLUMN_UNKNOWN` | |
| 5 | M (HRV GMV) = 0 hoặc trống | SKIP `NO_REVENUE` | |
| 6 | Status U = `Đã done BBNT + Chờ KT xuất HĐ nháp` | SKIP `BBNT_ALREADY_DONE` | |
| 7 | Status U = `xyz` | REVIEW `STATUS_UNKNOWN` | |
| 8 | NOTES có `GỘP LINE` | REVIEW `MERGED_LINE` | |
| 9 | H = NON OP nhưng DATA BRAND cột OP = `OP` | REVIEW `OP_CONFLICT_DATA_BRAND` | |
| 10 | 2 dòng cùng brand + campaign | REVIEW `FBJT_ROW_DUPLICATE` | |
| 11 | Không có link trong `NON OP 26` | SKIP `LINK_NOT_FOUND` (+ gợi ý) | |
| 12 | Link là chữ, không phải chip/URL | SKIP `LINK_INVALID` | |
| 13 | 2 link khác nhau cho cùng brand + campaign | SKIP `LINK_AMBIGUOUS` | |

## 2. Lập BBNT

| # | Dựng | Mong đợi | |
|---|---|---|---|
| 14 | Cột G (ngày kết thúc) trống | WAIT `NO_CAMP_END` | |
| 15 | G + 25 ngày > hôm nay | WAIT (có "gửi từ") | |
| 16 | Link group, thiếu 1 dòng brand trên FB.JT | REVIEW `BRAND_ROWS_MISMATCH` | |
| 17 | BÊN B = `Công ty lạ` | REVIEW `ENTITY_UNKNOWN` | |
| 18 | Brand không có HĐ `Đã ký` trong DATA CONTRACT | REVIEW `CONTRACT_NOT_UNIQUE` | |
| 19 | HĐ hết hạn trước ngày lập BBNT | REVIEW `CONTRACT_NOT_VALID_ON_BBNT_DATE` | |
| 20 | Bot không mở được link order (bỏ chia sẻ) | REVIEW `ORDER_SHEET_UNREADABLE` | |
| 21 | Ca sạch, I/K/M trống | DRAFT, có số BBNT | |
| 22 | Link group 2 brand, đủ dòng | DRAFT, NMV/K tách đúng theo brand, tổng K = BBNT | |

## 3. Ghi I/K/M + duyệt (run-once, `RECON_SHEET_WRITE=on`)

| # | Dựng | Mong đợi | |
|---|---|---|---|
| 23 | Ca #21 | I/K/M được ghi; log `đã ghi 3`; `audit_log` có 3 dòng SHEET_WRITE | |
| 24 | Chạy lại ngay | `case mới 0`, `ô chờ ghi mới 0`, không ghi lại; case → FIGURES_WRITTEN | |
| 25 | I có sẵn số khác BBNT | REVIEW `FIGURES_DIFFER_FBJT`; dashboard hiện 1 ô chờ duyệt | |
| 26 | Dashboard bấm **Đổi** ô #25, chạy lại | Ô được ghi số BBNT | |
| 27 | Dashboard bấm **Giữ** 1 ô | Không ghi; ô biến khỏi danh sách | |
| 28 | Ô M là công thức | `xung đột 1`, ô giữ nguyên | |
| 29 | Sửa tay I sau khi bot lập BBNT, chạy lại | REVIEW `FIGURES_CHANGED_AFTER_ISSUE`, không ghi | |
| 30 | Dashboard **Giữ case**, sửa dữ liệu, chạy lại | `bot không chạm 1`; bấm **Tiếp tục** → chạy lại xử lý bình thường | |
| 31 | Đổi status U sang `Đã done BBNT...` sau khi đã có ô PLANNED | Không ghi (ngoài phạm vi) | |
| 32 | Chạy 2 workflow cùng lúc | Lần thứ 2: "Đang có worker khác chạy" | |

## 4. Email test (`RECON_EMAIL_MODE=test`)

| # | Dựng | Mong đợi | |
|---|---|---|---|
| 33 | Ca DRAFT có EMAIL BRAND | 1 thư `[TEST] GROUP (BRAND) x EZS - FB - <TITLE MAIL>` về hộp thư test, kèm BBNT .docx mở được; đầu thư ghi To/CC thật | |
| 34 | Kiểm tra người nhận thư #33 | Chỉ `hiep.pham@`, không CC; brand KHÔNG nhận | |
| 35 | TITLE MAIL trống | Tiêu đề dùng tên campaign | |
| 36 | Brand trống EMAIL BRAND | Không gửi; log `không gửi {'NO_BRAND_EMAIL': 1}` | |
| 37 | EMAIL BRAND sai dạng | `BRAND_EMAIL_INVALID` | |
| 38 | Chạy lại | Không gửi thư trùng | |
| 39 | > 5 ca DRAFT | Chỉ 5 thư, còn lại `MAX_PER_RUN`; lần sau gửi tiếp | |
| 40 | Bảng confirm số trong thư (EMAIL 2) | Đúng bảng pháp nhân (EZS: VAT 8%, C 0%; SASSY: VAT 0%, cấn trừ 1%); A, B, C, D, F, E khớp file .docx; deadline = ngày gửi + 3 ngày làm việc | |
| 41 | `RECON_EMAIL_MODE=abc` | Workflow báo lỗi cấu hình, không làm gì | |
| 42 | Thư test của ca đã ghi I/K/M + TITLE MAIL đúng | Đầu thư: "Audit Gate: ĐẠT", loopmail = đúng thread | |
| 43 | 2 thread cùng TITLE MAIL | Dashboard: "Chọn loopmail" 2 nút; chọn 1 → lần sau dùng thread đó | |
| 44 | TITLE MAIL trống | Email chưa gửi: `TITLE_MAIL_MISSING` | |

## 4b. Email thật (`live`) — thử với 1 brand nội bộ / brand đồng ý thử

| # | Dựng | Mong đợi | |
|---|---|---|---|
| 45 | Chưa đặt `RECON_EMAIL_SIGNER` | Không gửi; `SIGNER_NOT_SET` | |
| 46 | Ca FIGURES_WRITTEN, đủ điều kiện, trong giờ | Reply vào đúng loopmail, To brand, CC nội bộ; case → Đã gửi nghiệm thu | |
| 47 | Chạy lại | Không gửi lần 2 | |
| 48 | Ngoài giờ / thứ Bảy | `OUTSIDE_SEND_WINDOW`, gửi ở lần chạy trong giờ kế tiếp | |
| 49 | Ca mới lập BBNT, chưa ghi I/K/M | `FIGURES_MISMATCH_FBJT`, không gửi | |

## 4b'. Email 1 — brand điền status

| # | Dựng | Mong đợi | |
|---|---|---|---|
| 51 | Ca qua G + 25, file order còn 1 dòng trống status | Case "Chờ G + 25 / chờ brand điền status", lý do `STATUS_INCOMPLETE`; chưa có số BBNT, không ghi I/K/M | |
| 52 | Như #51, chế độ test | 1 thư `[TEST]` Email 1: link order, kỳ đơn, hạn = ngày gửi + 3 ngày làm việc; "bot tạo luồng mới" | |
| 53 | Như #51, live, chưa có luồng tay | Thư mới đúng tiêu đề mẫu; case lưu loopmail; chạy lại không gửi lần 2 | |
| 54 | Có luồng tay đúng TITLE MAIL | Email 1 reply vào luồng đó | |
| 55 | Quá hạn chưa điền | Dashboard "Thư chờ duyệt" có thư nhắc lần 1; chưa bấm → không gửi | |
| 56 | Bấm **Gửi** | Lần chạy sau thư nhắc reply vào loopmail; 3 ngày sau có thư nhắc lần 2 chờ duyệt | |
| 57 | Brand điền đủ status | Lập BBNT, ghi I/K/M, Email 2 reply cùng luồng; thư nhắc chưa gửi → huỷ | |

## 4d. Sau email nghiệm thu

| # | Dựng | Mong đợi | |
|---|---|---|---|
| 58 | Brand trả lời vào loopmail | Dashboard "Brand trả lời chưa xem"; trả lời tự động không hiện; bounce ghi "thư lỗi gửi" | |
| 59 | Quá Email 2 + 3 ngày làm việc, U vẫn trước BBNT | Thư nhắc ký BBNT (3A) chờ duyệt; Gửi → reply loopmail | |
| 60 | U = "Đã có BBNT + Chờ Admin Check" khi 3A đang chờ gửi | 3A bị huỷ | |
| 61 | U = HĐ nháp | Case "Chờ confirm HĐ nháp"; quá 3 ngày làm việc → 3B chờ duyệt | |
| 62 | U = "Đã xuất hóa đơn + Chờ tiền", ghi chú "HĐ: x (dd/mm/yyyy)" | Case "Chờ thanh toán"; đúng ngày đến hạn → 4A tự gửi (không có số TK) | |
| 63 | Quá hạn | 4B: số ngày quá hạn + phạt ước tính = K × 0,05% × ngày; 3 ngày/lần | |
| 64 | Brand trả lời sau 4B | Lần sau không gửi, lý do `BRAND_REPLIED` | |
| 65 | Ghi chú HĐ sai dạng | `INVOICE_UNREADABLE`, không gửi | |
| 66 | U = Done | Case "Xong", hết nhắc | |

## 4c. Lịch

| # | Dựng | Mong đợi | |
|---|---|---|---|
| 50 | `RECON_SCHEDULE=on` | Workflow tự chạy 30 phút/lần trong giờ hành chính; `off` → không chạy | |

## 5. Trước khi chuyển sang thật

- [ ] Toàn bộ 66 dòng ✅.
- [ ] Tắt bước S5 của skill nhắc cũ trước khi bật live.
- [ ] Đặt `RECON_EMAIL_SIGNER`. Email 1/3/4 làm xong theo chốt Q15–Q18.
- [ ] Đặt lại `RECON_FBJT_ID` = FB.JT thật, `RECON_SHEET_WRITE=off`, `RECON_EMAIL_MODE=off`, `RECON_SCHEDULE=off`, `DATABASE_URL` = Neon main (mật khẩu mới), Render về main.
- [ ] Chạy migration `0003` trên Neon main.
- [ ] Lần đầu trên FB.JT thật: run-once **không ghi** → đọc báo cáo → mới bật ghi.
