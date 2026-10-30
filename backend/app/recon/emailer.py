"""
Email nghiệm thu (gửi BBNT + số liệu) — docs/ARCHITECTURE.md 11.2, 15.4.

* ``RECON_EMAIL_MODE=off`` (mặc định): không gửi gì.
* ``test``: thư CHỈ tới ``RECON_EMAIL_TEST_TO`` (1 địa chỉ @hannaholala.com), không CC, tiêu đề ``[TEST]``. Đầu thư
  ghi kết quả Audit Gate, loopmail tìm được và To/CC mà bản thật sẽ dùng — bot không gửi tới các địa chỉ đó.
* ``live``: **reply vào loopmail có sẵn** (tìm theo TITLE MAIL + email brand; không tạo thread mới), To = DATA BRAND
  ``EMAIL BRAND``, CC = email nội bộ. Chỉ gửi khi Audit Gate đạt.
* Nội dung = **EMAIL 2** của "Mẫu email nghiệm thu OM (Bot)" (Google Doc anh Hiệp gửi 30/10/2026): bảng confirm số
  theo pháp nhân (EZS cộng VAT, SASSY cấn trừ thuế), deadline = ngày gửi + 3 ngày làm việc (Q16), đính kèm BBNT.
"""
from __future__ import annotations

import base64
import html
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Any
from zoneinfo import ZoneInfo

from app.core.calculation import normalize_text
from app.recon.rules import BusinessCalendar, confirm_deadline

KIND = "ACCEPTANCE_TEST"
LIVE_KIND = "ACCEPTANCE"
#: Email 1 (nhắc brand điền status link order, tạo loopmail) và bản nhắc lại; chế độ test thêm hậu tố ``_TEST``.
STATUS_REQUEST, STATUS_REMINDER = "STATUS_REQUEST", "STATUS_REMINDER"
TEST_DOMAIN = "@hannaholala.com"
INTERNAL_CC = ("fb@hannaholala.com", "mcnolala@gmail.com", "admin@hannaholala.com", "trang.nguyen@hannaholala.com",
               "huong.nguyen@hannaholala.com", "hieu.nguyen@hannaholala.com", "tu.bui@hannaholala.com",
               "accounting02@hannaholala.com", "accounting01@hannaholala.com", "accounting04@hannaholala.com",
               "huong.phan@hannaholala.com")
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
VN = ZoneInfo("Asia/Ho_Chi_Minh")
SEND_FROM, SEND_TO = time(8, 0), time(18, 0)      # khung giờ tự gửi, T2–T6 giờ VN
_EMAIL = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")


class EmailConfigError(RuntimeError):
    """Cấu hình email sai — dừng bước email, không gửi."""


@dataclass(frozen=True)
class EmailSettings:
    mode: str               # off | test | live
    test_to: str = ""
    max_per_run: int = 5
    signer: str = ""        # {{NGUOI_GUI}} — chữ ký cuối thư (RECON_EMAIL_SIGNER); live bắt buộc

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> EmailSettings:
        mode = env.get("RECON_EMAIL_MODE", "off").strip().lower() or "off"
        if mode == "off":
            return cls("off")
        if mode not in ("test", "live"):
            raise EmailConfigError(f"RECON_EMAIL_MODE={mode!r} không hợp lệ ('off' | 'test' | 'live')")
        to = env.get("RECON_EMAIL_TEST_TO", "").strip().lower()
        if mode == "test" and (not _EMAIL.match(to) or not to.endswith(TEST_DOMAIN)):
            raise EmailConfigError(f"RECON_EMAIL_TEST_TO phải là 1 địa chỉ {TEST_DOMAIN}")
        try:
            limit = int(env.get("RECON_EMAIL_MAX", "5") or "5")
        except ValueError as exc:
            raise EmailConfigError("RECON_EMAIL_MAX phải là số") from exc
        return cls(mode, to if mode == "test" else "", max(0, min(limit, 50)),
                   " ".join(env.get("RECON_EMAIL_SIGNER", "").split()))


