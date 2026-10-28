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
| Q9 | Status đơn không nhận diện được: bot có chặn tự phát hành (REVIEW) không? | Audit Gate nghiệm thu | Hiện: loại khỏi NMV + cảnh báo, **không chặn** (giống portal). Đề xuất: bot REVIEW khi có ≥1 dòng status lạ |
| Q10 | Dòng thuế EZS/Sassy từ 01/01/2027 trong tab `PHAP_NHAN` | Mọi BBNT lập từ 2027 | Không đoán: `TAX_RULE_MISSING` → REVIEW |
| Q11 | TikTok Shop cho bot: template TTS, mapping TTS, sheet theo dõi TTS (FB.JT chỉ có FB) | Bot TTS | Bot chỉ chạy FB |
| Q12 | Bot có kiểm hiệu lực hợp đồng theo ngày kết thúc camp không? (portal cố ý không kiểm) | Chọn hợp đồng | Không kiểm (giống portal) |
| Q13 | Duyệt `docs/RECON_STATE_PROPOSAL.md` (ánh xạ state, migration 0002, quy tắc chạy lại) | Nối worker với DB, mọi thao tác ghi | Chưa nối |
