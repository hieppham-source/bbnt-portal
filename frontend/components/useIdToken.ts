"use client"

import { signIn, useSession } from "next-auth/react"
import { useEffect, useMemo } from "react"

import type { Tokens } from "@/lib/api"

/** Token Google của người đang đăng nhập (ID token để xác thực + access token để đọc Sheet).
 *  Hết phiên / làm mới thất bại -> đăng nhập lại. */
export function useIdToken(): Tokens | undefined {
  const { data, status } = useSession()
  useEffect(() => {
    if (status === "unauthenticated" || data?.error === "RefreshTokenError") void signIn("google")
  }, [status, data?.error])
  const idToken = data?.error ? undefined : data?.idToken
  const accessToken = data?.accessToken
  return useMemo(() => (idToken ? { idToken, accessToken } : undefined), [idToken, accessToken])
}

/** Cấp thêm quyền Google (vd đọc Drive cho file .xlsx): đăng nhập lại, Google hỏi quyền mới. */
export function reconsent(): void {
  void signIn("google", undefined, { prompt: "consent" })
}

/** Backend trả 401 (token hết hạn / sai) -> đăng nhập lại. Riêng GOOGLE_SCOPE_MISSING (người dùng bỏ tích
 *  quyền Google Sheets lúc đăng nhập) thì hiện lỗi thay vì đăng nhập lại liên tục. */
export function handleAuthError(status: number, code?: string): boolean {
  if (status === 401 && code !== "GOOGLE_SCOPE_MISSING") {
    void signIn("google")
    return true
  }
  return false
}