def split_addresses(text: str) -> list[str]:
    """Ô ``EMAIL BRAND`` có thể chứa nhiều địa chỉ (phẩy / chấm phẩy / xuống dòng). Sai dạng -> ``ValueError``."""
    out = [a.strip().lower() for a in re.split(r"[,;\s]+", text or "") if a.strip()]
    if any(not _EMAIL.match(a) for a in out):
        raise ValueError("EMAIL BRAND sai dạng")
    return list(dict.fromkeys(out))


def subject(group: str, brands: Sequence[str], entity: str, title: str) -> str:
    brand_part = ", ".join(brands)
    head = f"{group} ({brand_part})" if group and group.upper() != brand_part.upper() else brand_part
    return f"{head} x {entity} - FB - {title}"


def in_send_window(now: datetime) -> bool:
    local = now.astimezone(VN)
    return local.weekday() < 5 and SEND_FROM <= local.time() < SEND_TO


def _vnd(v: Decimal) -> str:
    return f"{v:,.0f}".replace(",", ".")


def _pct(v: Decimal | None) -> str:
    return "-" if v is None else f"{(v * 100).quantize(Decimal('0.01')).normalize():f}%".replace(".", ",")


def template_subject(group: str, brands: Sequence[str], entity: str, campaign: str) -> str:
    """Mẫu: ``{{GROUP_BRAND}} ({{BRAND}}) x {{PHAP_NHAN}} - FB - {{TEN_CAMP}} {{THANG_NGHIEM_THU}}``."""
    name, month = camp_parts(campaign)
    return subject(group, brands, entity, f"{name} {month}".strip())


def camp_parts(campaign: str) -> tuple[str, str]:
    """``'MIX - 07/2026'`` -> (``{{TEN_CAMP}}`` 'MIX', ``{{THANG_NGHIEM_THU}}`` '07/26')."""
    m = re.match(r"^\s*(.+?)\s*-\s*(\d{1,2})\s*/\s*(\d{2,4})\s*$", campaign or "")
    if not m:
        return (campaign or "").strip().upper(), ""
    return m[1].upper(), f"{int(m[2]):02d}/{m[3][-2:]}"


@dataclass(frozen=True)
class BbntMail:
    group: str
    brands: list[str]
    entity: str             # EZS | SASSY
    title: str              # cột TITLE MAIL (trống -> live chặn TITLE_MAIL_MISSING)
    campaign: str
    bbnt_number: str
    contract_no: str
    gmv: Decimal
    cancel_rate: Decimal | None
    nmv: Decimal
    rate: Decimal           # % hoa hồng
    a: Decimal              # commission chưa VAT
    b: Decimal              # VAT (EZS)
    c: Decimal              # cấn trừ thuế (SASSY)
    d: Decimal              # thu hộ brand
    f: Decimal              # phí tư vấn
    e: Decimal              # tổng brand thanh toán thêm = A + B - C - D + F
    vat_rate: Decimal       # % VAT (EZS) hoặc % cấn trừ (SASSY) theo pháp nhân
    brand_to: list[str]
    attachment_name: str
    attachment: bytes

    @property
    def commission(self) -> Decimal:
        """Cột K FB.JT = A + B - C."""
        return self.a + self.b - self.c

    def subject(self) -> str:
        return template_subject(self.group, self.brands, self.entity, self.campaign)

    def rows(self) -> list[tuple[str, str, str, str]]:
        """Bảng confirm số (EMAIL 2) đúng pháp nhân: (khoản, tỷ lệ, thành tiền, ghi chú)."""
        ezs = self.entity == "EZS"
        return [("GMV from HRV", "", _vnd(self.gmv), ""), ("Cancellation", "", _pct(self.cancel_rate), ""),
                ("NMV", "", _vnd(self.nmv), ""),
                ("Tổng Commission được hưởng (chưa VAT)", _pct(self.rate), _vnd(self.a), "A"),
                ("Tiền VAT", _pct(self.vat_rate) if ezs else "0%", _vnd(self.b), "B"),
                ("Cấn trừ thuế", "0%" if ezs else _pct(self.vat_rate), _vnd(self.c), "C"),
                ("Số tiền thu hộ brand", "", _vnd(self.d), "D"), ("Phí tư vấn", "", _vnd(self.f), "F"),
                ("Tổng tiền brand cần thanh toán thêm", "", _vnd(self.e), "E=A+B-C-D+F")]


