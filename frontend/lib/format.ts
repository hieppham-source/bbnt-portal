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

export function vnDate(iso: string | null | undefined): string {
  if (!iso) return "không thời hạn"
  const [y, m, d] = iso.slice(0, 10).split("-")
  return `${d}/${m}/${y}`
}
