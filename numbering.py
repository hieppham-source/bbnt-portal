"""
BBNT Numbering Engine.

Format::

    MM.YY/BBNT-{BRAND}-{ENTITY}-{CHANNEL}-CAMP-{PERIOD}

    06.26/BBNT-ORGANICA-EZ-FB-CAMP-05.26
    10.26/BBNT-SHISEIDO_GRP-SS-TTS-CAMP-07-09.26
    10.26/BBNT-ORGANICA-EZ-FB-CAMP-05,07,09.26

* ``MM.YY``  — tháng/năm LẬP biên bản, quy đổi về ``Asia/Ho_Chi_Minh`` tại thời điểm phát hành.
* ``BRAND``  — brand code (1 brand) hoặc group brand code (nhiều brand cùng group).
* ``ENTITY`` — ``EZ`` (EZS) | ``SS`` (SASSY).
* ``CHANNEL``— ``FB`` | ``TTS``.
* ``PERIOD`` — tháng nghiệm thu: ``05.26`` | ``07-09.26`` | ``05,07,09.26``.

Toàn bộ là hàm thuần (không DB, không I/O). Số BBNT tất định: cùng brand/pháp nhân/kênh/kỳ và cùng tháng lập
-> cùng số, nên tạo lại BBNT (dán lại link order) cho ra đúng số cũ. Tool không lưu BBNT nên không cấp số tăng dần.
"""
from __future__ import annotations

import itertools
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

CAMP_TOKEN = "CAMP"
DOC_TOKEN = "BBNT"

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
    """Số BBNT đã phân rã. ``str(number)`` cho ra chuỗi in trên biên bản."""

    issue_month: YearMonth
    scope_code: str
    entity_code: str
    channel_code: str
    period_months: tuple[YearMonth, ...]
    revision_no: int = 0
    issue_date: date | None = field(default=None, compare=False)  # ngày lập (VN) — lưu vào DB

    def __post_init__(self) -> None:
        if self.revision_no < 0:
            raise NumberingError("revision_no phải >= 0")

    @property
    def period_label(self) -> str:
        return format_period(self.period_months)

    @property
    def business_key(self) -> str:
        """Phần định danh nghiệp vụ, không phụ thuộc tháng lập — dùng cho unique 'live'."""
        return "-".join((DOC_TOKEN, self.scope_code, self.entity_code, self.channel_code,
                         CAMP_TOKEN, self.period_label))

    @property
    def scope_prefix(self) -> str:
        """``BBNT-{scope}-{entity}-{channel}-CAMP-`` — dùng tìm BBNT cùng scope để kiểm tra chồng tháng."""
        return "-".join((DOC_TOKEN, self.scope_code, self.entity_code, self.channel_code, CAMP_TOKEN)) + "-"

    @property
    def lock_key(self) -> str:
        return f"bbnt:{self.entity_code}:{self.channel_code}:{self.scope_code}"

    @property
    def value(self) -> str:
        suffix = f"-R{self.revision_no}" if self.revision_no else ""
        return f"{self.issue_month.label}/{self.business_key}{suffix}"

    def with_revision(self, revision_no: int) -> BbntNumber:
        return replace(self, revision_no=revision_no)

    def __str__(self) -> str:
        return self.value


def generate_bbnt_number(
    *,
    brands: Sequence[BrandRef],
    entity: str,
    channel: str,
    period_months: Iterable[YearMonthLike],
    issued_at: datetime | None = None,
    revision_no: int = 0,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> BbntNumber:
    """Sinh số BBNT (pure — KHÔNG kiểm tra trùng trong DB; dùng :func:`reserve_bbnt_number` để phát hành).

    Args:
        brands: Brand tham gia. 1 brand -> brand code; nhiều brand -> group code.
        entity: ``EZS``/``SASSY`` (hoặc ``EZ``/``SS``).
        channel: ``FB``/``TTS``.
        period_months: Tháng nghiệm thu (xem :meth:`YearMonth.of`), tự dedupe + sort.
        issued_at: Thời điểm phát hành (timezone-aware). Mặc định ``clock()``.
        revision_no: Hậu tố ``-R{n}`` khi phát hành lại.
        clock: Inject để test.

    Raises:
        NumberingError: input sai, datetime naive, hoặc kỳ nghiệm thu nằm SAU tháng lập.

    >>> from datetime import timezone
    >>> str(generate_bbnt_number(brands=[BrandRef("ORGANICA")], entity="EZS", channel="FB",
    ...     period_months=["05.26"], issued_at=datetime(2026, 5, 31, 17, 30, tzinfo=timezone.utc)))
    '06.26/BBNT-ORGANICA-EZ-FB-CAMP-05.26'
    """
    moment_vn = to_vn_time(issued_at if issued_at is not None else clock())
    issue_month = YearMonth.of(moment_vn)
    months = normalize_months(period_months)
    if months[-1] > issue_month:
        raise NumberingError(
            f"Tháng nghiệm thu {months[-1].label} nằm sau tháng lập biên bản {issue_month.label}")
    return BbntNumber(
        issue_month=issue_month,
        scope_code=resolve_scope_code(brands),
        entity_code=resolve_entity_code(entity),
        channel_code=resolve_channel_code(channel),
        period_months=months,
        revision_no=revision_no,
        issue_date=moment_vn.date(),
    )


_NUMBER_RE = re.compile(
    rf"^(?P<issue>\d{{2}}\.\d{{2}})/{DOC_TOKEN}-(?P<scope>[A-Z0-9_]+)-(?P<entity>[A-Z]{{2,5}})"
    rf"-(?P<channel>[A-Z0-9]+)-{CAMP_TOKEN}-(?P<period>[0-9.,-]+?)(?:-R(?P<rev>[1-9]\d*))?$"
)


def parse_bbnt_number(value: str) -> BbntNumber:
    """Đảo ngược ``str(BbntNumber)``. Dùng để validate số nhập tay / import dữ liệu cũ."""
    m = _NUMBER_RE.fullmatch(value.strip())
    if not m:
        raise NumberingError(f"Số BBNT sai định dạng: {value!r}")
    return BbntNumber(
        issue_month=YearMonth.of(m["issue"]),
        scope_code=m["scope"],
        entity_code=m["entity"],
        channel_code=m["channel"],
        period_months=parse_period(m["period"]),
        revision_no=int(m["rev"] or 0),
    )
