"use client"

import { useCallback, useEffect, useMemo, useState } from "react"

import {
  ApiError,
  type BbntRequest,
  type BreakdownMode,
  type CampLabel,
  generateBbnt,
  getMaster,
  type MasterOut,
  previewBbnt,
  previewBbntSplit,
  type PreviewOut,
  type RowIssue,
  type TabMode,
} from "@/lib/api"
import { pct, vnd, vnDate } from "@/lib/format"

import { handleAuthError, reconsent, useIdToken } from "./useIdToken"

/** Xem trước cách portal sẽ chuẩn hoá "đuôi biên bản nghiệm thu" người dùng gõ (chỉ để hiển thị — việc chuẩn
 * hoá thật sự luôn do backend làm qua `normalize_suffix`). */
function previewSuffix(raw: string): string {
  return raw
    .trim()
    .toUpperCase()
    .replace(/[^A-Z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
}

type Mode = "brand" | "group"
type Shown = { message: string; code?: string; details?: unknown }
type ContractChoice = { contract_no: string; scope?: string; channel?: string; valid_from?: string; valid_to?: string | null }

// Google Sheet, hoặc file Excel (.xlsx) trên Drive (mở trên Sheets hay link drive.google.com/file/d/…)
const SHEET_RE =
  /^https:\/\/(docs\.google\.com\/spreadsheets\/d\/|drive\.google\.com\/(file\/d\/|open\?id=))[A-Za-z0-9_-]{20,}/

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
  const [suffix, setSuffix] = useState("")
  const [campaign, setCampaign] = useState("")
  const [orderUrl, setOrderUrl] = useState("")
  const [extraOrderUrls, setExtraOrderUrls] = useState<string[]>([])
  const [extraOrderInput, setExtraOrderInput] = useState("")
  const [orderTab, setOrderTab] = useState("")
  const [tabMode, setTabMode] = useState<TabMode>("auto")
  const [mappingCode, setMappingCode] = useState("")
  const [collected, setCollected] = useState("")
  const [consulting, setConsulting] = useState("")
  const [stripComments, setStripComments] = useState(false)

  // ---------------------------------------------------------------- result
  const [preview, setPreview] = useState<PreviewOut | null>(null)
  const [error, setError] = useState<Shown | null>(null)
  const [busy, setBusy] = useState<"preview" | "generate" | "split" | null>(null)
  const [downloaded, setDownloaded] = useState<string | null>(null)

  // Tách BBNT theo tab (tab_mode === "split"): sau khi báo ORDER_TAB_AMBIGUOUS, người dùng có thể chọn
  // "Tách thành nhiều BBNT riêng" -> nhập Tên chương trình riêng cho từng tab -> xem trước mảng kết quả.
  const [splitTabs, setSplitTabs] = useState<string[] | null>(null)
  const [splitNames, setSplitNames] = useState<Record<string, string>>({})
  const [splitResults, setSplitResults] = useState<PreviewOut[] | null>(null)
  const [downloadingTab, setDownloadingTab] = useState<string | null>(null)

  // Tách bảng theo brand/camp (breakdown_mode) — bắt buộc chọn khi nghiệm thu group/nhiều brand cùng lúc.
  // "brand" không cần nhập gì thêm; "camp" cần đặt tên riêng cho từng (link, tab) — portal tự phát hiện danh
  // sách tab/link cần đặt tên qua lỗi TAB_CAMPAIGN_NAMES_REQUIRED của lần xem trước đầu tiên.
  const [breakdownMode, setBreakdownMode] = useState<BreakdownMode | null>(null)
  const [campSources, setCampSources] = useState<{ link_index: number; tab: string }[] | null>(null)
  const [campNames, setCampNames] = useState<Record<string, string>>({})

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
        if (!handleAuthError(err.status, err.code)) setMasterErr({ message: err.message, code: err.code })
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
    setOrderTab("")
    setTabMode("auto")
    setSplitTabs(null)
    setSplitNames({})
    setSplitResults(null)
    setBreakdownMode(null)
    setCampSources(null)
    setCampNames({})
  }

  const campKey = (linkIndex: number, tab: string) => `${linkIndex}:${tab}`

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

  // Nghiệm thu group/nhiều brand cùng lúc -> bắt buộc chọn cách tách bảng (theo brand hay theo camp)
  const needsBreakdownChoice = mode === "group" ? !!groupCode : brandCodes.length > 1 && !mixedGroups

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
  if (!contractNo) problems.push("Chọn số hợp đồng (luôn tự chọn tay, không tự suy luận)")
  if (!suffix.trim()) problems.push("Nhập đuôi biên bản nghiệm thu")
  if (!campaign.trim()) problems.push("Nhập tên chương trình")
  if (!SHEET_RE.test(orderUrl.trim())) problems.push("Dán link file order (Google Sheet hoặc file Excel trên Drive)")
  if (needsBreakdownChoice && !breakdownMode) problems.push("Chọn cách tách bảng: theo brand hay theo camp")
  if (breakdownMode === "camp" && campSources && campSources.some((s) => !(campNames[campKey(s.link_index, s.tab)] ?? "").trim()))
    problems.push("Đặt tên camp cho đủ các tab/link bên dưới")

  function buildRequest(tab = orderTab, tm: TabMode = tabMode, bm: BreakdownMode | null = breakdownMode): BbntRequest {
    const camps = bm === "camp" && campSources
      ? campSources.map((s) => ({ ...s, name: (campNames[campKey(s.link_index, s.tab)] ?? "").trim() }))
      : []
    return {
      channel,
      entity,
      ...(mode === "brand" ? { brand_codes: brandCodes } : { group_code: groupCode }),
      contract_no: contractNo,
      ...(mappingCode ? { mapping_code: mappingCode } : {}),
      bbnt_suffix: suffix.trim(),
      campaign_name: campaign.trim(),
      order_sheet_url: orderUrl.trim(),
      ...(extraOrderUrls.length ? { extra_order_sheet_urls: extraOrderUrls } : {}),
      collected_on_behalf: Number(collected || 0),
      consulting_fee: Number(consulting || 0),
      strip_comments: stripComments,
      ...(tab ? { order_tab: tab } : {}),
      ...(tm !== "auto" ? { tab_mode: tm } : {}),
      ...(bm ? { breakdown_mode: bm } : {}),
      ...(bm === "camp" ? { camp_names: camps as CampLabel[] } : {}),
    }
  }

  async function run(
    kind: "preview" | "generate",
    tab = orderTab,
    tm: TabMode = tabMode,
    bm: BreakdownMode | null = breakdownMode,
  ) {
    if (!token || problems.length) return
    setBusy(kind)
    setError(null)
    try {
      const req = buildRequest(tab, tm, bm)
      if (kind === "preview") {
        setPreview(await previewBbnt(token, req))
      } else {
        setDownloaded(await generateBbnt(token, req))
      }
    } catch (e) {
      const err = e as ApiError
      // breakdown_mode === "camp" lần đầu (campSources chưa biết) -> backend trả danh sách (link, tab) cần đặt
      // tên qua lỗi này; hiện form nhập tên thay vì báo lỗi thô
      if (err.code === "TAB_CAMPAIGN_NAMES_REQUIRED" && bm === "camp" && !campSources) {
        const tabs = (err.details as { tabs?: { link_index: number; tab: string }[] } | undefined)?.tabs
        if (tabs && tabs.length) {
          setCampSources(tabs)
          setBusy(null)
          return
        }
      }
      if (!handleAuthError(err.status, err.code)) setError({ message: err.message, code: err.code, details: err.details })
    } finally {
      setBusy(null)
    }
  }

  // Chọn tab khác trong file order -> tính lại ngay (order_tab không dùng được cùng breakdown_mode — bỏ luôn
  // nếu đang chọn tách bảng/tách BBNT riêng)
  function pickTab(tab: string) {
    setOrderTab(tab)
    setTabMode("auto")
    setBreakdownMode(null)
    setCampSources(null)
    setCampNames({})
    setSplitTabs(null)
    setSplitResults(null)
    setPreview(null)
    setDownloaded(null)
    void run("preview", tab, "auto", null)
  }

  // File order có nhiều tab/camp mơ hồ (ORDER_TAB_AMBIGUOUS) -> tách bảng theo CAMP (breakdown_mode="camp",
  // dùng được cho MỌI phạm vi kể cả 1 brand) rồi gộp lại thành 1 BBNT DUY NHẤT — thay cho "gộp không tách gì"
  // cũ (tab_mode="combine", đã bỏ hẳn). Backend sẽ báo TAB_CAMPAIGN_NAMES_REQUIRED ngay lần xem trước đầu tiên
  // để portal hiện form đặt tên camp cho từng tab (xem nhánh xử lý lỗi trong run()).
  function runCombine() {
    setOrderTab("")
    setSplitTabs(null)
    setSplitResults(null)
    setTabMode("auto")
    setBreakdownMode("camp")
    setCampSources(null)
    setCampNames({})
    setPreview(null)
    setDownloaded(null)
    void run("preview", "", "auto", "camp")
  }

  // Mở form nhập Tên chương trình riêng cho từng tab trước khi tách (tab_mode="split" không dùng cùng
  // breakdown_mode — bỏ luôn nếu đang chọn tách bảng)
  function startSplit(tabs: string[]) {
    setBreakdownMode(null)
    setCampSources(null)
    setCampNames({})
    setSplitTabs(tabs)
    setSplitNames(Object.fromEntries(tabs.map((t) => [t, ""])))
    setSplitResults(null)
    setError(null)
  }

  async function runSplit() {
    if (!token || !splitTabs) return
    setBusy("split")
    setError(null)
    try {
      const req = buildRequest("", "split")
      req.tab_campaign_names = Object.fromEntries(splitTabs.map((t) => [t, (splitNames[t] ?? "").trim()]))
      const res = await previewBbntSplit(token, req)
      setSplitResults(res.results)
      setPreview(null)
      setDownloaded(null)
    } catch (e) {
      const err = e as ApiError
      if (!handleAuthError(err.status, err.code)) setError({ message: err.message, code: err.code, details: err.details })
    } finally {
      setBusy(null)
    }
  }

  // Tải 1 trong các BBNT đã tách — gọi lại /generate bình thường với đúng tab + tên chương trình của BBNT đó
  async function downloadSplitResult(r: PreviewOut) {
    if (!token) return
    const tab = r.order_tab ?? ""
    setDownloadingTab(tab)
    setError(null)
    try {
      const req = buildRequest(tab, "auto")
      req.campaign_name = r.campaign_name || campaign.trim()
      setDownloaded(await generateBbnt(token, req))
    } catch (e) {
      const err = e as ApiError
      if (!handleAuthError(err.status, err.code)) setError({ message: err.message, code: err.code, details: err.details })
    } finally {
      setDownloadingTab(null)
    }
  }

  // Chọn "theo brand"/"theo camp" — KHÔNG dùng touch() vì touch() reset luôn breakdownMode về null
  function pickBreakdownMode(m: BreakdownMode) {
    setBreakdownMode(m)
    setPreview(null)
    setError(null)
    setDownloaded(null)
    setCampSources(null)
    setCampNames({})
  }

  function addExtraOrderUrl() {
    const u = extraOrderInput.trim()
    if (!u || !SHEET_RE.test(u)) return
    if (u === orderUrl.trim() || extraOrderUrls.includes(u)) {
      setExtraOrderInput("")
      return
    }
    touch(setExtraOrderUrls)([...extraOrderUrls, u])
    setExtraOrderInput("")
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

        {needsBreakdownChoice && (
          <fieldset>
            <legend>Tách bảng doanh thu</legend>
            <p className="hint">
              Nghiệm thu gộp nhiều brand/cả group cùng lúc — chọn cách tách bảng doanh thu ước tính, doanh thu thực
              nhận, tỷ lệ huỷ đơn. Hoa hồng vẫn tính chung cho toàn bộ BBNT dù chọn cách nào.
            </p>
            <div className="tabs">
              <button
                className={breakdownMode === "brand" ? "on" : ""}
                type="button"
                onClick={() => pickBreakdownMode("brand")}
              >
                Theo brand
              </button>
              <button
                className={breakdownMode === "camp" ? "on" : ""}
                type="button"
                onClick={() => pickBreakdownMode("camp")}
              >
                Theo camp
              </button>
            </div>
            {breakdownMode === "camp" && campSources === null && (
              <p className="hint">Bấm "Xem trước" để portal liệt kê các tab/link cần đặt tên camp.</p>
            )}
          </fieldset>
        )}

        {/* Đặt tên camp — hiện độc lập với "needsBreakdownChoice" ở trên: file order 1 brand nhiều tab/camp mơ
            hồ (ORDER_TAB_AMBIGUOUS) cũng dùng breakdown_mode="camp" qua nút "Gộp tất cả thành 1 BBNT" mà không
            cần chọn trước ở đây (xem runCombine) — vẫn cần nhập tên camp ngay khi backend báo thiếu. */}
        {breakdownMode === "camp" && campSources && (
          <fieldset>
            <legend>Đặt tên camp</legend>
            <p className="hint">Đặt tên camp riêng cho từng tab/link bên dưới (bắt buộc, không được trùng nhau).</p>
            <div className="camp-names">
              {campSources.map((s) => (
                <label key={campKey(s.link_index, s.tab)}>
                  {s.link_index === 0 ? "Link chính" : `Link phụ #${s.link_index}`} · tab {s.tab}
                  <input
                    value={campNames[campKey(s.link_index, s.tab)] ?? ""}
                    onChange={(e) =>
                      setCampNames((n) => ({ ...n, [campKey(s.link_index, s.tab)]: e.target.value }))
                    }
                    placeholder="VD: Camp Mix"
                    maxLength={100}
                  />
                </label>
              ))}
            </div>
          </fieldset>
        )}

        <fieldset>
          <legend>Hợp đồng & đuôi biên bản nghiệm thu</legend>
          <div className="row">
            <label>
              Số hợp đồng
              <select value={contractNo} onChange={(e) => touch(setContractNo)(e.target.value)}>
                <option value="">— Chọn hợp đồng —</option>
                {contractOptions.map((c) => (
                  <option key={c.contract_no} value={c.contract_no}>
                    {c.contract_no} — {pct(c.commission_rate)} ({vnDate(c.valid_from)} → {vnDate(c.valid_to)})
                  </option>
                ))}
              </select>
              <span className="hint">Luôn tự chọn tay — hợp đồng/% hoa hồng áp dụng không còn tự suy ra theo kỳ nghiệm thu.</span>
            </label>
            <label>
              Đuôi biên bản nghiệm thu
              <input
                value={suffix}
                onChange={(e) => touch(setSuffix)(e.target.value)}
                placeholder='VD: "BDAY 05" hoặc "Q3 CAMP"'
                maxLength={60}
              />
              <span className="hint">
                Tự gõ, ráp thẳng vào số BBNT{suffix.trim() ? ` (vd ...-${previewSuffix(suffix)})` : ""}. Portal tự
                chuẩn hoá: in hoa, đổi khoảng trắng/ký tự lạ thành gạch ngang. Link order mặc định tính TOÀN BỘ dòng
                đơn, không lọc theo tháng nữa.
              </span>
            </label>
          </div>
        </fieldset>

        <label>
          Tên chương trình
          <input value={campaign} onChange={(e) => touch(setCampaign)(e.target.value)} placeholder="VD: BDAY" maxLength={100} />
        </label>

        <label>
          Link file order
          <input
            value={orderUrl}
            onChange={(e) => {
              touch(setOrderUrl)(e.target.value)
              setOrderTab("")
            }}
            placeholder="https://docs.google.com/spreadsheets/d/…  hoặc link file Excel trên Drive"
          />
          <span className="hint">
            Google Sheet hoặc file Excel (.xlsx) trên Drive — tài khoản của bạn phải mở được file. File nhiều tab: tool tự
            chọn tab theo tên brand / tên chương trình, sai thì đổi tab ở phần kết quả.
          </span>
        </label>

        {extraOrderUrls.length > 0 && (
          <div className="chips">
            {extraOrderUrls.map((u, i) => (
              <span key={u} className="chip" title={u}>
                Link order #{i + 2}
                <button
                  type="button"
                  aria-label={`Bỏ link order #${i + 2}`}
                  onClick={() => touch(setExtraOrderUrls)(extraOrderUrls.filter((x) => x !== u))}
                >
                  ×
                </button>
              </span>
            ))}
          </div>
        )}
        <div className="row tight">
          <input
            value={extraOrderInput}
            onChange={(e) => setExtraOrderInput(e.target.value)}
            placeholder="Dán thêm link order nữa để gộp chung 1 BBNT (nếu dữ liệu nằm rải trên nhiều file)"
          />
          <button type="button" onClick={addExtraOrderUrl}>
            Thêm link
          </button>
        </div>

        <details>
          <summary>Tuỳ chọn thêm</summary>
          <div className="row">
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
        {error && (
          <ErrorBox
            error={error}
            onPickContract={(no) => touch(setContractNo)(no)}
            onPickTab={pickTab}
            onCombine={runCombine}
            onStartSplit={startSplit}
          />
        )}

        {splitTabs && !splitResults && (
          <div className="card">
            <h3>Tách thành nhiều BBNT riêng</h3>
            <p className="hint">
              Mỗi tab bên dưới sẽ thành 1 BBNT riêng — bắt buộc nhập Tên chương trình RIÊNG, khác nhau cho từng tab (số
              BBNT không phân biệt theo tên chương trình nên mỗi BBNT tách ra sẽ tự thêm hậu tố -R1, -R2…).
            </p>
            {splitTabs.map((t) => (
              <label key={t}>
                {t}
                <input
                  value={splitNames[t] ?? ""}
                  onChange={(e) => setSplitNames((n) => ({ ...n, [t]: e.target.value }))}
                  placeholder="VD: BDAY đợt 1"
                  maxLength={100}
                />
              </label>
            ))}
            <div className="actions">
              <button
                className="primary"
                disabled={busy === "split" || splitTabs.some((t) => !(splitNames[t] ?? "").trim())}
                onClick={runSplit}
              >
                {busy === "split" ? "Đang tính…" : "Xem trước tách"}
              </button>
              <button type="button" onClick={() => setSplitTabs(null)}>
                Huỷ
              </button>
            </div>
          </div>
        )}

        {downloaded && <p className="alert ok">Đã tải BBNT {downloaded}. Nhớ cất file vào thư mục Drive chung.</p>}

        {splitResults ? (
          <div className="split-results">
            <p className="hint">{splitResults.length} BBNT tách riêng theo tab — tải từng cái một.</p>
            {splitResults.map((r) => (
              <PreviewCard
                key={r.order_tab ?? r.bbnt_number}
                p={r}
                onPickTab={() => {}}
                busy={downloadingTab !== null}
                onDownload={() => downloadSplitResult(r)}
                downloading={downloadingTab === (r.order_tab ?? "")}
                selectableTabs={false}
              />
            ))}
          </div>
        ) : preview ? (
          <PreviewCard p={preview} onPickTab={pickTab} busy={!!busy} />
        ) : (
          !error && !splitTabs && <p className="muted">Điền form rồi bấm Xem trước để kiểm tra số liệu trước khi tải.</p>
        )}
      </section>
    </div>
  )
}

