"use client"

import { useCallback, useEffect, useMemo, useState } from "react"

import {
  ApiError,
  type BbntRequest,
  generateBbnt,
  getMaster,
  type MasterOut,
  previewBbnt,
  type PreviewOut,
  type RowIssue,
} from "@/lib/api"
import { monthToPeriod, pct, periodToMonth, previousMonth, vnd, vnDate } from "@/lib/format"

import { handleAuthError, useIdToken } from "./useIdToken"

type Mode = "brand" | "group"
type Shown = { message: string; code?: string; details?: unknown }
type ContractChoice = { contract_no: string; scope?: string; channel?: string; valid_from?: string; valid_to?: string | null }

const SHEET_RE = /^https:\/\/docs\.google\.com\/spreadsheets\/d\/[A-Za-z0-9_-]{20,}/

export default function BbntForm() {
  const token = useIdToken()

  // ---------------------------------------------------------------- master
  const [master, setMaster] = useState<MasterOut | null>(null)
  const [masterErr, setMasterErr] = useState<Shown | null>(null)
  const [loadingMaster, setLoadingMaster] = useState(false)

  // ---------------------------------------------------------------- form
  const [channel, setChannel] = useState("")
  const [entity, setEntity] = useState("")
  const [mode, setMode] = useState<Mode>("brand")
  const [brandCodes, setBrandCodes] = useState<string[]>([])
  const [groupCode, setGroupCode] = useState("")
  const [contractNo, setContractNo] = useState("")
  const [months, setMonths] = useState<string[]>(() => [monthToPeriod(previousMonth())])
  const [monthInput, setMonthInput] = useState(previousMonth())
  const [campaign, setCampaign] = useState("")
  const [orderUrl, setOrderUrl] = useState("")
  const [mappingCode, setMappingCode] = useState("")
  const [collected, setCollected] = useState("")
  const [consulting, setConsulting] = useState("")
  const [stripComments, setStripComments] = useState(false)

  // ---------------------------------------------------------------- result
  const [preview, setPreview] = useState<PreviewOut | null>(null)
  const [error, setError] = useState<Shown | null>(null)
  const [busy, setBusy] = useState<"preview" | "generate" | null>(null)
  const [downloaded, setDownloaded] = useState<string | null>(null)

  const loadMaster = useCallback(
    async (refresh = false) => {
      if (!token) return
      setLoadingMaster(true)
      try {
        const m = await getMaster(token, refresh)
        setMaster(m)
        setMasterErr(null)
        setChannel((c) => c || m.channels[0] || "")
        setEntity((e) => e || m.entities[0]?.code || "")
      } catch (e) {
        const err = e as ApiError
        if (!handleAuthError(err.status)) setMasterErr({ message: err.message, code: err.code })
      } finally {
        setLoadingMaster(false)
      }
    },
    [token],
  )

  useEffect(() => {
    void loadMaster()
  }, [loadMaster])

  // Mọi thay đổi trên form -> kết quả xem trước cũ không còn đúng
  const touch = <T,>(set: (v: T) => void) => (v: T) => {
    set(v)
    setPreview(null)
    setError(null)
    setDownloaded(null)
  }

  const brandsInChannel = useMemo(
    () => (master?.brands ?? []).filter((b) => b.channel === channel),
    [master, channel],
  )
  const groupsInChannel = useMemo(
    () => (master?.groups ?? []).filter((g) => g.channel === channel),
    [master, channel],
  )
  const mappingsInChannel = useMemo(
    () => (master?.mappings ?? []).filter((m) => m.channel === channel),
    [master, channel],
  )

  // Group của lựa chọn hiện tại (để lọc hợp đồng và cảnh báo khác group)
  const selectedBrands = brandsInChannel.filter((b) => brandCodes.includes(b.code))
  const brandGroups = new Set(selectedBrands.map((b) => b.group_code))
  const mixedGroups = mode === "brand" && selectedBrands.length > 1 && (brandGroups.size > 1 || brandGroups.has(null))

  const contractOptions = useMemo(() => {
    if (!master) return []
    return master.contracts.filter((c) => {
      if (c.entity !== entity || (c.channel && c.channel !== channel)) return false
      if (mode === "group") return !!groupCode && c.group_code === groupCode
      if (selectedBrands.length === 1) {
        const b = selectedBrands[0]
        return c.brand_code === b.code || (!!b.group_code && c.group_code === b.group_code)
      }
      if (selectedBrands.length > 1 && !mixedGroups) return c.group_code === selectedBrands[0].group_code
      return false
    })
  }, [master, entity, channel, mode, groupCode, selectedBrands, mixedGroups])

  // Đổi kênh -> bỏ các brand/group/mapping không thuộc kênh mới
  useEffect(() => {
    setBrandCodes((codes) => codes.filter((c) => brandsInChannel.some((b) => b.code === c)))
    setGroupCode((g) => (groupsInChannel.some((x) => x.code === g) ? g : ""))
    setMappingCode((m) => (mappingsInChannel.some((x) => x.code === m) ? m : ""))
  }, [brandsInChannel, groupsInChannel, mappingsInChannel])

  useEffect(() => {
    if (contractNo && !contractOptions.some((c) => c.contract_no === contractNo)) setContractNo("")
  }, [contractOptions, contractNo])

  // ---------------------------------------------------------------- validate + submit
  const problems: string[] = []
  if (!channel) problems.push("Chọn kênh")
  if (!entity) problems.push("Chọn pháp nhân")
  if (mode === "brand" && brandCodes.length === 0) problems.push("Chọn ít nhất 1 brand")
  if (mode === "group" && !groupCode) problems.push("Chọn group")
  if (mixedGroups) problems.push("Gộp nhiều brand chỉ được khi cùng 1 group")
  if (months.length === 0) problems.push("Chọn ít nhất 1 tháng nghiệm thu")
  if (!campaign.trim()) problems.push("Nhập tên chương trình")
  if (!SHEET_RE.test(orderUrl.trim())) problems.push("Dán link Google Sheet order (https://docs.google.com/spreadsheets/d/…)")

  function buildRequest(): BbntRequest {
    return {
      channel,
      entity,
      ...(mode === "brand" ? { brand_codes: brandCodes } : { group_code: groupCode }),
      ...(contractNo ? { contract_no: contractNo } : {}),
      ...(mappingCode ? { mapping_code: mappingCode } : {}),
      period_months: months,
      campaign_name: campaign.trim(),
      order_sheet_url: orderUrl.trim(),
      collected_on_behalf: Number(collected || 0),
      consulting_fee: Number(consulting || 0),
      strip_comments: stripComments,
    }
  }

  async function run(kind: "preview" | "generate") {
    if (!token || problems.length) return
    setBusy(kind)
    setError(null)
    try {
      const req = buildRequest()
      if (kind === "preview") {
        setPreview(await previewBbnt(token, req))
      } else {
        setDownloaded(await generateBbnt(token, req))
      }
    } catch (e) {
      const err = e as ApiError
      if (!handleAuthError(err.status)) setError({ message: err.message, code: err.code, details: err.details })
    } finally {
      setBusy(null)
    }
  }

  function addMonth() {
    if (!monthInput) return
    const p = monthToPeriod(monthInput)
    touch(setMonths)([...new Set([...months, p])].sort((a, b) => periodToMonth(a).localeCompare(periodToMonth(b))))
  }

  // ---------------------------------------------------------------- render
  if (!token) return <p className="muted">Đang kiểm tra đăng nhập…</p>

  return (
    <div className="grid">
      <section className="card">
        <div className="card-head">
          <h2>Tạo Biên bản nghiệm thu</h2>
          <button className="link" onClick={() => loadMaster(true)} disabled={loadingMaster}>
            {loadingMaster ? "Đang tải master…" : "Tải lại master"}
          </button>
        </div>

        {masterErr && (
          <p className="alert error">
            Không tải được Sheet master: {masterErr.message}
          </p>
        )}
        {master && master.error_count > 0 && (
          <p className="alert warn">
            Sheet master có {master.error_count} dòng lỗi đang bị bỏ qua — xem trang <a href="/master">Kiểm tra master</a>.
          </p>
        )}

        <div className="row">
          <label>
            Kênh
            <select value={channel} onChange={(e) => touch(setChannel)(e.target.value)}>
              {(master?.channels ?? []).map((c) => (
                <option key={c}>{c}</option>
              ))}
            </select>
          </label>
          <label>
            Pháp nhân
            <select value={entity} onChange={(e) => touch(setEntity)(e.target.value)}>
              {(master?.entities ?? []).map((e) => (
                <option key={e.code} value={e.code}>
                  {e.code} — {e.tax_today ?? "chưa có thuế hôm nay"}
                </option>
              ))}
            </select>
          </label>
        </div>

        <fieldset>
          <legend>Phạm vi</legend>
          <div className="tabs">
            <button className={mode === "brand" ? "on" : ""} onClick={() => touch(setMode)("brand")} type="button">
              Theo brand
            </button>
            <button className={mode === "group" ? "on" : ""} onClick={() => touch(setMode)("group")} type="button">
              Cả group
            </button>
          </div>
          {mode === "brand" ? (
            <div className="checks">
              {brandsInChannel.length === 0 && <span className="muted">Kênh này chưa có brand trong tab BRAND</span>}
              {brandsInChannel.map((b) => (
                <label key={b.code} className="check">
                  <input
                    type="checkbox"
                    checked={brandCodes.includes(b.code)}
                    onChange={(e) =>
                      touch(setBrandCodes)(
                        e.target.checked ? [...brandCodes, b.code] : brandCodes.filter((c) => c !== b.code),
                      )
                    }
                  />
                  {b.name} <span className="muted">{b.code}{b.group_code ? ` · ${b.group_code}` : ""}</span>
                </label>
              ))}
              {selectedBrands.length > 1 && !mixedGroups && (
                <p className="hint">Gộp {selectedBrands.length} brand → số BBNT dùng mã group {selectedBrands[0].group_code}.</p>
              )}
            </div>
          ) : (
            <select value={groupCode} onChange={(e) => touch(setGroupCode)(e.target.value)}>
              <option value="">— Chọn group —</option>
              {groupsInChannel.map((g) => (
                <option key={g.code} value={g.code}>
                  {g.name} ({g.code}) — {g.brand_codes.join(", ")}
                </option>
              ))}
            </select>
          )}
        </fieldset>

        <fieldset>
          <legend>Kỳ nghiệm thu</legend>
          <div className="row tight">
            <input type="month" value={monthInput} onChange={(e) => setMonthInput(e.target.value)} />
            <button type="button" onClick={addMonth}>
              Thêm tháng
            </button>
          </div>
          <div className="chips">
            {months.map((m) => (
              <span key={m} className="chip">
                {m}
                <button type="button" aria-label={`Bỏ ${m}`} onClick={() => touch(setMonths)(months.filter((x) => x !== m))}>
                  ×
                </button>
              </span>
            ))}
          </div>
        </fieldset>

        <label>
          Tên chương trình
          <input value={campaign} onChange={(e) => touch(setCampaign)(e.target.value)} placeholder="VD: BDAY" maxLength={100} />
        </label>

        <label>
          Link Google Sheet order
          <input
            value={orderUrl}
            onChange={(e) => touch(setOrderUrl)(e.target.value)}
            placeholder="https://docs.google.com/spreadsheets/d/…/edit#gid=0"
          />
          <span className="hint">Sheet phải share quyền Người xem cho service account. Link có #gid=… thì tool đọc đúng tab đó.</span>
        </label>

        <details>
          <summary>Tuỳ chọn thêm</summary>
          <div className="row">
            <label>
              Số hợp đồng
              <select value={contractNo} onChange={(e) => touch(setContractNo)(e.target.value)}>
                <option value="">Tự chọn theo hiệu lực</option>
                {contractOptions.map((c) => (
                  <option key={c.contract_no} value={c.contract_no}>
                    {c.contract_no} — {pct(c.commission_rate)} ({vnDate(c.valid_from)} → {vnDate(c.valid_to)})
                  </option>
                ))}
              </select>
            </label>
            {mappingsInChannel.length > 1 && (
              <label>
                Mapping file order
                <select value={mappingCode} onChange={(e) => touch(setMappingCode)(e.target.value)}>
                  <option value="">Mặc định</option>
                  {mappingsInChannel.map((m) => (
                    <option key={m.code} value={m.code}>
                      {m.code}
                      {m.is_default ? " (mặc định)" : ""}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>
          <div className="row">
            <label>
              Thu hộ (VNĐ)
              <input inputMode="numeric" value={collected} onChange={(e) => touch(setCollected)(e.target.value.replace(/\D/g, ""))} placeholder="0" />
            </label>
            <label>
              Phí tư vấn (VNĐ)
              <input inputMode="numeric" value={consulting} onChange={(e) => touch(setConsulting)(e.target.value.replace(/\D/g, ""))} placeholder="0" />
            </label>
          </div>
          <label className="check">
            <input type="checkbox" checked={stripComments} onChange={(e) => touch(setStripComments)(e.target.checked)} />
            Xoá comment hướng dẫn nội bộ khỏi file xuất
          </label>
        </details>

        {problems.length > 0 && (orderUrl || campaign) && (
          <ul className="problems">
            {problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        )}

        <div className="actions">
          <button className="primary" disabled={!!busy || problems.length > 0} onClick={() => run("preview")}>
            {busy === "preview" ? "Đang tính…" : "Xem trước"}
          </button>
          <button disabled={!!busy || problems.length > 0} onClick={() => run("generate")}>
            {busy === "generate" ? "Đang tạo file…" : "Tải BBNT (.docx)"}
          </button>
        </div>
        <p className="hint">
          Server không lưu BBNT. Cần làm lại: dán lại link order rồi tải lại (cùng tháng lập ra đúng số cũ). Cần sửa câu chữ: sửa trực tiếp trên file Word.
        </p>
      </section>

      <section className="result">
        {error && <ErrorBox error={error} onPickContract={(no) => touch(setContractNo)(no)} />}
        {downloaded && <p className="alert ok">Đã tải BBNT {downloaded}. Nhớ cất file vào thư mục Drive chung.</p>}
        {preview ? <PreviewCard p={preview} /> : !error && <p className="muted">Điền form rồi bấm Xem trước để kiểm tra số liệu trước khi tải.</p>}
      </section>
    </div>
  )
}

// =========================================================================== kết quả
function PreviewCard({ p }: { p: PreviewOut }) {
  const f = p.figures
  const byCode = p.warnings.reduce<Record<string, number>>((acc, w) => ((acc[w.code] = (acc[w.code] ?? 0) + 1), acc), {})
  return (
    <div className="card">
      <p className="muted">Số BBNT</p>
      <h2 className="number">{p.bbnt_number}</h2>
      <p>
        {p.program_name} · ngày lập {vnDate(p.issue_date)}
      </p>

      <table className="kv">
        <tbody>
          <tr>
            <th>Hợp đồng</th>
            <td>
              {p.contract.contract_no} <span className="muted">(HOP_DONG dòng {p.contract.master_row})</span>
            </td>
          </tr>
          <tr>
            <th>Bên A</th>
            <td>{p.contract.party_a_name}</td>
          </tr>
          <tr>
            <th>Brand</th>
            <td>{p.brands.join(", ")}</td>
          </tr>
        </tbody>
      </table>

      <table className="figures">
        <tbody>
          <tr><th>(A) Doanh thu ước tính</th><td>{vnd(f.gmv_estimated)}</td></tr>
          <tr><th>(B) Doanh thu thực nhận — đơn Đã giao</th><td>{vnd(f.nmv)}</td></tr>
          <tr><th>Tỷ lệ huỷ đơn</th><td>{pct(f.cancellation_rate)}</td></tr>
          <tr><th>Hoa hồng {pct(f.commission_rate)}</th><td>{vnd(f.commission_amount)}</td></tr>
          {f.tax_mode === "VAT_ADD" && <tr><th>{f.tax_label}</th><td>+ {vnd(f.vat_amount)}</td></tr>}
          {f.tax_mode === "TAX_DEDUCT" && <tr><th title={f.tax_label}>Khấu trừ {pct(f.tax_rate)}</th><td>− {vnd(f.tax_deduction_amount)}</td></tr>}
          {Number(f.collected_on_behalf) > 0 && <tr><th>Thu hộ</th><td>− {vnd(f.collected_on_behalf)}</td></tr>}
          {Number(f.consulting_fee) > 0 && <tr><th>Phí tư vấn</th><td>+ {vnd(f.consulting_fee)}</td></tr>}
          <tr className="total"><th>Tổng thanh toán</th><td>{vnd(f.total_payable)}</td></tr>
        </tbody>
      </table>
      <p className="muted words">Bằng chữ: {f.total_payable_words}</p>

      <h3>Sản phẩm ({p.items.length})</h3>
      <table className="items">
        <thead>
          <tr><th>Tên sản phẩm</th><th>SL</th><th>Doanh thu</th></tr>
        </thead>
        <tbody>
          {p.items.map((i, k) => (
            <tr key={k}>
              <td>{i.product_name}</td>
              <td>{i.quantity}</td>
              <td>{vnd(i.revenue)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <p className="muted">
        {p.order_line_count} dòng đơn của brand đã chọn · mapping {p.mapping_code}
        {p.other_brand_line_count > 0 && ` · bỏ qua ${p.other_brand_line_count} dòng của brand khác`}
      </p>

      {p.warning_count > 0 && (
        <details>
          <summary>
            {p.warning_count} cảnh báo (không chặn tạo BBNT):{" "}
            {Object.entries(byCode).map(([c, n]) => `${LABELS[c] ?? c} ${n}`).join(" · ")}
          </summary>
          <IssueTable rows={p.warnings} />
        </details>
      )}
    </div>
  )
}

const LABELS: Record<string, string> = {
  RETURNED: "hoàn",
  CANCELLED: "huỷ",
  UNKNOWN_STATUS: "trạng thái lạ",
  OUT_OF_PERIOD: "ngoài kỳ",
}

function IssueTable({ rows }: { rows: RowIssue[] }) {
  return (
    <table className="issues">
      <thead>
        <tr><th>Dòng</th><th>Mã đơn</th><th>Nội dung</th><th>Số tiền</th></tr>
      </thead>
      <tbody>
        {rows.map((w, k) => (
          <tr key={k} className={w.severity === "ERROR" ? "err" : ""}>
            <td>{w.row_number}</td>
            <td>{w.order_code ?? ""}</td>
            <td>{w.message}</td>
            <td>{w.amount ? vnd(w.amount) : ""}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function ErrorBox({ error, onPickContract }: { error: Shown; onPickContract: (no: string) => void }) {
  const d = error.details as { contracts?: ContractChoice[]; mappings?: string[] } | RowIssue[] | undefined
  return (
    <div className="alert error">
      <strong>{error.message}</strong>
      {error.code && <span className="code"> {error.code}</span>}
      {Array.isArray(d) && d.length > 0 && <IssueTable rows={d} />}
      {!Array.isArray(d) && d?.contracts && (
        <ul>
          {d.contracts.map((c) => (
            <li key={c.contract_no}>
              {c.contract_no} — {c.scope} · {c.channel} · {vnDate(c.valid_from)} → {vnDate(c.valid_to ?? null)}{" "}
              {error.code === "CONTRACT_AMBIGUOUS" && (
                <button className="link" onClick={() => onPickContract(c.contract_no)}>
                  Dùng hợp đồng này
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {!Array.isArray(d) && d?.mappings && <p>Mapping: {d.mappings.join(", ")} — chọn ở Tuỳ chọn thêm.</p>}
    </div>
  )
}
