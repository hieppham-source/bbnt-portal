"""
Định dạng số & đọc số tiền bằng chữ tiếng Việt cho BBNT.

    >>> amount_in_words(17185465)
    'Mười bảy triệu một trăm tám mươi lăm nghìn bốn trăm sáu mươi lăm đồng'
    >>> format_vnd(Decimal("17185465"))
    '17.185.465'
    >>> format_percent(Decimal("0.18"))
    '18%'
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

_DIGITS = ("không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín")
_SCALES = ("", "nghìn", "triệu")  # nhóm >= tỷ xử lý đệ quy: "một nghìn tỷ", "một triệu tỷ"
_BILLION = 10**9


def _read_triple(n: int, *, full: bool, linh: str, four_after_tens: str) -> list[str]:
    """Đọc nhóm 3 chữ số. ``full=True`` khi không phải nhóm đứng đầu -> luôn đọc hàng trăm ("không trăm")."""
    hundreds, rest = divmod(n, 100)
    tens, units = divmod(rest, 10)
    words: list[str] = []
    if full or hundreds:
        words += [_DIGITS[hundreds], "trăm"]
    if tens == 0:
        if units:
            if words:
                words.append(linh)
            words.append(_DIGITS[units])
    elif tens == 1:
        words.append("mười")
        if units == 5:
            words.append("lăm")
        elif units:
            words.append(_DIGITS[units])
    else:
        words += [_DIGITS[tens], "mươi"]
        if units == 1:
            words.append("mốt")
        elif units == 4:
            words.append(four_after_tens)
        elif units == 5:
            words.append("lăm")
        elif units:
            words.append(_DIGITS[units])
    return words


def _read_below_billion(n: int, *, leading: bool, linh: str, four_after_tens: str) -> list[str]:
    groups = [(n // 10**6) % 1000, (n // 1000) % 1000, n % 1000]  # triệu, nghìn, đơn vị
    words: list[str] = []
    for i, g in enumerate(groups):
        if g == 0:
            continue
        full = bool(words) or not leading
        words += _read_triple(g, full=full, linh=linh, four_after_tens=four_after_tens)
        scale = _SCALES[2 - i]
        if scale:
            words.append(scale)
    return words


def number_to_vietnamese(n: int, *, linh: str = "linh", four_after_tens: str = "bốn",
                         _leading: bool = True) -> str:
    """Đọc số nguyên không âm (chữ thường).

    Quy tắc: mốt (21), lăm (15, 25), mười (10-19), linh/lẻ (105), "không trăm" ở nhóm không đứng đầu
    (1.005 = một nghìn không trăm linh năm), nhóm 000 bỏ qua (2.000.000 = hai triệu),
    trên tỷ đọc đệ quy (1.000.000.000.000 = một nghìn tỷ).

    Args:
        linh: ``"linh"`` (miền Bắc, mặc định) hoặc ``"lẻ"``.
        four_after_tens: ``"bốn"`` (mặc định, như phần mềm kế toán) hoặc ``"tư"`` (24 = hai mươi tư).
    """
    if isinstance(n, bool) or not isinstance(n, int):
        raise TypeError("number_to_vietnamese cần int")
    if n < 0:
        raise ValueError("Không đọc số âm")
    if n == 0:
        return "không"
    opts = {"linh": linh, "four_after_tens": four_after_tens}
    high, low = divmod(n, _BILLION)
    words: list[str] = []
    if high:
        words.append(number_to_vietnamese(high, _leading=_leading, **opts))
        words.append("tỷ")
    if low:
        words += _read_below_billion(low, leading=_leading and not high, **opts)
    return " ".join(words)


def amount_in_words(amount: Decimal | int, *, currency: str = "đồng", capitalize: bool = True,
                    linh: str = "linh", four_after_tens: str = "bốn") -> str:
    """Số tiền VND bằng chữ. Không tự làm tròn: số có phần lẻ khác 0 -> ``ValueError``.

    >>> amount_in_words(Decimal("1005.00"))
    'Một nghìn không trăm linh năm đồng'
    """
    value = Decimal(amount)
    if value != value.to_integral_value():
        raise ValueError(f"Số tiền {amount} có phần lẻ — làm tròn tới đồng trước khi đọc bằng chữ")
    if value < 0:
        raise ValueError("Số tiền âm không đọc bằng chữ")
    text = f"{number_to_vietnamese(int(value), linh=linh, four_after_tens=four_after_tens)} {currency}".strip()
    return text[:1].upper() + text[1:] if capitalize else text


def format_vnd(amount: Decimal | int) -> str:
    """``17185465`` -> ``'17.185.465'``; ``1272997.44`` -> ``'1.272.997,44'`` (dấu chấm nghìn, phẩy thập phân)."""
    value = Decimal(amount)
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value == value.to_integral_value():
        return sign + f"{int(value):,}".replace(",", ".")
    q = value.quantize(Decimal("0.01"), ROUND_HALF_UP)
    int_part, frac = f"{q:.2f}".split(".")
    return sign + f"{int(int_part):,}".replace(",", ".") + "," + frac


def format_percent(rate: Decimal | None, *, max_decimals: int = 2) -> str:
    """Tỷ lệ thập phân -> %: 0.18 -> '18%', 0.053070 -> '5,31%', None -> ''."""
    if rate is None:
        return ""
    pct = (Decimal(rate) * 100).quantize(Decimal(1).scaleb(-max_decimals), ROUND_HALF_UP)
    text = f"{pct:f}".rstrip("0").rstrip(".") if "." in f"{pct:f}" else f"{pct:f}"
    return text.replace(".", ",") + "%"


def format_int(n: int) -> str:
    return f"{n:,}".replace(",", ".")
