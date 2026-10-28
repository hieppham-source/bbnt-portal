"""
Kết nối Google bằng **token nền của hộp thư gửi** (plan bước 5) — không dùng token của nhân viên đăng nhập portal.

Chỉ ĐỌC: FB.JT (tab ``FB-{YYYY}``, ``DATA BRAND``, ``DATA CONTRACT``), file link tổng order (tab ``NON OP {YY}``,
link là smart chip), Sheet master portal và các file order. Chưa có thao tác ghi nào.

Biến môi trường (GitHub Secrets / Variables, không bao giờ in ra log):
``RECON_GOOGLE_CLIENT_ID``, ``RECON_GOOGLE_CLIENT_SECRET``, ``RECON_GOOGLE_REFRESH_TOKEN``,
``RECON_FBJT_ID``, ``RECON_ORDER_LINKS_ID``, ``MASTER_SHEET_URL``.
"""
from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.core.calculation import normalize_text
from app.recon.bbnt_job import ContractRow
from app.recon.fbjt import brand_key
from app.recon.rules import entity_code

TOKEN_URL = "https://oauth2.googleapis.com/token"
SHEETS = "https://sheets.googleapis.com/v4/spreadsheets"
ENV_KEYS = ("RECON_GOOGLE_CLIENT_ID", "RECON_GOOGLE_CLIENT_SECRET", "RECON_GOOGLE_REFRESH_TOKEN",
            "RECON_FBJT_ID", "RECON_ORDER_LINKS_ID", "MASTER_SHEET_URL")


class BotConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class BotEnv:
    client_id: str
    client_secret: str
    refresh_token: str
    fbjt_id: str
    order_links_id: str
    master_sheet_url: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> BotEnv:
        missing = [k for k in ENV_KEYS if not env.get(k, "").strip()]
        if missing:
            raise BotConfigError("Thiếu biến môi trường: " + ", ".join(missing))
        return cls(*(env[k].strip() for k in ENV_KEYS))


def refresh_access_token(env: BotEnv, session: Any = None) -> str:
    """Đổi refresh token -> access token (1 giờ). Lỗi không kèm token trong thông báo."""
    if session is None:
        import requests

        session = requests
    r = session.post(TOKEN_URL, data={"client_id": env.client_id, "client_secret": env.client_secret,
                                      "refresh_token": env.refresh_token, "grant_type": "refresh_token"}, timeout=30)
    if r.status_code != 200:
        err = r.json().get("error", r.status_code) if r.headers.get("content-type", "").startswith("application/json") \
            else r.status_code
        raise BotConfigError(f"Không lấy được access token Google ({err}) — chạy lại get-recon-token, cập nhật secret")
    token = r.json().get("access_token")
    if not token:
        raise BotConfigError("Google không trả access token")
    return str(token)


def _header_index(rows: Sequence[Sequence[Any]], must: Sequence[str]) -> tuple[int, dict[str, int]]:
    """Dòng header đầu tiên chứa đủ ``must`` (so sau normalize_text); trả (vị trí dòng, tên -> cột)."""
    for i, row in enumerate(rows[:10]):
        norm = [normalize_text(c) for c in row]
        if all(m in norm for m in must):
            return i, {h: j for j, h in reversed(list(enumerate(norm))) if h}
    raise BotConfigError(f"Không thấy header có đủ cột {list(must)}")


def _get(row: Sequence[Any], i: int | None) -> Any:
    return row[i] if i is not None and i < len(row) else None


def _text(v: Any) -> str:
    return "" if v is None else " ".join(str(v).split())


