# Hướng dẫn sử dụng — Portal Đối soát FB

Dành cho PIC / KAM / kế toán mới. Đọc 10 phút là dùng được. Kỹ thuật (deploy, biến môi trường, DB) xem
`docs/DEPLOYMENT.md` và `docs/OPERATIONS_RUNBOOK.md`.

---

## 1. Hệ thống làm gì

Hệ thống tự động hoá đối soát kênh **FB (Non-OP)** từ lúc camp kết thúc tới lúc brand trả tiền:

```
Camp kết thúc ──25 ngày──▶ Bot kiểm tra file order ──▶ Thiếu status? ── Email 1 nhắc brand điền
                                                    └─▶ Đủ status? ── Lập BBNT + ghi số I/K/M lên FB.JT
                                                                      └─▶ Email 2: confirm số + BBNT
Brand confirm ──▶ KAM bấm "Brand đã confirm" ──▶ Bot ghi U "Đã done BBNT + Chờ KT xuất HĐ nháp"
Kế toán xuất HĐ (đổi U) ──▶ Bot nhắc confirm HĐ nháp / nhắc thanh toán khi tới hạn ──▶ U = Done trả tiền
```

Có 2 phần:

| Phần | Là gì | Ai dùng |
|---|---|---|
| **Bot** | Chạy trên GitHub (tự động 30 phút/lần trong giờ hành chính). Đọc FB.JT, file link order, file order của brand; lập BBNT, ghi số, gửi mail | Không ai thao tác trực tiếp |
| **Portal** | Trang web 3 tab: Tổng quan · Cần duyệt · Biên bản nghiệm thu | PIC, KAM, kế toán, quản lý |

**Bot không bao giờ tự đoán.** Thấy dữ liệu thiếu / lạ / mâu thuẫn → đưa vào tab **Cần duyệt** để người xử lý.

---

## 2. Đăng nhập

1. Mở link portal (quản trị cung cấp).
2. Đăng nhập bằng Google **email @hannaholala.com**. Khi Google hỏi quyền **đọc Google Sheets** → **cho phép**
   (portal đọc FB.JT và file order bằng chính quyền của bạn — bạn không xem được file thì portal cũng không).
3. Chỉ người có tên trong danh sách duyệt (`RECON_APPROVERS`) mới bấm được các nút duyệt; người khác chỉ xem.

---

## 3. Dữ liệu bạn phải giữ đúng (bot đọc từ đây)

### 3.1 FB.JT — tab `FB-2026`

| Cột | Phải đúng thế nào |
|---|---|
| **G** Ngày kết thúc camp | Ghi đủ `dd/mm/yyyy`. Trống → bot chờ (không làm gì) |
| **H** OP | `NON OP` hoặc `OP`. Trống / ghi khác → Cần duyệt |
| **F** Bên B | `Cty EZS` hoặc `HKD SASSY` |
| **M** HRV GMV | Có số > 0 mới xử lý |
| **P** Title mail | Tiêu đề luồng mail với brand. Bot reply vào luồng có tiêu đề này |
| **U** Status | Dùng đúng danh sách status có sẵn. Status lạ → Cần duyệt |

Bot **chỉ ghi** 3 ô **I (NMV), K (Commission), M (GMV)** và cột **U** (tối đa tới
"Đã done BBNT + Chờ KT xuất HĐ nháp"). Ô đã có số khác số BBNT → bot **không ghi đè**, đưa ra tab Cần duyệt.

### 3.2 FB.JT — tab `DATA BRAND` (nguồn hợp đồng + email)

Mỗi brand 1 dòng. Bot và portal lấy **brand, hợp đồng, Bên A, email** từ đây:

- **STATUS** = `CÒN HIỆU LỰC` thì hợp đồng mới được dùng.
- **SỐ HỢP ĐỒNG**: chỉ số HĐ gốc. Phụ lục ghi riêng ở **SỐ PHỤ LỤC HỢP ĐỒNG** + **NGÀY KÝ PHỤ LỤC HỢP ĐỒNG**.
- **COMMISSION (chưa bao gồm VAT)**: dạng `22%`. Không ghi kiểu `20% + 9.09%`.
- **PHÁP NHÂN B**: `Cty EZS` / `HKD SASSY`.
- **EMAIL NHẬN HÓA ĐƠN**: email brand, nhiều địa chỉ ngăn bằng dấu phẩy. Bot gửi mail tới đây.

### 3.3 File order của brand

- Brand điền trạng thái từng đơn vào **đúng cột "BRAND INPUT – Tình trạng lên đơn"** (mail của bot đã dặn).
  Status ở cột khác (Mã vận đơn, Note…) = coi như chưa điền.
