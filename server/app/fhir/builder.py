"""领域模型 → FHIR R4 资源的纯函数映射。

这一层刻意不碰数据库会话与 HTTP：输入是 ORM 对象或归档记录，输出是 dict。
好处是所有映射规则都能用 pytest 直接断言，不需要起服务。

ID 策略（重要）：
FHIR 的 `id` 必须匹配 `[A-Za-z0-9\\-\\.]{1,64}`。DICOM UID 动辄 60+ 字符且含点，
患者 MRN 直接进 URL 又等于把 PII 放进日志与浏览器历史。所以除报告（本身就是
UUID）以外，一律用 `前缀 + sha1(原值)[:16]` 生成不透明 ID，原值放 `identifier`。
代价是「按 ID 反查」只能遍历比对——MVP 数据量下可接受，也避免额外映射表。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

DICOM_CS = "http://dicom.nema.org/resources/ontology/DCM"
LOINC_CS = "http://loinc.org"
UCUM_CS = "http://unitsofmeasure.org"
OBS_CATEGORY_CS = "http://terminology.hl7.org/CodeSystem/observation-category"
ENDPOINT_CONN_CS = "http://terminology.hl7.org/CodeSystem/endpoint-connection-type"
ENDPOINT_PAYLOAD_CS = "http://terminology.hl7.org/CodeSystem/endpoint-payload-type"

# 测量类型没有可靠的 LOINC 等价物（长度/角度在影像语境下没有公认的通用 LOINC 编码），
# 与其硬填一个看起来像 LOINC 的假码，不如用自己的 code system 并如实标注来源。
MIIS_MEASUREMENT_CS = "urn:miis:code:measurement-type"
MIIS_MRN_SYSTEM = "urn:miis:mrn"

MEASUREMENT_DISPLAY: dict[str, str] = {
    "length": "长度",
    "angle": "角度",
    "area": "面积",
    "volume": "体积",
    "free-text": "文字标注",
    "arrow": "箭头标注",
}

# measurement.unit 的实测取值 → (UCUM code, 展示单位)
UNIT_TO_UCUM: dict[str, tuple[str, str]] = {
    "mm": ("mm", "mm"),
    "cm": ("cm", "cm"),
    "deg": ("deg", "°"),
    "°": ("deg", "°"),
    "mm2": ("mm2", "mm²"),
    "mm²": ("mm2", "mm²"),
    "ml": ("mL", "mL"),
    "mL": ("mL", "mL"),
}

# 报告状态 → FHIR DiagnosticReport.status
REPORT_STATUS: dict[str, str] = {
    "draft": "preliminary",
    "submitted": "final",
    "locked": "final",
    "withdrawn": "cancelled",
    # 引用已失效、内容待复核：FHIR 没有直接对应值，最接近的是 partial（内容不完整）
    "stale-ref": "partial",
}

# 测量状态 → FHIR Observation.status
OBSERVATION_STATUS: dict[str, str] = {
    "draft": "preliminary",
    "saved": "final",
    "withdrawn": "entered-in-error",
}


def _clean(value: Any) -> Any:
    """递归剔除 None / 空串 / 空容器。

    FHIR 服务端对空数组的容忍度不一（HAPI 会拒 `series: []`），
    与其赌下游实现，不如在出口就保证「没有就是没有」。
    """
    if isinstance(value, dict):
        out = {k: _clean(v) for k, v in value.items()}
        return {k: v for k, v in out.items() if v not in (None, "", [], {})}
    if isinstance(value, list):
        items = [_clean(v) for v in value]
        return [v for v in items if v not in (None, "", [], {})]
    return value


def stable_id(prefix: str, key: str) -> str:
    return f"{prefix}-{hashlib.sha1(key.encode('utf-8')).hexdigest()[:16]}"


def patient_id(patient_ref: str) -> str:
    return stable_id("pat", patient_ref)


def study_id(study_instance_uid: str) -> str:
    return stable_id("img", study_instance_uid)


def observation_id(sr_uid: str) -> str:
    return stable_id("obs", sr_uid)


def split_person_name(raw: str | None) -> dict[str, Any] | None:
    """DICOM 的 `LAST^FIRST^MIDDLE` 与中文姓名分开处理。

    中文名没有 family/given 的可信切分，硬拆会把「欧阳」拆成姓「欧」，
    所以只在有 `^` 分隔符时才拆，否则整串放 text。
    """
    if not raw or not raw.strip():
        return None
    value = raw.strip()
    if "^" not in value:
        return {"text": value}
    parts = [p.strip() for p in value.split("^")]
    family = parts[0]
    given = [p for p in parts[1:] if p]
    name: dict[str, Any] = {}
    if family:
        name["family"] = family
    if given:
        name["given"] = given
    return name or {"text": value}


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    # 统一按 UTC 输出：库里存的是 UTC，本地化会让同一份数据在不同部署时区下 URL 不一致
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def dicom_date(value: str | None) -> str | None:
    """DICOM DA (YYYYMMDD) → FHIR dateTime (YYYY-MM-DD)。无法解析时返回 None，不猜。"""
    if not value:
        return None
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    if len(text) == 10 and text[4] == "-":
        return text
    return None


def _ref(resource_type: str, resource_id: str) -> dict[str, str]:
    return {"reference": f"{resource_type}/{resource_id}"}


def endpoint_resource(fhir_base: str) -> dict[str, Any]:
    return _clean(
        {
            "resourceType": "Endpoint",
            "id": "dicomweb",
            "status": "active",
            "connectionType": {
                "coding": [{"system": ENDPOINT_CONN_CS, "code": "dicom-wado-rs", "display": "DICOM WADO-RS"}]
            },
            "payloadType": [
                {"coding": [{"system": ENDPOINT_PAYLOAD_CS, "code": "any", "display": "Any"}]}
            ],
            "address": f"{fhir_base.removesuffix('/fhir')}/dicomweb",
            "name": "MIIS DICOMweb 归档",
        }
    )


def patient_resource(
    patient_ref: str,
    patient_name: str | None,
    *,
    case_ids: list[str],
    last_updated: datetime | None,
    fhir_base: str,
) -> dict[str, Any]:
    resource: dict[str, Any] = {
        "resourceType": "Patient",
        "id": patient_id(patient_ref),
        "active": True,
        "identifier": [{"system": MIIS_MRN_SYSTEM, "value": patient_ref}],
        "name": [split_person_name(patient_name)] if split_person_name(patient_name) else [],
        "meta": {
            "lastUpdated": _iso(last_updated),
            # 用 tag 而不是 extension：调用方不需要读 profile 就能看出这是本系统的病例聚合
            "tag": [{"system": "urn:miis:case-count", "code": str(len(case_ids))}],
        },
    }
    return _clean(resource)


def imaging_study_resource(
    study_instance_uid: str,
    record: Any | None,
    *,
    patient_ref: str,
    case_id: str,
    fhir_base: str,
) -> dict[str, Any]:
    series: list[dict[str, Any]] = []
    if record is not None:
        rows = sorted(record.series.values(), key=lambda r: (r.series_number or 0, r.series_instance_uid))
        for index, item in enumerate(rows, start=1):
            series.append(
                {
                    "uid": item.series_instance_uid,
                    "number": item.series_number or index,
                    "modality": {"coding": [{"system": DICOM_CS, "code": item.modality or "OT"}]},
                    "numberOfInstances": len(item.instances),
                    "description": item.series_description,
                    "endpoint": [_ref("Endpoint", "dicomweb")],
                }
            )

    modality = sorted({s["modality"]["coding"][0]["code"] for s in series})
    total_instances = sum(s.get("numberOfInstances") or 0 for s in series)

    resource: dict[str, Any] = {
        "resourceType": "ImagingStudy",
        "id": study_id(study_instance_uid),
        "identifier": [{"system": "urn:dicom:uid", "value": f"urn:oid:{study_instance_uid}"}],
        "status": "available" if series else "unknown",
        "subject": _ref("Patient", patient_id(patient_ref)),
        "started": dicom_date(getattr(record, "study_date", None)),
        "numberOfSeries": len(series),
        "numberOfInstances": total_instances,
        "modality": [{"coding": [{"system": DICOM_CS, "code": code}]} for code in modality],
        "description": getattr(record, "study_description", None),
        "endpoint": [_ref("Endpoint", "dicomweb")],
        "series": series,
        "meta": {
            "tag": [{"system": "urn:miis:case-id", "code": case_id}],
        },
    }
    return _clean(resource)


def diagnostic_report_resource(
    report: Any,
    *,
    patient_ref: str,
    study_uids: list[str],
    fhir_base: str,
) -> dict[str, Any]:
    status = REPORT_STATUS.get(getattr(report.state, "value", str(report.state)), "unknown")
    issued = report.submitted_at or report.updated_at
    resource: dict[str, Any] = {
        "resourceType": "DiagnosticReport",
        "id": str(report.report_id),
        "status": status,
        "category": [
            {
                "coding": [
                    {"system": "http://terminology.hl7.org/CodeSystem/v2-0074", "code": "RAD", "display": "Radiology"}
                ]
            }
        ],
        "code": {
            "coding": [{"system": LOINC_CS, "code": "18748-4", "display": "Diagnostic imaging study"}],
            "text": "影像诊断报告",
        },
        "subject": _ref("Patient", patient_id(patient_ref)),
        "imagingStudy": [_ref("ImagingStudy", study_id(uid)) for uid in study_uids],
        "issued": _iso(issued),
        "conclusion": report.impression or None,
        # 发现（findings）是自由文本，用 narrative 承载；必须转义，否则就是 XSS 入口
        "text": {
            "status": "generated",
            "div": _narrative_div(report.findings),
        },
        "performer": [{"display": report.author_id}] if report.author_id else [],
        "meta": {
            "versionId": str(report.version),
            "tag": [
                {"system": "urn:miis:report-state", "code": getattr(report.state, "value", str(report.state))},
                {"system": "urn:miis:template", "code": report.template_id},
            ],
        },
    }
    if report.sr_content:
        resource["presentedForm"] = [
            {
                "contentType": "application/dicom",
                "title": "DICOM SR（TID 1500 风格）",
                "size": len(report.sr_content),
            }
        ]
    return _clean(resource)


def _narrative_div(text: str | None) -> str:
    from html import escape

    body = escape(text or "").replace("\n", "<br/>")
    return f'<div xmlns="http://www.w3.org/1999/xhtml">{body or "（无发现描述）"}</div>'


def observation_resource(
    measurement: Any,
    *,
    patient_ref: str,
    study_instance_uid: str,
) -> dict[str, Any]:
    mtype = getattr(measurement.type, "value", str(measurement.type))
    unit_raw = measurement.unit or ""
    ucum = UNIT_TO_UCUM.get(unit_raw)

    quantity: dict[str, Any] = {}
    if measurement.value is not None:
        quantity["value"] = measurement.value
        if ucum:
            quantity["unit"] = ucum[1]
            quantity["system"] = UCUM_CS
            quantity["code"] = ucum[0]
        elif unit_raw:
            # 未知单位不冒充 UCUM：只带 unit 文本，不填 system/code
            quantity["unit"] = unit_raw

    resource: dict[str, Any] = {
        "resourceType": "Observation",
        "id": observation_id(measurement.sr_uid),
        "status": OBSERVATION_STATUS.get(
            getattr(measurement.state, "value", str(measurement.state)), "unknown"
        ),
        "category": [
            {"coding": [{"system": OBS_CATEGORY_CS, "code": "imaging", "display": "Imaging"}]}
        ],
        "code": {
            "coding": [
                {
                    "system": MIIS_MEASUREMENT_CS,
                    "code": mtype,
                    "display": MEASUREMENT_DISPLAY.get(mtype, mtype),
                }
            ],
            "text": MEASUREMENT_DISPLAY.get(mtype, mtype),
        },
        "subject": _ref("Patient", patient_id(patient_ref)),
        "effectiveDateTime": _iso(measurement.created_at),
        "valueQuantity": quantity,
        "derivedFrom": [_ref("ImagingStudy", study_id(study_instance_uid))],
        "meta": {
            "tag": [{"system": "urn:miis:sr-uid", "code": measurement.sr_uid}],
        },
    }
    if measurement.text:
        resource["note"] = [{"text": measurement.text}]
    return _clean(resource)


def search_bundle(
    resources: list[dict[str, Any]],
    *,
    total: int,
    self_url: str,
    fhir_base: str,
) -> dict[str, Any]:
    entries = []
    for item in resources:
        rtype = item["resourceType"]
        rid = item["id"]
        entries.append(
            {
                "fullUrl": f"{fhir_base}/{rtype}/{quote(str(rid))}",
                "resource": item,
                "search": {"mode": "match"},
            }
        )
    bundle: dict[str, Any] = {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": total,
        "link": [{"relation": "self", "url": self_url}],
        "entry": entries,
    }
    return _clean(bundle)


def capability_statement(fhir_base: str, *, version: str) -> dict[str, Any]:
    def res(rtype: str, *, search: list[str], extra: dict[str, Any] | None = None) -> dict[str, Any]:
        item: dict[str, Any] = {
            "type": rtype,
            "interaction": [{"code": "read"}, {"code": "search-type"}],
            "searchParam": [{"name": name, "type": "string"} for name in search],
        }
        if extra:
            item.update(extra)
        return item

    return _clean(
        {
            "resourceType": "CapabilityStatement",
            "status": "active",
            "date": "2026-10-29",
            "kind": "instance",
            "software": {"name": "MIIS 影像阅片与手术规划", "version": version},
            "implementation": {
                "description": "只读 FHIR R4 门面：把本系统的病例、检查、报告与测量暴露给院方集成层，不接受写入",
            },
            "fhirVersion": "4.0.1",
            "format": ["json"],
            "rest": [
                {
                    "mode": "server",
                    "documentation": "全部资源只读；写操作（Patient/ServiceRequest/Observation 回写）不在本期范围",
                    "security": {
                        "service": [
                            {
                                "text": "网关注入 X-User-Id / X-User-Roles，本服务按五角色能力矩阵做可见性过滤"
                            }
                        ]
                    },
                    "resource": [
                        res("Patient", search=["identifier", "name", "_count"]),
                        res("ImagingStudy", search=["patient", "started", "modality", "_count"]),
                        res("DiagnosticReport", search=["patient", "status", "_count"]),
                        res("Observation", search=["patient", "status", "_count"]),
                        res("Endpoint", search=[]),
                    ],
                    "operation": [],
                }
            ],
        }
    )