// =========================================================================== kết quả
function PreviewCard({
  p,
  onPickTab,
  busy,
  onDownload,
  downloading,
  selectableTabs = true,
}: {
  p: PreviewOut
  onPickTab: (tab: string) => void
  busy: boolean
  onDownload?: () => void
  downloading?: boolean
  selectableTabs?: boolean
}) {
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
            <th>Ngày ký HĐ</th>
            <td>
              {vnDate(p.contract.signed_on)}
              {p.contract.signed_on_source !== "Ngày ký" && (
                <span className="muted"> (lấy theo Hiệu lực từ — HOP_DONG chưa có cột Ngày ký)</span>
              )}
            </td>
          </tr>
          <tr>
            <th>Bên A</th>
            <td>{p.contract.party_a_name}</td>
          </tr>
          <tr>
            <th>File order</th>
            <td>
              {selectableTabs && p.order_tabs.length > 1 ? (
                <select value={p.order_tab ?? ""} disabled={busy} onChange={(e) => onPickTab(e.target.value)}>
                  {p.order_tabs.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
              ) : p.order_tabs_combined.length > 1 ? (
                <>gộp {p.order_tabs_combined.length} tab: {p.order_tabs_combined.join(", ")}</>
              ) : (
                <>tab {p.order_tab}</>
              )}{" "}
              <span className="muted">
                · {p.order_file_kind === "xlsx" ? "Excel trên Drive" : "Google Sheet"} · tiêu đề dòng {p.order_header_row}
              </span>
            </td>
          </tr>
          <tr>
            <th>Brand</th>
            <td>{p.brands.join(", ")}</td>
          </tr>
        </tbody>
      </table>

      <SummaryTable f={f} />
      <p className="muted words">Bằng chữ: {f.total_payable_words}</p>

      {p.breakdown_kind !== "none" && p.brand_breakdown.length > 0 && (
        <BreakdownTable kind={p.breakdown_kind} rows={p.brand_breakdown} />
      )}

      {onDownload && (
        <div className="actions">
          <button className="primary" disabled={!!downloading} onClick={onDownload}>
            {downloading ? "Đang tạo file…" : "Tải BBNT này (.docx)"}
          </button>
        </div>
      )}

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

// Bảng tổng hợp doanh thu & thanh toán — luôn hiện ở mọi lần xem trước (kể cả chỉ có 1 BBNT). Chữ cái ở cột
// "Ghi chú" khớp đúng công thức tính tổng tiền brand cần thanh toán thêm: E = A + B − C − D + F.
function SummaryTable({ f }: { f: PreviewOut["figures"] }) {
  return (
    <table className="summary">
      <thead>
        <tr>
          <th>Hạng mục</th>
          <th>Số tiền</th>
          <th>Ghi chú</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td>GMV from HRV</td>
          <td>{vnd(f.gmv_estimated)}</td>
          <td></td>
        </tr>
        <tr>
          <td>Cancellation</td>
          <td>{pct(f.cancellation_rate)}</td>
          <td></td>
        </tr>
        <tr>
          <td>NMV</td>
          <td>{vnd(f.nmv)}</td>
          <td></td>
        </tr>
        <tr>
          <td>Tổng Commission được hưởng (chưa VAT) ({pct(f.commission_rate)})</td>
          <td>{vnd(f.commission_amount)}</td>
          <td>A</td>
        </tr>
        <tr>
          <td>Tiền VAT ({pct(f.tax_rate)})</td>
          <td>{vnd(f.vat_amount)}</td>
          <td>B</td>
        </tr>
        <tr>
          <td>Cần trừ thuế ({pct(f.tax_rate)})</td>
          <td>{vnd(f.tax_deduction_amount)}</td>
          <td>C</td>
        </tr>
        <tr>
          <td>Số tiền thu hộ brand</td>
          <td>{vnd(f.collected_on_behalf)}</td>
          <td>D</td>
        </tr>
        <tr>
          <td>Phí tư vấn</td>
          <td>{vnd(f.consulting_fee)}</td>
          <td>F</td>
        </tr>
        <tr className="total">
          <td>Tổng tiền brand cần thanh toán thêm</td>
          <td>{vnd(f.total_payable)}</td>
          <td>E</td>
        </tr>
      </tbody>
      <tfoot>
        <tr>
          <td colSpan={3} className="muted">
            E = A + B − C − D + F
          </td>
        </tr>
      </tfoot>
    </table>
  )
}

// Bảng tách doanh thu ước tính/thực nhận/tỷ lệ huỷ theo brand hoặc theo camp — chỉ hiển thị khi breakdown_kind !==
// "none" (chọn breakdown_mode, hoặc link order tự nhận diện "1 tab/brand"). Hoa hồng/VAT/thanh toán KHÔNG tách —
// xem bảng tổng hợp phía trên, dòng hint nhắc lại điều này. Dưới bảng tổng hợp là danh sách sản phẩm RIÊNG của
// từng brand/camp kèm tổng kết số lượng + doanh thu (issue #2: tách danh sách sản phẩm, không chỉ bảng doanh thu).
function BreakdownTable({ kind, rows }: { kind: "brand" | "camp"; rows: PreviewOut["brand_breakdown"] }) {
  return (
    <div>
      <h3>Tách theo {kind === "camp" ? "camp" : "brand"}</h3>
      <table className="breakdown">
        <thead>
          <tr>
            <th>{kind === "camp" ? "Camp" : "Brand"}</th>
            <th>GMV ước tính</th>
            <th>NMV</th>
            <th>Tỷ lệ huỷ</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((x) => (
            <tr key={x.index}>
              <td>{x.brand_name}</td>
              <td>{vnd(x.gmv_estimated)}</td>
              <td>{vnd(x.nmv)}</td>
              <td>{pct(x.cancellation_rate)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="hint">Hoa hồng/VAT/khấu trừ/tổng thanh toán tính chung trên TOÀN BỘ (xem bảng tổng hợp bên trên).</p>

      {rows.map((x) => {
        const totalQty = x.items.reduce((s, i) => s + i.quantity, 0)
        const totalRevenue = x.items.reduce((s, i) => s + Number(i.revenue), 0)
        return (
          <div key={x.index} className="breakdown-items">
            <h4>
              Sản phẩm — {x.brand_name} ({x.items.length})
            </h4>
            <table className="items">
              <thead>
                <tr>
                  <th>Tên sản phẩm</th>
                  <th>SL</th>
                  <th>Doanh thu</th>
                </tr>
              </thead>
              <tbody>
                {x.items.map((i, k) => (
                  <tr key={k}>
                    <td>{i.product_name}</td>
                    <td>{i.quantity}</td>
                    <td>{vnd(i.revenue)}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="total">
                  <td>Tổng {x.brand_name}</td>
                  <td>{totalQty}</td>
                  <td>{vnd(totalRevenue)}</td>
                </tr>
              </tfoot>
            </table>
          </div>
        )
      })}
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

function ErrorBox({
  error,
  onPickContract,
  onPickTab,
  onCombine,
  onStartSplit,
}: {
  error: Shown
  onPickContract: (no: string) => void
  onPickTab: (tab: string) => void
  onCombine: () => void
  onStartSplit: (tabs: string[]) => void
}) {
  const d = error.details as { contracts?: ContractChoice[]; mappings?: string[]; tabs?: string[] } | RowIssue[] | undefined
  const ambiguous = error.code === "ORDER_TAB_AMBIGUOUS"
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
      {error.code === "GOOGLE_DRIVE_SCOPE_MISSING" && (
        <p>
          <button className="primary" onClick={reconsent}>
            Đăng nhập lại & cấp quyền đọc Drive
          </button>
        </p>
      )}
      {!Array.isArray(d) && d?.tabs && d.tabs.length > 0 && (
        <>
          <ul>
            {d.tabs.map((t) => (
              <li key={t}>
                {t}{" "}
                {error.code !== "MAPPING_MISMATCH" && (
                  <button className="link" onClick={() => onPickTab(t)}>
                    Dùng tab này
                  </button>
                )}
              </li>
            ))}
          </ul>
          {ambiguous && (
            <p className="actions">
              <button type="button" onClick={onCombine}>
                Gộp tất cả thành 1 BBNT
              </button>
              <button type="button" onClick={() => onStartSplit(d.tabs!)}>
                Tách thành nhiều BBNT riêng
              </button>
            </p>
          )}
        </>
      )}
    </div>
  )
}