- Status hợp lệ: *Đã giao / Giao thành công / Hoàn thành…*, *Hoàn hàng / Trả hàng…*, *Đã hủy / Yêu cầu hủy…*.
  Còn đơn trống hoặc status lạ (*ĐANG GIAO, CHƯA XỬ LÝ…*) → bot **chờ** và nhắc brand, chưa lập BBNT.
- Tab **"ĐƠN SÓT…"** được cộng chung với tab **ORDER**. Dòng đơn sót phải đủ cột (nhất là **GMV SAU VOUCHER**).
  File có tab lạ khác → Cần duyệt.

---

## 4. Tab **Tổng quan**

Số liệu đọc trực tiếp FB.JT. Lọc theo **Năm / Tháng / PIC**.

| Ô | Cách tính | Bấm vào xem |
|---|---|---|
| **Đã thu tiền** | Tổng K các dòng U = *Done trả tiền* | Theo group brand: tháng thu gần nhất, CMS, % |
| **Còn pending** | Tổng K các dòng đã có K nhưng chưa Done | Theo group brand |
| **Commission ETA cả năm** | Đã thu + Pending + (GMV × 22% × 80%) cho camp chưa nghiệm thu | Theo group brand |
| **Cancel trung bình** | 1 − NMV / GMV (theo trọng số GMV) | Group brand cancel cao nhất |

Bên dưới:
- **Group brand chậm trả**: dòng đã xuất HĐ (cột *Oanh note* ghi `HĐ: số (dd/mm/yyyy)`), chưa Done, quá
  ngày HĐ + 10 ngày làm việc. Trễ > 30 ngày tô đỏ.
- **Theo tháng**: đã thu / pending / chưa nghiệm thu / cancel từng tháng.

---

## 5. Tab **Cần duyệt** (việc hằng ngày)

Mở tab này mỗi sáng. Làm từ trên xuống:

### 5.1 Ô FB.JT chờ duyệt
Ô I/K/M trên FB.JT đang khác số bot tính từ file order.
- **Đổi** → lần chạy sau bot ghi số của BBNT vào ô (vẫn kiểm tra lại ô trước khi ghi).
- **Giữ** → giữ số đang có trên FB.JT.

### 5.2 Brand trả lời chưa xem
Thư brand gửi vào luồng mail. Bot **không đọc nội dung** → mở Gmail đọc, xử lý, bấm **Đã xem**.

### 5.3 Thư chờ duyệt
Thư nhắc bot soạn sẵn (nhắc điền status lần 2+, nhắc ký BBNT, nhắc confirm HĐ nháp). Đọc nội dung →
**Gửi** (bot gửi ở lần chạy sau, trong giờ hành chính) hoặc **Bỏ qua**.
Email 1 lần đầu, Email 2 nghiệm thu và nhắc thanh toán bot **tự gửi**, không qua đây.

### 5.4 Case đối soát
Lọc theo trạng thái, tìm theo brand / campaign / PIC / số BBNT.

| Trạng thái | Nghĩa | Bạn cần làm |
|---|---|---|
| Chờ G + 25 / chờ brand điền status | Chưa đủ 25 ngày sau camp, hoặc brand chưa điền đủ status | Thường không cần; nhắc brand nếu lâu |
| **Cần xử lý** | Bot dừng vì lý do ghi ở cột *Lý do* (tiếng Việt) | Sửa dữ liệu theo lý do (mục 6), lần chạy sau bot tự làm tiếp |
| Đã lập BBNT | Có số BBNT, chờ ghi số lên FB.JT | Không |
| Đã ghi số FB.JT | I/K/M đã khớp BBNT, chờ gửi Email 2 | Không |
| **Đã gửi nghiệm thu** | Email 2 đã gửi brand | Khi brand confirm số + BBNT trong mail → bấm **Brand đã confirm** |
| Chờ confirm HĐ nháp | Kế toán đã gửi HĐ nháp | Theo dõi; duyệt thư nhắc nếu có |
| Chờ thanh toán | Đã xuất HĐ chính thức | Bot tự nhắc khi tới hạn / quá hạn |
| Đang giữ | Có người bấm **Giữ case** | **Tiếp tục** khi xử lý xong |
| Xong | U = Done | — |

Nút khác:
- **Chọn loopmail**: bot tìm thấy nhiều luồng mail khớp tiêu đề → chọn đúng luồng.
- **Giữ case**: tạm dừng mọi việc bot làm với case (ghi lý do). **Tiếp tục** để chạy lại.

---

## 6. Lý do thường gặp ở "Cần xử lý" và cách sửa

