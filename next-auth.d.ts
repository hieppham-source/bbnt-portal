import "next-auth"

declare module "next-auth" {
  interface Session {
    idToken?: string
    accessToken?: string // Google OAuth, scope spreadsheets.readonly
    error?: "RefreshTokenError"
  }
}
