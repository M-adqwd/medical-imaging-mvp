"""FHIR R4 只读门面。

这一组守三条约束：
1. **只读**。CapabilityStatement 里不能出现任何写交互——对外声明的能力
   就是实际能力，声明了 create 而没实现，集成方会按声明写，然后在运行期炸。
2. **不泄漏 PII / 不越权**。资源 ID 是不透明哈希；越权访问与不存在返回同一个
   404（403 与 404 的差异本身就是一次存在性探测）。
3. **没有标准编码就不编**。测量类型用本地 code system，未知单位不冒充 UCUM。
"""

from __future__ import annotations

import pytest

from app.fhir.builder import split_person_name

FHIR = "/fhir"
API = "/api/v1"


def _search(client, path: str, headers: dict[str, str], **params):  # noqa: ANN001, ANN202
    r = client.get(f"{FHIR}{path}", params=params, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _patient_entry(client, headers, mrn: str = "MRN:P001234") -> dict:  # noqa: ANN001
    bundle = _search(client, "/Patient", headers, identifier=mrn)
    assert bundle["total"] == 1, bundle
    return bundle["entry"][0]["resource"]


# ============ 能力声明 ============


def test_metadata_declares_read_only_surface(client, attending):  # noqa: ANN001
    r = client.get(f"{FHIR}/metadata", headers=attending)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/fhir+json")

    cap = r.json()
    assert cap["resourceType"] == "CapabilityStatement"
    assert cap["fhirVersion"] == "4.0.1"
    assert cap["kind"] == "instance"

    resources = {item["type"]: item for item in cap["rest"][0]["resource"]}
    assert set(resources) == {"Patient", "ImagingStudy", "DiagnosticReport", "Observation", "Endpoint"}

    # 声明的交互必须与实现一致：只有 read / search-type
    codes = {i["code"] for item in resources.values() for i in item.get("interaction", [])}
    assert codes == {"read", "search-type"}, codes


def test_metadata_requires_credentials(client):  # noqa: ANN001
    r = client.get(f"{FHIR}/metadata")
    assert r.status_code == 401
    body = r.json()
    assert body["resourceType"] == "OperationOutcome"
    assert body["issue"][0]["code"] == "login"


# ============ Patient ============


def test_patient_search_returns_bundle_with_self_link(client, case, attending):  # noqa: ANN001
    bundle = _search(client, "/Patient", attending, identifier="MRN:P001234", _count=10)
    assert bundle["resourceType"] == "Bundle"
    assert bundle["type"] == "searchset"
    assert bundle["total"] == 1
    assert bundle["link"][0]["relation"] == "self"
    assert len(bundle["entry"]) == 1


def test_patient_identifier_keeps_mrn_but_id_is_opaque(client, case, attending):  # noqa: ANN001
    resource = _patient_entry(client, attending)
    assert resource["identifier"] == [{"system": "urn:miis:mrn", "value": "MRN:P001234"}]
    # ID 不得包含 MRN 原文，否则等于把 PII 写进 URL、日志与浏览器历史
    assert "P001234" not in resource["id"]
    assert resource["id"].startswith("pat-")


def test_patient_identifier_accepts_bare_number(client, case, attending):  # noqa: ANN001
    bundle = _search(client, "/Patient", attending, identifier="P001234")
    assert bundle["total"] == 1


def test_patient_name_search_is_case_insensitive(client, case, attending):  # noqa: ANN001
    # conftest 的病例没有 patient_name，这里只验证「不存在的名字返回空 Bundle」这条路径
    bundle = _search(client, "/Patient", attending, name="不存在的人")
    assert bundle["total"] == 0
    assert "entry" not in bundle


def test_patient_read_unknown_id_returns_operation_outcome(client, case, attending):  # noqa: ANN001
    r = client.get(f"{FHIR}/Patient/pat-doesnotexist", headers=attending)
    assert r.status_code == 404
    body = r.json()
    assert body["resourceType"] == "OperationOutcome"
    assert body["issue"][0]["code"] == "not-found"
    assert body["issue"][0]["expression"] == ["Patient/pat-doesnotexist"]


def test_person_name_splits_only_on_dicom_separator():
    assert split_person_name("ZHANG^SAN") == {"family": "ZHANG", "given": ["SAN"]}
    assert split_person_name("LI^SI^MING") == {"family": "LI", "given": ["SI", "MING"]}
    # 中文名没有可信的姓/名切分点，整串放 text —— 硬拆会把「欧阳」拆成姓欧
    assert split_person_name("欧阳明") == {"text": "欧阳明"}
    assert split_person_name("") is None
    assert split_person_name(None) is None


# ============ ImagingStudy ============


def test_imaging_study_uses_dicom_uid_identifier(client, case, study_uid, attending):  # noqa: ANN001
    bundle = _search(client, "/ImagingStudy", attending, patient="MRN:P001234")
    assert bundle["total"] == 1
    resource = bundle["entry"][0]["resource"]
    assert resource["identifier"] == [{"system": "urn:dicom:uid", "value": f"urn:oid:{study_uid}"}]
    assert resource["subject"]["reference"].startswith("Patient/pat-")
    # 测试环境归档里没有这份检查的实例，必须如实报 unknown，而不是假装 available
    assert resource["status"] == "unknown"
    assert resource["numberOfSeries"] == 0


def test_imaging_study_search_by_unknown_patient_returns_empty_bundle(client, case, attending):  # noqa: ANN001
    bundle = _search(client, "/ImagingStudy", attending, patient="pat-nobody")
    assert bundle["total"] == 0


# ============ DiagnosticReport ============


def _create_report(client, case, attending, idem, **overrides):  # noqa: ANN001, ANN003
    payload = {"template_id": "ct-chest-v1", "findings": "双肺纹理清晰\n未见明显实变", "impression": "未见异常"}
    payload.update(overrides)
    r = client.post(
        f"{API}/cases/{case['case_id']}/reports",
        json=payload,
        headers={**attending, **idem()},
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_draft_report_maps_to_preliminary(client, case, attending, idem):  # noqa: ANN001
    report = _create_report(client, case, attending, idem)
    r = client.get(f"{FHIR}/DiagnosticReport/{report['report_id']}", headers=attending)
    assert r.status_code == 200, r.text
    resource = r.json()
    assert resource["resourceType"] == "DiagnosticReport"
    assert resource["status"] == "preliminary"
    assert resource["subject"]["reference"].startswith("Patient/pat-")
    assert resource["imagingStudy"][0]["reference"].startswith("ImagingStudy/img-")
    # 发现文本进 narrative，必须转义且保留换行
    assert "双肺纹理清晰" in resource["text"]["div"]
    assert "<br/>" in resource["text"]["div"]


def test_submitted_report_maps_to_final(client, case, attending, idem):  # noqa: ANN001
    report = _create_report(client, case, attending, idem)
    r = client.post(f"{API}/reports/{report['report_id']}:submit", json={}, headers={**attending, **idem()})
    assert r.status_code == 200, r.text

    resource = client.get(f"{FHIR}/DiagnosticReport/{report['report_id']}", headers=attending).json()
    assert resource["status"] == "final"
    # 提交后必然生成了 SR，presentedForm 要指出来
    assert resource["presentedForm"][0]["contentType"] == "application/dicom"


def test_report_search_filters_by_status(client, case, attending, idem):  # noqa: ANN001
    _create_report(client, case, attending, idem)
    drafts = _search(client, "/DiagnosticReport", attending, status="preliminary")
    finals = _search(client, "/DiagnosticReport", attending, status="final")
    assert drafts["total"] == 1
    assert finals["total"] == 0


# ============ Observation ============


def _create_measurement(client, case, study_uid, attending, idem, **overrides):  # noqa: ANN001, ANN003
    payload = {"study_instance_uid": study_uid, "type": "length", "value": 12.5, "unit": "mm"}
    payload.update(overrides)
    r = client.post(
        f"{API}/cases/{case['case_id']}/measurements",
        json=payload,
        headers={**attending, **idem()},
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_length_measurement_maps_to_ucum_quantity(client, case, study_uid, attending, idem):  # noqa: ANN001
    measurement = _create_measurement(client, case, study_uid, attending, idem)
    bundle = _search(client, "/Observation", attending, patient="MRN:P001234")
    assert bundle["total"] == 1
    resource = bundle["entry"][0]["resource"]
    assert resource["status"] == "preliminary"
    assert resource["code"]["coding"][0]["system"] == "urn:miis:code:measurement-type"
    assert resource["code"]["coding"][0]["code"] == "length"
    quantity = resource["valueQuantity"]
    assert quantity["value"] == 12.5
    assert quantity["system"] == "http://unitsofmeasure.org"
    assert quantity["code"] == "mm"
    assert resource["derivedFrom"][0]["reference"].startswith("ImagingStudy/img-")
    assert measurement["sr_uid"]


def test_unknown_unit_is_not_forced_into_ucum(client, case, study_uid, attending, idem):  # noqa: ANN001
    _create_measurement(
        client,
        case,
        study_uid,
        attending,
        idem,
        unit="HU",
        type="free-text",
        value=266,
        text="主动脉腔平均密度",
    )
    resource = _search(client, "/Observation", attending, patient="MRN:P001234")["entry"][0]["resource"]
    quantity = resource["valueQuantity"]
    assert quantity["unit"] == "HU"
    # 没有 UCUM 映射就不填 system/code，避免下游按错误编码换算
    assert "system" not in quantity and "code" not in quantity


# ============ 权限隔离 ============


def test_other_user_sees_nothing(client, case, planner):  # noqa: ANN001
    # planner 也是 case.read 角色，但不是这条病例的 owner 也不是参与者
    bundle = _search(client, "/Patient", planner, identifier="MRN:P001234")
    assert bundle["total"] == 0


def test_other_user_gets_404_not_403(client, case, attending, planner):  # noqa: ANN001
    patient_id = _patient_entry(client, attending)["id"]
    r = client.get(f"{FHIR}/Patient/{patient_id}", headers=planner)
    assert r.status_code == 404
    # 越权与不存在必须同响应，否则 403/404 的差异可用来枚举他人病例
    assert r.json()["issue"][0]["code"] == "not-found"


def test_admin_sees_everything(client, case, admin):  # noqa: ANN001
    bundle = _search(client, "/Patient", admin, identifier="MRN:P001234")
    assert bundle["total"] == 1


# ============ Endpoint ============


def test_endpoint_points_at_dicomweb(client, attending):  # noqa: ANN001
    resource = client.get(f"{FHIR}/Endpoint/dicomweb", headers=attending).json()
    assert resource["resourceType"] == "Endpoint"
    assert resource["connectionType"]["coding"][0]["code"] == "dicom-wado-rs"
    assert resource["address"].endswith("/dicomweb")


def test_contract_endpoints_still_use_api_error_envelope(client, attending, idem):  # noqa: ANN001
    """FHIR 换了错误壳，但 /api/v1 必须保持不变——否则前端全部要改。"""
    r = client.get(f"{API}/cases/not-a-real-case", headers=attending)
    assert r.status_code == 404
    assert "error" in r.json()
    assert r.json()["error"]["code"] == "NOT_FOUND"
