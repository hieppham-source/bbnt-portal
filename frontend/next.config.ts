import type { NextConfig } from "next"

// Cloudflare Workers (adapter @opennextjs/cloudflare) -> bind binding/dev proxy khi chạy `next dev` để local dev
// giống hệt môi trường Worker thật. Không ảnh hưởng gì khi build thường (next build) hay deploy lên Netlify/Render —
// chỉ có tác dụng lúc `next dev`.
import { initOpenNextCloudflareForDev } from "@opennextjs/cloudflare"
initOpenNextCloudflareForDev()

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
}

export default nextConfig
