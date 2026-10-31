#!/usr/bin/env python3
"""端到端冒烟：真实 HTTP 调用，覆盖 FR-01 → FR-07 主链路。

用法：先启动 uvicorn，再执行
    .venv/bin/python scripts/smoke.py
"""

from __future__ import annotations

import io
import json
import os
import sys
import uuid

import httpx

# 端口可被启动器自动避让，冒烟脚本跟随环境变量
BASE = os.environ.get("MIIS_BASE", "http://localhost:8000")
API = f"{BASE}/api/v1"
ATTENDING = {"X-User-Id": "u-attending", "X-User-Roles": "attending"}
PLANNER = {"X-User-Id": "u-planner", "X-User-Roles": "planner"}
RESIDENT = {"X-User-Id": "u-resident", "X-User-Roles": "resident"}

passed = 0
failed: list[str] = []


def check(label: str, cond: bool, extra: str = "") -> None:
    global passed
    if cond:
        passed += 1
        print(f"  \033[32mPASS\033[0m {label}")
    else:
        failed.append(label)
        print(f"  \033[31mFAIL\033[0m {label} {extra}")


def post(client, path, headers=None, **kw):
    h = {**(headers or ATTENDING), "Idempotency-Key": str(uuid.uuid4())}
    return client.post(f"{API}{path}", headers=h, **kw)


