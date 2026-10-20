"""
BBNT Numbering Engine.

Format::

    MM.YY/BBNT-{BRAND}-{ENTITY}-{CHANNEL}-{SUFFIX}

    06.26/BBNT-ORGANICA-EZ-FB-BDAY-05
    10.26/BBNT-SHISEIDO_GRP-SS-TTS-Q3-CAMP

* ``MM.YY``  — tháng/năm LẬP biên bản, quy đổi về ``Asia/Ho_Chi_Minh`` tại thời điểm phát hành.
* ``BRAND``  — brand code (1 brand) hoặc group brand code (nhiều brand cùng group).
* ``ENTITY`` — ``EZ`` (EZS) | ``SS`` (SASSY).
* ``CHANNEL``— ``FB`` | ``TTS``.
* ``SUFFIX`` — "đuôi biên bản nghiệm thu" người làm TỰ GÕ (không suy ra từ kỳ nghiệm thu nữa — link order mặc
  định tính TOÀN BỘ dòng đơn, không giới hạn theo tháng). Tự do gõ chữ/số/khoảng trắng, portal tự chuẩn hoá
  (in hoa, gộp khoảng trắng/ký tự lạ liên tiếp thành 1 gạch ngang) qua :func:`normalize_suffix`.

Toàn bộ là hàm thuần (không DB, không I/O). Số BBNT tất định: cùng brand/pháp nhân/kênh/đuôi và cùng tháng lập
-> cùng số, nên tạo lại BBNT (dán lại link order, gõ lại đúng đuôi cũ) cho ra đúng số cũ. Tool không lưu BBNT
nên không cấp số tăng dần.
"""
from __future__ import annotations

import itertools
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

DOC_TOKEN = "BBNT"

#: Đuôi biên bản nghiệm thu sau chuẩn hoá: A-Z, 0-9, gạch ngang đơn (không ở đầu/cuối).
_SUFFIX_RE = re.compile(r"^[A-Z0-9]+(?:-[A-Z0-9]+)*$")
SUFFIX_MAX_LEN = 60

#: Mã pháp nhân hệ thống -> token ENTITY trong số BBNT (đồng bộ legal_entities.number_code).
ENTITY_NUMBER_CODES: dict[str, str] = {"EZS": "EZ", "SASSY": "SS"}

#: Channel được phép đánh số. Thêm "SHP" khi Shopee bắt đầu ra BBNT.
SUPPORTED_CHANNELS: frozenset[str] = frozenset({"FB", "TTS"})


# Code chỉ gồm A-Z 0-9 _ để không phá vỡ các dấu phân cách '-', '/', ',', '.' trong số BBNT.
_CODE_RE = re.compile(r"^[A-Z0-9_]+$")


# =============================================================================
# Errors
# =============================================================================
class NumberingError(ValueError):
    """Input không hợp lệ để đánh số BBNT."""


# =============================================================================
# Đuôi biên bản nghiệm thu (suffix) — người làm tự gõ, thay cho kỳ nghiệm thu cũ
# =============================================================================
def normalize_suffix(raw: str) -> str:
    """Chuẩn hoá "đuôi biên bản nghiệm thu" người dùng tự gõ trước khi ráp vào số BBNT: in hoa, gộp mọi
    khoảng trắng/ký tự không phải chữ-số liên tiếp thành 1 gạch ngang, bỏ gạch ngang thừa ở đầu/cuối.

    >>> normalize_suffix("  bday dot 1 ")
    'BDAY-DOT-1'
    >>> normalize_suffix("Q3-CAMP")
    'Q3-CAMP'
    """
    s = re.sub(r"[^A-Z0-9]+", "-", raw.strip().upper()).strip("-")
    if not s:
        raise NumberingError("Đuôi biên bản nghiệm thu không được để trống")
    if len(s) > SUFFIX_MAX_LEN:
        raise NumberingError(f"Đuôi biên bản nghiệm thu quá dài ({len(s)} ký tự, tối đa {SUFFIX_MAX_LEN})")
    return s


