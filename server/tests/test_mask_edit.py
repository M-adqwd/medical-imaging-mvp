"""掩膜编辑：裁切 / 形态学平滑。

这一组守的两条约束：
1. **几何不变**。编辑只改体素归属，Rows/Cols/Frames/origin/spacing 一个都不许动 ——
   一旦重采样，编辑后的分割与规划引用就在两个坐标系里，而界面上看不出来。
2. **空结果不写库**。一份 0 体素的分割在列表上显示「0.00 mL」，看起来像"裁得很准"，
   实际是盒子没罩住结构 / 核把主体吃光了。这类失败必须变成 422，而不是一条新记录。
"""

from __future__ import annotations

import base64

import numpy as np
import pytest

from app.dicom import mask as mask_builder
from app.dicom import seg as seg_builder
from app.services import mask_edit

API = "/api/v1"
SPACING = (0.7, 0.7, 1.0)  # (行, 列, 层) mm
ORIGIN = (-3.15, -3.15, -4.0)


def _mask_seg(mask: np.ndarray, *, origin=ORIGIN) -> bytes:
    return mask_builder.build_seg_from_mask(
        seg_uid="2.25.700",
        study_instance_uid="2.25.999",
        series_instance_uid=None,
        patient_id="P1",
        patient_name="T^P",
        segment_name="测试",
        algorithm="algo:test-v1",
        mask=mask,
        spacing=SPACING,
        origin=origin,
        producer="unit-test",
        method="unit-test",
    )


def _geom(mask: np.ndarray, *, origin=ORIGIN) -> mask_builder.MaskGeometry:
    frames, rows, cols = mask.shape
    return mask_builder.MaskGeometry(
        rows=rows,
        cols=cols,
        frames=frames,
        spacing=SPACING,
        origin=origin,
        row_dir=(0.0, 1.0, 0.0),
        col_dir=(1.0, 0.0, 0.0),
    )


def _block() -> np.ndarray:
    """8×10×10 的立方体块：帧 3-6、行 2-4、列 2-4，共 4×3×3=36 体素。"""
    m = np.zeros((8, 10, 10), dtype=bool)
    m[3:7, 2:5, 2:5] = True
    return m


# ============ 裁切 ============


def test_crop_keeps_geometry_and_only_zeroes_outside():
    mask = _block()
    geom = _geom(mask)
    # 盒只罩住帧 3-4（z 方向），行列给足
    cropped, stats = mask_edit.crop_to_box(
        mask, geom, (-50.0, -50.0, -1.0), (50.0, 50.0, 0.0)
    )

    assert cropped.shape == mask.shape, "裁切不允许改变数组形状（那就是重采样了）"
    assert (cropped.sum()) < mask.sum()
    # 盒外的体素必须清零，盒内必须原样保留
    assert not cropped[5:7].any()
    assert cropped[3:5].sum() == mask[3:5].sum()
    assert stats["removed_voxels"] == int(mask.sum() - cropped.sum())
    assert stats["operation"] == "crop"


def test_crop_box_outside_volume_yields_empty_but_no_exception():
    """盒完全落在体数据外时返回全 False，由上层判空 —— 不能悄悄返回原掩膜。"""
    mask = _block()
    geom = _geom(mask)
    cropped, stats = mask_edit.crop_to_box(
        mask, geom, (500.0, 500.0, 500.0), (510.0, 510.0, 510.0)
    )
    assert not cropped.any()
    assert stats["voxels_after"] == 0


def test_crop_rejects_inverted_box():
    mask = _block()
    with pytest.raises(ValueError, match="min 必须严格小于 max"):
        mask_edit.crop_to_box(mask, _geom(mask), (10.0, 10.0, 10.0), (0.0, 0.0, 0.0))


def test_crop_slices_cover_whole_volume_when_box_is_large():
    mask = _block()
    geom = _geom(mask)
    fs, rs, cs = mask_edit.crop_slices(geom, (-1e4, -1e4, -1e4), (1e4, 1e4, 1e4))
    assert (fs.start, fs.stop) == (0, geom.frames)
    assert (rs.start, rs.stop) == (0, geom.rows)
    assert (cs.start, cs.stop) == (0, geom.cols)


# ============ 平滑 ============


