# Đề xuất: trạng thái bền vững + idempotency cho worker (CHỜ DUYỆT)

Trạng thái: **đề xuất**. Chưa có migration mới, chưa nối worker với DB, chưa có thao tác ghi thật nào. Dựa trên
schema đã merge `backend/migrations/0001_recon_init.up.sql` và `app/recon/db.py`.

## 1. Định danh

| Đối tượng | Khoá ổn định | Đã có |
|---|---|---|
| Case | `recon_case.order_sheet_id` (spreadsheet id của link order; 1 link = 1 case) | UNIQUE trong 0001; worker dry-run đã nhóm theo id (PR #5) |
| Dòng FB.JT | `sheet_row (tab, brand_key, campaign_key)` — khoá tự nhiên chuẩn hoá (`fbjt.row_key`) | UNIQUE trong 0001 |
| BBNT | `recon_case.bbnt_number` + `bbnt_issued_at` (ghi cùng lúc, CHECK cả hai cùng có) | UNIQUE + CHECK trong 0001 |
| Email | `email_outbox.idempotency_key = case_id:kind:cycle_no` | UNIQUE trong 0001 |
| Lần chạy | `workflow_run.id` | 0001 |

Không dùng số dòng Sheet hay chuỗi URL làm khoá.

## 2. Ánh xạ kết quả dry-run → trạng thái

| Plan | Có case? | State | Ghi chú |
|---|---|---|---|
| SKIP (không/nhiều link, link sai) | Không (chưa có `order_sheet_id`) | — | Chỉ vào báo cáo `workflow_run`; KAM sửa NON OP {YY} |
| REVIEW dòng FB.JT lỗi/trùng | Không | — | Như trên |
| WAIT | Có | `WAITING` | Tạo case lần đầu |
| REVIEW (case) | Có | `NEEDS_REVIEW` + `review_reasons` | |
| DRAFT | Có | `BBNT_DRAFTED` | Lần đầu: lưu `bbnt_number`, `bbnt_issued_at`, `contract_no`, `figures` trong **cùng transaction** với transition |

## 3. Bổ sung đề xuất (migration `0002`, chưa viết)

```sql
ALTER TABLE recon_case ADD COLUMN review_reasons text[] NOT NULL DEFAULT '{}';
ALTER TABLE recon_case ADD COLUMN last_plan_hash text;          -- sha256 của plan đã chuẩn hoá
ALTER TABLE recon_case ADD COLUMN last_run_id uuid REFERENCES workflow_run(id);

CREATE TABLE sheet_write (                                      -- ý định ghi I/K/M, idempotent
  id            bigserial PRIMARY KEY,
  case_id       uuid NOT NULL REFERENCES recon_case(id),
  sheet_row_id  bigint NOT NULL REFERENCES sheet_row(id),
  column_name   text NOT NULL CHECK (column_name IN ('nmv','commission','gmv')),
  seen_value    numeric,                                        -- giá trị lúc quét
  target_value  numeric NOT NULL,
  status        text NOT NULL CHECK (status IN ('PLANNED','WRITTEN','VERIFIED','CONFLICT','CANCELLED')),
  approved_by   text, run_id uuid, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz,
  UNIQUE (case_id, sheet_row_id, column_name, target_value)
);
```

## 4. Quy tắc idempotency

1. **Một worker một lúc**: GitHub Actions `concurrency` + `pg_try_advisory_lock` đầu mỗi lần chạy; không lấy được lock → thoát, không làm gì.
2. **Chạy lại cùng case**: tính plan, so `last_plan_hash` và state; giống → không ghi transition/audit (chỉ cập nhật `last_run_id`). Khác → 1 transition có lý do.
3. **Chạy lại ở tháng khác**: worker truyền `issued_at = recon_case.bbnt_issued_at` cho `plan_cases(..., issued_at=...)` → cùng số BBNT (đã có test). Số liệu khác số đã lưu → `NEEDS_REVIEW` (`FIGURES_CHANGED_AFTER_ISSUE`); **không** tự tạo bản sửa `-R1` — chỉ người duyệt.
4. **Ghi Sheet** (bước D): `PLANNED` → đọc lại ô, khác `seen_value` → `CONFLICT` (REVIEW) → ghi `RAW` đúng 1 ô → đọc lại → `VERIFIED`; mỗi bước 1 dòng `audit_log` (before/after). Chạy lại: hàng `VERIFIED` cùng target → bỏ qua.
5. **Email**: lưu outbox `QUEUED` + Message-ID tự sinh **trước** khi gửi; trước khi gửi lại tìm Sent theo `rfc822msgid:` → có thì đánh `SENT`, không gửi lần 2. Nghiệm thu chỉ `cycle_no = 0`.
6. **Optimistic lock** (`state_version`) như `db.py`; `StaleCase` → đọc lại ở lần chạy sau.

## 5. Cờ bật/tắt (mặc định an toàn)

`RECON_SHEET_WRITE=off`, `RECON_SEND_MODE=off`. Worker từ chối chạy `run-once` khi thiếu `DATABASE_URL` hoặc token bot; `dry-run` không cần cả hai.

## 6. Cần anh duyệt

1. Bảng ánh xạ mục 2 (đặc biệt: SKIP không tạo case).
2. Migration 0002 (mục 3).
3. Quy tắc 4.3: số liệu đổi sau khi đã lập BBNT → luôn REVIEW, không tự sửa.