# =============================================================================
# YearMonth & period formatting
# =============================================================================
@dataclass(frozen=True, order=True)
class YearMonth:
    """Một tháng dương lịch. So sánh/sort theo (year, month)."""

    year: int
    month: int

    def __post_init__(self) -> None:
        if not isinstance(self.year, int) or not isinstance(self.month, int):
            raise NumberingError(f"YearMonth cần int, nhận {self.year!r}/{self.month!r}")
        if not 1 <= self.month <= 12:
            raise NumberingError(f"Tháng không hợp lệ: {self.month}")
        if not 2000 <= self.year <= 2099:
            raise NumberingError(f"Năm ngoài phạm vi 2000-2099: {self.year}")

    @property
    def index(self) -> int:
        """Số thứ tự tuyệt đối của tháng — 2 tháng liên tiếp chênh nhau đúng 1 (kể cả qua năm)."""
        return self.year * 12 + (self.month - 1)

    @property
    def yy(self) -> str:
        return f"{self.year % 100:02d}"

    @property
    def mm(self) -> str:
        return f"{self.month:02d}"

    @property
    def label(self) -> str:
        """``'05.26'``"""
        return f"{self.mm}.{self.yy}"

    def first_day(self) -> date:
        return date(self.year, self.month, 1)

    @classmethod
    def of(cls, value: YearMonthLike) -> YearMonth:
        """Chuẩn hoá mọi kiểu input về YearMonth.

        Chấp nhận: ``YearMonth``, ``date``/``datetime`` (lấy năm-tháng, KHÔNG đổi timezone),
        ``(year, month)``, chuỗi ``'05.26'``, ``'5.26'``, ``'05.2026'``, ``'2026-05'``, ``'05/2026'``.
        """
        if isinstance(value, YearMonth):
            return value
        if isinstance(value, (date, datetime)):
            return cls(value.year, value.month)
        if isinstance(value, tuple) and len(value) == 2:
            return cls(int(value[0]), int(value[1]))
        if isinstance(value, str):
            s = value.strip()
            m = re.fullmatch(r"(\d{1,2})[./](\d{2}|\d{4})", s)
            if m:
                month, year = int(m.group(1)), int(m.group(2))
                return cls(year if year >= 100 else 2000 + year, month)
            m = re.fullmatch(r"(\d{4})-(\d{1,2})", s)
            if m:
                return cls(int(m.group(1)), int(m.group(2)))
        raise NumberingError(f"Không đọc được tháng nghiệm thu: {value!r}")


YearMonthLike = YearMonth | date | datetime | tuple[int, int] | str


def normalize_months(months: Iterable[YearMonthLike]) -> tuple[YearMonth, ...]:
    """Parse, loại trùng và sắp xếp tăng dần."""
    result = tuple(sorted({YearMonth.of(m) for m in months}))
    if not result:
        raise NumberingError("Cần ít nhất 1 tháng nghiệm thu")
    return result


def _is_contiguous(months: Sequence[YearMonth]) -> bool:
    return all(b.index - a.index == 1 for a, b in itertools.pairwise(months))


def format_period(months: Iterable[YearMonthLike]) -> str:
    """Định dạng tháng nghiệm thu.

    Quy tắc (sau khi dedupe + sort):

    ========================  ======================  =================
    Trường hợp                Ví dụ input             Output
    ========================  ======================  =================
    1 tháng                   05/2026                 ``05.26``
    Liên tục, cùng năm        07, 08, 09/2026         ``07-09.26``
    Không liên tục, cùng năm  09, 05, 07, 05/2026     ``05,07,09.26``
    Liên tục, qua năm         11/25, 12/25, 01/26     ``11.25-01.26``
    Không liên tục, qua năm   10/25, 12/25, 01/26     ``10.25,12.25,01.26``
    ========================  ======================  =================

    Chuỗi tháng chỉ "liên tục" khi KHÔNG có lỗ hổng; có 1 lỗ hổng thì liệt kê đủ từng tháng
    (vd 05,07,08,09 -> ``05,07,08,09.26``) đúng theo spec "không liên tục".
    """
    ms = normalize_months(months)
    same_year = len({m.year for m in ms}) == 1
    if len(ms) == 1:
        return ms[0].label
    if _is_contiguous(ms):
        if same_year:
            return f"{ms[0].mm}-{ms[-1].mm}.{ms[0].yy}"
        return f"{ms[0].label}-{ms[-1].label}"
    if same_year:
        return ",".join(m.mm for m in ms) + f".{ms[0].yy}"
    return ",".join(m.label for m in ms)


