"use client"

import { useCallback, useEffect, useMemo, useState } from "react"

import {
  type ApiError,
  chooseLoopmail,
  decideEmail,
  decideReview,
  holdCase,
  type ReconCase,
  type ReconEmail,
  type ReconMe,
  type ReconReview,
  reconCases,
  reconEmails,
  reconMe,
  reconReviews,
  resumeCase,
} from "@/lib/api"
import { vnd } from "@/lib/format"

import { handleAuthError, useIdToken } from "./useIdToken"

const STATE_LABEL: Record<string, string> = {
  WAITING: "Chờ G + 25 / chờ brand điền status",
  NEEDS_REVIEW: "Cần xử lý",
  BBNT_DRAFTED: "Đã lập BBNT",
  FIGURES_WRITTEN: "Đã ghi số FB.JT",
  ACCEPTANCE_SENT: "Đã gửi nghiệm thu",
  AWAITING_INVOICE_CONFIRM: "Chờ confirm HĐ nháp",
  AWAITING_PAYMENT: "Chờ thanh toán",
  ON_HOLD: "Đang giữ",
  ERROR: "Lỗi",
  DONE: "Xong",
}
const COLUMN_LABEL: Record<string, string> = { nmv: "I · NMV", commission: "K · Hoa hồng", gmv: "M · HRV GMV" }

