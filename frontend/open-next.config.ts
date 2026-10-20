import { defineCloudflareConfig } from "@opennextjs/cloudflare"

// App này không có trang ISR/SSG nào (mọi route đều dynamic — xác thực qua middleware + NextAuth JWT), nên
// không cần bật incremental cache (R2) như mặc định của @opennextjs/cloudflare. Cần ISR sau này thì thêm R2
// bucket vào wrangler.jsonc rồi set incrementalCache (xem https://opennext.js.org/cloudflare/caching).
export default defineCloudflareConfig({})
