# Hannah Reconciliation Automation — Tổng quan dự án (bản để review)

> **Cách dùng file này:** dán toàn bộ file vào Claude (hoặc đính kèm), kèm câu hỏi ở **mục 12**. File tự đủ
> ngữ cảnh. Không chứa mật khẩu, khoá truy cập, email cá nhân hay số liệu tài chính chi tiết của brand.

Người phụ trách: Hiệp (KAM Hannah website + Facebook, OM / EZS) · Cập nhật: 05/11/2026 ·
Repo: `hieppham-source/bbnt-portal` (private) · Trạng thái: **test xong trên bản sao, chờ duyệt launch**.

---

## 1. Bài toán

OM bán hàng affiliate cho brand qua website hannaholala.com + fanpage Hannah Olala. Mô hình **Non-OP**: OM bán,
brand giao hàng, cuối mỗi camp hai bên đối soát đơn và brand trả **hoa hồng** cho OM (pháp nhân EZS hoặc SASSY).

Quy trình đối soát kênh FB trước đây làm tay trên Google Sheet **FB.JT** (~1.700 dòng/năm, ~80 group brand):

1. Chờ brand điền trạng thái từng đơn vào file order.
2. Tính NMV, hoa hồng, VAT/khấu trừ → lập **Biên bản nghiệm thu (BBNT)** Word.
3. Gửi mail confirm số + BBNT, nhắc ký, nhắc xác nhận hoá đơn nháp, nhắc thanh toán.
4. Cập nhật FB.JT (số liệu cột I/K/M, Status cột U).

Vấn đề: tốn thời gian PIC, dễ sai số / sai hợp đồng / sai pháp nhân, nhắc nợ không đều, không có tổng quan công nợ.

## 2. Mục tiêu và phạm vi

**Làm:** kênh FB, mô hình Non-OP, camp từ 08/2026. Tự động từ lúc camp kết thúc tới khi brand trả tiền; con người
chỉ duyệt các điểm có rủi ro tài chính / pháp lý.

**Không làm (giai đoạn này):** kênh TikTok Shop / Shopee; mô hình OP; xuất hoá đơn (kế toán làm); đọc nội dung thư
brand trả lời; upload BBNT lên Drive.

**Nguyên tắc thiết kế:**
- Bot **không tự đoán**: dữ liệu thiếu / lạ / mâu thuẫn → đưa vào hàng đợi "Cần duyệt".
- Bot **chỉ ghi** 3 ô số (I, K, M) và cột Status U (tối đa tới "Đã done BBNT + Chờ KT xuất HĐ nháp"); không ghi đè
  ô đã có số khác; đọc lại ô trước và sau khi ghi.
- Mọi lần ghi sheet, đổi trạng thái, duyệt, gửi thư đều có **audit log** (ai, lúc nào, trước/sau).
- Có công tắc tắt từng phần: ghi sheet, gửi mail, lịch chạy.

## 3. Kiến trúc

```
Google Sheets (FB.JT, file link order, file order của brand)
        │ đọc / ghi I,K,M,U (Google API, quyền hộp thư bot)
        ▼
Bot (Python, GitHub Actions: dry-run / run-once, lịch 30 phút giờ hành chính) ──▶ Gmail API (gửi/đọc luồng mail)
        │
        ▼
Postgres (Neon): case, trạng thái, lệnh ghi ô, hàng đợi thư, audit log
        ▲
        │ API (FastAPI trên Render)
Portal web (Next.js trên Cloudflare, đăng nhập Google @hannaholala.com)
   Tab 1 Tổng quan · Tab 2 Cần duyệt · Tab 3 Lập BBNT tay
```

| Thành phần | Công nghệ | Ghi chú |
|---|---|---|
| Backend + bot | Python 3.12, FastAPI, psycopg3 | ~9.800 dòng code, 27 file test, **703 test tự động** |
| Frontend | Next.js 15, TypeScript | ~2.600 dòng |
| DB | Neon Postgres, 6 migration có up/down | Branch `test-write` cho test |
| CI/CD | GitHub Actions (CI test + migration), Render (backend tự deploy khi CI xanh), Cloudflare (frontend) | |
| Nguồn dữ liệu | FB.JT tab `FB-2026` + tab `DATA BRAND` (brand, hợp đồng, Bên A, email) | Sheet master chỉ còn pháp nhân B + mapping cột file order |

## 4. Luồng nghiệp vụ tự động

