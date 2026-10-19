"use client"

import { useCallback, useEffect, useState } from "react"

import { type ApiError, checkMaster, type MasterCheckOut } from "@/lib/api"

import { handleAuthError, useIdToken } from "./useIdToken"

const TAB_LABEL: Record<string, string> = {
  entities: "Pháp nhân",
  brands: "Brand",
  contracts: "Hợp đồng",
  mappings: "Mapping",
}

export default function MasterCheck() {
  const token = useIdToken()
  const [data, setData] = useState<MasterCheckOut | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    if (!token) return
    setLoading(true)
    try {
      setData(await checkMaster(token)) // luôn đọc lại Sheet, bỏ qua cache
      setError(null)
    } catch (e) {
      const err = e as ApiError
      if (!handleAuthError(err.status, err.code)) setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [token])

  useEffect(() => {
    void load()
  }, [load])

  if (!token) return <p className="muted">Đang kiểm tra đăng nhập…</p>

  return (
    <section className="card wide">
      <div className="card-head">
        <h2>Kiểm tra Sheet master</h2>
        <button onClick={load} disabled={loading}>
          {loading ? "Đang đọc…" : "Kiểm tra lại"}
        </button>
      </div>
      {error && <p className="alert error">{error}</p>}
      {data && (
        <>
          <p className={`alert ${data.ok ? "ok" : "error"}`}>
            {data.ok
              ? "Sheet master không có dòng lỗi."
              : `Có ${data.issues.filter((i) => i.severity === "ERROR").length} dòng lỗi — các dòng này đang bị bỏ qua khi tạo BBNT.`}
          </p>
          <p className="muted">
            {Object.entries(data.counts)
              .map(([k, n]) => `${n} ${TAB_LABEL[k] ?? k}`)
              .join(" · ")}
            {data.loaded_at && ` · đọc lúc ${new Date(data.loaded_at).toLocaleString("vi-VN")}`}
          </p>
          <p className="hint">
            Tool đọc Sheet bằng tài khoản Google của từng người: ai dùng tool phải mở được Sheet master và file order
            của mình.
          </p>
          {data.issues.length > 0 && (
            <table className="issues">
              <thead>
                <tr>
                  <th>Tab</th>
                  <th>Dòng</th>
                  <th>Mức</th>
                  <th>Nội dung</th>
                </tr>
              </thead>
              <tbody>
                {data.issues.map((i, k) => (
                  <tr key={k} className={i.severity === "ERROR" ? "err" : ""}>
                    <td>{i.tab}</td>
                    <td>{i.row}</td>
                    <td>{i.severity === "ERROR" ? "Lỗi" : "Cảnh báo"}</td>
                    <td>{i.message}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </section>
  )
}
