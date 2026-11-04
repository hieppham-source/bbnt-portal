import Link from "next/link"

import BbntForm from "@/components/BbntForm"

export default function BbntPage() {
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Biên bản nghiệm thu</h1>
          <p className="hint">Lập BBNT kênh FB từ link file order. Brand, hợp đồng, Bên A lấy từ tab DATA BRAND (FB.JT).</p>
        </div>
        <Link href="/master" className="btn-ghost">Kiểm tra dữ liệu master</Link>
      </div>
      <BbntForm />
    </>
  )
}