def parse_period(label: str) -> tuple[YearMonth, ...]:
    """Đảo ngược :func:`format_period`. Raise :class:`NumberingError` nếu sai cú pháp."""
    s = label.strip()
    if m := re.fullmatch(r"(\d{2})\.(\d{2})", s):
        return (YearMonth(2000 + int(m[2]), int(m[1])),)
    if m := re.fullmatch(r"(\d{2})-(\d{2})\.(\d{2})", s):
        start, end, year = int(m[1]), int(m[2]), 2000 + int(m[3])
        if start >= end:
            raise NumberingError(f"Khoảng tháng không hợp lệ: {label}")
        return tuple(YearMonth(year, mo) for mo in range(start, end + 1))
    if m := re.fullmatch(r"(\d{2}(?:,\d{2})+)\.(\d{2})", s):
        year = 2000 + int(m[2])
        months = tuple(YearMonth(year, int(x)) for x in m[1].split(","))
        if list(months) != sorted(set(months)):
            raise NumberingError(f"Danh sách tháng phải tăng dần, không trùng: {label}")
        return months
    if m := re.fullmatch(r"(\d{2}\.\d{2})-(\d{2}\.\d{2})", s):
        a, b = YearMonth.of(m[1]), YearMonth.of(m[2])
        if a >= b:
            raise NumberingError(f"Khoảng tháng không hợp lệ: {label}")
        return tuple(_from_index(i) for i in range(a.index, b.index + 1))
    if re.fullmatch(r"\d{2}\.\d{2}(?:,\d{2}\.\d{2})+", s):
        months = tuple(YearMonth.of(x) for x in s.split(","))
        if list(months) != sorted(set(months)):
            raise NumberingError(f"Danh sách tháng phải tăng dần, không trùng: {label}")
        return months
    raise NumberingError(f"Không đọc được kỳ nghiệm thu: {label!r}")


def _from_index(i: int) -> YearMonth:
    year, month0 = divmod(i, 12)
    return YearMonth(year, month0 + 1)


# =============================================================================
# Issue time (Asia/Ho_Chi_Minh)
# =============================================================================
def to_vn_time(moment: datetime) -> datetime:
    """Quy đổi về giờ Việt Nam. Từ chối datetime naive để tránh hiểu nhầm UTC/local."""
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise NumberingError(
            "issued_at phải có timezone (vd datetime.now(timezone.utc)); datetime naive bị từ chối")
    return moment.astimezone(VN_TZ)


def issue_month_of(issued_at: datetime) -> YearMonth:
    """Tháng LẬP biên bản theo giờ VN. 2026-05-31T17:30Z -> 06/2026."""
    return YearMonth.of(to_vn_time(issued_at))


# =============================================================================
# Scope / entity / channel resolution
# =============================================================================
@dataclass(frozen=True)
class BrandRef:
    """Brand tham gia BBNT. ``group_code`` = mã group brand (None nếu brand đứng riêng)."""

    code: str
    group_code: str | None = None


def _clean_code(value: str, what: str) -> str:
    code = (value or "").strip().upper()
    if not _CODE_RE.fullmatch(code):
        raise NumberingError(f"{what} '{value}' chỉ được chứa A-Z, 0-9, '_'")
    return code


def resolve_scope_code(brands: Sequence[BrandRef]) -> str:
    """Token BRAND: 1 brand -> brand code; nhiều brand -> group code chung.

    Raise nếu nhiều brand nhưng không cùng group (hoặc có brand không thuộc group nào).
    Brand trùng lặp được gộp trước khi xét.
    """
    unique: dict[str, BrandRef] = {}
    for b in brands:
        unique.setdefault(_clean_code(b.code, "Brand code"), b)
    if not unique:
        raise NumberingError("Cần ít nhất 1 brand")
    if len(unique) == 1:
        return next(iter(unique))
    groups = {_clean_code(b.group_code, "Group code") if b.group_code else None for b in unique.values()}
    if None in groups:
        missing = sorted(c for c, b in unique.items() if not b.group_code)
        raise NumberingError(f"Gộp nhiều brand nhưng brand không thuộc group: {', '.join(missing)}")
    if len(groups) > 1:
        raise NumberingError(f"Các brand thuộc nhiều group khác nhau: {', '.join(sorted(groups))}")  # type: ignore[arg-type]
    return groups.pop()  # type: ignore[return-value]


def resolve_entity_code(entity: str) -> str:
    """'EZS'|'ezs'|'EZ' -> 'EZ'; 'SASSY'|'Sassy'|'SS' -> 'SS'."""
    key = (entity or "").strip().upper()
    if key in ENTITY_NUMBER_CODES:
        return ENTITY_NUMBER_CODES[key]
    if key in ENTITY_NUMBER_CODES.values():
        return key
    raise NumberingError(f"Pháp nhân không hỗ trợ: {entity!r}")


def resolve_channel_code(channel: str) -> str:
    key = (channel or "").strip().upper()
    if key not in SUPPORTED_CHANNELS:
        raise NumberingError(f"Channel không hỗ trợ: {channel!r} (hỗ trợ: {', '.join(sorted(SUPPORTED_CHANNELS))})")
    return key


