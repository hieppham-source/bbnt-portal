-- Trạng thái bền vững + idempotency cho worker (docs/RECON_STATE_PROPOSAL.md, anh Hiệp duyệt 28/10/2026).
-- Chạy trong 1 transaction; down: 0002_recon_idempotency.down.sql. Không chứa credential.
BEGIN;

ALTER TABLE recon_case ADD COLUMN review_reasons text[] NOT NULL DEFAULT '{}';
ALTER TABLE recon_case ADD COLUMN last_plan_hash text;          -- plan không đổi -> không ghi transition/audit
ALTER TABLE recon_case ADD COLUMN last_run_id uuid REFERENCES workflow_run(id);

CREATE TABLE sheet_write (                                      -- ý định ghi I/K/M trên FB.JT, idempotent
  id            bigserial PRIMARY KEY,
  case_id       uuid NOT NULL REFERENCES recon_case(id),
  sheet_row_id  bigint NOT NULL REFERENCES sheet_row(id),
  column_name   text NOT NULL CHECK (column_name IN ('nmv', 'commission', 'gmv')),
  seen_value    numeric,                                        -- giá trị lúc quét; khác lúc ghi -> CONFLICT
  target_value  numeric NOT NULL,
  status        text NOT NULL CHECK (status IN ('PLANNED', 'WRITTEN', 'VERIFIED', 'CONFLICT', 'CANCELLED')),
  approved_by   text,
  run_id        uuid REFERENCES workflow_run(id),
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz,
  UNIQUE (case_id, sheet_row_id, column_name, target_value)
);

COMMIT;
