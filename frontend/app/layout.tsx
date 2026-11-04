import type { Metadata } from "next"
import Link from "next/link"

import { auth, signOut } from "@/auth"
import NavTabs from "@/components/NavTabs"
import Providers from "@/components/Providers"

import "./globals.css"

export const metadata: Metadata = {
  title: "Đối soát FB — Hannah Olala",
  description: "Tổng quan công nợ, lệnh cần duyệt và lập Biên bản nghiệm thu kênh FB",
  robots: { index: false, follow: false },
}

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const session = await auth()
  return (
    <html lang="vi">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap"
        />
      </head>
      <body>
        <Providers>
          {session?.user && (
            <header className="topbar">
              <Link href="/" className="brand">
                <span className="brand-mark" aria-hidden="true">HO</span>
                Đối soát FB
              </Link>
              <NavTabs />
              <form
                action={async () => {
                  "use server"
                  await signOut({ redirectTo: "/login" })
                }}
              >
                <span className="user">{session.user.email}</span>
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
