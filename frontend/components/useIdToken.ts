"use client"

import { signIn, useSession } from "next-auth/react"
import { useEffect } from "react"

/** ID token Google của người đang đăng nhập; hết phiên / làm mới thất bại -> đăng nhập lại. */
export function useIdToken(): string | undefined {
  const { data, status } = useSession()
  useEffect(() => {
    if (status === "unauthenticated" || data?.error === "RefreshTokenError") void signIn("google")
  }, [status, data?.error])
  return data?.error ? undefined : data?.idToken
}

/** Backend trả 401 (token hết hạn / sai) -> đăng nhập lại. */
export function handleAuthError(status: number): boolean {
  if (status === 401) {
    void signIn("google")
    return true
  }
  return false
}
