"use client"

import { useCallback, useEffect, useState } from "react"

import { type ApiError, getSummary, type SummaryBucket, type SummaryOut } from "@/lib/api"
import { pct, vnd } from "@/lib/format"

import { handleAuthError, useIdToken } from "./useIdToken"

const YEAR = new Date().getFullYear()

type Drill = "collected" | "pending" | "eta" | "cancel"

function Kpi({ label, value, note, tone, open, onClick }: {
  label: string; value: string; note: string; tone: Drill; open: boolean; onClick: () => void
}) {
  return (
    <button type="button" className={`kpi ${tone}${open ? " open" : ""}`} onClick={onClick} aria-expanded={open}
            aria-controls="drill">
      <span className="kpi-label">{label}</span>
      <span className="kpi-value">{value}</span>
      <span className="kpi-note">{note}</span>
      <span className="kpi-more">{open ? "Ẩn chi tiết" : "Xem theo group brand"}</span>
    </button>
  )
}

const DRILL_TITLE: Record<Drill, string> = {
  collected: "Đã thu tiền theo group brand",
  pending: "Còn pending theo group brand",
  eta: "Commission ETA cả năm theo group brand",
  cancel: "Group brand cancel cao nhất",
}
const MONTH_HEAD: Record<Exclude<Drill, "cancel">, string> = {
  collected: "Tháng thu tiền tới hiện tại",
  pending: "Tháng pending gần nhất",
  eta: "Tới tháng",
}

