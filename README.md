BBNT Generator — Hannah Olala
Tạo Biên bản nghiệm thu (BBNT) từ file order. Không database, không lưu BBNT:
thông tin hợp đồng nằm ở Google Sheet master, người làm dán link order → tải file .docx.
```
bbnt-portal/
├── backend/     FastAPI (Render Free) — đọc Sheet master + file order, tính, điền template Word
├── frontend/    Next.js + Auth.js (Cloudflare Workers Free, adapter @opennextjs/cloudflare) — đăng nhập Google
│                @hannaholala.com, form tạo BBNT
├── .github/workflows/backend-ci.yml
└── render.yaml  Render Blueprint
```
Chạy thử trên máy (không cần Google)
```bash
# Terminal 1 — backend, master đọc từ file mẫu
cd backend
pip install -r requirements-dev.txt
APP_ENV=development DEV_AUTH_HEADER=true MASTER_FILE=deploy/MASTER_BBNT_TEMPLATE.xlsx \
  CORS_ALLOWED_ORIGINS=http://localhost:3000 \
  uvicorn app.main:create_app --factory --port 8000
pytest -q                       # toàn bộ test

# Tạo 1 BBNT từ dòng lệnh, dùng file order .xlsx
python -m app.cli generate --master-file deploy/MASTER_BBNT_TEMPLATE.xlsx --order-file order.xlsx \
  --entity EZS --channel FB --brand ORGANICA --suffix "BDAY 05" --contract HD-001 --campaign BDAY --out ./out
```
```bash
# Terminal 2 — frontend (cần OAuth Client ID thật để đăng nhập)
cd frontend
cp .env.example .env.local      # điền AUTH_*, NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
npm install
npm run dev                     # http://localhost:3000
```
Deploy frontend lên Cloudflare Workers: `cd frontend && npm run cf:deploy` (xem `wrangler.jsonc` +
`open-next.config.ts`, cần đăng nhập `npx wrangler login` trước và set secrets — xem `.env.example`).
Hướng dẫn deploy đầy đủ: tài liệu "Hướng dẫn Deploy BBNT Generator Portal".
