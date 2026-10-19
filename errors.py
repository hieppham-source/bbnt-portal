"""Lỗi nghiệp vụ có mã + HTTP status — API trả nguyên văn cho giao diện."""
from __future__ import annotations

from typing import Any


class BbntError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.status_code, self.code, self.message, self.details = status_code, code, message, details


def fail(code: str, message: str, status: int = 422, details: Any = None) -> BbntError:
    return BbntError(status, code, message, details)
