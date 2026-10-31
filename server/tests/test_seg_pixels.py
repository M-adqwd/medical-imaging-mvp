"""SEG 像素通路：掩膜编码 / 几何往返 / 解剖距离 / 区域生长。

这一组测试守的是一条**能力是否存在**，不是"函数写得对不对"：
Q-SEG-PIXELS 的原始状态是"没有任何路径能产出带像素的 SEG"，所以断言落在
「有没有一条产品内路径能让 has_pixels 变成 true」，以及「像素出现之后，
风险分级不会退回拿入路长度冒充安全距离」。
"""

from __future__ import annotations

import base64

import numpy as np
import pytest

from app.dicom import mask as mask_builder
from app.dicom import seg as seg_builder
from app.dicom.store import archive

API = "/api/v1"
def _synth_study_uid() -> str | None:
    """按 StudyDescription 反查合成 CT 的 UID，不写死。

    写死 UID 的代价是无声的：脚本重新生成数据换了 UID，测试就整组 skip，
    报告上仍是一片绿。这类"永远跳过"的测试比失败的测试更危险。
    """
    for study in archive.list_studies():
        if "SYNTHETIC" in (study.study_description or "").upper():
            return study.study_instance_uid
    return None


SYNTH_STUDY = _synth_study_uid()  # scripts/make_synthetic_ct.py 产出；无数据时为 None

# 坐标契约 §3：所有点位都在 patient_lps_mm 下表达
LPS = "patient_lps_mm"


def _point(x: float, y: float, z: float) -> dict:
    return {"geometry": LPS, "coordinates": [x, y, z]}


def _mask_seg(mask: np.ndarray, *, spacing=(0.7, 0.7, 1.0), origin=(-3.15, -3.15, -4.0)) -> bytes:
    return mask_builder.build_seg_from_mask(
        seg_uid="2.25.777",
        study_instance_uid="2.25.999",
        series_instance_uid=None,
        patient_id="P1",
        patient_name="T^P",
        segment_name="靶区",
        algorithm="algo:test-v1",
        mask=mask,
        spacing=spacing,
        origin=origin,
        referenced_sop_uids=["2.25.1001"],
        producer="unit-test",
        method="unit-test",
    )


def _cube() -> np.ndarray:
    """8×10×10 里放一个 3×4×4 的实心块：48 体素。"""
    arr = np.zeros((8, 10, 10), dtype=bool)
    arr[2:5, 3:7, 3:7] = True
    return arr


# ============ 编码 ============


def test_mask_seg_carries_real_pixels():
    """核心断言：掩膜进来，像素出去。这是 Q-SEG-PIXELS 的"有没有"那一层。"""
    data = _mask_seg(_cube())

    assert seg_builder.has_pixel_data(data) is True
    volume = seg_builder.compute_volume(data)
    assert volume.status == seg_builder.VOLUME_OK
    assert volume.voxel_count == 48
    assert volume.volume_mm3 == pytest.approx(48 * 0.7 * 0.7 * 1.0)


def test_mask_roundtrip_is_lossless():
    """位打包 + 帧优先顺序错了不会报错，只会把掩膜搬到别的地方，所以必须逐体素比。"""
    arr = _cube()
    decoded = mask_builder.seg_mask_array(_mask_seg(arr))
    assert decoded is not None
    assert decoded.shape == arr.shape
    assert np.array_equal(decoded, arr)


def test_provenance_is_self_declared():
    """算法产出必须自曝，否则下游无法把它和医师勾画区分开。"""
    ds = mask_builder.read_seg(_mask_seg(_cube()))
    assert ds is not None
    assert ds.SegmentSequence[0].SegmentAlgorithmType == "SEMIAUTOMATIC"
    block = ds.private_block(0x000B, mask_builder.PRIVATE_CREATOR)
    assert block[mask_builder.TAG_CLINICAL_USE].value == "NO"
    assert "不得用于临床诊断" in block[mask_builder.TAG_NOTICE].value


# ============ 几何 ============


def test_geometry_survives_roundtrip():
    data = _mask_seg(_cube())
    geom = mask_builder.seg_geometry(data)
    assert geom is not None
    assert geom.spacing == (0.7, 0.7, 1.0)
    assert geom.frames == 8 and geom.rows == 10 and geom.cols == 10

    for voxel in [(0.0, 0.0, 0.0), (3.5, 4.5, 3.0), (9.0, 9.0, 7.0)]:
        back = geom.lps_to_voxel(geom.voxel_to_lps(*voxel))
        assert back == pytest.approx(voxel)