def deadline(now: datetime, cal: BusinessCalendar | None = None) -> str:
    """``{{DEADLINE}}`` = ngày gửi + 3 **ngày làm việc** (anh Hiệp chốt 30/10/2026 — Q16, thay "+3 ngày" của mẫu)."""
    return confirm_deadline(now.astimezone(VN).date(), cal or BusinessCalendar()).strftime("%d/%m/%Y")


def acceptance_text(m: BbntMail, due: str, signer: str) -> str:
    """EMAIL 2 — bản chữ (phần thân thư, sau phần ghi chú của thư test)."""
    name, month = camp_parts(m.campaign)
    head = f"{', '.join(m.brands)} {name} {month}".strip()
    table = [head] + [" | ".join(x for x in r if x) for r in m.rows()]
    return "\n".join([
        f"Kính gửi anh/chị team {', '.join(m.brands)},", "",
        f"Cảm ơn anh/chị đã cập nhật status đơn hàng. Hannah gửi anh/chị số liệu nghiệm thu campaign "
        f"{name} {month} như sau:".replace("  ", " "), "", *table, "",
        "Đính kèm là biên bản nghiệm thu tương ứng. Nhờ anh/chị:",
        "1. Kiểm tra và xác nhận số liệu trên bằng cách reply mail này;",
        "2. Ký, đóng dấu biên bản nghiệm thu và gửi lại bản scan cho Hannah,",
        f"trước ngày {due}. Nếu có chênh lệch số liệu, anh/chị phản hồi chi tiết đơn hàng trong cùng thời hạn "
        "để hai bên đối chiếu.",
        "Sau khi nhận được xác nhận, Hannah sẽ gửi hóa đơn nháp để anh/chị kiểm tra.", "",
        "Trân trọng,", signer or "(chưa cấu hình RECON_EMAIL_SIGNER)", "Hannah Olala"])


def acceptance_html(m: BbntMail, due: str, signer: str) -> str:
    """EMAIL 2 — bản HTML (bảng confirm số như mẫu)."""
    e = html.escape
    name, month = camp_parts(m.campaign)
    td = 'style="border:1px solid #999;padding:4px 8px"'
    rows = "".join(
        f"<tr><td {td}>{'<b>' + e(r[0]) + '</b>' if r[3].startswith('E') else e(r[0])}</td><td {td}>{e(r[1])}</td>"
        f'<td {td} align="right">{"<b>" + e(r[2]) + "</b>" if r[3].startswith("E") else e(r[2])}</td>'
        f"<td {td}>{e(r[3])}</td></tr>" for r in m.rows())
    camp = e(f"{name} {month}".strip())
    return (
        f"<p>Kính gửi anh/chị team {e(', '.join(m.brands))},</p>"
        f"<p>Cảm ơn anh/chị đã cập nhật status đơn hàng. Hannah gửi anh/chị số liệu nghiệm thu campaign "
        f"<b>{camp}</b> như sau:</p>"
        f'<table style="border-collapse:collapse"><tr><th {td}>{e(", ".join(m.brands))} {camp}</th><th {td}></th>'
        f"<th {td}>Thành tiền</th><th {td}>Ghi chú</th></tr>{rows}</table>"
        "<p>Đính kèm là <b>biên bản nghiệm thu</b> tương ứng. Nhờ anh/chị:</p><ol>"
        "<li>Kiểm tra và <b>xác nhận số liệu</b> trên bằng cách reply mail này;</li>"
        "<li><b>Ký, đóng dấu biên bản nghiệm thu</b> và gửi lại bản scan cho Hannah,</li></ol>"
        f"<p>trước ngày <b>{e(due)}</b>. Nếu có chênh lệch số liệu, anh/chị phản hồi chi tiết đơn hàng trong cùng "
        "thời hạn để hai bên đối chiếu.</p>"
        "<p>Sau khi nhận được xác nhận, Hannah sẽ gửi hóa đơn nháp để anh/chị kiểm tra.</p>"
        f"<p>Trân trọng,<br>{e(signer)}<br>Hannah Olala</p>")


