"""坐标契约（docs/COORDINATE-CONTRACT.md）的服务端验收断言 A1-A9 / B1-B6。

核心是「不做静默兜底」：坐标系没声明、坐标缺上下文、引用不全，一律 422，
而不是生成一个看起来正常、但空间位置无从解读的 SR。
"""

from __future__ import annotations

import io

import pydicom
import pytest

from app.core.db import _backfill_coord_geometry, engine
from app.dicom.store import archive

API = "/api/v1"


@pytest.fixture(scope="module")
def real_instance():
    """从归档里取一个真实实例，FOR UID 派生要靠它。"""
    archive.ensure_loaded()
    for study in archive.list_studies():
        for series in study.series.values():
            if series.instances:
                inst = series.instances[0]
                return {
                    "study": study.study_instance_uid,
                    "series": series.series_instance_uid,
                    "sop": inst.sop_instance_uid,
                    "for": inst.meta["FrameOfReferenceUID"],
                }
    pytest.skip("归档里没有 DICOM 实例")


def _coord(real_instance, **over):
    base = {
        "geometry": "patient_lps_mm",
        "graphic_type": "POLYLINE",
        "graphic_data": [0.0, 0.0, 0.0, 10.0, 0.0, 0.0],
        "study_instance_uid": real_instance["study"],
        "sop_instance_uid": real_instance["sop"],
    }
    base.update(over)
    return base


def _post(client, headers, case, real_instance, **over):
    body = {
        "study_instance_uid": real_instance["study"],
        "type": "length",
        "value": 10.0,
        "unit": "mm",
        "series_instance_uid": real_instance["series"],
        "coord_ref": _coord(real_instance, **over),
    }
    return client.post(
        f"{API}/cases/{case['case_id']}/measurements", json=body, headers=headers
    )


# ============ A1 / A2：geometry 归一与拒绝 ============


def test_a1_legacy_geometry_is_normalized(client, attending, idem, case, real_instance):
    """历史写法 frame_of_reference / volume_world 落库后读回为 patient_lps_mm。"""
    for legacy in ("frame_of_reference", "volume_world"):
        r = _post(client, {**attending, **idem()}, case, real_instance, geometry=legacy)
        assert r.status_code == 201, r.text
        assert r.json()["coord_ref"]["geometry"] == "patient_lps_mm", legacy


def test_a2_unknown_geometry_rejected(client, attending, idem, case, real_instance):
    r = _post(client, {**attending, **idem()}, case, real_instance, geometry="ras_mm")
    assert r.status_code == 422
    # 错误体必须带上规范值，否则调用方不知道该填什么
    assert "patient_lps_mm" in r.text


def test_backfill_rewrites_legacy_rows(client, attending, idem, case, real_instance):
    """存量回填（契约 §3.3）：直接往库里塞旧写法，回填后必须归一并残留为 0。"""
    import sqlalchemy as sa

    r = _post(client, {**attending, **idem()}, case, real_instance)
    assert r.status_code == 201, r.text
    sr_uid = r.json()["sr_uid"]

    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "UPDATE measurements SET coord_ref = json_replace("
                "coord_ref, '$.geometry', 'volume_world') WHERE sr_uid = :u"
            ),
            {"u": sr_uid},
        )
        before = conn.execute(
            sa.text(
                "SELECT json_extract(coord_ref, '$.geometry') FROM measurements WHERE sr_uid = :u"
            ),
            {"u": sr_uid},
        ).scalar()
    assert before == "volume_world"

    _backfill_coord_geometry()

    with engine.begin() as conn:
        after = conn.execute(
            sa.text(
                "SELECT json_extract(coord_ref, '$.geometry') FROM measurements WHERE sr_uid = :u"
            ),
            {"u": sr_uid},
        ).scalar()
        leftover = conn.execute(
            sa.text(
                "SELECT count(*) FROM measurements WHERE json_extract(coord_ref, '$.geometry') "
                "IS NOT NULL AND json_extract(coord_ref, '$.geometry') <> 'patient_lps_mm'"
            )
        ).scalar()
    assert after == "patient_lps_mm"
    assert leftover == 0


# ============ A3 / A4：graphic_data 长度 ============


def test_a3_graphic_data_length_not_multiple_of_three(client, attending, idem, case, real_instance):
    r = _post(
        client, {**attending, **idem()}, case, real_instance, graphic_data=[0.0, 0.0, 0.0, 1.0]
    )
    assert r.status_code == 422


def test_a4_length_requires_polyline_of_six(client, attending, idem, case, real_instance):
    """length 必须 POLYLINE + 6 个值；给 9 个（angle 的形状）要 422。"""
    r = _post(
        client,
        {**attending, **idem()},
        case,
        real_instance,
        graphic_data=[0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 2.0, 2.0, 2.0],
    )
    assert r.status_code == 422
    assert "POLYLINE" in r.text


def test_a4_polygon_must_be_closed(client, attending, idem, case, real_instance):
    """PS3.3 C.18.9.1.2：POLYGON 首尾顶点必须相同，2 个点是非法的。"""
    r = _post(
        client,
        {**attending, **idem()},
        case,
        real_instance,
        graphic_type="POLYGON",
        graphic_data=[0.0, 0.0, 0.0, 1.0, 1.0, 1.0],
    )
    assert r.status_code == 422


# ============ A5 / A6 / A7 / A9：上下文与归属 ============


