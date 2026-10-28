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
| Q13 | Duyệt `docs/RECON_STATE_PROPOSAL.md` (ánh xạ state, migration 0002, quy tắc chạy lại) | Nối worker với DB, mọi thao tác ghi | Chưa nối |

## Đã chốt 28/10/2026

| # | Quyết định | Hiện thực |
|---|---|---|
| Q9 | NMV tính đơn `Đã giao` và các status tương tự hoàn thành / đã xong / confirm — **cả portal và bot**. Status khác vẫn loại khỏi NMV + cảnh báo, không chặn | `DEFAULT_STATUS_ALIASES` (`calculation.py`): thêm `Hoàn thành`, `Đã hoàn thành`, `Finish(ed)`, `Completed`, `Đã xong`, `Done`, `Confirm(ed)`, `Đã xác nhận` |
| Q10 | Bỏ qua, giữ cấu hình thuế hiện tại | Không đổi code; BBNT lập từ 01/01/2027 vẫn `TAX_RULE_MISSING` → REVIEW cho tới khi `PHAP_NHAN` có dòng mới |
| Q11 | Tạm bỏ TikTok Shop cho bot | Bot chỉ FB |
| Q12 | Bot kiểm hiệu lực hợp đồng theo **ngày lập BBNT** | Worker: ngoài [Hiệu lực từ, Hiệu lực đến] của tab HOP_DONG → `CONTRACT_NOT_VALID_ON_BBNT_DATE` (REVIEW). Portal không đổi |

