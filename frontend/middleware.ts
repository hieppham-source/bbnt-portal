// Mọi trang cần đăng nhập, trừ /login và /api/auth/* (callback của Google).
export { auth as middleware } from "@/auth"

export const config = {
  matcher: ["/((?!api/auth|login|_next/static|_next/image|favicon.ico).*)"],
}