# ----------------------------------------------------------------------------------------- DATA CONTRACT / BRAND
def parse_contracts(values: Sequence[Sequence[Any]]) -> list[ContractRow]:
    """``DATA CONTRACT`` (FB.JT, chỉ kênh FB). Mỗi dòng sinh 1 ContractRow theo BRAND và 1 theo GROUP BRAND
    (nếu khác) — ``signed_contracts`` khớp brand HOẶC group, số hợp đồng trùng được gộp."""
    h, col = _header_index(values, ["brand", "status", "so hop dong"])
    pn = next((j for name, j in col.items() if name.startswith("phap nhan b")), None)
    if pn is None:
        raise BotConfigError("DATA CONTRACT thiếu cột 'PHÁP NHÂN B'")
    out: list[ContractRow] = []
    for row in values[h + 1:]:
        no = _text(_get(row, col["so hop dong"]))
        if not no:
            continue
        entity = entity_code(_text(_get(row, pn))) or _text(_get(row, pn))
        status = _text(_get(row, col["status"]))
        brands = re.split(r"[\r\n,]+", str(_get(row, col["brand"]) or ""))   # 1 ô có thể ghi nhiều brand
        for b in {_text(x) for x in brands} - {""}:
            out.append(ContractRow(no, b, entity, "FB", status))
        group = _text(_get(row, col.get("group brand")))
        if group:
            out.append(ContractRow(no, group, entity, "FB", status, level="group"))
    return out


def parse_brand_op(values: Sequence[Sequence[Any]]) -> dict[str, str]:
    """``DATA BRAND``: brand (chuẩn hoá) -> giá trị cột OP (trống = Non-OP)."""
    h, col = _header_index(values, ["brand", "op"])
    return {brand_key(_get(r, col["brand"])): _text(_get(r, col["op"]))
            for r in values[h + 1:] if _text(_get(r, col["brand"]))}


# ----------------------------------------------------------------------------------------- NON OP {YY} chip links
def chip_url(cell: Mapping[str, Any]) -> str:
    """URL của smart chip (``chipRuns[].chip.richLinkProperties.uri``), hoặc hyperlink thường; không có -> chữ
    hiển thị (worker coi là LINK_INVALID)."""
    for run in cell.get("chipRuns", []) or []:
        uri = (run.get("chip") or {}).get("richLinkProperties", {}).get("uri")
        if uri:
            return str(uri)
    return str(cell.get("hyperlink") or cell.get("formattedValue") or "")


def parse_order_links(grid_rows: Sequence[Mapping[str, Any]]) -> list[tuple[str, str, str]]:
    """``rowData`` của tab ``NON OP {YY}`` -> [(BRAND, CAMP, url)]. Bỏ dòng tiêu đề tháng / dòng không có CAMP."""
    texts = [[_text(c.get("formattedValue")) for c in r.get("values", [])] for r in grid_rows]
    h, col = _header_index(texts, ["brand", "camp"])
    link = next((j for name, j in col.items() if name.startswith("order link")), None)
    if link is None:
        raise BotConfigError("NON OP thiếu cột 'ORDER link order sheet'")
    out: list[tuple[str, str, str]] = []
    for r, t in zip(grid_rows[h + 1:], texts[h + 1:], strict=True):
        brand, camp = _get(t, col["brand"]), _get(t, col["camp"])
        if brand and camp:
            cells = r.get("values", [])
            out.append((brand, camp, chip_url(cells[link]) if link < len(cells) else ""))
    return out


class BotSheets:
    """Đọc grid có chip link (``spreadsheets.get``) — phần ``values`` dùng lại :class:`GoogleSheetsReader`."""

    def __init__(self, access_token: str, session: Any = None) -> None:
        if session is None:
            import requests

            session = requests.Session()
        session.headers["Authorization"] = f"Bearer {access_token}"
        self._s = session

    def grid_rows(self, spreadsheet_id: str, tab: str) -> list[dict[str, Any]]:
        r = self._s.get(f"{SHEETS}/{spreadsheet_id}", timeout=60, params={
            "ranges": f"'{tab}'", "includeGridData": "true",
            "fields": "sheets.data.rowData.values(formattedValue,hyperlink,chipRuns)"})
        if r.status_code != 200:
            raise BotConfigError(f"Không đọc được tab '{tab}' (HTTP {r.status_code}) — kiểm tra quyền chia sẻ")
        sheets = r.json().get("sheets", [])
        data = sheets[0].get("data", [{}]) if sheets else [{}]
        return list(data[0].get("rowData", []))
