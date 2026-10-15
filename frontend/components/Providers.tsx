"use client"

import { SessionProvider } from "next-auth/react"

// refetchInterval: hỏi lại session 5 phút/lần -> server làm mới ID token trước khi hết hạn 1 giờ
export default function Providers({ children }: { children: React.ReactNode }) {
  return (
    <SessionProvider refetchInterval={5 * 60} refetchOnWindowFocus>
      {children}
    </SessionProvider>
  )
}
