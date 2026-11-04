"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"

const TABS = [
  { href: "/", label: "Tổng quan" },
  { href: "/reconcile", label: "Cần duyệt" },
  { href: "/bbnt", label: "Biên bản nghiệm thu" },
]

export default function NavTabs() {
  const path = usePathname()
  const active = (href: string) =>
    href === "/" ? path === "/" : path === href || path.startsWith(`${href}/`) || (href === "/bbnt" && path === "/master")
  return (
    <nav className="navtabs" aria-label="Chức năng">
      {TABS.map((t) => (
        <Link key={t.href} href={t.href} className={active(t.href) ? "on" : undefined}
              aria-current={active(t.href) ? "page" : undefined}>
          {t.label}
        </Link>
      ))}
    </nav>
  )
}
