"""
Nguồn master data + cache trong RAM.

Tool không có database: mỗi lần cache hết hạn (``MASTER_CACHE_SECONDS``, mặc định 60s) sẽ đọc lại Sheet master,
nên người sửa Sheet chỉ cần đợi tối đa 1 phút (hoặc bấm "Tải lại master" -> ``?refresh=true``).
Nếu Google lỗi tạm thời mà đã có bản cũ trong cache -> dùng tiếp bản cũ, không chặn người dùng.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from app.core.calculation import normalize_text
from app.errors import BbntError, fail
from app.services.sheets import (
    GoogleSheetsReader,
    SheetAccessError,
    SheetAuthError,
    SheetRef,
    read_xlsx_tabs,
    xlsx_sheet_names,
)

from .loader import TABS, load_master
from .models import MasterData

log = logging.getLogger("bbnt.master")

Tables = Mapping[str, Sequence[Sequence[Any]]]
TablesSource = Callable[[], Tables]

_WANTED = {normalize_text(n) for names in TABS.values() for n in names}


def _pick_master_tabs(titles: Sequence[str]) -> list[str]:
    return [t for t in titles if normalize_text(t) in _WANTED]


def google_sheet_source(reader: GoogleSheetsReader, ref: SheetRef) -> TablesSource:
    """2 request/lần tải: danh sách tab + batchGet đúng 4 tab master (bỏ qua tab HUONG_DAN, tab nháp...)."""
    def load() -> Tables:
        return reader.read_tabs(ref, _pick_master_tabs(reader.titles(ref)))
    return load


def xlsx_source(path: str) -> TablesSource:
    def load() -> Tables:
        return read_xlsx_tabs(path, _pick_master_tabs(xlsx_sheet_names(path)))
    return load


def build_master(tables: Tables, now: datetime | None = None) -> MasterData:
    by_name = {normalize_text(k): v for k, v in tables.items()}
    return load_master(lambda name: by_name.get(normalize_text(name)), now)


class MasterRepository:
    """``source`` mặc định có thể là None (chế độ OAuth: chỉ đọc được Sheet master khi có token của người dùng,
    truyền vào :meth:`get` qua tham số ``source``)."""

    def __init__(self, source: TablesSource | None, ttl_seconds: float = 60,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self._source, self._ttl, self._mono = source, ttl_seconds, monotonic
        self._lock = threading.Lock()
        self._data: MasterData | None = None
        self._loaded_mono = 0.0

    def peek(self) -> MasterData | None:
        """Bản đang cache (có thể đã cũ), không đọc Sheet."""
        return self._data

    @property
    def has_default_source(self) -> bool:
        return self._source is not None

    def get(self, refresh: bool = False, source: TablesSource | None = None) -> MasterData:
        with self._lock:
            fresh = self._data is not None and self._mono() - self._loaded_mono < self._ttl
            if fresh and not refresh:
                assert self._data is not None
                return self._data
            src = source or self._source
            if src is None:
                raise fail("GOOGLE_TOKEN_MISSING", "Cần đăng nhập Google để đọc Sheet master", status=401)
            try:
                tables = src()
            except SheetAuthError as exc:
                raise fail("GOOGLE_TOKEN_EXPIRED", str(exc), status=401) from exc
            except SheetAccessError as exc:
                if self._data is not None and not refresh:
                    log.warning("Không tải lại được Sheet master, dùng bản cache cũ: %s", exc)
                    return self._data
                raise fail("MASTER_UNREADABLE", str(exc), status=502) from exc
            except BbntError:
                raise
            except Exception as exc:  # noqa: BLE001 - mạng/Google lỗi bất kỳ
                if self._data is not None and not refresh:
                    log.warning("Lỗi tải Sheet master, dùng bản cache cũ", exc_info=True)
                    return self._data
                raise fail("MASTER_UNREADABLE", f"Không đọc được Sheet master: {type(exc).__name__}",
                           status=502) from exc
            self._data = build_master(tables, datetime.now(UTC))
            self._loaded_mono = self._mono()
            if self._data.errors:
                log.warning("Sheet master có %d dòng lỗi (bị bỏ qua)", len(self._data.errors))
            return self._data
