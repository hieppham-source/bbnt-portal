"""
API đọc Sheet master (chỉ đọc — sửa master = sửa thẳng trên Google Sheet).

* ``GET /api/v1/master``        dữ liệu cho dropdown (kênh, pháp nhân, brand, group, hợp đồng, mapping)
* ``GET /api/v1/master/check``  danh sách dòng lỗi/cảnh báo trong Sheet master (tab + số dòng)
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import User, get_master_repo, get_user
from app.config import Settings, get_settings
from app.core.numbering import to_vn_time
from app.master.models import MasterData
from app.master.repository import MasterRepository
from app.schemas.bbnt import (
    BrandOut,
    ContractBriefOut,
    EntityOut,
    GroupOut,
    MappingOut,
    MasterCheckOut,
    MasterIssueOut,
    MasterOut,
)

router = APIRouter(prefix="/api/v1/master", tags=["Master"])

RefreshQ = Annotated[bool, Query(description="Đọc lại Sheet master ngay, bỏ qua cache")]


def to_master_out(m: MasterData) -> MasterOut:
    today = to_vn_time(datetime.now(UTC)).date()
    groups: dict[tuple[str, str], GroupOut] = {}
    for b in m.brands:
        if b.active and b.group_code:
            g = groups.setdefault((b.channel, b.group_code),
                                  GroupOut(channel=b.channel, code=b.group_code,
                                           name=b.group_name or b.group_code, brand_codes=[]))
            g.brand_codes.append(b.code)
    return MasterOut(
        loaded_at=m.loaded_at,
        channels=m.channels(),
        entities=[EntityOut(code=e.code, number_code=e.number_code, name=e.name,
                            tax_today=(t.label if (t := e.tax_on(today)) else None))
                  for e in sorted(m.entities.values(), key=lambda e: e.code)],
        brands=[BrandOut(channel=b.channel, code=b.code, name=b.name, group_code=b.group_code,
                         group_name=b.group_name) for b in m.brands if b.active],
        groups=sorted(groups.values(), key=lambda g: (g.channel, g.code)),
        contracts=[ContractBriefOut(contract_no=c.contract_no, entity=c.entity_code, channel=c.channel,
                                    brand_code=c.brand_code, group_code=c.group_code, model=c.model,
                                    commission_rate=c.commission_rate, valid_from=c.valid_from,
                                    valid_to=c.valid_to) for c in m.contracts if c.active],
        mappings=[MappingOut(code=x.code, channel=x.channel, sheet_name=x.sheet_name, is_default=x.is_default)
                  for x in m.mappings],
        error_count=len(m.errors),
        warning_count=len(m.issues) - len(m.errors),
    )


@router.get("", response_model=MasterOut, summary="Dữ liệu master cho form tạo BBNT")
def get_master(user: Annotated[User, Depends(get_user)],
               repo: Annotated[MasterRepository, Depends(get_master_repo)],
               refresh: RefreshQ = False) -> MasterOut:
    return to_master_out(repo.get(refresh=refresh))


@router.get("/check", response_model=MasterCheckOut, summary="Kiểm tra lỗi trong Sheet master")
def check_master(user: Annotated[User, Depends(get_user)],
                 repo: Annotated[MasterRepository, Depends(get_master_repo)],
                 settings: Annotated[Settings, Depends(get_settings)],
                 refresh: RefreshQ = True) -> MasterCheckOut:
    m = repo.get(refresh=refresh)
    return MasterCheckOut(
        loaded_at=m.loaded_at, ok=not m.errors, service_account_email=settings.service_account_email,
        counts={"entities": len(m.entities), "brands": len(m.brands), "contracts": len(m.contracts),
                "mappings": len(m.mappings)},
        issues=[MasterIssueOut(tab=i.tab, row=i.row, severity=i.severity, message=i.message) for i in m.issues],
    )