export default function ReconcileDashboard() {
  const token = useIdToken()
  const [me, setMe] = useState<ReconMe | null>(null)
  const [cases, setCases] = useState<ReconCase[]>([])
  const [reviews, setReviews] = useState<ReconReview[]>([])
  const [emails, setEmails] = useState<ReconEmail[]>([])
  const [state, setState] = useState("NEEDS_REVIEW")
  const [query, setQuery] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const fail = useCallback((e: unknown) => {
    const err = e as ApiError
    if (!handleAuthError(err.status, err.code)) setError(err.message)
  }, [])

  const load = useCallback(async () => {
    if (!token) return
    setBusy(true)
    try {
      const [m, c, r, e] = await Promise.all([
        reconMe(token),
        reconCases(token),
        reconReviews(token),
        reconEmails(token),
      ])
      setMe(m)
      setCases(c)
      setReviews(r)
      setEmails(e)
      setError(null)
    } catch (e) {
      fail(e)
    } finally {
      setBusy(false)
    }
  }, [token, fail])

  useEffect(() => {
    void load()
  }, [load])

  const act = async (run: () => Promise<unknown>) => {
    setBusy(true)
    try {
      await run()
      await load()
    } catch (e) {
      fail(e)
      setBusy(false)
    }
  }

  const counts = useMemo(() => {
    const out: Record<string, number> = {}
    for (const c of cases) out[c.state] = (out[c.state] ?? 0) + 1
    return out
  }, [cases])

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase()
    return cases.filter(
      (c) =>
        (state === "ALL" || c.state === state) &&
        (!q || [c.campaign, c.pic ?? "", c.bbnt_number ?? "", ...c.brands].join(" ").toLowerCase().includes(q)),
    )
  }, [cases, state, query])

  if (!token) return <p className="muted">Đang kiểm tra đăng nhập…</p>

  const canApprove = me?.can_approve ?? false

  return (
    <>
      <section className="card wide">
        <div className="card-head">
          <h2>Ô FB.JT chờ duyệt ({reviews.length})</h2>
          <button onClick={load} disabled={busy}>
            {busy ? "Đang tải…" : "Tải lại"}
          </button>
        </div>
        {error && <p className="alert error">{error}</p>}
        {!canApprove && me && (
          <p className="hint">Bạn chỉ xem được. Người được duyệt do quản trị cấu hình (RECON_APPROVERS).</p>
        )}
        <p className="hint">
          Ô trên FB.JT khác số BBNT. <b>Đổi</b>: lần chạy bot sau ghi số BBNT vào ô (bot vẫn đọc lại ô trước khi
          ghi). <b>Giữ</b>: giữ nguyên số trên FB.JT.
        </p>
        {reviews.length === 0 ? (
          <p className="muted">Không có ô nào chờ duyệt.</p>
        ) : (
          <table className="issues">
            <thead>
              <tr>
                <th>Dòng</th>
                <th>Brand · Campaign</th>
                <th>Cột</th>
                <th>Đang có</th>
                <th>Số BBNT</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {reviews.map((r) => (
                <tr key={r.id}>
                  <td>{r.row ?? "—"}</td>
                  <td>
                    {r.brands.join(", ")} · {r.campaign}
                  </td>
                  <td>{COLUMN_LABEL[r.column] ?? r.column}</td>
                  <td>{vnd(r.current_value)}</td>
                  <td>{vnd(r.bbnt_value)}</td>
                  <td className="actions">
                    <button
                      className="primary"
                      disabled={!canApprove || busy}
                      onClick={() => act(() => decideReview(token, r.id, "CHANGE"))}
                    >
                      Đổi
                    </button>
                    <button disabled={!canApprove || busy} onClick={() => act(() => decideReview(token, r.id, "KEEP"))}>
                      Giữ
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="card wide">
        <div className="card-head">
          <h2>Thư chờ duyệt ({emails.length})</h2>
        </div>
        <p className="hint">
          Thư nhắc bot soạn sẵn. <b>Gửi</b>: lần chạy bot sau gửi (trong giờ hành chính), hạn mới tính từ ngày gửi.{" "}
          <b>Bỏ</b>: không gửi. Brand đã điền đủ status thì thư chưa gửi tự huỷ.
        </p>
        {emails.length === 0 ? (
          <p className="muted">Không có thư nào chờ duyệt.</p>
        ) : (
          emails.map((m) => (
            <details key={m.id} className="email-preview">
              <summary>
                {m.brands.join(", ")} · {m.campaign} · lần nhắc {m.cycle_no} — {m.subject}
              </summary>
              <p className="muted">
                To: {m.to.join(", ")}
                {m.cc.length > 0 && <> · CC: {m.cc.join(", ")}</>}
              </p>
              <pre>{m.body}</pre>
              <div className="actions">
                <button
                  className="primary"
                  disabled={!canApprove || busy}
                  onClick={() => act(() => decideEmail(token, m.id, "SEND"))}
                >
                  Gửi
                </button>
                <button disabled={!canApprove || busy} onClick={() => act(() => decideEmail(token, m.id, "SKIP"))}>
                  Bỏ
                </button>
              </div>
            </details>
          ))
        )}
      </section>

      <section className="card wide">
        <div className="card-head">
          <h2>Case đối soát</h2>
          <input placeholder="Tìm brand, campaign, PIC, số BBNT" value={query} onChange={(e) => setQuery(e.target.value)} />
        </div>
        <div className="chips">
          {["NEEDS_REVIEW", "WAITING", "BBNT_DRAFTED", "FIGURES_WRITTEN", "ACCEPTANCE_SENT", "ON_HOLD", "ALL"].map((s) => (
            <button key={s} className={state === s ? "primary" : ""} onClick={() => setState(s)}>
              {s === "ALL" ? `Tất cả (${cases.length})` : `${STATE_LABEL[s]} (${counts[s] ?? 0})`}
            </button>
          ))}
        </div>
        <table className="issues">
          <thead>
            <tr>
              <th>Dòng FB.JT</th>
              <th>Brand · Campaign</th>
              <th>PIC</th>
              <th>Trạng thái</th>
              <th>Lý do / Số BBNT</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {shown.map((c) => (
              <tr key={c.id}>
                <td>{c.rows.join(", ") || "—"}</td>
                <td>
                  {c.brands.join(", ")} · {c.campaign}
                </td>
                <td>{c.pic ?? "—"}</td>
                <td>{STATE_LABEL[c.state] ?? c.state}</td>
                <td>
                  {c.state === "ON_HOLD" ? c.hold_reason : c.reasons.join(", ")}
                  {c.bbnt_number && <div className="muted">{c.bbnt_number}</div>}
                  {c.email_reasons.length > 0 && c.state !== "ACCEPTANCE_SENT" && (
                    <div className="muted">Email chưa gửi: {c.email_reasons.join(", ")}</div>
                  )}
                  {c.loopmail_candidates && (
                    <div>
                      Chọn loopmail:{" "}
                      {c.loopmail_candidates.map((t) => (
                        <button
                          key={t.id}
                          disabled={!canApprove || busy}
                          title={t.last_at ? new Date(Number(t.last_at)).toLocaleString("vi-VN") : undefined}
                          onClick={() => act(() => chooseLoopmail(token, c.id, t.id))}
                        >
                          {t.subject} · {t.id}
                        </button>
                      ))}
                    </div>
                  )}
                </td>
                <td>
                  {canApprove && c.state === "ON_HOLD" && (
                    <button disabled={busy} onClick={() => act(() => resumeCase(token, c.id))}>
                      Tiếp tục
                    </button>
                  )}
                  {canApprove && c.state !== "ON_HOLD" && c.state !== "DONE" && (
                    <button
                      disabled={busy}
                      onClick={() => {
                        const reason = window.prompt("Lý do giữ case (vd: brand xin gia hạn)")
                        if (reason && reason.trim().length >= 3) void act(() => holdCase(token, c.id, reason))
                      }}
                    >
                      Giữ case
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {shown.length === 0 && <p className="muted">Không có case.</p>}
      </section>
    </>
  )
}
