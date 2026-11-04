import ReconcileDashboard from "@/components/ReconcileDashboard"

export default function ReconcilePage() {
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Lệnh cần duyệt</h1>
          <p className="hint">Ô FB.JT lệch số, thư chờ gửi, thư brand trả lời và các case bot cần người xử lý.</p>
        </div>
      </div>
      <ReconcileDashboard />
    </>
  )
}