function DrillTable({ kind, data }: { kind: Drill; data: SummaryOut }) {
  if (kind === "cancel") {
    const rows = data.groups.filter((g) => g.cancel_rate !== null)
      .sort((a, b) => Number(b.cancel_rate) - Number(a.cancel_rate))
    return (
      <table className="num-table">
        <thead><tr><th scope="col">Group brand</th><th scope="col">Cancel</th><th scope="col">GMV đã chốt</th></tr></thead>
        <tbody>
          {rows.map((g) => (
            <tr key={g.group}>
              <th scope="row">{g.group}</th>
              <td className={Number(g.cancel_rate) > 0.2 ? "late-bad" : undefined}>{pct(g.cancel_rate)}</td>
              <td>{vnd(g.gmv)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    )
  }
  const total = Number(data.total[kind])
  const rows = data.groups.filter((g) => Number(g[kind]) > 0).sort((a, b) => Number(b[kind]) - Number(a[kind]))
  return (
    <table className="num-table">
      <thead>
        <tr>
          <th scope="col">Group brand</th>
          <th scope="col">{MONTH_HEAD[kind]}</th>
          <th scope="col">CMS</th>
          <th scope="col">% trên tổng CMS</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((g) => (
          <tr key={g.group}>
            <th scope="row">{g.group}</th>
            <td>{g.last_month[kind] ? `Tháng ${g.last_month[kind]}` : "—"}</td>
            <td>{vnd(g[kind])}</td>
            <td>{total ? pct(Number(g[kind]) / total) : "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function Bar({ b, max }: { b: SummaryBucket; max: number }) {
  const w = (v: string) => `${max ? (Number(v) / max) * 100 : 0}%`
  return (
    <div className="stack-bar" aria-hidden="true">
      <span className="seg collected" style={{ width: w(b.collected) }} />
      <span className="seg pending" style={{ width: w(b.pending) }} />
      <span className="seg estimate" style={{ width: w(b.estimate) }} />
    </div>
  )
}

export default function SummaryDashboard() {
  const token = useIdToken()
  const [year, setYear] = useState(YEAR)
  const [month, setMonth] = useState("")
  const [pic, setPic] = useState("")
  const [data, setData] = useState<SummaryOut | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [drill, setDrill] = useState<Drill | null>(null)
  const toggle = (d: Drill) => () => setDrill((cur) => (cur === d ? null : d))

  const load = useCallback(async () => {
    if (!token) return
    setBusy(true)
    try {
      setData(await getSummary(token, { year, month, pic }))
      setError(null)
    } catch (e) {
      const err = e as ApiError
      if (!handleAuthError(err.status, err.code)) setError(err.message)
    } finally {
      setBusy(false)
    }
  }, [token, year, month, pic])

  useEffect(() => {
    void load()
  }, [load])

  if (!token) return <p className="muted">Đang kiểm tra đăng nhập…</p>

  const t = data?.total
  const max = Math.max(
    0,
    ...(data?.months ?? []).map((m) => Number(m.collected) + Number(m.pending) + Number(m.estimate)),
  )

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Tổng quan công nợ</h1>
          <p className="hint">Số liệu đọc trực tiếp từ FB.JT (tab FB-{year}), chỉ tính brand NON OP (cột H).</p>
        </div>
        <div className="filters">
          <label>
            Năm
            <select value={year} onChange={(e) => { setYear(Number(e.target.value)); setMonth("") }}>
              {[YEAR - 1, YEAR].map((y) => <option key={y} value={y}>{y}</option>)}
            </select>
          </label>
          <label>
            Tháng
            <select value={month} onChange={(e) => setMonth(e.target.value)}>
              <option value="">Cả năm</option>
              {data?.month_options.map((m) => <option key={m} value={m}>Tháng {m}</option>)}
            </select>
          </label>
          <label>
            PIC
            <select value={pic} onChange={(e) => setPic(e.target.value)}>
              <option value="">Tất cả</option>
              {data?.pic_options.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </label>
          <button onClick={load} disabled={busy} className="btn-ghost">
            {busy ? "Đang tải…" : "Tải lại"}
          </button>
        </div>
      </div>

      {error && <p className="alert error" role="alert">{error}</p>}

      <section className="kpis" aria-busy={busy}>
        <Kpi tone="collected" label="Đã thu tiền" value={t ? vnd(t.collected) : "—"}
             note="Hoa hồng (cột K) các dòng Done trả tiền" open={drill === "collected"} onClick={toggle("collected")} />
        <Kpi tone="pending" label="Còn pending" value={t ? vnd(t.pending) : "—"}
             note="Đã có hoa hồng K, chưa Done" open={drill === "pending"} onClick={toggle("pending")} />
        <Kpi tone="eta" label="Commission ETA cả năm" value={t ? vnd(t.eta) : "—"}
             note={t ? `Đã thu + pending + chưa nghiệm thu ${vnd(t.estimate)} (GMV × 22% × 80%)` : "—"}
             open={drill === "eta"} onClick={toggle("eta")} />
        <Kpi tone="cancel" label="Cancel trung bình" value={t ? pct(t.cancel_rate) : "—"}
             note="1 − NMV / GMV, theo trọng số GMV" open={drill === "cancel"} onClick={toggle("cancel")} />
      </section>

      {drill && data && (
        <section className="card" id="drill" aria-live="polite">
          <div className="card-head">
            <h2>{DRILL_TITLE[drill]}</h2>
            <button type="button" className="btn-ghost" onClick={() => setDrill(null)}>Đóng</button>
          </div>
          <div className="table-wrap"><DrillTable kind={drill} data={data} /></div>
        </section>
      )}

      <section className="card">
        <div className="card-head">
          <h2>Group brand chậm trả ({data?.overdue.length ?? 0})</h2>
          <span className="hint">Hạn = ngày hoá đơn + 10 ngày làm việc, chưa Done trả tiền</span>
        </div>
        {!data || data.overdue.length === 0 ? (
          <p className="hint">{busy ? "Đang tải…" : "Không có group brand nào quá hạn."}</p>
        ) : (
          <div className="table-wrap">
            <table className="num-table">
              <thead>
                <tr>
                  <th scope="col">Group brand</th>
                  <th scope="col">Tổng tiền</th>
                  <th scope="col">Số ngày trễ</th>
                </tr>
              </thead>
              <tbody>
                {data.overdue.map((o) => (
                  <tr key={o.brand}>
                    <th scope="row">{o.brand}</th>
                    <td>{vnd(o.amount)}</td>
                    <td className={o.days_late > 30 ? "late-bad" : undefined}>{o.days_late}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card">
        <div className="card-head">
          <h2>Theo tháng</h2>
          <div className="legend" aria-hidden="true">
            <span><i className="dot collected" />Đã thu</span>
            <span><i className="dot pending" />Pending</span>
            <span><i className="dot estimate" />Chưa nghiệm thu</span>
          </div>
        </div>
        {!data || data.months.length === 0 ? (
          <p className="hint">{busy ? "Đang tải…" : "Không có dòng nào khớp bộ lọc."}</p>
        ) : (
          <div className="table-wrap">
            <table className="num-table">
              <thead>
                <tr>
                  <th scope="col">Tháng</th>
                  <th scope="col">Đã thu</th>
                  <th scope="col">Pending</th>
                  <th scope="col">Chưa nghiệm thu</th>
                  <th scope="col">Cancel</th>
                  <th scope="col">Dòng</th>
                  <th scope="col" className="bar-col">Tỷ trọng</th>
                </tr>
              </thead>
              <tbody>
                {data.months.map((m) => (
                  <tr key={m.month}>
                    <th scope="row">{m.month}</th>
                    <td>{vnd(m.collected)}</td>
                    <td>{vnd(m.pending)}</td>
                    <td>{vnd(m.estimate)}</td>
                    <td>{pct(m.cancel_rate)}</td>
                    <td>{m.rows}</td>
                    <td className="bar-col"><Bar b={m} max={max} /></td>
                  </tr>
                ))}
              </tbody>
              {t && (
                <tfoot>
                  <tr>
                    <th scope="row">Tổng</th>
                    <td>{vnd(t.collected)}</td>
                    <td>{vnd(t.pending)}</td>
                    <td>{vnd(t.estimate)}</td>
                    <td>{pct(t.cancel_rate)}</td>
                    <td>{t.rows}</td>
                    <td className="bar-col" />
                  </tr>
                </tfoot>
              )}
            </table>
          </div>
        )}
      </section>
    </>
  )
}
