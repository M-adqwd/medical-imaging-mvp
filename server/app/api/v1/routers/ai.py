"""AI 推理 API（契约 §2.4）。

POST /api/v1/ai/infer      投递任务（幂等键为 job_id）
POST /api/v1/ai-results    AI 回调，结果落为 suggested 态 SEG
GET  /api/v1/ai/jobs/{id}  查询任务状态
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1 import serializers as ser
from app.api.v1.deps import commit_or_409, current_user, db_session, idempotent
from app.api.v1.schemas import AiCallback, SegmentOut
from app.adapters.ai import AiClient, InferRequest, ai_client
from app.core import audit
from app.core.errors import not_found, validation_error
from app.core.security import Principal, require
from app.models.ai_job import AiJob
from app.models.enums import AiJobStatus, SegmentState
from app.services import ai_classify
from app.services import cases as case_svc
from app.services import segments as seg_svc
from app.services.ai_local import local_infer_enabled, submit_local

router = APIRouter(tags=["ai"])


@router.post("/ai/infer", status_code=202)
@idempotent("POST /api/v1/ai/infer", status_code=202)
def submit_infer(
    request: Request,
    study_instance_uid: str,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
    model: str = "totalsegmentator/multi-structure",
    model_version: str = "2.0.0",
    targets: str = "",
) -> dict:
    """投递自动分割任务。job_id 由客户端生成，作为幂等键的一部分。"""
    require(principal, "segment.ai_trigger")

    case = db.scalar(
        select(case_svc.Case).where(case_svc.Case.study_uids.like(f'%"{study_instance_uid}"%'))
    )
    # 本地推理已开启：显式拒绝不满足的条件，绝不静默退回桩——
    # "投进去了但永远不会完成"和"当场失败"对调用方是两种完全不同的信息。
    if local_infer_enabled() and not ai_client.enabled:
        if not ai_client.can_accept_local():
            raise validation_error(
                "本地推理已开启但环境不可用（缺少 totalsegmentator CLI）",
                reason="local_infer_unavailable",
            )
        if case is None:
            raise validation_error(
                "本地推理需要能定位到 Case（患者信息 / 学习日期）",
                field="study_instance_uid",
            )

    job_id = request.headers.get("X-Job-Id") or f"job-{study_instance_uid[-12:]}"
    existing = db.get(AiJob, job_id)
    if existing is not None:
        return {
            "job_id": existing.job_id,
            "queue_pos": existing.queue_pos,
            "estimated_sec": existing.estimated_sec,
            "status": existing.status.value,
            "mode": ai_client.mode,
            "notice": ai_client.stub_notice,
        }

    job = AiJob(
        job_id=job_id,
        case_id=case.id if case else None,
        study_instance_uid=study_instance_uid,
        model=model,
        model_version=model_version,
        targets=[t for t in targets.split(",") if t],
        status=AiJobStatus.QUEUED,
        requested_by=principal.user_id,
    )
    db.add(job)
    db.flush()

    resp = ai_client.submit(
        InferRequest(
            job_id=job_id,
            model=model,
            model_version=model_version,
            study_instance_uid=study_instance_uid,
            targets=job.targets,
            callback=str(request.base_url).rstrip("/") + "/api/v1/ai-results",
        )
    )
    job.queue_pos = resp.get("queue_pos")
    job.estimated_sec = resp.get("estimated_sec")
    job.status = AiJobStatus.RUNNING

    # 本地真实推理：后台线程跑 TotalSegmentator，完成后回调自身
    # /ai-results——状态迁移、审计、suggested 态 SEG 全部走同一条链路。
    if ai_client.mode == "local":
        submit_local(
            job_id=job_id,
            study_instance_uid=study_instance_uid,
            series_instance_uid=None,
            targets=job.targets,
            callback_url=str(request.base_url).rstrip("/") + "/api/v1/ai-results",
            patient_id=case.patient_ref or "",
            patient_name=case.patient_name or "",
            study_date=case.study_date,
        )

    audit.record(
        db,
        category="ai",
        action="ai_trigger",
        actor_id=principal.user_id,
        target_type="ai_job",
        target_uid=job_id,
        after_state=job.status.value,
        extra={"model": model, "model_version": model_version, "study": study_instance_uid},
    )
    return resp


@router.post("/ai/classify", status_code=201)
async def classify(
    request: Request,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> dict:
    """对「非分割」任务（眼底分级、心电判读）跑一次分类模型。

    与 `/ai/infer` 的区别：这里不产出 SEG，产出的是结论 + draft 报告。
    入参两种形态：
      - multipart：case_id、model、file（图像），可选 study_instance_uid
      - JSON：case_id、model、rows（波形/表格一行或多行）
    结果固定为 draft 态报告，必须医师复核后才能提交——AI 不签发报告。
    """
    require(principal, "segment.ai_trigger")
    ctype = request.headers.get("content-type", "")

    content: bytes | None = None
    rows: list[dict] | None = None
    filename = "input.png"

    if "multipart/form-data" in ctype:
        form = await request.form()
        case_id = str(form.get("case_id") or "")
        model_ref = str(form.get("model") or "")
        study_uid = str(form.get("study_instance_uid") or "") or None
        up = form.get("file")
        if up is not None and hasattr(up, "read"):
            content = await up.read()
            filename = getattr(up, "filename", None) or filename
    else:
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            raise validation_error("请求体需为 multipart（图像）或 JSON（波形行）") from None
        case_id = str(body.get("case_id") or "")
        model_ref = str(body.get("model") or "")
        study_uid = body.get("study_instance_uid") or None
        rows = body.get("rows")

    if not case_id:
        raise validation_error("缺少 case_id", field="case_id")
    case = db.get(case_svc.Case, case_id)
    if case is None:
        raise not_found("Case", case_id)

    result = ai_classify.classify(
        db, case, principal,
        model_ref=model_ref, content=content, filename=filename,
        rows=rows, study_instance_uid=study_uid,
        job_id=request.headers.get("X-Job-Id"),
    )
    commit_or_409(db)
    return result


@router.post("/ai-results", status_code=200)
def ai_callback(
    request: Request,
    payload: AiCallback,
    db: Session = Depends(db_session),
) -> dict:
    """AI → 系统回调。结果固定写入 suggested 态，等待医师确认。"""
    job = db.get(AiJob, payload.job_id)
    if job is None:
        raise not_found("AiJob", payload.job_id)

    job.status = AiJobStatus(payload.status) if payload.status in {e.value for e in AiJobStatus} else AiJobStatus.FAILED
    # 收尾回调（不带 seg_content）会带来汇总 stats；已收集的产物 UID 列表
    # 不许被它覆盖掉——那是逐结构回调一点点攒出来的。
    prev_stats = dict(job.stats or {})
    new_stats = dict(payload.stats or {})
    if "result_seg_uids" not in new_stats and "result_seg_uids" in prev_stats:
        new_stats["result_seg_uids"] = prev_stats["result_seg_uids"]
    job.stats = new_stats
    job.error = payload.error

    if payload.status == "done" and payload.seg_content:
        case = db.get(case_svc.Case, job.case_id) if job.case_id else None
        if case is None:
            raise validation_error("AI 回调找不到关联 Case", job_id=payload.job_id)
        seg = seg_svc.create_from_ai(
            db,
            case,
            _system_principal(),
            study_instance_uid=job.study_instance_uid,
            segment_name=payload.segment_name or "AI",
            model=job.model,
            model_version=job.model_version,
            seg_content_b64=payload.seg_content,
            stats=payload.stats,
        )
        # 一次任务可产出多个结构（多结构 SEG 逐个回调）：result_seg_uid 记
        # 首个，全集收进 stats["result_seg_uids"]，最后到的覆盖先到的会丢产物。
        if job.result_seg_uid is None:
            job.result_seg_uid = seg.seg_uid
        collected = list((job.stats or {}).get("result_seg_uids", []))
        collected.append(seg.seg_uid)
        merged_stats = dict(job.stats or {})
        merged_stats["result_seg_uids"] = collected
        job.stats = merged_stats
        job.status = AiJobStatus.DONE
        audit.record(
            db,
            category="ai",
            action="ai_callback",
            actor_id="ai-service",
            target_type="segment",
            target_uid=seg.seg_uid,
            after_state=SegmentState.SUGGESTED.value,
            extra={"job_id": job.job_id, "stats": payload.stats},
        )
    elif payload.status == "failed":
        audit.record(
            db,
            category="ai",
            action="ai_failed",
            actor_id="ai-service",
            target_type="ai_job",
            target_uid=job.job_id,
            after_state=AiJobStatus.FAILED.value,
            extra=payload.error or {},
        )
    # 回调是一次性的外部事件：不落库等于没收到。此前这里从未提交事务，
    # 响应里的 "done" 只是会话内快照，任务在库里永远停在 RUNNING。
    commit_or_409(db)
    return {"job_id": job.job_id, "status": job.status.value, "seg_uid": job.result_seg_uid}


@router.get("/ai/jobs/{job_id}")
def get_job(
    request: Request,
    job_id: str,
    db: Session = Depends(db_session),
    principal: Principal = Depends(current_user),
) -> dict:
    require(principal, "case.read")
    job = db.get(AiJob, job_id)
    if job is None:
        raise not_found("AiJob", job_id)
    return {
        "job_id": job.job_id,
        "status": job.status.value,
        "queue_pos": job.queue_pos,
        "estimated_sec": job.estimated_sec,
        "result_seg_uid": job.result_seg_uid,
        "stats": job.stats,
        "error": job.error,
        "model": job.model,
        "model_version": job.model_version,
        # stub 模式下任务不会自动收敛，查询方需要据此决定轮询还是提示人工回传
        "mode": ai_client.mode,
        "notice": ai_client.stub_notice,
    }


def _system_principal() -> Principal:
    from app.core.security import Role

    return Principal(user_id="ai-service", roles={Role.ADMIN})