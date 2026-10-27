# Security

- **Repo đang public** (kiểm 27/10/2026). Không tạo workflow chạy worker có credential, không commit dữ liệu brand cho tới khi repo chuyển private (OPEN_QUESTIONS Q6).
- Không commit secret; secret chỉ ở GitHub Secrets / Render env / Cloudflare secrets. Không in token ra log.
- CI chỉ có `contents: read`, không có secret production; workflow deploy và workflow worker tách riêng, chỉ chạy trên `main`/lịch, không chạy cho PR từ fork.
- OAuth scope worker: `spreadsheets`, `drive.readonly`, `drive.file`, `gmail.send`, `gmail.readonly` — không thêm scope khác.
- Bot chỉ ghi FB.JT ở 3 cột I/K/M theo quy tắc ARCHITECTURE 12.1; không bao giờ ghi cột Status (U).
- Không tự xác nhận thanh toán; UNC chỉ là bằng chứng chờ kế toán xác minh.
- `audit_log` append-only (trigger chặn UPDATE/DELETE).
- Quyền duyệt kiểm ở backend (không chỉ ẩn nút trên frontend).