def body(m: BbntMail) -> str:
    """Bản lưu ``email_outbox.body_html`` (không phụ thuộc ngày gửi)."""
    return acceptance_text(m, "{{DEADLINE}}", "{{NGUOI_GUI}}")


# ----------------------------------------------------------------------------------------------- loopmail
@dataclass(frozen=True)
class Thread:
    id: str
    subject: str                     # tiêu đề thư đầu thread
    message_ids: list[str]           # Message-ID (RFC 822) theo thứ tự thời gian
    participants: frozenset[str]     # mọi địa chỉ From/To/Cc trong thread (chữ thường)
    last_at: str = ""                # internalDate thư cuối (ms) — để KAM chọn

    def summary(self) -> dict[str, str]:
        return {"id": self.id, "subject": self.subject, "last_at": self.last_at}


def _clean_subject(s: str) -> str:
    s = normalize_text(s)
    while (m := re.match(r"^(re|fw|fwd|tl|tr|trả lời|chuyển tiếp)\s*:\s*", s)):
        s = s[m.end():]
    return s.strip()


def matches_loopmail(t: Thread, title: str, brand_to: Sequence[str]) -> bool:
    """Tiêu đề thread (bỏ Re:/Fw:) = TITLE MAIL **và** thread có ít nhất 1 email brand."""
    return _clean_subject(t.subject) == _clean_subject(title) and bool(t.participants & set(brand_to))


