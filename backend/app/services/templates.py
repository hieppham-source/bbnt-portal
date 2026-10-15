"""
Template BBNT nằm trong repo (thư mục ``templates/``), không lưu ở đâu khác.

Chọn theo (pháp nhân, mô hình hợp tác). Muốn template riêng cho 1 kênh: thêm file
``bbnt_{phap_nhan}_{kenh}_{mo_hinh}.docx`` (vd ``bbnt_ezs_tts_nonop.docx``) — file này được ưu tiên.
Sửa template = sửa file .docx trong repo rồi deploy lại.
"""
from __future__ import annotations

import threading
from pathlib import Path

from app.docx.filler import BbntDocxRenderer, TemplateError
from app.errors import fail

DEFAULT_TEMPLATES: dict[tuple[str, str], str] = {
    ("EZS", "NON_OP"): "bbnt_ezs_fb_nonop.v2.docx",
    ("SASSY", "NON_OP"): "bbnt_sassy_fb_nonop.v2.docx",
}


def _slug(model: str) -> str:
    return model.lower().replace("_", "")


class TemplateRegistry:
    def __init__(self, directory: str | Path, defaults: dict[tuple[str, str], str] | None = None) -> None:
        self.dir = Path(directory)
        self.defaults = DEFAULT_TEMPLATES if defaults is None else defaults
        self._cache: dict[Path, BbntDocxRenderer] = {}
        self._lock = threading.Lock()

    def path_for(self, entity: str, model: str, channel: str) -> Path:
        override = self.dir / f"bbnt_{entity.lower()}_{channel.lower()}_{_slug(model)}.docx"
        if override.is_file():
            return override
        name = self.defaults.get((entity, model))
        if name is None or not (self.dir / name).is_file():
            raise fail("TEMPLATE_NOT_FOUND", f"Chưa có template BBNT cho {entity} · {model}"
                                             f"{'' if name is None else f' (thiếu file {name})'}")
        return self.dir / name

    def renderer(self, entity: str, model: str, channel: str) -> BbntDocxRenderer:
        path = self.path_for(entity, model, channel)
        with self._lock:
            r = self._cache.get(path)
            if r is None:
                try:
                    r = BbntDocxRenderer(path.read_bytes())
                except TemplateError as exc:
                    raise fail("TEMPLATE_INVALID", f"Template {path.name} lỗi: {exc}", status=500) from exc
                self._cache[path] = r
            return r

    def check(self) -> dict[str, str]:
        """Dùng cho /readyz và CLI: parse thử mọi template mặc định."""
        out: dict[str, str] = {}
        for (entity, model), name in self.defaults.items():
            try:
                self.renderer(entity, model, "_")
                out[name] = "ok"
            except Exception as exc:  # noqa: BLE001
                out[name] = getattr(exc, "message", str(exc))
        return out
