/** "1234567" -> "1.234.567" (kiểu VN). */
export function vnd(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—"
  const n = Math.round(Number(value))
  if (!Number.isFinite(n)) return String(value)
  return n.toLocaleString("vi-VN")
}

/** "0.18" -> "18%", "0.0625" -> "6,25%". */
export function pct(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—"
  const n = Number(value) * 100
  return `${n.toLocaleString("vi-VN", { maximumFractionDigits: 2 })}%`
}

/** "2026-05" (input type=month) -> "05.26" (định dạng kỳ của BBNT). */
export function monthToPeriod(v: string): string {
  const [y, m] = v.split("-")
  return `${m}.${y.slice(2)}`
}

/** "05.26" -> "2026-05". */
export function periodToMonth(p: string): string {
  const [m, yy] = p.split(".")
  return `20${yy}-${m}`
}

/** Tháng trước theo giờ VN, dạng "2026-09". */
export function previousMonth(now = new Date()): string {
  const vn = new Date(now.toLocaleString("en-US", { timeZone: "Asia/Ho_Chi_Minh" }))
  vn.setDate(1)
  vn.setMonth(vn.getMonth() - 1)
  return `${vn.getFullYear()}-${String(vn.getMonth() + 1).padStart(2, "0")}`
}

export function vnDate(iso: string | null | undefined): string {
  if (!iso) return "không thời hạn"
  const [y, m, d] = iso.slice(0, 10).split("-")
  return `${d}/${m}/${y}`
}
