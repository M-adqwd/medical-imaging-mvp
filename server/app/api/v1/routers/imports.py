"""介质导入 API：光盘 / 移动存储（PRD：支持 PACS 调阅及光盘、移动存储导入）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.v1 import serializers as ser
from app.api.v1.deps import client_ip, current_user, db_session, idempotent
from app.api.v1.schemas import DirectoryImportRequest, MediaImportOut
from app.core.security import Principal, require
from app.services import imports as svc

router = APIRouter(prefix="/imports", tags=["import"])


@router.post("/directory", response_model=MediaImportOut, status_code=201)
@idempotent("POST /api/v1/imports/directory", status_code=201)
def import_directory(
    request: Request,
    payload: DirectoryImportRequest,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> MediaImportOut:
    """导入一个本地目录（光盘 / U 盘 / 移动硬盘）。

    有 DICOMDIR 时按介质索引走；没有则递归扫描，并能识别无扩展名的
    DICOM 实例文件。导入后刷新归档索引，结果立刻可通过 QIDO / WADO 查到。
    """
    require(principal, "media.import")
    return ser.import_out(
        svc.import_directory(
            db,
            payload.path,
            principal,
            actor_ip=client_ip(request),
        )
    )