| Bước | Điều kiện | Bot làm | Người làm |
|---|---|---|---|
| 1. Chờ | Camp kết thúc chưa đủ 25 ngày (cột G) | Không | — |
| 2. Email 1 | Đủ 25 ngày, file order còn đơn chưa có status / status lạ | Gửi thư nhắc brand điền đúng cột "Tình trạng lên đơn"; ghi U "Brand chưa gửi data" | Duyệt thư nhắc lại lần 2+ |
| 3. Lập BBNT | Đủ status, hợp đồng CÒN HIỆU LỰC duy nhất, số liệu nhất quán | Tính NMV / hoa hồng / thuế, lập BBNT .docx, ghi I/K/M | Duyệt ô nếu FB.JT đã có số khác |
| 4. Email 2 | Số trên FB.JT khớp BBNT | Gửi bảng confirm số + BBNT vào luồng mail (TITLE MAIL), hạn 3 ngày làm việc; ghi U "Đã Confirm số + Chờ brand gửi BBNT" | — |
| 5. Brand confirm | Brand đồng ý trong mail | Ghi U "Đã done BBNT + Chờ KT xuất HĐ nháp" | KAM bấm **Brand đã confirm** |
| 6. Hoá đơn | Kế toán đổi U | Theo dõi giai đoạn; soạn nhắc confirm HĐ nháp | Kế toán xuất HĐ; duyệt thư nhắc |
| 7. Thanh toán | U "Đã xuất hóa đơn + Chờ tiền", có số/ngày HĐ | Tự nhắc khi tới hạn (ngày HĐ + 10 ngày làm việc); quá hạn kèm phạt ước tính 0,05%/ngày | — |
| 8. Xong | Kế toán đổi U = Done trả tiền | Dừng | — |

Giới hạn gửi: T2–T6, 8h–18h, tối đa 5 thư / lần chạy (ưu tiên Email 2). Thư xưng **EZS**, CC hộp thư nội bộ.

## 5. Quy tắc nghiệp vụ đã chốt (với ngày)

| Ngày | Quyết định |
|---|---|
| 28/10 | Chỉ FB Non-OP; cột H "NON OP" xử lý, "OP" bỏ qua, trống/lạ → duyệt. Phạm vi từ 08/2026. NMV tính cả status Hoàn thành / Đã xong / Confirm |
| 30/10 | Email 1 ở G+25; hạn brand 3 ngày làm việc; thanh toán 10 ngày làm việc + phạt; KT gửi HĐ nháp, bot chỉ nhắc; bỏ số tài khoản trong thư; bot ghi U chỉ tiến lên |
| 01/11 | Thư nhắc brand điền đúng cột "Tình trạng lên đơn" |
| 04/11 | Status đơn lạ (vd "CHƯA XỬ LÝ", "ĐANG GIAO") = chưa đủ → chờ + nhắc, chưa lập BBNT (chỉ bot; portal giữ quy tắc cũ) |
| 04/11 | Nguồn dữ liệu = tab DATA BRAND; hợp đồng dùng được = STATUS "CÒN HIỆU LỰC" (không xét ngày); % = COMMISSION chưa VAT; phụ lục 2 cột riêng; portal chỉ FB |
| 05/11 | Tổng quan: Đã thu = K dòng Done; Pending = K chưa Done; ETA cả năm = đã thu + pending + GMV × 22% × 80% (camp chưa nghiệm thu); Cancel = 1 − ΣNMV/ΣGMV; chậm trả theo group brand |
| 05/11 | Tab "ĐƠN SÓT…" trong file order cộng chung với tab ORDER; thư xưng EZS |

## 6. Portal

| Tab | Nội dung |
|---|---|
| **Tổng quan** | 4 ô: Đã thu, Còn pending, Commission ETA cả năm, Cancel trung bình — bấm vào xem theo group brand. Bảng group brand chậm trả (số tiền, số ngày trễ). Bảng theo tháng. Lọc năm / tháng / PIC |
| **Cần duyệt** | Ô FB.JT lệch số (Đổi / Giữ), thư brand trả lời, thư chờ duyệt (Gửi / Bỏ qua), danh sách case theo trạng thái với lý do tiếng Việt; nút Brand đã confirm, Giữ / Tiếp tục case, chọn luồng mail |
| **Biên bản nghiệm thu** | Lập BBNT tay từ link file order (chọn brand có ô tìm kiếm), xem trước số liệu, tải .docx |

Phân quyền: đăng nhập Google domain công ty; chỉ email trong danh sách duyệt mới bấm được nút duyệt (kiểm tra ở
backend, không chỉ ẩn nút).

## 7. Kiểm soát rủi ro

| Rủi ro | Kiểm soát |
|---|---|
| Ghi sai số lên FB.JT | Chỉ ghi ô trống hoặc ô đã được duyệt; đọc lại trước/sau; ô là công thức → không ghi; audit log có giá trị cũ để khôi phục |
| Gửi nhầm brand / gửi trùng | Email lấy từ DATA BRAND; mỗi thư có khoá chống trùng (case + loại + lần); chế độ `test` chỉ gửi về 1 hộp thư nội bộ |
| Sai hợp đồng / pháp nhân | Phải có đúng 1 hợp đồng CÒN HIỆU LỰC khớp brand + pháp nhân; không có → duyệt |
| Brand điền thiếu / sai cột | Không lập BBNT; gửi Email 1 nhắc đúng cột |
| Dữ liệu bị sửa sau khi gửi BBNT | Phát hiện số đổi → chuyển duyệt, không gửi lại tự động |
| Lộ dữ liệu | Không secret trong code/log; log CI chỉ có số đếm; lỗi đọc sheet che tên file |
| Sự cố | Tắt `RECON_EMAIL_MODE` / `RECON_SHEET_WRITE` / `RECON_SCHEDULE`; runbook hoàn tác ô, rollback code/migration |

