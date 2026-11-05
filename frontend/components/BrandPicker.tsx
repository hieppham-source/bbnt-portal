"use client"

import { useId, useMemo, useState } from "react"

type B = { code: string; name: string; group_code: string | null }

const norm = (s: string) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/đ/gi, "d").toLowerCase()

/** Chọn nhiều brand: gõ vài ký tự để lọc, Enter / bấm để thêm, ✕ để bỏ. */
export default function BrandPicker({ brands, value, onChange }: {
  brands: B[]
  value: string[]
  onChange: (codes: string[]) => void
}) {
  const [q, setQ] = useState("")
  const [open, setOpen] = useState(false)
  const [hi, setHi] = useState(0)
  const listId = useId()
  const matches = useMemo(() => {
    const k = norm(q.trim())
    return brands
      .filter((b) => !value.includes(b.code))
      .filter((b) => !k || norm(`${b.name} ${b.code} ${b.group_code ?? ""}`).includes(k))
      .slice(0, 50)
  }, [brands, value, q])
  const add = (code: string) => {
    onChange([...value, code])
    setQ("")
    setHi(0)
  }
  const byCode = new Map(brands.map((b) => [b.code, b]))

  return (
    <div className="picker">
      {value.length > 0 && (
        <div className="chips">
          {value.map((c) => (
            <span key={c} className="chip">
              {byCode.get(c)?.name ?? c}
              <button type="button" aria-label={`Bỏ ${byCode.get(c)?.name ?? c}`}
                      onClick={() => onChange(value.filter((x) => x !== c))}>✕</button>
            </span>
          ))}
        </div>
      )}
      <input
        role="combobox"
        aria-expanded={open && matches.length > 0}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-label="Tìm brand"
        placeholder={brands.length ? `Gõ để tìm trong ${brands.length} brand…` : "Kênh này chưa có brand"}
        value={q}
        onChange={(e) => { setQ(e.target.value); setOpen(true); setHi(0) }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 120)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") { e.preventDefault(); setOpen(true); setHi((h) => Math.min(h + 1, matches.length - 1)) }
          if (e.key === "ArrowUp") { e.preventDefault(); setHi((h) => Math.max(h - 1, 0)) }
          if (e.key === "Enter" && matches[hi]) { e.preventDefault(); add(matches[hi].code) }
          if (e.key === "Escape") setOpen(false)
          if (e.key === "Backspace" && !q && value.length) onChange(value.slice(0, -1))
        }}
      />
      {open && matches.length > 0 && (
        <ul id={listId} role="listbox" className="picker-list">
          {matches.map((b, i) => (
            <li key={b.code} role="option" aria-selected={i === hi} className={i === hi ? "hi" : undefined}
                onMouseDown={(e) => { e.preventDefault(); add(b.code) }} onMouseEnter={() => setHi(i)}>
              {b.name} <span className="muted">{b.group_code ? `· ${b.group_code}` : ""}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
