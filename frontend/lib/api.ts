// Gọi backend FastAPI (Render) trực tiếp từ trình duyệt, kèm Google ID token.
const API = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "").replace(/\/+$/, "")

export type Channel = string

// "auto" (mặc định, hành vi cũ) | "combine" (gộp mọi tab/camp ứng viên thành 1 BBNT) | "split" (mỗi tab 1 BBNT
// riêng — chỉ hợp lệ qua previewBbntSplit, bắt buộc kèm tab_campaign_names)
export type TabMode = "auto" | "combine" | "split"

// "brand" (tách bảng doanh thu/huỷ theo brand, commission chung) | "camp" (tách theo camp/đợt chạy, bắt buộc
// kèm camp_names) — CHỈ dùng khi nghiệm thu group/nhiều brand cùng lúc, không dùng cùng order_tab/tab_mode khác "auto"
export type BreakdownMode = "brand" | "camp"

// 1 camp = 1 tab trong 1 link order cụ thể. link_index: 0 = order_sheet_url chính, 1.. = vị trí (1-based)
// trong extra_order_sheet_urls (tên tab có thể trùng giữa 2 link nên cần cả link_index lẫn tab).
export type CampLabel = { link_index: number; tab: string; name: string }

export type BbntRequest = {
  channel: Channel
  entity: string
  brand_codes?: string[] // 1 brand, hoặc nhiều brand cùng group
  group_code?: string // hoặc cả group (chỉ 1 trong 2)
  contract_no?: string // chỉ cần khi API báo CONTRACT_AMBIGUOUS
  period_months: string[] // ["05.26"] hoặc ["07.26","08.26","09.26"]
  campaign_name: string
  order_sheet_url: string
  extra_order_sheet_urls?: string[] // link order BỔ SUNG để gộp chung 1 BBNT
  mapping_code?: string
  collected_on_behalf?: number
  consulting_fee?: number
  strip_comments?: boolean
  order_tab?: string // tab trong file order — bỏ trống để tự chọn
  tab_mode?: TabMode
  tab_campaign_names?: Record<string, string> // bắt buộc khi tab_mode === "split": {tên tab: tên chương trình riêng}
  breakdown_mode?: BreakdownMode
  camp_names?: CampLabel[] // bắt buộc khi breakdown_mode === "camp"
}

export type BrandBreakdownOut = {
  index: number
  brand_code: string | null // null khi breakdown_kind === "camp" (brand_name khi đó là tên camp)
  brand_name: string
  order_tabs: string[]
  gmv_estimated: string
  nmv: string
  cancellation_rate: string | null
  items: { brand_name: string | null; sku: string | null; product_name: string; quantity: number; revenue: string }[]
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
  campaign_name: string
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
  // "none" (không tách bảng) | "brand" | "camp" — chiều tách của brand_breakdown (xem BbntRequest.breakdown_mode;
  // "brand" cũng dùng cho link tự nhận diện "1 tab/brand" dù không chọn breakdown_mode)
  breakdown_kind: "none" | "brand" | "camp"
  brand_breakdown: BrandBreakdownOut[]
  order_line_count: number
  other_brand_line_count: number
  order_tab: string | null
  order_tabs: string[]
  // Tên các tab đã GỘP lại thành BBNT này (tab_mode === "combine" và/hoặc nhiều order_sheet_url, >= 2 tab) —
  // rỗng khi không gộp.
  order_tabs_combined: string[]
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

export type SplitPreviewOut = { results: PreviewOut[] }

/** tab_mode: "split" — mỗi tab/camp ứng viên trong file order xem trước thành 1 BBNT riêng (bắt buộc kèm
 * tab_campaign_names, khác tên nhau từng tab — số BBNT không phân biệt theo tên chương trình nên mỗi kết quả
 * tự có hậu tố -R{n}). Chỉ dùng cho xem trước — tải file gọi lại generateBbnt với order_tab + campaign_name
 * của 1 trong các kết quả trả về. */
export const previewBbntSplit = (t: Tokens, body: BbntRequest, refresh = false): Promise<SplitPreviewOut> =>
  call(t, `/api/v1/bbnt/preview-split${refresh ? "?refresh=true" : ""}`, {
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
