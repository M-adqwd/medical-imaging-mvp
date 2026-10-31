"""AI 分类结论落库：把「模型判定」变成可追溯的报告草稿。

## 这一步解决什么问题

训练平台能给出「DR 3 级，置信度 0.83」，但这句话原来的归宿是聊天窗口。
真正临床要的是：这条结论挂在哪个病例上、用的是哪个模型哪一版、谁触发的、
医生有没有确认——也就是要能进证据链。

所以本模块做三件事，顺序不能反：
1. 先调模型（失败就直接报错，不在库里留半条记录）；
2. 把这次推理登记为 AiJob（模型名 + 版本 + 概率 + 免责声明全进 stats），
   它是可被报告引用的证据对象；
3. 生成 **draft 态** 报告草稿，refs 指向该 AiJob。草稿必须由医师复核后才能提交，
   AI 不写任何已签发的报告。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.adapters.modelforge import ModelForgeError, modelforge_client
from app.core import audit
from app.core.errors import validation_error
from app.core.security import Principal
from app.models.ai_job import AiJob
from app.models.case import Case
from app.models.enums import AiJobStatus
from app.services import reports as report_svc

TEMPLATE_ID = "ai-classification"


def classify(
    db: Session,
    case: Case,
    principal: Principal,
    *,
    model_ref: str,
    content: bytes | None = None,
    filename: str = "input.png",
    rows: list[dict] | None = None,
    study_instance_uid: str | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    """跑一次分类模型，落 AiJob + draft 报告。"""
    if not model_ref:
        raise validation_error("必须指定模型", field="model")
    if content is None and not rows:
        raise validation_error("必须提供图像文件或波形行数据")

    # 1) 推理。失败直接抛，不留脏数据。
    if content is not None:
        env = modelforge_client.classify_image(model_ref, content, filename)
    else:
        env = modelforge_client.classify_rows(model_ref, rows or [])

    m = env.get("model") or {}
    top = env.get("top") or {}
    rep = env.get("report") or {}
    findings = (rep.get("findings") or "").strip()
    impression = (rep.get("impression") or "").strip()
    if not findings or not impression:
        raise ModelForgeError("模型返回缺少结论文本，无法生成报告草稿")

    jid = job_id or f"cls-{uuid.uuid4().hex[:12]}"
    uid = study_instance_uid or (case.study_uids[0] if case.study_uids else "")

    job = db.get(AiJob, jid)
    if job is None:
        job = AiJob(
            job_id=jid,
            case_id=case.id,
            study_instance_uid=uid or f"case:{case.id}",
            model=str(m.get("name") or model_ref),
            model_version=str(m.get("version") or 1),
            targets=[str(top.get("label") or "")],
            status=AiJobStatus.DONE,
            requested_by=principal.user_id,
        )
        db.add(job)
    else:
        job.status = AiJobStatus.DONE
        job.case_id = case.id

    # stats 里保留完整信封：概率向量、阈值口径、免责声明都要能事后追溯
    job.stats = {
        "kind": "classification",
        "model_id": m.get("id"),
        "task_hint": m.get("task_hint"),
        "modality": m.get("modality"),
        "top": top,
        "predictions": env.get("predictions") or [],
        "quality": m.get("quality") or {},
        "disclaimer": env.get("disclaimer") or "",
    }
    db.flush()

    report = report_svc.create(
        db,
        case,
        principal,
        template_id=TEMPLATE_ID,
        findings=findings,
        impression=impression,
        refs=[{"kind": "ai_job", "uid": jid, "version": 1}],
    )

    audit.record(
        db,
        category="ai",
        action="ai_classify",
        actor_id=principal.user_id,
        target_type="report",
        target_uid=report.report_id,
        after_state="draft",
        extra={
            "job_id": jid,
            "model": job.model,
            "model_version": job.model_version,
            "top": top,
            "report_id": report.report_id,
            # 必须留痕：这是 AI 生成的草稿，不是医师结论
            "ai_generated": True,
        },
    )
    return {
        "job_id": jid,
        "report_id": report.report_id,
        "model": {"id": m.get("id"), "name": job.model, "version": job.model_version},
        "top": top,
        "findings": findings,
        "impression": impression,
        "disclaimer": env.get("disclaimer") or "",
        "state": report.state.value,
        "mode": "modelforge",
    }