def test_a5_missing_sop_instance_uid(client, attending, idem, case, real_instance):
    body = {
        "study_instance_uid": real_instance["study"],
        "type": "length",
        "value": 10.0,
        "unit": "mm",
        "series_instance_uid": real_instance["series"],
        "coord_ref": {
            "geometry": "patient_lps_mm",
            "graphic_type": "POLYLINE",
            "graphic_data": [0, 0, 0, 1, 0, 0],
            "study_instance_uid": real_instance["study"],
        },
    }
    r = client.post(
        f"{API}/cases/{case['case_id']}/measurements",
        json=body,
        headers={**attending, **idem()},
    )
    assert r.status_code == 422


def test_a6_top_level_series_instance_uid_required(client, attending, idem, case, real_instance):
    """顶层 series_instance_uid 缺失 → 422，不再回落 f"{sr_uid}.1"。"""
    body = {
        "study_instance_uid": real_instance["study"],
        "type": "length",
        "value": 10.0,
        "unit": "mm",
        "coord_ref": _coord(real_instance),
    }
    r = client.post(
        f"{API}/cases/{case['case_id']}/measurements",
        json=body,
        headers={**attending, **idem()},
    )
    assert r.status_code == 422
    assert "series_instance_uid" in r.json()["error"]["details"].get("field", "")


def test_a7_series_instance_uid_forbidden_inside_coord_ref(
    client, attending, idem, case, real_instance
):
    """coord_ref 里再带一份 series_instance_uid 就是第二份事实源。"""
    r = _post(
        client,
        {**attending, **idem()},
        case,
        real_instance,
        series_instance_uid=real_instance["series"],
    )
    assert r.status_code == 422


def test_x5_derived_fields_not_accepted(client, attending, idem, case, real_instance):
    """spacing / frame_of_reference_uid 属服务端可派生数据，不接收前端值。"""
    for extra in ({"row_spacing": 0.7}, {"frame_of_reference_uid": "1.2.3.4"}):
        r = _post(client, {**attending, **idem()}, case, real_instance, **extra)
        assert r.status_code == 422, extra


def test_a9_unknown_sop_instance_uid_rejected(client, attending, idem, case, real_instance):
    """取不到 FrameOfReferenceUID → 422，不生成 SCOORD3D。"""
    r = _post(client, {**attending, **idem()}, case, real_instance, sop_instance_uid="1.2.3.4.5")
    assert r.status_code == 422
    assert "FrameOfReferenceUID" in r.text


def test_coord_ref_study_mismatch(client, attending, idem, case, real_instance):
    r = _post(
        client, {**attending, **idem()}, case, real_instance, study_instance_uid="1.9.9.9"
    )
    assert r.status_code == 422


# ============ B1-B6：DICOM 产物层 ============


def _sr(client, headers, sr_uid):
    content = client.get(f"{API}/measurements/{sr_uid}/content", headers=headers)
    assert content.status_code == 200
    return pydicom.dcmread(io.BytesIO(content.content), force=True)


def test_b1_b2_b4_b5_b6_sr_contract(client, attending, idem, case, real_instance):
    r = _post(client, {**attending, **idem()}, case, real_instance)
    assert r.status_code == 201, r.text
    sr_uid = r.json()["sr_uid"]
    ds = _sr(client, attending, sr_uid)

    num = ds.ContentSequence[0]
    # B4：引用留在 NUM 项上
    refs = num.ReferencedSOPSequence
    assert len(refs) >= 1
    assert (
        refs[0].ReferencedSeriesSequence[0].ReferencedSOPSequence[0].ReferencedSOPInstanceUID
        == real_instance["sop"]
    )
    # B6：UCUM mm
    assert num.MeasurementUnitsCodeSequence[0].CodeValue == "mm"

    # B1：SCOORD3D 存在且带非空 FOR UID（服务端派生，不是前端传的）
    scoord = num.ContentSequence[0]
    assert scoord.ValueType == "SCOORD3D"
    assert scoord.ReferencedFrameOfReferenceUID == real_instance["for"]
    assert scoord.ReferencedFrameOfReferenceUID.strip() != ""
    # SCOORD3D has no children（PS3.3 Supplement 162）
    assert "ContentSequence" not in scoord

    # B2：GraphicData 与 graphic_type 匹配
    assert scoord.GraphicType == "POLYLINE"
    assert len(scoord.GraphicData) == 6

    # B5：SR 挂在真实序列下，不是凭空捏造的 f"{sr_uid}.1"
    assert ds.SeriesInstanceUID == real_instance["series"]
    assert ds.SeriesInstanceUID != f"{sr_uid}.1"


# ============ 规划点位（契约 §5）============


def test_plan_points_require_geometry_declaration(client, attending, idem, case, study_uid):
    """裸 {x,y,z} 不再被接受 —— 没有声明就分不清是体素索引还是 LPS 毫米。"""
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
            "entry_point": {"x": 0, "y": 0, "z": 0},
            "target_point": {"x": 30, "y": 40, "z": 0},
        },
        headers={**attending, **idem()},
    )
    assert r.status_code == 422


def test_plan_point_geometry_must_be_canonical(client, attending, idem, case, study_uid):
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
            "entry_point": {"geometry": "voxel_index", "coordinates": [0, 0, 0]},
            "target_point": {"geometry": "patient_lps_mm", "coordinates": [30, 40, 0]},
        },
        headers={**attending, **idem()},
    )
    assert r.status_code == 422
    assert "patient_lps_mm" in r.text


def test_plan_distance_uses_declared_coordinates(client, attending, idem, case, study_uid):
    seg = client.post(
        f"{API}/cases/{case['case_id']}/segments",
        json={"study_instance_uid": study_uid, "segment_name": "肝"},
        headers={**attending, **idem()},
    ).json()
    client.post(f"{API}/segments/{seg['seg_uid']}:confirm", headers={**attending, **idem()})
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
    assert r.status_code == 201, r.text
    assert r.json()["safe_distance"] == 50.0
