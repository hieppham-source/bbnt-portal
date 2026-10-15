import { redirect } from "next/navigation"

import { auth, signIn } from "@/auth"

const MESSAGES: Record<string, string> = {
  AccessDenied: "Chỉ tài khoản @hannaholala.com được dùng công cụ này.",
  Configuration: "Cấu hình đăng nhập chưa đúng — báo IT kiểm tra biến AUTH_*.",
}

export default async function LoginPage({ searchParams }: { searchParams: Promise<{ error?: string }> }) {
  const session = await auth()
  if (session?.user && !session.error) redirect("/")
  const { error } = await searchParams
  return (
    <div className="login">
      <h1>BBNT Generator</h1>
      <p className="muted">Tạo Biên bản nghiệm thu từ file order — Hannah Olala</p>
      {error && <p className="alert error">{MESSAGES[error] ?? "Đăng nhập không thành công, thử lại."}</p>}
      <form
        action={async () => {
          "use server"
          await signIn("google", { redirectTo: "/" })
        }}
      >
        <button className="primary">Đăng nhập bằng Google</button>
      </form>
    </div>
  )
}
