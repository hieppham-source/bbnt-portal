# Open questions — Hannah Reconciliation Automation

Chỉ ghi điều **chưa chốt**. Quyết định đã chốt nằm ở `ARCHITECTURE.md` (mục sau ưu tiên mục trước, mới nhất: 16).

| # | Câu hỏi | Chặn phần nào | Mặc định đang dùng |
|---|---|---|---|
| Q1 | Template email chính thức: nghiệm thu, nhắc confirm BBNT, nhắc HĐ nháp, nhắc thanh toán + phạt | Gửi email (mọi loại) | `email_template.status = NOT_CONFIGURED` → Audit Gate trả `BLOCKED` |
| Q2 | Danh sách người được duyệt trên portal (`RECON_APPROVERS`) | API duyệt / dashboard | Không ai được duyệt (API trả 403) |
| Q3 | Hộp thư bot chính thức | Chuyển khỏi hộp thư cá nhân | Tạm dùng `hiep.pham@hannaholala.com` (đã chốt), lưu Message-ID để chuyển luồng sau |
| Q4 | Quyền chia sẻ file BBNT trên Drive (hiện "ai có link đều sửa") | Upload BBNT lên Drive | Chưa tạo link chia sẻ; đề xuất brand = Xem, nội bộ = Sửa |
| Q5 | Các case ngoại lệ đang treo (CETAPHIL, DKSH, HSVG): nhập sẵn trạng thái Giữ? | Khởi tạo dữ liệu | Nhập ON_HOLD khi seed |
| Q7 | Cách xác định "đã trong khung giờ gửi" (giờ gửi email cho phép) | Audit Gate `within_send_window` | Đề xuất 8:00–18:00 T2–T6 giờ VN — cần duyệt |
| Q8 | Ngày lễ âm lịch / nghỉ bù năm 2026–2027: nguồn chính thức + người duyệt đầu tiên | Tính hạn chính xác | Chỉ tính T2–T6 + lễ dương lịch cố định |

## Đã chốt 28/10/2026

| # | Quyết định | Hiện thực |
|---|---|---|
| Q9 | NMV tính đơn `Đã giao` và các status tương tự hoàn thành / đã xong / confirm — **cả portal và bot**. Status khác vẫn loại khỏi NMV + cảnh báo, không chặn | `DEFAULT_STATUS_ALIASES` (`calculation.py`): thêm `Hoàn thành`, `Đã hoàn thành`, `Finish(ed)`, `Completed`, `Đã xong`, `Done`, `Confirm(ed)`, `Đã xác nhận` |
| Q10 | Bỏ qua, giữ cấu hình thuế hiện tại | Không đổi code; BBNT lập từ 01/01/2027 vẫn `TAX_RULE_MISSING` → REVIEW cho tới khi `PHAP_NHAN` có dòng mới |
| Q11 | Tạm bỏ TikTok Shop cho bot | Bot chỉ FB |
| Q12 | Bot kiểm hiệu lực hợp đồng theo **ngày lập BBNT** | Worker: ngoài [Hiệu lực từ, Hiệu lực đến] của tab HOP_DONG → `CONTRACT_NOT_VALID_ON_BBNT_DATE` (REVIEW). Portal không đổi |
| Q13 | Duyệt `RECON_STATE_PROPOSAL.md`: đồng ý cả 3 ý; bot được thêm cột vào file link tổng order, không thêm cột FB.JT | Migration `0002_recon_idempotency` + test |
| — | Phạm vi Non-OP | Chốt 28/10: cột H = "NON OP" → xử lý; "OP" → bỏ; trống/khác → chờ duyệt; DATA BRAND.OP chỉ dùng để báo mâu thuẫn |
| — | Camp chưa có cột G | Chốt 28/10: chỉ xét campaign từ 08/2026; trước đó bỏ qua (`BEFORE_SCOPE`) |
| — | Dòng đã nghiệm thu tay / gộp line | Chốt 29/10: chỉ lập BBNT khi U chưa qua bước BBNT; "GỘP LINE" → chờ duyệt |
| Q14 | Chốt 30/10: giữ so sánh cột M với GMV của BBNT; lệch → chờ duyệt Đổi/Giữ | — |

## Mẫu email (30/10/2026) — Google Doc "Mẫu email nghiệm thu OM (Bot)"

EMAIL 2 (confirm số + BBNT) là thư nghiệm thu của bot. Mâu thuẫn giữa mẫu và quyết định cũ — **anh chốt 30/10/2026**:

| # | Mẫu ghi | Trước đó (ARCHITECTURE) | Chốt |
|---|---|---|---|
| Q15 | Email 1: bot **tạo luồng mới** (nhắc brand điền status link order); Email 2–4 reply luồng đó | 15.4: bot không tạo thread | **Theo mẫu**: case mới bot tạo luồng bằng Email 1; case đã có luồng gửi tay vẫn tìm theo TITLE MAIL. Email 1 gửi tại **G + 25**, **tự gửi**; nhắc lại **cần duyệt**. Bot quét file order mỗi lần chạy: **chỉ khi mọi dòng đã có status** mới lập BBNT, ghi I/K/M, gửi Email 2 |
| Q16 | Deadline = ngày gửi + 3 ngày (lịch) | 11.3: 3 ngày làm việc | **3 ngày làm việc** (bỏ T7, CN, ngày lễ đã duyệt). Email 2 đã sửa |
| Q17 | Hạn thanh toán = ngày HĐ + số ngày theo HĐ (mặc định 7); 4B không ghi phạt | 13.4: 10 ngày làm việc + phạt ước tính 0,05%/ngày | **Giữ cũ**: 10 ngày làm việc, thư nhắc có dòng phạt ước tính |
| Q18 | 3B đính kèm HĐ nháp PDF; Email 4 cần `{{THONG_TIN_TK}}` | Chưa có nguồn | **Kế toán tự gửi HĐ nháp**, bot chỉ gửi bản nhắc lại 3B. Email 4 **bỏ dòng thông tin chuyển khoản** |

Ghi chú triển khai (30/10/2026): nhắc thanh toán bắt đầu từ **ngày đến hạn** (4A, theo mẫu "từ ngày {{HAN_THANH_TOAN}}"), quá hạn dùng 4B + phạt (Q17). Bot chưa tự hiểu nội dung thư brand (xác nhận / từ chối) — chỉ báo "brand trả lời" để KAM xử lý (ARCHITECTURE 2.4-5: câu chữ xác nhận do anh định nghĩa).