def test_median_removes_speck_keeps_body():
    mask = _block()
    mask[0, 0, 0] = True  # 孤立单点噪声
    geom = _geom(mask)

    smoothed, stats = mask_edit.smooth_mask(mask, geom, method="median", radius_mm=1.0)

    assert not smoothed[0, 0, 0], "中值滤波必须去掉孤立体素"
    assert smoothed[5, 3, 3], "主体不能被削掉"
    assert stats["voxels_after"] < stats["voxels_before"]
    assert stats["erased"] is False
    assert stats["method"] == "median"
    assert stats["retention"] > 0.9


def test_morphology_keeps_more_than_it_should_on_thin_structure():
    """守住这次改默认值的理由：形态学开运算会削穿薄结构，中值不会。

    真实 CTA 的主动脉腔上实测：1mm 开运算只保留 12%，中值保留 87%。
    这里用一块 1 体素厚的薄片复现同一现象 —— 若哪天有人把默认改回 morphology，
    这条会立刻红。
    """
    thin = np.zeros((8, 10, 10), dtype=bool)
    thin[3:6, 2, 2:8] = True  # 1 体素厚的薄片

    med, _ = mask_edit.smooth_mask(thin, _geom(thin), method="median", radius_mm=0.7)
    morph, _ = mask_edit.smooth_mask(thin, _geom(thin), method="morphology", radius_mm=0.7)

    assert med.sum() >= thin.sum() * 0.6, "中值滤波应当保住薄片"
    assert morph.sum() < med.sum(), "开运算一定比中值削得多"


def test_closing_fills_single_voxel_hole():
    mask = _block()
    mask[5, 3, 3] = False  # 主体内部一个针孔
    geom = _geom(mask)

    smoothed, stats = mask_edit.smooth_mask(
        mask, geom, method="morphology", radius_mm=0.0, closing_mm=1.4
    )

    assert smoothed[5, 3, 3], "闭运算必须补上单个体素孔洞"
    assert stats["voxels_after"] > stats["voxels_before"]


def test_smooth_requires_a_positive_radius():
    with pytest.raises(ValueError, match="半径必须大于 0"):
        mask_edit.smooth_mask(_block(), _geom(_block()), method="median", radius_mm=0)


def test_smooth_rejects_kernel_that_would_eat_small_structures():
    geom = _geom(_block())
    with pytest.raises(ValueError, match="超过上限"):
        mask_edit.smooth_mask(_block(), geom, method="morphology", radius_mm=50.0)


# ============ 端点 ============