def main() -> int:
    study = f"1.2.840.113619.{uuid.uuid4().int}"

    with httpx.Client(timeout=30) as c:
        print("\n[0] 健康检查与契约自描述")
        r = c.get(f"{BASE}/healthz")
        check("healthz", r.status_code == 200 and r.json()["status"] == "ok")
        r = c.get(f"{API}/contract")
        check("contract 自描述", r.json()["contract_version"] == "0.9")

        print("\n[1] FR-01 建立 Case")
        r = post(c, "/cases", json={"patient_ref": "MRN:P20261022", "studies": [study], "modality": "CT"})
        check("创建 Case → 201", r.status_code == 201, r.text[:200])
        cid = r.json()["case_id"]
        check("Case 初始状态 open", r.json()["status"] == "open")

        r2 = post(c, "/cases", json={"patient_ref": "MRN:P20261022", "studies": [study]})
        check("同一 Study 自动归并", r2.json()["case_id"] == cid)

        print("\n[2] FR-02 测量 SR")
        r = post(
            c,
            f"/cases/{cid}/measurements",
            json={"study_instance_uid": study, "type": "length", "value": 34.7, "unit": "mm"},
        )
        check("创建测量 → draft", r.json()["state"] == "draft")
        sr = r.json()["sr_uid"]

        r = c.get(f"{API}/measurements/{sr}/content", headers=ATTENDING)
        check("SR 原文可下载", r.status_code == 200 and r.content[128:132] == b"DICM")
        import pydicom

        ds = pydicom.dcmread(io.BytesIO(r.content), force=True)
        check("SR Modality / 患者引用", ds.Modality == "SR" and ds.PatientID == "MRN:P20261022")
        check("SR 数值与单位", ds.ContentSequence[0].NumericValue == "34.7")

        r = post(c, f"/measurements/{sr}:save")
        check("draft → saved", r.json()["state"] == "saved")

        print("\n[3] FR-04 分割 SEG + AI 建议态")
        r = post(
            c,
            f"/cases/{cid}/segments",
            json={"study_instance_uid": study, "segment_name": "肝脏"},
        )
        seg = r.json()["seg_uid"]
        check("创建分割 → draft", r.json()["state"] == "draft")

        r = post(c, f"/cases/{cid}/plans", json={
            "base_seg_uid": seg, "base_seg_version": 1,
            "entry_point": {"geometry": "patient_lps_mm", "coordinates": [0, 0, 0]},
            "target_point": {"geometry": "patient_lps_mm", "coordinates": [10, 0, 0]}})
        check("draft SEG 不可用于规划 → 409", r.status_code == 409, r.text[:150])

        r = c.post(f"{API}/ai/infer", headers={**ATTENDING, "Idempotency-Key": str(uuid.uuid4()),
                                               "X-Job-Id": "smoke-job"}, params={"study_instance_uid": study})
        check("AI 任务投递 → 202", r.status_code == 202, r.text[:150])
        job = r.json()["job_id"]

        r = c.post(f"{API}/ai-results", json={"job_id": job, "status": "done",
                                                "segment_name": "liver", "seg_content": None,
                                                "stats": {"segments_produced": 14}})
        check("AI 回调受理", r.status_code == 200)
        # 回调必须落库：此前 /ai-results 从不提交事务，响应里的 done 只是
        # 会话内快照，任务在库里永远停在 RUNNING（静默失效）。
        r = c.get(f"{API}/ai/jobs/{job}", headers=ATTENDING)
        check("AI 回调已落库 → done", r.json().get("status") == "done", r.text[:150])

        r = c.get(f"{API}/segments/{seg}:confirm", headers=ATTENDING)
        r = post(c, f"/segments/{seg}:confirm")
        check("医师确认 → confirmed", r.json()["state"] == "confirmed")
        check("确认人写入", r.json()["confirmed_by"] == "u-attending")

        r = c.post(f"{API}/segments/{seg}:confirm", headers={**RESIDENT, "Idempotency-Key": str(uuid.uuid4())})
        check("规培不可确认已确认分割 → 403", r.status_code == 403)

        print("\n[4] FR-05 手术规划")
        # 点位必须自声明坐标系（坐标契约 §5 / TC-07 方案 b）。裸 {x,y,z} 已被
        # PlanCreate 拒绝；冒烟脚本曾停留在旧结构，导致本段整体 crash 而非 FAIL。
        r = post(c, f"/cases/{cid}/plans", json={
            "base_seg_uid": seg, "base_seg_version": 1,
            "entry_point": {"geometry": "patient_lps_mm", "coordinates": [0, 0, 0]},
            "target_point": {"geometry": "patient_lps_mm", "coordinates": [30, 40, 0]}})
        check("创建规划 → 201", r.status_code == 201, r.text[:200])
        plan = r.json()
        check("安全距离 50mm", plan["safe_distance"] == 50.0)
        # 该 SEG 无像素数据，风险等级必须为 unknown —— 不能拿入路长度冒充
        # 解剖安全距离，否则一条 50mm 入路会被渲染成绿色「低风险」。
        check("无像素 SEG → 风险等级 unknown", plan["risk_level"] == "unknown")
        check("风险依据说明存在", "无像素" in (plan.get("risk_basis") or ""))

        r = post(c, f"/cases/{cid}/plans", json={
            "base_seg_uid": seg, "base_seg_version": 99,
            "entry_point": {"geometry": "patient_lps_mm", "coordinates": [0, 0, 0]},
            "target_point": {"geometry": "patient_lps_mm", "coordinates": [1, 0, 0]}})
        check("引用版本不匹配 → 422", r.status_code == 422)

        r = post(c, f"/plans/{plan['plan_id']}:export")
        check("draft 规划不可导出 → 409", r.status_code == 409)
        post(c, f"/plans/{plan['plan_id']}:save")
        r = post(c, f"/plans/{plan['plan_id']}:export")
        check("导出携带 SEG 版本", r.status_code == 200 and f"v{plan['base_seg_version']}".encode() in r.content)

        print("\n[5] FR-06 结构化报告")
        r = post(c, f"/cases/{cid}/reports", json={
            "template_id": "ct-chest-default-v3", "findings": "", "impression": "",
            "refs": [{"kind": "measurement", "uid": sr, "version": 1}]})
        rep = r.json()["report_id"]
        r = post(c, f"/reports/{rep}:submit")
        check("必填缺失 → 422", r.status_code == 422)
        check("错误详情列出失效项", "findings" in json.dumps(r.json()["error"]["details"]))

        c.request(
            "PUT", f"{API}/reports/{rep}",
            headers={**ATTENDING, "Idempotency-Key": str(uuid.uuid4())},
            json={"findings": "肝右叶可见低密度灶", "impression": "建议进一步 MRI"},
        )
        r = post(c, f"/reports/{rep}:submit")
        check("提交成功 → submitted", r.json().get("state") == "submitted", r.text[:200])

        r = c.get(f"{API}/reports/{rep}/content", headers=ATTENDING)
        check("报告 SR 已生成", r.status_code == 200 and r.content[128:132] == b"DICM")

        r = post(c, f"/reports/{rep}:lock")
        check("签收 → locked", r.json()["state"] == "locked")
        r = post(c, f"/reports/{rep}:withdraw")
        check("锁定报告不可撤回 → 423", r.status_code == 423)

        print("\n[6] FR-07 异步共享")
        r = post(c, f"/cases/{cid}/shares", json={"permission": "readonly+comment", "scope": "full"})
        share = r.json()
        code = share["access_code"]
        check("访问码 8 字符", len(code.replace(" ", "")) == 8)
        sid = share["share_id"]

        r = c.get(f"{API}/shares/{sid}/access", params={"access_code": "0000 0000"}, headers=PLANNER)
        check("错误访问码 → 401", r.status_code == 401)
        r = c.get(f"{API}/shares/{sid}/access", params={"access_code": code}, headers=PLANNER)
        check("正确访问码 → 200", r.status_code == 200)
        r = c.get(f"{API}/shares/{sid}", headers=ATTENDING)
        check("再次读取不再返回访问码", "access_code" not in r.json())

        post(c, f"/shares/{sid}:revoke")
        r = c.get(f"{API}/shares/{sid}/access", params={"access_code": code}, headers=PLANNER)
        check("撤销后访问 → 410 GONE", r.status_code == 410 and r.json()["error"]["code"] == "GONE")

        print("\n[7] 幂等与错误体")
        key = str(uuid.uuid4())
        body = {"patient_ref": "MRN:IDEM", "studies": [f"1.2.3.{uuid.uuid4().int}"]}
        a = c.post(f"{API}/cases", json=body, headers={**ATTENDING, "Idempotency-Key": key})
        b = c.post(f"{API}/cases", json=body, headers={**ATTENDING, "Idempotency-Key": key})
        check("幂等重放返回同一 Case", a.json()["case_id"] == b.json()["case_id"])

        r = c.post(f"{API}/cases", json=body, headers=ATTENDING)
        check("缺幂等键 → 400", r.status_code == 400 and r.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED")

        r = c.get(f"{API}/cases/{cid}", headers={"X-User-Id": "u-stranger", "X-User-Roles": "reviewer"})
        check("越权访问 Case → 404", r.status_code == 404)

        r = c.get(f"{API}/cases/{cid}")
        check("未登录 → 401", r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHENTICATED")

        print("\n[8] FR-04 分割像素闭环")
        # Q-SEG-PIXELS 的关闭条件是"存在一条产品内路径能产出带像素的 SEG"。
        # 这一段就是那条路径的端到端验证：种子 → 像素 → 体积 → 解剖安全距离。
        r = c.get(f"{BASE}/dicomweb/studies", params={"StudyDescription": "SYNTHETIC"})
        studies = r.json()
        if not studies:
            check("归档存在合成 CT（scripts/make_synthetic_ct.py）", False,
                  "无数据则整段无法验证")
        else:
            # DICOM JSON：每个属性是 [{vr, Value}] 数组
            synth = studies[0]["0020000D"][0]["Value"][0]
            r = post(c, "/cases", json={"patient_ref": "MRN:PIXEL", "studies": [synth],
                                        "source": "pacs", "modality": "CT"})
            check("像素用例 Case → 201", r.status_code == 201, r.text[:200])
            pcid = r.json()["case_id"]

            def grow(name: str, seed, radius: float):
                res = post(c, f"/cases/{pcid}/segments/grow", json={
                    "study_instance_uid": synth,
                    "seed": {"geometry": "patient_lps_mm", "coordinates": list(seed)},
                    "segment_name": name, "hu_tolerance": 5.0, "radius_mm": radius})
                check(f"种子生长「{name}」→ 201", res.status_code == 201, res.text[:200])
                obj = res.json()
                check(f"「{name}」带像素", obj.get("has_pixels") is True)
                vol = obj.get("volume") or {}
                check(f"「{name}」体积可算",
                      vol.get("status") == "ok" and (vol.get("volume_mm3") or 0) > 0,
                      json.dumps(vol, ensure_ascii=False)[:160])
                post(c, f"/segments/{obj['seg_uid']}:confirm")
                return obj

            a = grow("肝A", (-78.0, 15.0, 16.5), 40.0)
            b = grow("肝B", (-70.0, 25.0, 12.0), 20.0)

            # 布尔合并的对象必须是像素；老实现会建一个 0 像素的空壳，界面照样显示成功。
            r = post(c, f"/cases/{pcid}/segments/merge", json={
                "seg_uids": [a["seg_uid"], b["seg_uid"]],
                "result_name": "合并肝", "operation": "union"})
            check("布尔合并 → 201", r.status_code == 201, r.text[:200])
            merged = r.json()
            check("合并结果带像素", merged.get("has_pixels") is True)
            check("合并体积 ≥ 任一源",
                  (merged.get("volume") or {}).get("voxel_count", 0)
                  >= max(a["volume"]["voxel_count"], b["volume"]["voxel_count"]))

            # 入路从对侧穿向靶点：safe_distance 仍是直线长度，risk 必须来自解剖距离。
            r = post(c, f"/cases/{pcid}/plans", json={
                "base_seg_uid": a["seg_uid"], "base_seg_version": 1,
                "entry_point": {"geometry": "patient_lps_mm", "coordinates": [-200.0, 15.0, 16.5]},
                "target_point": {"geometry": "patient_lps_mm", "coordinates": [50.0, 15.0, 16.5]}})
            check("像素用例规划 → 201", r.status_code == 201, r.text[:200])
            plan = r.json()
            check("解剖距离有值", plan.get("anatomical_distance_mm") == 0.0,
                  str(plan.get("anatomical_distance_mm")))
            check("穿入分割 → 高风险", plan.get("risk_level") == "high", plan.get("risk_level"))
            check("风险依据写明轴线穿入分割", "穿过了分割区域" in (plan.get("risk_basis") or ""),
                  plan.get("risk_basis") or "")

            # 同一段长度、但远离分割的入路：距离为正，依据写明按外表面距离分级。
            r = post(c, f"/cases/{pcid}/plans", json={
                "base_seg_uid": a["seg_uid"], "base_seg_version": 1,
                "entry_point": {"geometry": "patient_lps_mm", "coordinates": [-200.0, -100.0, 16.5]},
                "target_point": {"geometry": "patient_lps_mm", "coordinates": [50.0, -100.0, 16.5]}})
            far = r.json()
            check("远离分割的入路 → 201", r.status_code == 201, r.text[:200])
            check("解剖距离为正且按外表面计算",
                  (far.get("anatomical_distance_mm") or 0) > 0
                  and "外表面" in (far.get("risk_basis") or ""),
                  f"{far.get('anatomical_distance_mm')} / {far.get('risk_basis')}")

    print(f"\n{'=' * 52}")
    if failed:
        print(f"\033[31m{len(failed)} 项失败\033[0m / 共 {passed + len(failed)} 项")
        for f in failed:
            print(f"  - {f}")
        return 1
    print(f"\033[32m全部 {passed} 项通过\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())