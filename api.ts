// Gọi backend FastAPI (Render) trực tiếp từ trình duyệt, kèm Google ID token.
const API = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "").replace(/\/+$/, "")

export type Channel = string

export type BbntRequest = {
  channel: Channel
  entity: string
  brand_codes?: string[] // 1 brand, hoặc nhiều brand cùng group
  group_code?: string // hoặc cả group (chỉ 1 trong 2)
  contract_no?: string // chỉ cần khi API báo CONTRACT_AMBIGUOUS
  period_months: string[] // ["05.26"] hoặc ["07.26","08.26","09.26"]
  campaign_name: string
  order_sheet_url: string
  mapping_code?: string
  collected_on_behalf?: number
  consulting_fee?: number
  strip_comments?: boolean
  order_tab?: string // tab trong file order — bỏ trống để tự chọn
}

export type MasterOut = {
  loaded_at: string | null
  channels: string[]
  entities: { code: string; number_code: string; name: string; tax_today: string | null }[]
  brands: { channel: string; code: string; name: string; group_code: string | null; group_name: string | null }[]
  groups: { channel: string; code: string; name: string; brand_codes: string[] }[]
  contracts: {
    contract_no: string
    entity: string
    channel: string | null
    brand_code: string | null
    group_code: string | null
    model: string
    commission_rate: string
    valid_from: string
    valid_to: string | null
  }[]
  mappings: { code: string; channel: string; sheet_name: string | null; is_default: boolean }[]
  error_count: number
  warning_count: number
}

export type MasterCheckOut = {
  loaded_at: string | null
  ok: boolean
  counts: Record<string, number>
  issues: { tab: string; row: number; severity: "ERROR" | "WARNING"; message: string }[]
}

export type RowIssue = {
  row_number: number
  code: string
  severity: string
  message: string
  order_code?: string | null
  amount?: string | null
}

export type PreviewOut = {
  bbnt_number: string
  file_name: string
  issue_date: string
  period_label: string
  program_name: string
  scope_code: string
  brands: string[]
  mapping_code: string
  contract: {
    contract_no: string
    scope: string
    channel: string | null
    model: string
    commission_rate: string
    valid_from: string
    valid_to: string | null
    signed_on: string
    signed_on_source: string
    party_a_name: string
    master_row: number
  }
  figures: {
    gmv_estimated: string
    nmv: string
    cancellation_rate: string | null
    commission_rate: string
    commission_amount: string
    tax_mode: "VAT_ADD" | "TAX_DEDUCT" | "NONE"
    tax_rate: string
    tax_label: string
    vat_amount: string
    tax_deduction_amount: string
    collected_on_behalf: string
    consulting_fee: string
    total_payable: string
    total_payable_words: string
  }
  items: { brand_name: string | null; sku: string | null; product_name: string; quantity: number; revenue: string }[]
  order_line_count: number
  other_brand_line_count: number
  order_tab: string | null
  order_tabs: string[]
  order_file_kind: "google_sheet" | "xlsx"
  order_header_row: number
  warning_count: number
  warnings: RowIssue[]
}

/** idToken: xác thực với backend. accessToken: backend đọc Google Sheet bằng quyền của người dùng. */
export type Tokens = { idToken: string; accessToken?: string }

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public code?: string,
    public details?: unknown,
  ) {
    super(message)
  }
}

async function call(t: Tokens, path: string, init: RequestInit = {}): Promise<Response> {
  if (!API) throw new ApiError("Thiếu NEXT_PUBLIC_API_BASE_URL", 0, "CONFIG")
  let res: Response
  try {
    res = await fetch(`${API}${path}`, {
      ...init,
      headers: {
        Authorization: `Bearer ${t.idToken}`,
        ...(t.accessToken ? { "X-Google-Access-Token": t.accessToken } : {}),
        "Content-Type": "application/json",
        ...init.headers,
      },
    })
  } catch {
    throw new ApiError("Không kết nối được máy chủ (có thể đang khởi động, thử lại sau 1 phút)", 0, "NETWORK")
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    // Lỗi đăng nhập nằm trong "detail"; lỗi nghiệp vụ ở ngoài; lỗi validate của FastAPI là mảng "detail"
    const err = Array.isArray(body.detail)
      ? { code: "INVALID_INPUT", message: body.detail.map((d: { msg: string }) => d.msg).join("; ") }
      : (body.detail ?? body)
    throw new ApiError(err.message ?? `Lỗi ${res.status}`, res.status, err.code, body.details)
  }
  return res
}

export const getMaster = (t: Tokens, refresh = false): Promise<MasterOut> =>
  call(t, `/api/v1/master${refresh ? "?refresh=true" : ""}`).then((r) => r.json())

export const checkMaster = (t: Tokens): Promise<MasterCheckOut> =>
  call(t, "/api/v1/master/check").then((r) => r.json())

export const previewBbnt = (t: Tokens, body: BbntRequest, refresh = false): Promise<PreviewOut> =>
  call(t, `/api/v1/bbnt/preview${refresh ? "?refresh=true" : ""}`, {
    method: "POST",
    body: JSON.stringify(body),
  }).then((r) => r.json())

/** Tạo và tải .docx ngay — server không lưu, không có link tải lại. Trả về số BBNT. */
export async function generateBbnt(t: Tokens, body: BbntRequest): Promise<string> {
  const res = await call(t, "/api/v1/bbnt/generate", { method: "POST", body: JSON.stringify(body) })
  const cd = res.headers.get("Content-Disposition") ?? ""
  const name = decodeURIComponent(/filename\*=UTF-8''([^;]+)/.exec(cd)?.[1] ?? "BBNT.docx")
  const url = URL.createObjectURL(await res.blob())
  const a = Object.assign(document.createElement("a"), { href: url, download: name })
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
  return decodeURIComponent(res.headers.get("X-BBNT-Number") ?? "")
}