| Lý do (portal) | Sửa ở đâu |
|---|---|
| Thiếu ngày kết thúc camp (cột G) | Điền cột G trên FB.JT |
| Cột H (OP) trống / ghi lạ / lệch DATA BRAND | Sửa cột H hoặc cột OP trong DATA BRAND |
| Cột Bên B trống hoặc lạ | Điền cột F |
| Status U lạ | Đổi U về đúng danh sách status |
| Không có đúng 1 hợp đồng CÒN HIỆU LỰC | DATA BRAND: kiểm tra STATUS, số HĐ, pháp nhân B. Nhiều brand chung 1 link nhưng khác HĐ → tách link hoặc lập BBNT tay |
| Link order gồm brand không khớp các dòng FB.JT | Link dùng chung nhiều brand nhưng FB.JT thiếu dòng / dòng không có doanh thu → thêm dòng hoặc sửa file link tổng |
| Brand chưa có trong DATA BRAND | Thêm dòng brand vào DATA BRAND |
| File order có nhiều tab đơn hàng | Ẩn/xoá tab thừa (giữ ORDER + ĐƠN SÓT…) |
| File order có dòng lỗi dữ liệu | Thường thiếu GMV SAU VOUCHER / số lượng / mã đơn → nhờ brand điền |
| Không mở được file order | Kiểm tra quyền share file order cho hộp thư bot |
| Số trên FB.JT khác số BBNT | Duyệt ô ở mục 5.1 |
| Số liệu đổi sau khi đã gửi BBNT | File order bị sửa sau khi lập BBNT → kiểm tra với brand, xử lý tay |
| DATA BRAND chưa có email nhận thư | Điền EMAIL NHẬN HÓA ĐƠN |

Sửa xong **không cần bấm gì** — lần chạy bot sau (≤ 30 phút, giờ hành chính) tự đọc lại.

---

## 7. Tab **Biên bản nghiệm thu** (lập BBNT tay)

Dùng khi cần lập BBNT ngoài bot (case bot đưa ra Cần duyệt, lập lại, kiểm tra số):

1. Dán **link file order** (Google Sheet).
2. Chọn **pháp nhân** (EZS / SASSY).
3. **Phạm vi**: *Theo brand* → gõ vài ký tự vào ô tìm, chọn brand (chọn nhiều được, bấm ✕ để bỏ);
   hoặc *Cả group*.
4. Chọn **số hợp đồng**, điền tên chương trình nếu cần.
5. **Xem trước** → kiểm tra số liệu, cảnh báo → **Tạo BBNT** để tải file .docx.

Chỉ kênh FB. Hợp đồng, Bên A lấy từ DATA BRAND. Nút **Kiểm tra dữ liệu master** liệt kê dòng DATA BRAND lỗi.

---

## 8. Email bot gửi

| Thư | Khi nào | Tự gửi? |
|---|---|---|
| Email 1 — nhắc điền status | Camp kết thúc + 25 ngày, file order còn đơn chưa có status | Lần đầu tự gửi; nhắc lại cần duyệt |
| Email 2 — confirm số + BBNT | Đủ status, đã ghi số FB.JT | Tự gửi (reply vào luồng mail theo TITLE MAIL) |
| Nhắc ký BBNT / confirm HĐ nháp | Brand quá hạn xác nhận | Cần duyệt |
| Nhắc thanh toán | Tới hạn / quá hạn (ngày HĐ + 10 ngày làm việc), quá hạn kèm phạt ước tính 0,05%/ngày | Tự gửi |

- Thư xưng **EZS**, CC hộp thư nội bộ, chỉ gửi **T2–T6, 8h–18h**, tối đa 5 thư / lần chạy.
- Hạn brand xác nhận = ngày gửi + **3 ngày làm việc**.
- Thư có tiêu đề `[TEST]` là bản thử, chỉ tới hộp thư nội bộ, không tới brand.

---

## 9. Câu hỏi hay gặp

**Bot ghi sai số lên FB.JT thì sao?** Mỗi ô bot ghi đều lưu giá trị cũ. Báo quản trị để khôi phục
(xem `OPERATIONS_RUNBOOK.md` → "Hoàn tác ô I/K/M"). Đồng thời **Giữ case** để bot dừng.

**Muốn bot ngừng hẳn với 1 brand?** Bấm **Giữ case**. Ngừng toàn bộ: báo quản trị (dừng khẩn cấp trong runbook).

**Sửa FB.JT rồi mà portal chưa đổi?** Bot chạy 30 phút/lần trong giờ hành chính. Tab Tổng quan đọc FB.JT
ngay khi bấm **Tải lại**.

**Brand trả lời trong mail nhưng case không đổi?** Bot không đọc nội dung thư. KAM đọc và bấm
**Brand đã confirm** (nếu brand đồng ý số + BBNT) hoặc xử lý tay.

**Cột "Ngày cập nhật status"** do script có sẵn trên FB.JT điền, bot không ghi.

---

## 10. Liên hệ

Lỗi portal / bot, xin quyền duyệt, thêm người dùng: liên hệ quản trị (anh Hiệp).
