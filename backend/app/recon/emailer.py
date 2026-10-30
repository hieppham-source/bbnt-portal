"""
Email nghiệm thu (gửi BBNT + số liệu) — docs/ARCHITECTURE.md 11.2, 15.4.

* ``RECON_EMAIL_MODE=off`` (mặc định): không gửi gì.
* ``test``: thư CHỈ tới ``RECON_EMAIL_TEST_TO`` (1 địa chỉ @hannaholala.com), không CC, tiêu đề ``[TEST]``. Đầu thư
  ghi kết quả Audit Gate, loopmail tìm được và To/CC mà bản thật sẽ dùng — bot không gửi tới các địa chỉ đó.
* ``live``: **reply vào loopmail có sẵn** (tìm theo TITLE MAIL + email brand; không tạo thread mới), To = DATA BRAND
  ``EMAIL BRAND``, CC = email nội bộ, nội dung = mẫu ``ACCEPTANCE`` đã DUYỆT trong DB. Chỉ gửi khi Audit Gate đạt.
"""
from __future__ import annotations

import base64
import html
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, time
from decimal import Decimal
from email.message import EmailMessage
from typing import Any
from zoneinfo import ZoneInfo

from app.core.calculation import normalize_text

KIND = "ACCEPTANCE_TEST"
LIVE_KIND = "ACCEPTANCE"
TEST_DOMAIN = "@hannaholala.com"
INTERNAL_CC = ("fb@hannaholala.com", "mcnolala@gmail.com", "admin@hannaholala.com", "trang.nguyen@hannaholala.com",
               "huong.nguyen@hannaholala.com", "hieu.nguyen@hannaholala.com", "tu.bui@hannaholala.com",
               "accounting02@hannaholala.com", "accounting01@hannaholala.com", "accounting04@hannaholala.com",
               "huong.phan@hannaholala.com")
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
VN = ZoneInfo("Asia/Ho_Chi_Minh")
SEND_FROM, SEND_TO = time(8, 0), time(18, 0)      # khung giờ tự gửi, T2–T6 giờ VN
#: Biến được dùng trong mẫu ACCEPTANCE (``{bbnt_number}``…). Biến lạ -> TEMPLATE_INVALID, không gửi.
TEMPLATE_FIELDS = ("brands", "group", "campaign", "bbnt_number", "contract_no", "nmv", "gmv", "commission")
_EMAIL = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")


class EmailConfigError(RuntimeError):
    """Cấu hình email sai — dừng bước email, không gửi."""


@dataclass(frozen=True)
class EmailSettings:
    mode: str               # off | test | live
    test_to: str = ""
    max_per_run: int = 5

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
        return cls(mode, to if mode == "test" else "", max(0, min(limit, 50)))


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
    return f"{v:,.0f}".replace(",", ".") + " đ"


@dataclass(frozen=True)
class BbntMail:
    group: str
    brands: list[str]
    entity: str             # EZS | SASSY
    title: str              # cột TITLE MAIL (trống -> "" ; live chặn TITLE_MAIL_MISSING)
    campaign: str
    bbnt_number: str
    contract_no: str
    nmv: Decimal
    commission: Decimal     # cột K: sau VAT (EZS) / sau cấn trừ (Sassy)
    gmv: Decimal
    brand_to: list[str]
    attachment_name: str
    attachment: bytes

    def fields(self) -> dict[str, str]:
        return {"brands": ", ".join(self.brands), "group": self.group, "campaign": self.campaign,
                "bbnt_number": self.bbnt_number, "contract_no": self.contract_no, "nmv": _vnd(self.nmv),
                "gmv": _vnd(self.gmv), "commission": _vnd(self.commission)}


