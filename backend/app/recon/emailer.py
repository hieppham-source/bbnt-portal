"""
Email gửi BBNT cho brand — hiện chỉ có **chế độ test** (``RECON_EMAIL_MODE=test``).

* Test: thư CHỈ tới ``RECON_EMAIL_TEST_TO`` (1 địa chỉ @hannaholala.com), không CC, tiêu đề có ``[TEST]``.
  Đầu thư ghi To/CC mà bản thật SẼ dùng để người test kiểm tra — bot không gửi tới các địa chỉ đó.
* ``live`` chưa được hỗ trợ: cấu hình ``live`` -> lỗi, không gửi gì (chờ duyệt mẫu thư + cách tìm loopmail).
* Tiêu đề / To / CC theo skill nhắc đối soát: ``[GROUP] ([BRAND]) x [PHÁP NHÂN B] - FB - [TITLE MAIL | CAMPAIGN]``;
  To = DATA BRAND ``EMAIL BRAND``; CC = email nội bộ mặc định.
"""
from __future__ import annotations

import base64
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from email.message import EmailMessage
from typing import Any

KIND = "ACCEPTANCE_TEST"
TEST_DOMAIN = "@hannaholala.com"
INTERNAL_CC = ("fb@hannaholala.com", "mcnolala@gmail.com", "admin@hannaholala.com", "trang.nguyen@hannaholala.com",
               "huong.nguyen@hannaholala.com", "hieu.nguyen@hannaholala.com", "tu.bui@hannaholala.com",
               "accounting02@hannaholala.com", "accounting01@hannaholala.com", "accounting04@hannaholala.com",
               "huong.phan@hannaholala.com")
GMAIL_SEND = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
_EMAIL = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")


class EmailConfigError(RuntimeError):
    """Cấu hình email sai — dừng bước email, không gửi."""


@dataclass(frozen=True)
class EmailSettings:
    mode: str               # off | test
    test_to: str = ""
    max_per_run: int = 5

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> EmailSettings:
        mode = env.get("RECON_EMAIL_MODE", "off").strip().lower() or "off"
        if mode == "off":
            return cls("off")
        if mode != "test":
            raise EmailConfigError(f"RECON_EMAIL_MODE={mode!r} chưa được hỗ trợ (chỉ 'off' hoặc 'test')")
        to = env.get("RECON_EMAIL_TEST_TO", "").strip().lower()
        if not _EMAIL.match(to) or not to.endswith(TEST_DOMAIN):
            raise EmailConfigError(f"RECON_EMAIL_TEST_TO phải là 1 địa chỉ {TEST_DOMAIN}")
        try:
            limit = int(env.get("RECON_EMAIL_MAX", "5") or "5")
        except ValueError as exc:
            raise EmailConfigError("RECON_EMAIL_MAX phải là số") from exc
        return cls("test", to, max(0, min(limit, 50)))


def split_addresses(text: str) -> list[str]:
    """Ô ``EMAIL BRAND`` có thể chứa nhiều địa chỉ (phẩy / chấm phẩy / xuống dòng). Sai dạng -> ``ValueError``."""
    out = [a.strip().lower() for a in re.split(r"[,;\s]+", text or "") if a.strip()]
    bad = [a for a in out if not _EMAIL.match(a)]
    if bad:
        raise ValueError("EMAIL BRAND sai dạng")
    return list(dict.fromkeys(out))


def subject(group: str, brands: Sequence[str], entity: str, title: str) -> str:
    brand_part = ", ".join(brands)
    head = f"{group} ({brand_part})" if group and group.upper() != brand_part.upper() else brand_part
    return f"{head} x {entity} - FB - {title}"


def _vnd(v: Decimal) -> str:
    return f"{v:,.0f}".replace(",", ".") + " đ"


@dataclass(frozen=True)
class BbntMail:
    group: str
    brands: list[str]
    entity: str             # EZS | SASSY
    title: str              # TITLE MAIL, trống -> campaign
    campaign: str
    bbnt_number: str
    contract_no: str
    nmv: Decimal
    commission: Decimal     # cột K: sau VAT (EZS) / sau cấn trừ (Sassy)
    gmv: Decimal
    brand_to: list[str]
    attachment_name: str
    attachment: bytes


def body(m: BbntMail) -> str:
    return "\n".join([
        f"Kính gửi Quý đối tác {', '.join(m.brands)},",
        "",
        f"HannahOlala gửi Biên bản nghiệm thu (BBNT) chương trình {m.campaign} như file đính kèm:",
        f"- Số BBNT: {m.bbnt_number}",
        f"- Hợp đồng: {m.contract_no}",
        f"- Doanh thu thực (NMV): {_vnd(m.nmv)}",
        f"- GMV: {_vnd(m.gmv)}",
        f"- Hoa hồng: {_vnd(m.commission)}",
        "",
        "Quý đối tác vui lòng kiểm tra, ký BBNT và phản hồi trong luồng mail này giúp HannahOlala.",
        "",
        "Trân trọng,",
        "HannahOlala",
    ])


def build_test_message(m: BbntMail, settings: EmailSettings) -> EmailMessage:
    """Thư test: To = ``settings.test_to`` duy nhất, không CC. To/CC thật chỉ in trong nội dung."""
    if settings.mode != "test" or not settings.test_to.endswith(TEST_DOMAIN):
        raise EmailConfigError("Chỉ gửi được ở chế độ test")
    cc = [a for a in INTERNAL_CC if a not in m.brand_to]
    msg = EmailMessage()
    msg["To"] = settings.test_to
    msg["Subject"] = "[TEST] " + subject(m.group, m.brands, m.entity, m.title)
    msg.set_content("\n".join([
        "===== BẢN TEST — bot KHÔNG gửi thư này cho brand =====",
        f"Khi chạy thật sẽ gửi To: {', '.join(m.brand_to)}",
        f"CC: {', '.join(cc)}",
        "=====================================================",
        "",
        body(m)]))
    msg.add_attachment(m.attachment, maintype="application",
                       subtype="vnd.openxmlformats-officedocument.wordprocessingml.document",
                       filename=m.attachment_name)
    return msg


def recipients(msg: EmailMessage) -> list[str]:
    return [a.strip() for h in ("To", "Cc", "Bcc") for a in (msg.get(h) or "").split(",") if a.strip()]


def gmail_send(session: Any, msg: EmailMessage) -> str:
    """Gửi qua Gmail API (scope gmail.send). Trả về id thư. Lỗi HTTP -> ``RuntimeError`` (không kèm nội dung)."""
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    r = session.post(GMAIL_SEND, json={"raw": raw}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"Gmail HTTP {r.status_code}")
    return str(r.json().get("id", ""))