class GmailApi:
    """Gmail REST (scope ``gmail.readonly`` để tìm thread, ``gmail.send`` để gửi). Lỗi -> ``RuntimeError`` chỉ có
    mã HTTP (không lộ nội dung thư)."""

    def __init__(self, session: Any) -> None:
        self._s = session

    def _ok(self, r: Any) -> dict[str, Any]:
        if r.status_code != 200:
            raise RuntimeError(f"Gmail HTTP {r.status_code}")
        return dict(r.json())

    def search(self, title: str) -> list[str]:
        q = 'subject:"' + title.replace('"', " ") + '"'
        r = self._ok(self._s.get(f"{GMAIL}/threads", params={"q": q, "maxResults": 10}, timeout=60))
        return [t["id"] for t in r.get("threads", [])]

    def thread(self, thread_id: str) -> Thread:
        r = self._ok(self._s.get(f"{GMAIL}/threads/{thread_id}", timeout=60, params=[
            ("format", "metadata"), *(("metadataHeaders", h) for h in ("Subject", "Message-ID", "From", "To", "Cc"))]))
        msgs = r.get("messages", [])
        ids, people, first_subject = [], set(), ""
        for i, msg in enumerate(msgs):
            h = {x["name"].lower(): x["value"] for x in msg.get("payload", {}).get("headers", [])}
            if i == 0:
                first_subject = h.get("subject", "")
            if h.get("message-id"):
                ids.append(h["message-id"])
            for k in ("from", "to", "cc"):
                people |= {a.lower() for a in re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", h.get(k, ""))}
        return Thread(thread_id, first_subject, ids, frozenset(people), str(msgs[-1].get("internalDate", ""))
                      if msgs else "")

    def send(self, msg: EmailMessage, thread_id: str | None = None) -> tuple[str, str]:
        """(gmail message id, thread id)."""
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        payload: dict[str, str] = {"raw": raw}
        if thread_id:
            payload["threadId"] = thread_id
        r = self._ok(self._s.post(f"{GMAIL}/messages/send", json=payload, timeout=60))
        return str(r.get("id", "")), str(r.get("threadId", ""))


def find_loopmail(gmail: Any, title: str, brand_to: Sequence[str]) -> list[Thread]:
    """Các thread khớp loopmail. Đúng 1 -> dùng; 0 / nhiều -> KAM chọn trên portal."""
    return [t for t in (gmail.thread(i) for i in gmail.search(title)) if matches_loopmail(t, title, brand_to)]


# ----------------------------------------------------------------------------------------------- Email 1
@dataclass(frozen=True)
class StatusMail:
    """Email 1 (G + 25, brand chưa điền đủ status link order) và bản nhắc lại."""

    group: str
    brands: list[str]
    entity: str
    title: str                          # TITLE MAIL (tìm loopmail tay có sẵn)
    campaign: str
    link: str                           # {{LINK_ORDER}}
    period: tuple[date, date] | None    # {{TU_NGAY}}, {{DEN_NGAY}} = ngày đơn đầu / cuối trong file order
    brand_to: list[str]

    def subject(self) -> str:
        return template_subject(self.group, self.brands, self.entity, self.campaign)


def _camp(campaign: str) -> str:
    name, month = camp_parts(campaign)
    return f"{name} {month}".strip()


def status_request_text(m: StatusMail, due: str, signer: str) -> str:
    """EMAIL 1 — nhắc điền status vào link order (mail mới, tạo loopmail)."""
    when = f" (từ {m.period[0]:%d/%m/%Y} đến {m.period[1]:%d/%m/%Y})" if m.period else ""
    return "\n".join([
        f"Kính gửi anh/chị team {', '.join(m.brands)},", "",
        f"Hannah Olala xin gửi anh/chị danh sách đơn hàng của campaign {_camp(m.campaign)}{when} để phục vụ đối soát "
        "và nghiệm thu.", "", f"Link order: {m.link}", "",
        "Nhờ anh/chị cập nhật status giao hàng cho từng đơn (Giao thành công / Hoàn / Hủy) vào file trên trước ngày "
        f"{due}.",
        "Sau khi brand cập nhật xong, Hannah sẽ tổng hợp số liệu và gửi bảng confirm số cùng biên bản nghiệm thu trong "
        "luồng mail này.",
        "Nếu có đơn chưa rõ trạng thái hoặc cần hỗ trợ, anh/chị phản hồi ngay trong mail này giúp Hannah nhé.", "",
        "Trân trọng,", signer or "(chưa cấu hình RECON_EMAIL_SIGNER)", "Hannah Olala"])


def status_reminder_text(m: StatusMail, due: str, signer: str) -> str:
    """EMAIL 1 — bản nhắc lại (quá hạn chưa cập nhật, reply vào luồng)."""
    return "\n".join([
        f"Kính gửi anh/chị team {', '.join(m.brands)},", "",
        f"Hannah xin nhắc lại: file order campaign {_camp(m.campaign)} hiện vẫn chưa được cập nhật đầy đủ status "
        f"({m.link}). Anh/chị vui lòng hoàn tất trước ngày {due} để hai bên kịp chốt số nghiệm thu.", "",
        "Trân trọng,", signer or "(chưa cấu hình RECON_EMAIL_SIGNER)"])


# ----------------------------------------------------------------------------------------------- messages
def _attach(msg: EmailMessage, m: BbntMail) -> None:
    msg.add_attachment(m.attachment, maintype="application",
                       subtype="vnd.openxmlformats-officedocument.wordprocessingml.document",
                       filename=m.attachment_name)


def cc_for(brand_to: Sequence[str]) -> list[str]:
    return [a for a in INTERNAL_CC if a not in brand_to]


@dataclass(frozen=True)
class TestNotes:
    """Kết quả bot sẽ làm ở chế độ thật — in đầu thư test."""

    gate: tuple[str, ...] = ()                 # lý do chặn (rỗng = chế độ thật sẽ gửi)
    loopmail: str = ""
    extra: list[str] = field(default_factory=list)


def envelope(settings: EmailSettings, brand_to: Sequence[str], subject_line: str, text: str, *,
             thread: Thread | None = None, notes: TestNotes | None = None, html_body: str | None = None
             ) -> EmailMessage:
    """live: To brand, CC nội bộ; có ``thread`` -> reply (Re:, In-Reply-To/References), không -> luồng mới với
    ``subject_line``. test: To = ``settings.test_to`` duy nhất, đầu thư ghi To/CC/loopmail mà bản thật sẽ dùng."""
    msg = EmailMessage()
    if settings.mode == "live":
        msg["To"] = ", ".join(brand_to)
        msg["Cc"] = ", ".join(cc_for(brand_to))
        if thread is not None:
            msg["Subject"] = "Re: " + re.sub(r"^\s*(re|fw|fwd)\s*:\s*", "", thread.subject, flags=re.I)
            if thread.message_ids:
                msg["In-Reply-To"] = thread.message_ids[-1]
                msg["References"] = " ".join(thread.message_ids)
        else:
            msg["Subject"] = subject_line
        msg["Message-ID"] = make_msgid(domain="hannaholala.com")
        msg.set_content(text)
        if html_body:
            msg.add_alternative(html_body, subtype="html")
        return msg
    if settings.mode != "test" or not settings.test_to.endswith(TEST_DOMAIN):
        raise EmailConfigError("Chỉ gửi được ở chế độ test")
    n = notes or TestNotes()
    msg["To"] = settings.test_to
    msg["Subject"] = "[TEST] " + subject_line
    msg.set_content("\n".join([
        "===== BẢN TEST — bot KHÔNG gửi thư này cho brand =====",
        "Chế độ thật: " + ("SẼ GỬI" if not n.gate else "CHƯA GỬI — " + ", ".join(n.gate)),
        f"Loopmail: {n.loopmail or '-'}",
        f"Khi chạy thật sẽ gửi To: {', '.join(brand_to)}",
        f"CC: {', '.join(cc_for(brand_to))}", *n.extra,
        "=====================================================", "", text]))
    return msg


def build_test_message(m: BbntMail, settings: EmailSettings, notes: TestNotes | None = None, *,
                       now: datetime, cal: BusinessCalendar | None = None) -> EmailMessage:
    """Thư test EMAIL 2: To = ``settings.test_to`` duy nhất, không CC."""
    msg = envelope(settings, m.brand_to, m.subject(), acceptance_text(m, deadline(now, cal), settings.signer),
                   notes=notes)
    _attach(msg, m)
    return msg


def build_live_reply(m: BbntMail, thread: Thread | None, *, now: datetime, signer: str,
                     cal: BusinessCalendar | None = None) -> EmailMessage:
    """EMAIL 2 thật: reply vào loopmail (``thread``); chưa có loopmail -> luồng mới theo tiêu đề mẫu."""
    due = deadline(now, cal)
    msg = envelope(EmailSettings("live", signer=signer), m.brand_to, m.subject(), acceptance_text(m, due, signer),
                   thread=thread, html_body=acceptance_html(m, due, signer))
    _attach(msg, m)
    return msg


def recipients(msg: EmailMessage) -> list[str]:
    return [a.strip() for h in ("To", "Cc", "Bcc") for a in (msg.get(h) or "").split(",") if a.strip()]
