import type { Metadata } from "next"
import Link from "next/link"

import { auth, signOut } from "@/auth"
import Providers from "@/components/Providers"

import "./globals.css"

export const metadata: Metadata = {
  title: "BBNT Generator — Hannah Olala",
  description: "Tạo Biên bản nghiệm thu từ file order",
  robots: { index: false, follow: false },
}

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const session = await auth()
  return (
    <html lang="vi">
      <body>
        <Providers>
          {session?.user && (
            <header className="topbar">
              <Link href="/" className="brand">
                BBNT Generator
              </Link>
              <nav>
                <Link href="/">Tạo BBNT</Link>
                <Link href="/master">Kiểm tra master</Link>
                <Link href="/reconcile">Đối soát</Link>
              </nav>
              <form
                action={async () => {
                  "use server"
                  await signOut({ redirectTo: "/login" })
                }}
              >
                <span className="muted">{session.user.email}</span>
                <button className="link">Đăng xuất</button>
              </form>
            </header>
          )}
          <main>{children}</main>
        </Providers>
      </body>
    </html>
  )
}