## 8. Kết quả test (bản sao FB.JT + DB test, 29/10–05/11)

| Vòng | Nội dung | Kết quả |
|---|---|---|
| 1 | Lọc dòng (OP, cột trống, status U, gộp dòng…) | Đạt |
| 2 | Dry-run toàn bộ tab (1.735 dòng) | Đạt — phân loại khớp dự kiến; phát hiện & sửa nhiều lỗi dữ liệu |
| 3 | Lập BBNT + ghi I/K/M + Email 2 (thư test) | Đạt — 3 ca thật ghi đúng số, thư đúng bảng số / thuế / hạn, kèm BBNT |
| 4 | Brand confirm → bot ghi U | Đạt (giả lập trạng thái "đã gửi nghiệm thu" trên DB test) |
| 5 | HĐ nháp, thanh toán, Done | Đạt theo xác nhận của người phụ trách; thư nhắc thanh toán chưa quan sát được vì case giả lập không có luồng mail |
| Tổng quan | So với ô tổng trên sheet | Đã thu + Pending khớp ô Sub cột K (lệch 3đ làm tròn) |

**Lỗi tìm ra khi test và đã sửa** (có test tự động đi kèm): ngày dạng tháng/ngày bị bỏ dòng; tab đơn sót bị bỏ qua
hoặc mất đơn do trống cột Hãng; Email 1 chiếm hết lượt gửi của Email 2; bot nạp 2 lần khi chạy bằng
`python -m` khiến case DRAFT không lưu số BBNT (lỗi có từ đầu, chỉ lộ khi chạy thật trên dữ liệu thật).

Lần dry-run gần nhất: SKIP 888 · WAIT 35 · REVIEW 27 · DRAFT 3 (đa số WAIT do brand chưa điền đủ status — đúng
quy tắc).

## 9. Việc còn lại trước launch

1. Dữ liệu DATA BRAND thật: 13 dòng lỗi (thiếu pháp nhân, % không phải số, hợp đồng trùng); 3 brand chưa có hợp
   đồng còn hiệu lực; 1 group trống Bên B; 11 nhóm link order không khớp dòng FB.JT.
2. Cột "Ngày cập nhật status": xác nhận script hiện có chạy theo kiểu nào (nếu theo sửa ô thì không tự cập nhật
   khi bot ghi qua API).
3. Tạo DB production sạch (branch riêng, chạy 6 migration), không dùng DB test.
4. Thư nhắc thanh toán chưa được quan sát end-to-end trên luồng mail thật.

## 10. Kế hoạch launch (mỗi bước duyệt riêng, tắt được ngay)

| Bước | Thay đổi | Ảnh hưởng |
|---|---|---|
| L1 | Bot đọc FB.JT thật, DB production; không ghi sheet; mail test | Chỉ đọc |
| L2 | Bật ghi I/K/M/U vào FB.JT thật | Ghi số; chạy 1–2 ngày, PIC đối chiếu |
| L3 | Bật gửi mail thật cho brand | Brand nhận Email 1 / Email 2 / nhắc thanh toán |
| L4 | Bật lịch tự chạy 30 phút/lần | Vận hành tự động |

## 11. Lợi ích kỳ vọng (định tính — chưa đo)

- PIC không phải tự tính NMV / hoa hồng / thuế và soạn BBNT, mail cho từng brand.
- Nhắc brand và nhắc thanh toán đều đặn, có hạn rõ ràng, có lịch sử.
- Quản lý xem công nợ (đã thu / pending / ETA / chậm trả) theo thời gian thực.
- Mọi thay đổi số liệu đều truy vết được.

Số giờ tiết kiệm / tỷ lệ sai sót cần đo sau 1–2 tháng chạy thật.

## 12. Gợi ý câu hỏi để review bằng Claude

- "Đánh giá mức độ sẵn sàng launch của dự án này. Rủi ro lớn nhất còn lại là gì, cần làm gì trước?"
- "Quy trình ở mục 4 có lỗ hổng kiểm soát tài chính / pháp lý nào không? Điểm nào nên có người duyệt?"
- "Các quy tắc ở mục 5 có mâu thuẫn hoặc trường hợp biên nào chưa xử lý?"
- "Kế hoạch launch ở mục 10 có hợp lý không? Nên đo những chỉ số nào sau launch?"
- "Tóm tắt dự án này trong 5 gạch đầu dòng cho ban giám đốc."

Tài liệu chi tiết trong repo: `docs/ARCHITECTURE.md` (thiết kế), `docs/OPEN_QUESTIONS.md` (quyết định),
`docs/RECON_E2E_TEST_PLAN.md` (72 ca test), `docs/OPERATIONS_RUNBOOK.md` (vận hành, khôi phục),
`docs/USER_GUIDE.md` (hướng dẫn người dùng), `docs/SECURITY.md`.
