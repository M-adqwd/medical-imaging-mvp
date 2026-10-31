"""契约测试：逐条验证接口与契约规范 v0.9 的约束。"""

from __future__ import annotations

import io

import pydicom
import pytest

from app.core.errors import HTTP_FOR_CODE

API = "/api/v1"


# ============ §5.4 幂等 ============

def test_write_requires_idempotency_key(client, attending, study_uid):
    r = client.post(
        f"{API}/cases",
        json={"patient_ref": "P1", "studies": [study_uid]},
        headers=attending,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"


def test_idempotent_replay_returns_same_response(client, attending, idem, study_uid):
    key = idem()
    body = {"patient_ref": "P1", "studies": [study_uid]}
    r1 = client.post(f"{API}/cases", json=body, headers={**attending, **key})
    r2 = client.post(f"{API}/cases", json=body, headers={**attending, **key})
    assert r1.status_code == r2.status_code == 201
    assert r1.json()["case_id"] == r2.json()["case_id"]


def test_idempotency_key_conflict_on_different_payload(client, attending, idem, study_uid):
    key = idem()
    client.post(f"{API}/cases", json={"patient_ref": "P1", "studies": [study_uid]}, headers={**attending, **key})
    r = client.post(
        f"{API}/cases",
        json={"patient_ref": "P2", "studies": [study_uid]},
        headers={**attending, **key},
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"


# ============ §4 错误体 ============

def test_error_envelope_shape(client):
    r = client.get(f"{API}/cases/x-not-exist")
    body = r.json()
    assert set(body["error"]) == {"code", "message", "details", "request_id"}
    assert body["error"]["code"] == "UNAUTHENTICATED"
    assert body["error"]["request_id"]


def test_business_code_maps_to_http_status():
    assert HTTP_FOR_CODE["NOT_FOUND"] == 404
    assert HTTP_FOR_CODE["STATE_CONFLICT"] == 409
    assert HTTP_FOR_CODE["VALIDATION_ERROR"] == 422
    assert HTTP_FOR_CODE["LOCKED"] == 423
    assert HTTP_FOR_CODE["GONE"] == 410


# ============ §3.1 Case ============

def test_create_case_defaults(client, case):
    assert case["status"] == "open"
    assert case["source"] == "pacs"
    assert case["owner_id"] == "u-attending"
    assert len(case["studies"]) == 1


def test_case_auto_merge_same_study(client, attending, idem, study_uid):
    """FR-01 自动归并：同一 Study 不重复建 Case。"""
    first = client.post(
        f"{API}/cases",
        json={"patient_ref": "P1", "studies": [study_uid]},
        headers={**attending, **idem()},
    ).json()
    second = client.post(
        f"{API}/cases",
        json={"patient_ref": "P1", "studies": [study_uid]},
        headers={**attending, **idem()},
    ).json()
    assert first["case_id"] == second["case_id"]


def test_case_list_only_shows_own(client, attending, planner, idem, study_uid):
    client.post(
        f"{API}/cases",
        json={"patient_ref": "P1", "studies": [study_uid]},
        headers={**attending, **idem()},
    )
    r = client.get(f"{API}/cases", headers=planner)
    assert r.status_code == 200
    assert r.json()["total"] == 0


def test_case_state_machine_closed(client, attending, idem, case):
    cid = case["case_id"]
    close = client.post(f"{API}/cases/{cid}:close", headers={**attending, **idem()})
    assert close.status_code == 200
    assert close.json()["status"] == "closed"
    # 关闭后不能重复 close
    again = client.post(f"{API}/cases/{cid}:close", headers={**attending, **idem()})
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "STATE_CONFLICT"
    reopen = client.post(f"{API}/cases/{cid}:reopen", headers={**attending, **idem()})
    assert reopen.json()["status"] == "open"


def test_closed_case_blocks_new_measurement(client, attending, idem, case, study_uid):
    cid = case["case_id"]
    client.post(f"{API}/cases/{cid}:close", headers={**attending, **idem()})
    r = client.post(
        f"{API}/cases/{cid}/measurements",
        json={"study_instance_uid": study_uid, "type": "length", "value": 10, "unit": "mm"},
        headers={**attending, **idem()},
    )
    assert r.status_code == 409
    assert r.json()["error"]["details"]["current_state"] == "closed"


# ============ §3.2 Measurement SR ============

def test_measurement_requires_unit(client, attending, idem, case, study_uid):
    r = client.post(
        f"{API}/cases/{case['case_id']}/measurements",
        json={"study_instance_uid": study_uid, "type": "length", "value": 10, "unit": ""},
        headers={**attending, **idem()},
    )
    assert r.status_code == 422


def test_measurement_text_type_requires_text(client, attending, idem, case, study_uid):
    r = client.post(
        f"{API}/cases/{case['case_id']}/measurements",
        json={"study_instance_uid": study_uid, "type": "free-text", "text": ""},
        headers={**attending, **idem()},
    )
    assert r.status_code == 422


def test_measurement_state_machine(client, attending, idem, case, study_uid):
    r = client.post(
        f"{API}/cases/{case['case_id']}/measurements",
        json={"study_instance_uid": study_uid, "type": "length", "value": 34.7, "unit": "mm"},
        headers={**attending, **idem()},
    )
    sr_uid = r.json()["sr_uid"]
    assert r.json()["state"] == "draft"

    saved = client.post(f"{API}/measurements/{sr_uid}:save", headers={**attending, **idem()})
    assert saved.json()["state"] == "saved"

    # saved 后不可再 PUT
    put = client.put(
        f"{API}/measurements/{sr_uid}",
        json={"value": 40},
        headers={**attending, **idem(), "If-Match": "1"},
    )
    assert put.status_code == 409

    wd = client.post(f"{API}/measurements/{sr_uid}:withdraw", headers={**attending, **idem()})
    assert wd.json()["state"] == "withdrawn"

    # withdrawn 是终态，不可回到 saved
    back = client.post(f"{API}/measurements/{sr_uid}:save", headers={**attending, **idem()})
    assert back.status_code == 409


def test_measurement_generates_valid_dicom_sr(client, attending, idem, case, study_uid):
    r = client.post(
        f"{API}/cases/{case['case_id']}/measurements",
        json={"study_instance_uid": study_uid, "type": "length", "value": 34.7, "unit": "mm"},
        headers={**attending, **idem()},
    )
    sr_uid = r.json()["sr_uid"]
    content = client.get(f"{API}/measurements/{sr_uid}/content", headers=attending)
    assert content.status_code == 200
    assert content.headers["content-type"].startswith("application/dicom")

    ds = pydicom.dcmread(io.BytesIO(content.content), force=True)
    assert ds.SOPInstanceUID == sr_uid
    assert ds.Modality == "SR"
    assert ds.PatientID == "MRN:P001234"
    assert ds.ContentSequence[0].NumericValue == "34.7"
    assert ds.ContentSequence[0].MeasurementUnitsCodeSequence[0].CodeValue == "mm"


def test_cross_series_flag_persisted(client, attending, idem, case, study_uid):
    r = client.post(
        f"{API}/cases/{case['case_id']}/measurements",
        json={
            "study_instance_uid": study_uid,
            "type": "length",
            "value": 12.0,
            "unit": "mm",
            "cross_series": True,
        },
        headers={**attending, **idem()},
    )
    sr_uid = r.json()["sr_uid"]
    assert r.json()["cross_series"] is True
    ds = pydicom.dcmread(
        io.BytesIO(client.get(f"{API}/measurements/{sr_uid}/content", headers=attending).content),
        force=True,
    )
    assert ds.DerivationDescription == "CROSS-SERIES MEASUREMENT"


# ============ §3.3 Segment ============

def _confirmed_seg(client, headers, idem, case, study_uid, name="肝脏"):  # noqa: ANN001
    seg = client.post(
        f"{API}/cases/{case['case_id']}/segments",
        json={"study_instance_uid": study_uid, "segment_name": name, "algorithm": "manual"},
        headers={**headers, **idem()},
    ).json()
    done = client.post(f"{API}/segments/{seg['seg_uid']}:confirm", headers={**headers, **idem()}).json()
    return done


def test_segment_ai_output_is_suggested_until_confirmed(client, attending, idem, case, study_uid):
    seg = client.post(
        f"{API}/cases/{case['case_id']}/segments",
        json={"study_instance_uid": study_uid, "segment_name": "肝", "algorithm": "manual"},
        headers={**attending, **idem()},
    ).json()
    assert seg["state"] == "draft"
    confirmed = client.post(f"{API}/segments/{seg['seg_uid']}:confirm", headers={**attending, **idem()})
    assert confirmed.json()["state"] == "confirmed"
    assert confirmed.json()["confirmed_by"] == "u-attending"


def test_resident_cannot_confirm_segment(client, resident, idem, attending, case, study_uid):
    seg = client.post(
        f"{API}/cases/{case['case_id']}/segments",
        json={"study_instance_uid": study_uid, "segment_name": "肝"},
        headers={**attending, **idem()},
    ).json()
    r = client.post(f"{API}/segments/{seg['seg_uid']}:confirm", headers={**resident, **idem()})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "PERMISSION_DENIED"


def test_segment_revise_increments_version(client, attending, idem, case, study_uid):
    seg = _confirmed_seg(client, attending, idem, case, study_uid)
    assert seg["version"] == 1
    rev = client.post(f"{API}/segments/{seg['seg_uid']}:revise", headers={**attending, **idem()}).json()
    assert rev["version"] == 2
    assert rev["state"] == "draft"


def test_segment_merge_requires_confirmed_or_suggested(client, attending, idem, case, study_uid):
    a = client.post(
        f"{API}/cases/{case['case_id']}/segments",
        json={"study_instance_uid": study_uid, "segment_name": "肝左叶"},
        headers={**attending, **idem()},
    ).json()
    b = _confirmed_seg(client, attending, idem, case, study_uid, "肝右叶")
    r = client.post(
        f"{API}/cases/{case['case_id']}/segments/merge",
        json={"seg_uids": [a["seg_uid"], b["seg_uid"]], "result_name": "完整肝脏", "operation": "union"},
        headers={**attending, **idem()},
    )
    assert r.status_code == 409  # a 仍是 draft


def test_segment_merge_success(client, attending, idem, case, study_uid):
    """状态与归属都合法的两个 SEG 才会走到像素合并这一步。

    这里两个 SEG 是元数据骨架（无像素），所以合并被拒 —— 这正是要守住的语义：
    布尔运算的对象是像素，产出一个「名字叫完整肝脏、实际 0 像素」的对象，
    比直接报错危险得多。带像素的成功路径见 test_seg_pixels.py。
    """
    a = _confirmed_seg(client, attending, idem, case, study_uid, "肝左叶")
    b = _confirmed_seg(client, attending, idem, case, study_uid, "肝右叶")
    r = client.post(
        f"{API}/cases/{case['case_id']}/segments/merge",
        json={"seg_uids": [a["seg_uid"], b["seg_uid"]], "result_name": "完整肝脏", "operation": "union"},
        headers={**attending, **idem()},
    )
    assert r.status_code == 422
    assert "pixel_merge_failed" in r.text


# ============ §3.4 Plan ============

def test_plan_requires_confirmed_seg(client, attending, idem, case, study_uid):
    seg = client.post(
        f"{API}/cases/{case['case_id']}/segments",
        json={"study_instance_uid": study_uid, "segment_name": "肝"},
        headers={**attending, **idem()},
    ).json()
    r = client.post(
        f"{API}/cases/{case['case_id']}/plans",
        json={
            "base_seg_uid": seg["seg_uid"],
            "base_seg_version": 1,
            "entry_point": {"geometry": "patient_lps_mm", "coordinates": [0, 0, 0]},
            "target_point": {"geometry": "patient_lps_mm", "coordinates": [10, 0, 0]},
        },
        headers={**attending, **idem()},
    )
    assert r.status_code == 409
    assert "confirmed" in r.json()["error"]["message"]


def test_plan_requires_exact_seg_version(client, attending, idem, case, study_uid):
    seg = _confirmed_seg(client, attending, idem, case, study_uid)
    client.post(f"{API}/segments/{seg['seg_uid']}:revise", headers={**attending, **idem()})
    r = client.post(
        f"{API}/cases/{case['case_id']}/plans",
        json={
            "base_seg_uid": seg["seg_uid"],
            "base_seg_version": 1,
            "entry_point": {"geometry": "patient_lps_mm", "coordinates": [0, 0, 0]},
            "target_point": {"geometry": "patient_lps_mm", "coordinates": [10, 0, 0]},
        },
        headers={**attending, **idem()},
    )
    assert r.status_code == 422
    assert r.json()["error"]["details"]["current_version"] == 2


def test_plan_computes_safe_distance_and_risk(client, attending, idem, case, study_uid):
    seg = _confirmed_seg(client, attending, idem, case, study_uid)
    r = client.post(
        f"{API}/cases/{case['case_id']}/plans",
        json={
            "base_seg_uid": seg["seg_uid"],
            "base_seg_version": 1,
            "entry_point": {"geometry": "patient_lps_mm", "coordinates": [0, 0, 0]},
            "target_point": {"geometry": "patient_lps_mm", "coordinates": [30, 40, 0]},
        },
        headers={**attending, **idem()},
    )
    assert r.status_code == 201
    assert r.json()["safe_distance"] == 50.0
    # 测试用 SEG 是无像素空壳，此时不得给出分级结论 —— 否则一条 50mm 的
    # 入路会被渲染成绿色「低风险」，而它与解剖结构毫无关系。
    assert r.json()["risk_level"] == "unknown"
    assert "无像素" in (r.json()["risk_basis"] or "")


def test_plan_export_requires_saved_state(client, attending, idem, case, study_uid):
    seg = _confirmed_seg(client, attending, idem, case, study_uid)
    plan = client.post(
        f"{API}/cases/{case['case_id']}/plans",
        json={
            "base_seg_uid": seg["seg_uid"],
            "base_seg_version": 1,
            "entry_point": {"geometry": "patient_lps_mm", "coordinates": [0, 0, 0]},
            "target_point": {"geometry": "patient_lps_mm", "coordinates": [30, 0, 0]},
        },
        headers={**attending, **idem()},
    ).json()
    r = client.post(f"{API}/plans/{plan['plan_id']}:export", headers={**attending, **idem()})
    assert r.status_code == 409


def test_plan_export_contains_seg_version(client, attending, idem, case, study_uid):
    seg = _confirmed_seg(client, attending, idem, case, study_uid)
    plan = client.post(
        f"{API}/cases/{case['case_id']}/plans",
        json={
            "base_seg_uid": seg["seg_uid"],
            "base_seg_version": 1,
            "entry_point": {"geometry": "patient_lps_mm", "coordinates": [0, 0, 0]},
            "target_point": {"geometry": "patient_lps_mm", "coordinates": [30, 0, 0]},
        },
        headers={**attending, **idem()},
    ).json()
    client.post(f"{API}/plans/{plan['plan_id']}:save", headers={**attending, **idem()})
    r = client.post(f"{API}/plans/{plan['plan_id']}:export", headers={**attending, **idem()})
    assert r.status_code == 200
    assert seg["seg_uid"].encode() in r.content
    assert b"v1" in r.content


def test_resident_cannot_export_plan(client, resident, attending, planner, idem, case, study_uid):
    seg = _confirmed_seg(client, attending, idem, case, study_uid)
    plan = client.post(
        f"{API}/cases/{case['case_id']}/plans",
        json={
            "base_seg_uid": seg["seg_uid"],
            "base_seg_version": 1,
            "entry_point": {"geometry": "patient_lps_mm", "coordinates": [0, 0, 0]},
            "target_point": {"geometry": "patient_lps_mm", "coordinates": [30, 0, 0]},
        },
        headers={**attending, **idem()},
    ).json()
    client.post(f"{API}/plans/{plan['plan_id']}:save", headers={**attending, **idem()})
    r = client.post(f"{API}/plans/{plan['plan_id']}:export", headers={**resident, **idem()})
    assert r.status_code == 403


# ============ §3.5 Report ============

def test_report_submit_requires_findings_and_impression(client, attending, idem, case):
    rep = client.post(
        f"{API}/cases/{case['case_id']}/reports",
        json={"template_id": "ct-chest-default-v3", "findings": "", "impression": ""},
        headers={**attending, **idem()},
    ).json()
    r = client.post(f"{API}/reports/{rep['report_id']}:submit", headers={**attending, **idem()})
    assert r.status_code == 422
    failed = r.json()["error"]["details"]["failed_refs"]
    assert any("findings" in x for x in failed)
    assert any("impression" in x for x in failed)


def test_report_ref_must_carry_version(client, attending, idem, case):
    r = client.post(
        f"{API}/cases/{case['case_id']}/reports",
        json={
            "template_id": "t1",
            "refs": [{"kind": "measurement", "uid": "1.2.3"}],
        },
        headers={**attending, **idem()},
    )
    assert r.status_code == 422
    errs = r.json()["error"]["details"]["errors"]
    assert any("version" in e["loc"] for e in errs)


def test_report_full_flow_generates_sr(client, attending, idem, case, study_uid):
    m = client.post(
        f"{API}/cases/{case['case_id']}/measurements",
        json={"study_instance_uid": study_uid, "type": "length", "value": 12.0, "unit": "mm"},
        headers={**attending, **idem()},
    ).json()
    client.post(f"{API}/measurements/{m['sr_uid']}:save", headers={**attending, **idem()})

    rep = client.post(
        f"{API}/cases/{case['case_id']}/reports",
        json={
            "template_id": "ct-chest-default-v3",
            "findings": "双肺纹理清晰",
            "impression": "未见明显异常",
            "refs": [{"kind": "measurement", "uid": m["sr_uid"], "version": 1}],
        },
        headers={**attending, **idem()},
    ).json()
    sub = client.post(f"{API}/reports/{rep['report_id']}:submit", headers={**attending, **idem()})
    assert sub.status_code == 200
    assert sub.json()["state"] == "submitted"
    assert sub.json()["submitted_at"]

    content = client.get(f"{API}/reports/{rep['report_id']}/content", headers=attending)
    assert content.status_code == 200
    ds = pydicom.dcmread(io.BytesIO(content.content), force=True)
    assert ds.Modality == "SR"


def test_report_submit_rejects_withdrawn_ref(client, attending, idem, case, study_uid):
    m = client.post(
        f"{API}/cases/{case['case_id']}/measurements",
        json={"study_instance_uid": study_uid, "type": "length", "value": 12.0, "unit": "mm"},
        headers={**attending, **idem()},
    ).json()
    client.post(f"{API}/measurements/{m['sr_uid']}:save", headers={**attending, **idem()})
    client.post(f"{API}/measurements/{m['sr_uid']}:withdraw", headers={**attending, **idem()})

    rep = client.post(
        f"{API}/cases/{case['case_id']}/reports",
        json={
            "template_id": "t1",
            "findings": "x",
            "impression": "y",
            "refs": [{"kind": "measurement", "uid": m["sr_uid"], "version": 1}],
        },
        headers={**attending, **idem()},
    ).json()
    r = client.post(f"{API}/reports/{rep['report_id']}:submit", headers={**attending, **idem()})
    assert r.status_code == 422
    assert "withdrawn" in str(r.json()["error"]["details"]["failed_refs"])


def test_locked_report_is_immutable(client, attending, idem, case):
    rep = client.post(
        f"{API}/cases/{case['case_id']}/reports",
        json={"template_id": "t1", "findings": "a", "impression": "b"},
        headers={**attending, **idem()},
    ).json()
    client.post(f"{API}/reports/{rep['report_id']}:submit", headers={**attending, **idem()})
    locked = client.post(f"{API}/reports/{rep['report_id']}:lock", headers={**attending, **idem()})
    assert locked.json()["state"] == "locked"
    assert locked.json()["reviewed_by"] == "u-attending"

    edit = client.put(
        f"{API}/reports/{rep['report_id']}",
        json={"findings": "changed"},
        headers={**attending, **idem()},
    )
    assert edit.status_code == 409
    wd = client.post(f"{API}/reports/{rep['report_id']}:withdraw", headers={**attending, **idem()})
    assert wd.status_code == 423


# ============ §3.6 Share ============

def test_share_access_code_shown_once(client, attending, idem, case):
    created = client.post(
        f"{API}/cases/{case['case_id']}/shares",
        json={"permission": "readonly+comment", "scope": "full"},
        headers={**attending, **idem()},
    ).json()
    assert len(created["access_code"].replace(" ", "")) == 8
    assert created["url"].endswith(created["share_id"])

    fetched = client.get(f"{API}/shares/{created['share_id']}", headers=attending).json()
    assert "access_code" not in fetched


def test_share_wrong_code_returns_401(client, attending, reviewer, idem, case):
    created = client.post(
        f"{API}/cases/{case['case_id']}/shares",
        json={"permission": "readonly"},
        headers={**attending, **idem()},
    ).json()
    r = client.get(
        f"{API}/shares/{created['share_id']}/access",
        params={"access_code": "0000 0000"},
        headers=reviewer,
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHENTICATED"


def test_revoked_share_returns_410(client, attending, reviewer, idem, case):
    created = client.post(
        f"{API}/cases/{case['case_id']}/shares",
        json={"permission": "readonly"},
        headers={**attending, **idem()},
    ).json()
    client.post(f"{API}/shares/{created['share_id']}:revoke", headers={**attending, **idem()})
    r = client.get(
        f"{API}/shares/{created['share_id']}/access",
        params={"access_code": created["access_code"]},
        headers=reviewer,
    )
    assert r.status_code == 410
    assert r.json()["error"]["code"] == "GONE"


def test_expired_share_returns_410(client, attending, reviewer, idem, case):
    created = client.post(
        f"{API}/cases/{case['case_id']}/shares",
        json={"permission": "readonly", "expires_at": "2020-01-01T00:00:00Z"},
        headers={**attending, **idem()},
    ).json()
    r = client.get(
        f"{API}/shares/{created['share_id']}/access",
        params={"access_code": created["access_code"]},
        headers=reviewer,
    )
    assert r.status_code == 410


def test_share_custom_scope_requires_refs(client, attending, idem, case):
    r = client.post(
        f"{API}/cases/{case['case_id']}/shares",
        json={"permission": "readonly", "scope": "custom", "scope_refs": []},
        headers={**attending, **idem()},
    )
    assert r.status_code == 422


def test_reviewer_cannot_create_share(client, reviewer, idem, case):
    r = client.post(
        f"{API}/cases/{case['case_id']}/shares",
        json={"permission": "readonly"},
        headers={**reviewer, **idem()},
    )
    assert r.status_code == 403


# ============ §2.4 AI ============

def test_ai_callback_creates_suggested_segment(client, attending, idem, case, study_uid):
    job_id = "job-test-1"
    r = client.post(
        f"{API}/ai/infer",
        params={"study_instance_uid": study_uid},
        headers={**attending, **idem(), "X-Job-Id": job_id},
    )
    assert r.status_code == 202
    assert r.json()["job_id"] == job_id

    cb = client.post(
        f"{API}/ai-results",
        json={
            "job_id": job_id,
            "status": "done",
            "segment_name": "liver",
            "seg_content": None,
            "stats": {"segments_produced": 14, "infer_sec": 82.4},
        },
    )
    assert cb.status_code == 200
    assert cb.json()["status"] == "done"


def test_ai_job_query(client, attending, idem, case, study_uid):
    job_id = "job-test-2"
    client.post(
        f"{API}/ai/infer",
        params={"study_instance_uid": study_uid},
        headers={**attending, **idem(), "X-Job-Id": job_id},
    )
    r = client.get(f"{API}/ai/jobs/{job_id}", headers=attending)
    assert r.status_code == 200
    assert r.json()["model_version"] == "2.0.0"


# ============ §0 契约自描述 ============

def test_contract_endpoint(client):
    r = client.get(f"{API}/contract")
    assert r.status_code == 200
    assert r.json()["contract_version"] == "0.9"
    assert r.json()["actions_use_colon_suffix"] is True


def test_healthz(client):
    assert client.get("/healthz").json()["status"] == "ok"


@pytest.mark.parametrize("method", ["get", "post", "put", "delete"])
def test_unauthenticated_rejected(client, method):
    r = getattr(client, method)(f"{API}/cases")
    assert r.status_code in (401, 405)