def _create_pixel_segment(client, attending, idem, case, study_uid, name="源分割"):  # noqa: ANN001
    """建一个带真实像素的 draft 分割（走 create 端点的 seg_content 通路）。"""
    payload = {
        "study_instance_uid": study_uid,
        "segment_name": name,
        "seg_content": base64.b64encode(_mask_seg(_block())).decode("ascii"),
    }
    r = client.post(
        f"{API}/cases/{case['case_id']}/segments",
        json=payload,
        headers={**attending, **idem()},
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_crop_endpoint_derives_new_segment(client, attending, idem, case, study_uid):
    src = _create_pixel_segment(client, attending, idem, case, study_uid)

    # 以块中心为心、半径 1.0mm 的盒：行列罩住全部 3×3，z 方向只剩 3 层（块本身 4 层）
    center = [-1.05, -1.05, 1.0]
    r = client.post(
        f"{API}/segments/{src['seg_uid']}:crop",
        json={
            "center": {"geometry": "patient_lps_mm", "coordinates": center},
            "radius_mm": 1.0,
        },
        headers={**attending, **idem()},
    )
    assert r.status_code == 201, r.text
    out = r.json()

    assert out["seg_uid"] != src["seg_uid"], "编辑必须产出新对象，不能原地改源分割"
    assert out["state"] == "draft"
    assert out["has_pixels"] is True
    assert out["base_seg_uids"] == [{"uid": src["seg_uid"], "version": src["version"]}]
    assert out["volume"]["voxel_count"] < src["volume"]["voxel_count"]

    # 几何必须原样继承：行列层数、间距、原点都要与源分割一致
    # （走 /mask 端点读，等于顺带验证了前端叠加拿到的是同一套栅格）
    src_geo = client.get(f"{API}/segments/{src['seg_uid']}/mask", headers=attending).json()
    new_geo = client.get(f"{API}/segments/{out['seg_uid']}/mask", headers=attending).json()
    assert new_geo["shape"] == src_geo["shape"] == [8, 10, 10]
    assert new_geo["spacing"] == pytest.approx(src_geo["spacing"])
    assert new_geo["origin"] == pytest.approx(src_geo["origin"])
    assert new_geo["row_dir"] == pytest.approx(src_geo["row_dir"])
    assert new_geo["col_dir"] == pytest.approx(src_geo["col_dir"])


def test_crop_endpoint_refuses_empty_result(client, attending, idem, case, study_uid):
    src = _create_pixel_segment(client, attending, idem, case, study_uid)

    r = client.post(
        f"{API}/segments/{src['seg_uid']}:crop",
        json={
            "center": {"geometry": "patient_lps_mm", "coordinates": [500.0, 500.0, 500.0]},
            "radius_mm": 5.0,
        },
        headers={**attending, **idem()},
    )
    assert r.status_code == 422, r.text
    assert "empty_after_crop" in r.text


def test_crop_endpoint_rejects_pixel_less_segment(client, attending, idem, case, study_uid):
    """没有像素的分割不能编辑 —— 否则会派生出一份同样没有像素的空壳。"""
    shell = client.post(
        f"{API}/cases/{case['case_id']}/segments",
        json={"study_instance_uid": study_uid, "segment_name": "空壳"},
        headers={**attending, **idem()},
    ).json()

    r = client.post(
        f"{API}/segments/{shell['seg_uid']}:crop",
        json={"min_lps": [0.0, 0.0, 0.0], "max_lps": [1.0, 1.0, 1.0]},
        headers={**attending, **idem()},
    )
    assert r.status_code == 422, r.text
    assert "no_pixel_data" in r.text


def test_smooth_endpoint_derives_segment(client, attending, idem, case, study_uid):
    src = _create_pixel_segment(client, attending, idem, case, study_uid)

    r = client.post(
        f"{API}/segments/{src['seg_uid']}:smooth",
        json={"method": "median", "radius_mm": 1.0},
        headers={**attending, **idem()},
    )
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["has_pixels"] is True
    assert out["volume"]["voxel_count"] <= src["volume"]["voxel_count"]
    assert out["algorithm"] == "algo:smooth-v1"


def test_smooth_endpoint_refuses_to_erase_everything(client, attending, idem, case, study_uid):
    """单点分割 + 大半径开运算 = 空结果，必须 422 而不是写一条 0 体素的记录。"""
    dot = np.zeros((8, 10, 10), dtype=bool)
    dot[4, 5, 5] = True
    payload = {
        "study_instance_uid": study_uid,
        "segment_name": "单点",
        "seg_content": base64.b64encode(_mask_seg(dot)).decode("ascii"),
    }
    src = client.post(
        f"{API}/cases/{case['case_id']}/segments",
        json=payload,
        headers={**attending, **idem()},
    ).json()

    r = client.post(
        f"{API}/segments/{src['seg_uid']}:smooth",
        json={"method": "median", "radius_mm": 1.0},
        headers={**attending, **idem()},
    )
    assert r.status_code == 422, r.text
    assert "empty_after_smooth" in r.text


def test_edit_rejects_withdrawn_source(client, attending, idem, case, study_uid):
    src = _create_pixel_segment(client, attending, idem, case, study_uid)
    client.post(f"{API}/segments/{src['seg_uid']}:withdraw", headers={**attending, **idem()})

    r = client.post(
        f"{API}/segments/{src['seg_uid']}:smooth",
        json={"method": "median", "radius_mm": 1.0},
        headers={**attending, **idem()},
    )
    assert r.status_code in (409, 422), r.text
    assert "withdrawn" in r.text


def test_edit_requires_idempotency_key(client, attending, case, study_uid, idem):
    src = _create_pixel_segment(client, attending, idem, case, study_uid)
    r = client.post(
        f"{API}/segments/{src['seg_uid']}:smooth",
        json={"method": "median", "radius_mm": 1.0},
        headers=attending,  # 故意不带幂等键
    )
    assert r.status_code == 400, r.text