# =============================================================================
# BbntNumber value object
# =============================================================================
@dataclass(frozen=True)
class BbntNumber:
    """Số BBNT đã phân rã. ``str(number)`` cho ra chuỗi in trên biên bản. ``suffix`` ("đuôi biên bản nghiệm
    thu") do người làm TỰ GÕ, đã chuẩn hoá qua :func:`normalize_suffix` — KHÔNG còn suy ra từ kỳ nghiệm thu."""

    issue_month: YearMonth
    scope_code: str
    entity_code: str
    channel_code: str
    suffix: str
    revision_no: int = 0
    issue_date: date | None = field(default=None, compare=False)  # ngày lập (VN) — lưu vào DB

    def __post_init__(self) -> None:
        if self.revision_no < 0:
            raise NumberingError("revision_no phải >= 0")

    @property
    def business_key(self) -> str:
        """Phần định danh nghiệp vụ, không phụ thuộc tháng lập — dùng cho unique 'live'."""
        return "-".join((DOC_TOKEN, self.scope_code, self.entity_code, self.channel_code, self.suffix))

    @property
    def scope_prefix(self) -> str:
        """``BBNT-{scope}-{entity}-{channel}-`` — dùng tìm BBNT cùng scope/pháp nhân/kênh."""
        return "-".join((DOC_TOKEN, self.scope_code, self.entity_code, self.channel_code)) + "-"

    @property
    def lock_key(self) -> str:
        return f"bbnt:{self.entity_code}:{self.channel_code}:{self.scope_code}"

    @property
    def value(self) -> str:
        rev_suffix = f"-R{self.revision_no}" if self.revision_no else ""
        return f"{self.issue_month.label}/{self.business_key}{rev_suffix}"

    def with_revision(self, revision_no: int) -> BbntNumber:
        return replace(self, revision_no=revision_no)

    def __str__(self) -> str:
        return self.value


def generate_bbnt_number(
    *,
    brands: Sequence[BrandRef],
    entity: str,
    channel: str,
    suffix: str,
    issued_at: datetime | None = None,
    revision_no: int = 0,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> BbntNumber:
    """Sinh số BBNT (pure — KHÔNG kiểm tra trùng trong DB; dùng :func:`reserve_bbnt_number` để phát hành).

    Args:
        brands: Brand tham gia. 1 brand -> brand code; nhiều brand -> group code.
        entity: ``EZS``/``SASSY`` (hoặc ``EZ``/``SS``).
        channel: ``FB``/``TTS``.
        suffix: "Đuôi biên bản nghiệm thu" người làm tự gõ — chuẩn hoá qua :func:`normalize_suffix`.
        issued_at: Thời điểm phát hành (timezone-aware). Mặc định ``clock()``.
        revision_no: Hậu tố ``-R{n}`` khi phát hành lại.
        clock: Inject để test.

    Raises:
        NumberingError: input sai hoặc datetime naive.

    >>> from datetime import timezone
    >>> str(generate_bbnt_number(brands=[BrandRef("ORGANICA")], entity="EZS", channel="FB",
    ...     suffix="BDAY 05", issued_at=datetime(2026, 5, 31, 17, 30, tzinfo=timezone.utc)))
    '06.26/BBNT-ORGANICA-EZ-FB-BDAY-05'
    """
    moment_vn = to_vn_time(issued_at if issued_at is not None else clock())
    return BbntNumber(
        issue_month=YearMonth.of(moment_vn),
        scope_code=resolve_scope_code(brands),
        entity_code=resolve_entity_code(entity),
        channel_code=resolve_channel_code(channel),
        suffix=normalize_suffix(suffix),
        revision_no=revision_no,
        issue_date=moment_vn.date(),
    )


_NUMBER_RE = re.compile(
    rf"^(?P<issue>\d{{2}}\.\d{{2}})/{DOC_TOKEN}-(?P<scope>[A-Z0-9_]+)-(?P<entity>[A-Z]{{2,5}})"
    rf"-(?P<channel>[A-Z0-9]+)-(?P<suffix>[A-Z0-9]+(?:-[A-Z0-9]+)*?)(?:-R(?P<rev>[1-9]\d*))?$"
)


def parse_bbnt_number(value: str) -> BbntNumber:
    """Đảo ngược ``str(BbntNumber)``. Dùng để validate số nhập tay / import dữ liệu cũ. KHÔNG dùng trong luồng
    tạo BBNT (``BbntGenerator`` không gọi hàm này) — suffix tự gõ kết thúc bằng "-R{số}" (vd "ROUND-R1") có thể
    bị hiểu nhầm thành hậu tố revision khi parse ngược; trường hợp hiếm này chấp nhận được vì chỉ ảnh hưởng tới
    việc đọc lại 1 số đã có, không ảnh hưởng tới số được SINH ra."""
    m = _NUMBER_RE.fullmatch(value.strip())
    if not m:
        raise NumberingError(f"Số BBNT sai định dạng: {value!r}")
    return BbntNumber(
        issue_month=YearMonth.of(m["issue"]),
        scope_code=m["scope"],
        entity_code=m["entity"],
        channel_code=m["channel"],
        suffix=m["suffix"],
        revision_no=int(m["rev"] or 0),
    )
