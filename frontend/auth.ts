import NextAuth from "next-auth"
import Google from "next-auth/providers/google"

const DOMAIN = process.env.ALLOWED_EMAIL_DOMAIN ?? "hannaholala.com"

export const { handlers, auth, signIn, signOut } = NextAuth({
  providers: [
    Google({
      authorization: {
        // access_type=offline + prompt=consent -> Google cấp refresh token để làm mới ID token (hết hạn sau 1 giờ)
        params: { hd: DOMAIN, access_type: "offline", prompt: "consent", scope: "openid email profile" },
      },
    }),
  ],
  pages: { signIn: "/login", error: "/login" },
  session: { strategy: "jwt", maxAge: 8 * 60 * 60 }, // 8 giờ
  callbacks: {
    // middleware.ts: chưa đăng nhập -> chuyển sang /login
    authorized({ auth }) {
      return !!auth?.user
    },
    // Chặn ngay từ frontend; backend vẫn kiểm lại độc lập
    async signIn({ account, profile }) {
      return account?.provider === "google" && profile?.email_verified === true && profile?.hd === DOMAIN
    },
    async jwt({ token, account }) {
      if (account) {
        return {
          ...token,
          id_token: account.id_token,
          expires_at: account.expires_at,
          refresh_token: account.refresh_token,
          error: undefined,
        }
      }
      if (Date.now() < (token.expires_at as number) * 1000 - 5 * 60_000) return token
      if (!token.refresh_token) return { ...token, error: "RefreshTokenError" }
      const res = await fetch("https://oauth2.googleapis.com/token", {
        method: "POST",
        body: new URLSearchParams({
          client_id: process.env.AUTH_GOOGLE_ID!,
          client_secret: process.env.AUTH_GOOGLE_SECRET!,
          grant_type: "refresh_token",
          refresh_token: token.refresh_token as string,
        }),
      })
      const data = await res.json()
      if (!res.ok) return { ...token, error: "RefreshTokenError" }
      return {
        ...token,
        id_token: data.id_token ?? token.id_token,
        expires_at: Math.floor(Date.now() / 1000 + data.expires_in),
        refresh_token: data.refresh_token ?? token.refresh_token,
        error: undefined,
      }
    },
    async session({ session, token }) {
      return {
        ...session,
        idToken: token.id_token as string | undefined,
        error: token.error as "RefreshTokenError" | undefined,
      }
    },
  },
})