def test_row_and_col_spacing_are_not_interchangeable():
    """各向异性间距下，行/列配错方向会整体偏一个间距因子，且不会报错。

    这里用 spacing=(1.0, 2.0, 3.0)：行间距 1mm、列间距 2mm、层间距 3mm。
    行的方向是 IOP 后三个值 (0,1,0)→y，列是前三个 (1,0,0)→x。
    """
    arr = np.zeros((2, 2, 2), dtype=bool)
    arr[1, 1, 1] = True  # 行 1 / 列 1 / 帧 1
    data = _mask_seg(arr, spacing=(1.0, 2.0, 3.0), origin=(0.0, 0.0, 0.0))

    geom = mask_builder.seg_geometry(data)
    assert geom is not None
    x, y, z = geom.voxel_to_lps(1, 1, 1)
    assert (x, y, z) == pytest.approx((2.0, 1.0, 3.0))

    assert mask_builder.point_inside_mask(data, (2.0, 1.0, 3.0)) is True
    assert mask_builder.point_inside_mask(data, (1.0, 2.0, 3.0)) is False


def test_header_only_seg_has_no_geometry():
    """元数据骨架没有空间定位 —— 不是"默认在原点"，是"不知道在哪"。"""
    header = seg_builder.build_seg_header(
        seg_uid="2.25.901",
        study_instance_uid="2.25.902",
        series_instance_uid=None,
        patient_id="P1",
        patient_name="T^P",
        segment_name="肝",
        algorithm="manual",
        referenced_series=[],
    )
    assert mask_builder.seg_geometry(seg_builder.to_bytes(header)) is None


# ============ 解剖距离 ============


def test_distance_to_surface_not_to_center():
    """方块外表面在 x=1.05，靶点在 x=21.05 → 距离 20mm。

    如果错取"到体素质心"或"到入点长度"，这个数会变成完全不同的量。
    """
    data = _mask_seg(_cube())
    distance, basis = mask_builder.min_distance_to_segment(
        data, (41.05, 0.0, -2.0), (21.05, 0.0, -2.0)
    )
    assert distance == pytest.approx(20.0, abs=0.05)
    assert "最小距离" in basis


def test_axis_through_structure_is_zero():
    data = _mask_seg(_cube())
    # 轴线横穿方块内部（y 从 -10 到 10，穿过 y=0 的中心列）
    distance, basis = mask_builder.min_distance_to_segment(
        data, (-20.0, 0.0, -2.0), (20.0, 0.0, -2.0)
    )
    assert distance == 0.0
    assert "穿过" in basis


def test_distance_unavailable_without_geometry():
    """没有空间定位的分割算不出距离，必须返回 None 而不是某个数。"""
    header = seg_builder.build_seg_header(
        seg_uid="2.25.903",
        study_instance_uid="2.25.904",
        series_instance_uid=None,
        patient_id="P1",
        patient_name="T^P",
        segment_name="肝",
        algorithm="manual",
        referenced_series=[],
    )
    distance, basis = mask_builder.min_distance_to_segment(
        seg_builder.to_bytes(header), (0.0, 0.0, 0.0), (10.0, 0.0, 0.0)
    )
    assert distance is None
    assert "空间定位" in basis


# ============ 产品内路径：区域生长 ============


