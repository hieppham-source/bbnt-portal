"use client"

import { useCallback, useEffect, useState } from "react"

import { type ApiError, getSummary, type SummaryBucket, type SummaryOut } from "@/lib/api"
import { pct, vnd } from "@/lib/format"

import { handleAuthError, useIdToken } from "./useIdToken"

const YEAR = new Date().getFullYear()

function Kpi({ label, value, note, tone }: { label: string; value: string; note: string; tone: string }) {
  return (
    <div className={`kpi ${tone}`}>
      <span className="kpi-label">{label}</span>
      <span className="kpi-value">{value}</span>
      <span className="kpi-note">{note}</span>
    </div>
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
          <p className="hint">Số liệu đọc trực tiếp từ FB.JT (tab FB-{year}), % hoa hồng theo hợp đồng trong DATA BRAND.</p>
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
             note="Hoa hồng (cột K) các dòng Done trả tiền" />
        <Kpi tone="pending" label="Còn pending" value={t ? vnd(t.pending) : "—"}
             note="Đã có hoa hồng K, chưa Done" />
        <Kpi tone="estimate" label="Commission estimate" value={t ? vnd(t.estimate) : "—"}
             note={t && t.no_rate ? `GMV × % HĐ · ${t.no_rate} dòng thiếu % HĐ` : "GMV × % hoa hồng theo HĐ (chưa VAT)"} />
        <Kpi tone="cancel" label="Cancel trung bình" value={t ? pct(t.cancel_rate) : "—"}
             note="1 − NMV / GMV, theo trọng số GMV" />
      </section>

      <section className="card">
        <div className="card-head">
          <h2>Theo tháng</h2>
          <div className="legend" aria-hidden="true">
            <span><i className="dot collected" />Đã thu</span>
            <span><i className="dot pending" />Pending</span>
            <span><i className="dot estimate" />Estimate</span>
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
                  <th scope="col">Estimate</th>
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
