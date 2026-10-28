-- Hannah Reconciliation Automation — schema khởi tạo (docs/ARCHITECTURE.md mục 5, chỉnh theo mục 10–16).
-- Chạy trong 1 transaction; down: 0001_recon_init.down.sql. Không chứa credential.
BEGIN;

CREATE TABLE recon_case (                       -- 1 link order = 1 BBNT = 1 loopmail
  id               uuid PRIMARY KEY,
  order_sheet_id   text NOT NULL UNIQUE,        -- spreadsheet id của link order (NON OP {YY} cột F)
  order_sheet_url  text NOT NULL,
  channel          text NOT NULL DEFAULT 'FB',
  entity_code      text,
  group_code       text,
  brand_codes      text[] NOT NULL,
  campaign         text NOT NULL,
  camp_end         date,
  state            text NOT NULL,
  held_from        text,                         -- trạng thái trước khi Giữ (resume)
  hold_reason      text,
  state_version    integer NOT NULL DEFAULT 0,  -- optimistic lock
  pic              text,
  contract_no      text,
  bbnt_number      text UNIQUE,
  bbnt_issued_at   timestamptz,                  -- giữ số BBNT ổn định khi tạo lại
  bbnt_drive_file_id text,
  bbnt_sha256      text,
  figures          jsonb,
  gmail_thread_id  text,
  loopmail_rfc822_id text,                       -- Message-ID gốc, để nối luồng khi đổi hộp thư gửi
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  CHECK ((bbnt_number IS NULL) = (bbnt_issued_at IS NULL))
);

CREATE TABLE sheet_row (                        -- dòng FB.JT; khoá tự nhiên vì không được thêm cột ID
  id               bigserial PRIMARY KEY,
  tab              text NOT NULL,
  brand_key        text NOT NULL,
  campaign_key     text NOT NULL,
  case_id          uuid REFERENCES recon_case(id),
  last_row_number  integer,
  last_values      jsonb,
  last_hash        text,
  last_seen_at     timestamptz,
  UNIQUE (tab, brand_key, campaign_key)
);

CREATE TABLE sheet_change_event (
  id          bigserial PRIMARY KEY,
  sheet_row_id bigint NOT NULL REFERENCES sheet_row(id),
  column_name text NOT NULL,
  old_value   text,
  new_value   text,
  detected_at timestamptz NOT NULL DEFAULT now(),
  run_id      uuid
);