def _case_with_synth_study(client, attending, idem) -> dict:
    r = client.post(
        f"{API}/cases",
        json={"patient_ref": "MRN:P0099", "studies": [SYNTH_STUDY], "source": "pacs", "modality": "CT"},
        headers={**attending, **idem()},
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_grow_endpoint_produces_pixel_seg(client, attending, idem):
    """从 API 走一遍：种子点 → 带像素 SEG → 体积有数。"""
    if not SYNTH_STUDY:
        pytest.skip("本机没有合成 CT 序列（scripts/make_synthetic_ct.py）")

    case = _case_with_synth_study(client, attending, idem)
    r = client.post(
        f"{API}/cases/{case['case_id']}/segments/grow",
        json={
            "study_instance_uid": SYNTH_STUDY,
            "seed": _point(-78.0, 15.0, 16.5),  # 合成 CT 的肝脏内部
            "segment_name": "肝",
            "hu_tolerance": 5.0,
            "radius_mm": 45.0,
        },
        headers={**attending, **idem()},
    )
    assert r.status_code == 201, r.text
    body = r.json()

    assert body["has_pixels"] is True
    assert body["volume"]["status"] == "ok"
    assert body["volume"]["volume_mm3"] > 0
    assert body["volume"]["spacing_mm"] == pytest.approx([0.7, 0.7, 1.0])
    assert body["state"] == "draft"
    assert body["algorithm"].startswith("algo:")


def test_grow_rejects_seed_outside_volume(client, attending, idem):
    """种子落在体外 → 422。不能悄悄在别处长出一块东西。"""
    if not SYNTH_STUDY:
        pytest.skip("本机没有合成 CT 序列")

    case = _case_with_synth_study(client, attending, idem)
    r = client.post(
        f"{API}/cases/{case['case_id']}/segments/grow",
        json={
            "study_instance_uid": SYNTH_STUDY,
            "seed": _point(5000.0, 5000.0, 5000.0),
            "segment_name": "越界",
        },
        headers={**attending, **idem()},
    )
    assert r.status_code == 422, r.text
    assert "seed" in r.text


def test_grow_rejects_unknown_study(client, attending, idem):
    case = _case_with_synth_study(client, attending, idem)
    r = client.post(
        f"{API}/cases/{case['case_id']}/segments/grow",
        json={
            "study_instance_uid": "2.25.404404",
            "seed": _point(-78.0, 15.0, 16.5),
            "segment_name": "肝",
        },
        headers={**attending, **idem()},
    )
    assert r.status_code == 422, r.text
    assert "series_not_found" in r.text


# ============ 风险分级不再拿长度冒充安全距离 ============


def test_risk_uses_anatomical_distance(client, attending, idem, case, study_uid):
    created = client.post(
        f"{API}/cases/{case['case_id']}/segments",
        json={
            "study_instance_uid": study_uid,
            "segment_name": "血管",
            "algorithm": "manual",
            "seg_content": base64.b64encode(_mask_seg(_cube())).decode(),
        },
        headers={**attending, **idem()},
    )
    assert created.status_code == 201, created.text
    seg_uid = created.json()["seg_uid"]
    client.post(f"{API}/segments/{seg_uid}:confirm", headers={**attending, **idem()})

    # 靶点在方块外侧 20mm，入路再往外 20mm：入路长度 20mm、解剖距离 20mm。
    # 若代码仍在长达 20mm 上分级，两者恰好同值 —— 所以这里同时断言两个字段
    # 都存在，再另用一条 case 验证"长度与距离不等"时取的是后者。
    plan = client.post(
        f"{API}/cases/{case['case_id']}/plans",
        json={
            "base_seg_uid": seg_uid,
            "base_seg_version": 1,
            "entry_point": _point(41.05, 0.0, -2.0),
            "target_point": _point(21.05, 0.0, -2.0),
        },
        headers={**attending, **idem()},
    )
    assert plan.status_code == 201, plan.text
    body = plan.json()

    assert body["safe_distance"] == pytest.approx(20.0)
    assert body["anatomical_distance_mm"] == pytest.approx(20.0, abs=0.05)
    assert body["risk_level"] == "low"
    assert "最小距离" in body["risk_basis"]


def test_long_path_near_structure_is_not_low_risk(client, attending, idem, case, study_uid):
    """Q-SAFE-DISTANCE 的原案：180mm 长入路、贴着结构走。

    旧实现在这里给出 LOW（长度 ≥ 10mm），而真实解剖距离是 1mm —— 该是 HIGH。
    """
    created = client.post(
        f"{API}/cases/{case['case_id']}/segments",
        json={
            "study_instance_uid": study_uid,
            "segment_name": "血管",
            "algorithm": "manual",
            "seg_content": base64.b64encode(_mask_seg(_cube())).decode(),
        },
        headers={**attending, **idem()},
    )
    assert created.status_code == 201, created.text
    seg_uid = created.json()["seg_uid"]
    client.post(f"{API}/segments/{seg_uid}:confirm", headers={**attending, **idem()})

    # 入路从 y=-90 走到 y=+90（180mm），x 停在 1.05+1.0=2.05，贴着方块侧面 1mm
    plan = client.post(
        f"{API}/cases/{case['case_id']}/plans",
        json={
            "base_seg_uid": seg_uid,
            "base_seg_version": 1,
            "entry_point": _point(2.05, -90.0, -2.0),
            "target_point": _point(2.05, 90.0, -2.0),
        },
        headers={**attending, **idem()},
    )
    assert plan.status_code == 201, plan.text
    body = plan.json()

    assert body["safe_distance"] == pytest.approx(180.0)          # 路径确实很长
    assert body["anatomical_distance_mm"] == pytest.approx(1.0, abs=0.1)
    assert body["risk_level"] == "high"                          # 而不是 low


def test_target_inside_seg_is_not_graded(client, attending, idem, case, study_uid):
    """靶点就在分割里（分割即靶区）→ 不判等级，而不是判成 HIGH。

    这条例外不是"放过"，是方向问题：把靶区当回避结构会得出刚好相反的建议。
    """
    created = client.post(
        f"{API}/cases/{case['case_id']}/segments",
        json={
            "study_instance_uid": study_uid,
            "segment_name": "肿瘤",
            "algorithm": "manual",
            "seg_content": base64.b64encode(_mask_seg(_cube())).decode(),
        },
        headers={**attending, **idem()},
    )
    seg_uid = created.json()["seg_uid"]
    client.post(f"{API}/segments/{seg_uid}:confirm", headers={**attending, **idem()})

    plan = client.post(
        f"{API}/cases/{case['case_id']}/plans",
        json={
            "base_seg_uid": seg_uid,
            "base_seg_version": 1,
            "entry_point": _point(-60.0, 0.0, -2.0),
            "target_point": _point(0.0, 0.0, -2.0),  # 方块内部
        },
        headers={**attending, **idem()},
    )
    assert plan.status_code == 201, plan.text
    body = plan.json()

    assert body["risk_level"] == "unknown"
    assert body["anatomical_distance_mm"] is None
    assert "靶区" in body["risk_basis"]


# ============ TC-08 AC-0：体积不接受手填 ============


def test_manual_volume_measurement_is_rejected(client, attending, idem, case, study_uid):
    r = client.post(
        f"{API}/cases/{case['case_id']}/measurements",
        json={"study_instance_uid": study_uid, "type": "volume", "value": 1234.0, "unit": "mm3"},
        headers={**attending, **idem()},
    )
    assert r.status_code == 422, r.text
    assert "volume_requires_seg_pixels" in r.text


# ============ 布尔运算：合并必须产出像素，而不是一个空壳 ============


def test_boolean_union_operates_on_pixels():
    """合并的产物必须是"两个掩膜的并"。曾经这里只建了个空壳 SEG，
    界面上叫「完整肝脏」，实际 0 个像素 —— 合并按钮等于什么都不做。"""
    left = np.zeros((8, 10, 10), dtype=bool)
    left[2:5, 2:5, 3:7] = True  # 3 行 × 3 列 × 4 层 = 36
    right = np.zeros((8, 10, 10), dtype=bool)
    right[2:5, 4:8, 3:7] = True  # 3 × 4 × 4 = 48，其中第 4 列 12 个体素与左侧重叠

    merged, geom = mask_builder.merge_masks(
        [_mask_seg(left), _mask_seg(right)], "union"
    )
    assert int(merged.sum()) == 72  # 36 + 48 - 12
    assert merged.shape == (8, 10, 10)
    # 几何取自第一个 SEG，合并结果才能和后续的距离/体积计算对齐
    assert geom.spacing == (0.7, 0.7, 1.0)

    encoded = mask_builder.build_seg_from_mask(
        seg_uid="2.25.888",
        study_instance_uid="2.25.999",
        series_instance_uid=None,
        patient_id="P1",
        patient_name="T^P",
        segment_name="完整",
        algorithm="merged",
        mask=merged,
        spacing=geom.spacing,
        origin=geom.origin,
        row_dir=geom.row_dir,
        col_dir=geom.col_dir,
        producer="boolean-merge",
        method="union",
    )
    assert seg_builder.has_pixel_data(encoded)
    assert int(mask_builder.seg_mask_array(encoded).sum()) == 72


def test_boolean_intersection_and_difference():
    left = np.zeros((8, 10, 10), dtype=bool)
    left[2:5, 2:5, 3:7] = True
    right = np.zeros((8, 10, 10), dtype=bool)
    right[2:5, 4:8, 3:7] = True

    both = [_mask_seg(left), _mask_seg(right)]
    inter, _ = mask_builder.merge_masks(both, "intersection")
    assert int(inter.sum()) == 12  # 重叠只有 1 列 × 3 行 × 4 层

    diff, _ = mask_builder.merge_masks(both, "difference")
    assert int(diff.sum()) == 24  # 左减右：3 列里去掉重叠的 1 列


def test_boolean_merge_rejects_mismatched_grid():
    """跨栅格做逐体素布尔运算，结果会整体错位而界面上看不出来。必须拒绝。"""
    a = _mask_seg(_cube())
    b = _mask_seg(_cube(), origin=(10.0, -3.15, -4.0))
    with pytest.raises(ValueError, match="同一体素栅格"):
        mask_builder.merge_masks([a, b], "union")


def test_boolean_merge_rejects_pixel_less_seg():
    shell = seg_builder.to_bytes(
        seg_builder.build_seg_header(
            seg_uid="2.25.555",
            study_instance_uid="2.25.999",
            series_instance_uid=None,
            patient_id="P1",
            patient_name="T^P",
            segment_name="空壳",
            algorithm="ai:demo",
            referenced_series=[],
        )
    )
    with pytest.raises(ValueError, match="没有像素数据"):
        mask_builder.merge_masks([shell, _mask_seg(_cube())], "union")


def test_merge_endpoint_refuses_pixel_less_sources(client, attending, idem, case, study_uid):
    """接口层同样不能"合并成功但产出空壳"。"""
    segs = []
    for name in ("肝左叶", "肝右叶"):
        s = client.post(
            f"{API}/cases/{case['case_id']}/segments",
            json={"study_instance_uid": study_uid, "segment_name": name},
            headers={**attending, **idem()},
        ).json()
        client.post(f"{API}/segments/{s['seg_uid']}:confirm", headers={**attending, **idem()})
        segs.append(s)

    r = client.post(
        f"{API}/cases/{case['case_id']}/segments/merge",
        json={"seg_uids": [s["seg_uid"] for s in segs], "result_name": "完整肝脏",
              "operation": "union"},
        headers={**attending, **idem()},
    )
    assert r.status_code == 422, r.text
    assert "pixel_merge_failed" in r.text


def test_merge_endpoint_produces_pixel_union(client, attending, idem):
    """有像素的两个 SEG 合并：结果必须仍带像素、体积可算。"""
    if not SYNTH_STUDY:
        pytest.skip("本机没有合成 CT 序列（scripts/make_synthetic_ct.py）")

    case = _case_with_synth_study(client, attending, idem)
    grown = []
    for name, seed in (("肝A", (-78.0, 15.0, 16.5)), ("肝B", (-70.0, 25.0, 12.0))):
        r = client.post(
            f"{API}/cases/{case['case_id']}/segments/grow",
            json={
                "study_instance_uid": SYNTH_STUDY,
                "seed": _point(*seed),
                "segment_name": name,
                "hu_tolerance": 5.0,
                "radius_mm": 20.0,
            },
            headers={**attending, **idem()},
        )
        assert r.status_code == 201, r.text
        grown.append(r.json())
        confirm = client.post(
            f"{API}/segments/{r.json()['seg_uid']}:confirm", headers={**attending, **idem()}
        )
        assert confirm.status_code == 200, confirm.text

    r = client.post(
        f"{API}/cases/{case['case_id']}/segments/merge",
        json={
            "seg_uids": [g["seg_uid"] for g in grown],
            "result_name": "合并肝",
            "operation": "union",
        },
        headers={**attending, **idem()},
    )
    assert r.status_code == 201, r.text
    body = r.json()

    assert body["algorithm"] == "merged"
    assert body["has_pixels"] is True
    assert body["volume"]["status"] == "ok"
    # 并集不小于任一输入的体素数；两个种子若长出同一块，等号成立
    assert body["volume"]["voxel_count"] >= max(
        g["volume"]["voxel_count"] for g in grown
    )