def body(m: BbntMail) -> str:
    """Nội dung mặc định (chế độ test, chưa có mẫu duyệt)."""
    f = m.fields()
    return "\n".join([
        f"Kính gửi Quý đối tác {f['brands']},", "",
        f"HannahOlala gửi Biên bản nghiệm thu (BBNT) chương trình {m.campaign} như file đính kèm:",
        f"- Số BBNT: {m.bbnt_number}", f"- Hợp đồng: {m.contract_no}", f"- Doanh thu thực (NMV): {f['nmv']}",
        f"- GMV: {f['gmv']}", f"- Hoa hồng: {f['commission']}", "",
        "Quý đối tác vui lòng kiểm tra, ký BBNT và phản hồi trong luồng mail này giúp HannahOlala.", "",
        "Trân trọng,", "HannahOlala"])


def render_template(body_html: str, m: BbntMail) -> str:
    """Mẫu ``ACCEPTANCE`` đã duyệt: ``{bbnt_number}``… -> giá trị (đã escape HTML). Biến lạ -> ``KeyError``."""
    return body_html.format_map({k: html.escape(v) for k, v in m.fields().items()})


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


# ----------------------------------------------------------------------------------------------- messages
def _attach(msg: EmailMessage, m: BbntMail) -> None:
    msg.add_attachment(m.attachment, maintype="application",
                       subtype="vnd.openxmlformats-officedocument.wordprocessingml.document",
                       filename=m.attachment_name)


def cc_for(m: BbntMail) -> list[str]:
    return [a for a in INTERNAL_CC if a not in m.brand_to]


@dataclass(frozen=True)
class TestNotes:
    """Kết quả bot sẽ làm ở chế độ thật — in đầu thư test."""

    gate: tuple[str, ...] = ()                 # lý do Audit Gate không đạt (rỗng = đạt)
    loopmail: str = ""
    extra: list[str] = field(default_factory=list)


def build_test_message(m: BbntMail, settings: EmailSettings, notes: TestNotes | None = None) -> EmailMessage:
    """Thư test: To = ``settings.test_to`` duy nhất, không CC. To/CC thật chỉ in trong nội dung."""
    if settings.mode != "test" or not settings.test_to.endswith(TEST_DOMAIN):
        raise EmailConfigError("Chỉ gửi được ở chế độ test")
    n = notes or TestNotes()
    msg = EmailMessage()
    msg["To"] = settings.test_to
    msg["Subject"] = "[TEST] " + subject(m.group, m.brands, m.entity, m.title or m.campaign)
    msg.set_content("\n".join([
        "===== BẢN TEST — bot KHÔNG gửi thư này cho brand =====",
        "Audit Gate: " + ("ĐẠT — chế độ thật sẽ tự gửi" if not n.gate else "KHÔNG ĐẠT: " + ", ".join(n.gate)),
        f"Loopmail: {n.loopmail or '-'}",
        f"Khi chạy thật sẽ gửi To: {', '.join(m.brand_to)}",
        f"CC: {', '.join(cc_for(m))}", *n.extra,
        "=====================================================", "", body(m)]))
    _attach(msg, m)
    return msg


def build_live_reply(m: BbntMail, thread: Thread, body_html: str) -> EmailMessage:
    """Reply vào loopmail: To brand, CC nội bộ, Re: tiêu đề thread, In-Reply-To/References để nối luồng phía brand."""
    msg = EmailMessage()
    msg["To"] = ", ".join(m.brand_to)
    msg["Cc"] = ", ".join(cc_for(m))
    msg["Subject"] = "Re: " + re.sub(r"^\s*(re|fw|fwd)\s*:\s*", "", thread.subject, flags=re.I)
    if thread.message_ids:
        msg["In-Reply-To"] = thread.message_ids[-1]
        msg["References"] = " ".join(thread.message_ids)
    msg.set_content(re.sub(r"<[^>]+>", "", body_html))
    msg.add_alternative(body_html, subtype="html")
    _attach(msg, m)
    return msg


def recipients(msg: EmailMessage) -> list[str]:
    return [a.strip() for h in ("To", "Cc", "Bcc") for a in (msg.get(h) or "").split(",") if a.strip()]