CREATE TABLE cell_review (                      -- I/K/M trên FB.JT khác số BBNT -> KAM chọn Đổi / Giữ
  id           bigserial PRIMARY KEY,
  case_id      uuid NOT NULL REFERENCES recon_case(id),
  sheet_row_id bigint NOT NULL REFERENCES sheet_row(id),
  column_name  text NOT NULL CHECK (column_name IN ('nmv', 'commission', 'gmv')),
  current_value numeric,
  bbnt_value   numeric NOT NULL,
  decision     text CHECK (decision IN ('CHANGE', 'KEEP')),
  decided_by   text,
  decided_at   timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX cell_review_open ON cell_review (sheet_row_id, column_name) WHERE decision IS NULL;

CREATE TABLE state_transition (
  id          bigserial PRIMARY KEY,
  case_id     uuid NOT NULL REFERENCES recon_case(id),
  from_state  text,
  to_state    text NOT NULL,
  trigger     text NOT NULL CHECK (trigger IN ('SHEET', 'EMAIL', 'USER', 'WORKER')),
  actor       text NOT NULL,
  reason      text,
  evidence_id uuid,
  at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE email_template (
  code        text NOT NULL,
  version     integer NOT NULL,
  status      text NOT NULL DEFAULT 'NOT_CONFIGURED' CHECK (status IN ('NOT_CONFIGURED', 'APPROVED', 'RETIRED')),
  subject     text,
  body_html   text,
  approved_by text,
  approved_at timestamptz,
  PRIMARY KEY (code, version),
  CHECK (status <> 'APPROVED' OR (subject IS NOT NULL AND body_html IS NOT NULL AND approved_by IS NOT NULL))
);

CREATE TABLE email_outbox (
  id               uuid PRIMARY KEY,
  case_id          uuid NOT NULL REFERENCES recon_case(id),
  kind             text NOT NULL,
  cycle_no         integer NOT NULL,
  idempotency_key  text NOT NULL UNIQUE,         -- case_id:kind:cycle_no
  template_code    text NOT NULL,
  template_version integer NOT NULL,
  to_addrs         text[] NOT NULL,
  cc_addrs         text[] NOT NULL DEFAULT '{}',
  subject          text NOT NULL,
  body_html        text NOT NULL,
  reply_to_thread_id text,
  audit_reasons    text[] NOT NULL DEFAULT '{}',
  status           text NOT NULL CHECK (status IN ('PENDING_REVIEW', 'QUEUED', 'SENDING', 'SENT', 'FAILED',
                                                   'CANCELLED')),
  approved_by      text,
  approved_at      timestamptz,
  attempts         integer NOT NULL DEFAULT 0,
  last_error       text,
  created_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (case_id, kind, cycle_no),
  FOREIGN KEY (template_code, template_version) REFERENCES email_template(code, version)
);

CREATE TABLE email_message (                    -- mọi thư gửi/nhận trong loopmail
  gmail_message_id  text PRIMARY KEY,
  gmail_thread_id   text NOT NULL,
  rfc822_message_id text,
  mailbox           text NOT NULL,
  case_id           uuid REFERENCES recon_case(id),
  direction         text NOT NULL CHECK (direction IN ('OUT', 'IN')),
  from_addr         text,
  sent_at           timestamptz,
  is_auto_reply     boolean NOT NULL DEFAULT false,
  classification    text CHECK (classification IN ('CONFIRM', 'REJECT', 'QUESTION', 'INFO', 'OOO', 'BOUNCE',
                                                   'UNKNOWN')),
  outbox_id         uuid REFERENCES email_outbox(id)
);

CREATE TABLE payment_evidence (                 -- UNC / BBNT ký / xác nhận: luôn cần người xác minh
  id               uuid PRIMARY KEY,
  case_id          uuid NOT NULL REFERENCES recon_case(id),
  kind             text NOT NULL CHECK (kind IN ('FIGURES_CONFIRM', 'SIGNED_BBNT', 'INVOICE_DRAFT_CONFIRM',
                                                 'PAYMENT_UNC')),
  gmail_message_id text REFERENCES email_message(gmail_message_id),
  file_name        text,
  file_sha256      text,
  extracted        jsonb,
  review_status    text NOT NULL DEFAULT 'PENDING' CHECK (review_status IN ('PENDING', 'ACCEPTED', 'REJECTED')),
  reviewed_by      text,
  reviewed_at      timestamptz
);

CREATE TABLE holiday (                          -- ngày nghỉ; AI đề xuất -> người duyệt mới có hiệu lực
  day         date PRIMARY KEY,
  name        text NOT NULL,
  source_url  text NOT NULL,
  status      text NOT NULL DEFAULT 'PROPOSED' CHECK (status IN ('PROPOSED', 'APPROVED', 'REJECTED')),
  proposed_by text NOT NULL,
  approved_by text,
  approved_at timestamptz,
  CHECK (status <> 'APPROVED' OR approved_by IS NOT NULL)
);

CREATE TABLE workflow_run (
  id           uuid PRIMARY KEY,
  mode         text NOT NULL CHECK (mode IN ('DRY_RUN', 'RUN_ONCE', 'BACKFILL')),
  started_at   timestamptz NOT NULL DEFAULT now(),
  finished_at  timestamptz,
  status       text NOT NULL DEFAULT 'RUNNING' CHECK (status IN ('RUNNING', 'OK', 'FAILED', 'ABORTED')),
  rows_scanned integer NOT NULL DEFAULT 0,
  events       integer NOT NULL DEFAULT 0,
  emails_sent  integer NOT NULL DEFAULT 0,
  error        text
);

CREATE TABLE gmail_sync_state (
  mailbox    text PRIMARY KEY,
  history_id text NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE audit_log (                        -- append-only, chặn bằng trigger
  id        bigserial PRIMARY KEY,
  at        timestamptz NOT NULL DEFAULT now(),
  actor     text NOT NULL,
  action    text NOT NULL,
  entity    text,
  entity_id text,
  before    jsonb,
  after     jsonb,
  run_id    uuid
);

CREATE FUNCTION audit_log_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'audit_log is append-only';
END $$;
CREATE TRIGGER audit_log_no_change BEFORE UPDATE OR DELETE ON audit_log
  FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();

COMMIT;
