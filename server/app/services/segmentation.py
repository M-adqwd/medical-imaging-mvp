"""本地分割来源：种子点区域生长。

为什么需要它：SEG 是分割的唯一事实源（契约 §1.4），但在 TotalSegmentator 不
首期接入的前提下，系统里**没有任何路径能产出带像素的 SEG**——`build_seg_header`
只写元数据骨架。结果是体积恒为 `unavailable`、解剖距离无从计算（Q-SEG-PIXELS）。

本模块给出一个确定性的、CPU 可跑的像素来源：医师在影像上点一个种子点，按
HU 相似度做三维区域生长。它**不是 AI**，也不冒充 AI：

- 种子是人给的，所以 `SegmentAlgorithmType = SEMIAUTOMATIC`
- 产出带 `clinical_use=NO` 与固定 notice，界面不得把它渲染成临床结论
- 算法参数（HU 容差、迭代次数、体素数）全部回传给调用方，可复现

诚实性是这里的核心约束：一个"看起来像分割"的绿色区域，比一个报错更难纠正。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.dicom.mask import MaskGeometry
from app.dicom.store import archive

# 区域生长默认参数。所有值都是工程初始值，不是临床验证值。
DEFAULT_HU_TOLERANCE = 30.0
DEFAULT_RADIUS_MM = 60.0
MAX_RADIUS_VOXELS = 160
MAX_ITERATIONS = 400


class SeriesNotFound(Exception):
    """按 study / series 找不到可用的体数据。"""


@dataclass
class Volume:
    """一层可运算的体数据 + 它的空间定位。"""

    hu: np.ndarray                    # (帧, 行, 列) 的 HU 值
    geometry: MaskGeometry
    sop_uids: list[str] = field(default_factory=list)
    series_instance_uid: str = ""
    modality: str = ""


@dataclass
class GrowResult:
    mask: np.ndarray
    seed_hu: float
    hu_low: float
    hu_high: float
    iterations: int
    bbox_voxels: int
    stats: dict


def _slice_spacing(datasets) -> float | None:
    """层间距：优先头里声明的值，否则用相邻层 ImagePositionPatient 的间距反推。

    只有 SliceThickness 的序列可能是重叠/带间隙采集，反推比声明值更接近真实。
    """
    first = datasets[0]
    for attr in ("SpacingBetweenSlices", "SliceThickness"):
        raw = getattr(first, attr, None)
        if raw is None:
            continue
        try:
            value = abs(float(raw))
        except (TypeError, ValueError):
            continue
        if value > 0:
            declared = value
            break
    else:
        declared = None

    if len(datasets) >= 2:
        try:
            z0 = np.array([float(v) for v in datasets[0].ImagePositionPatient])
            z1 = np.array([float(v) for v in datasets[1].ImagePositionPatient])
            measured = float(np.linalg.norm(z1 - z0))
            if measured > 1e-6:
                return measured
        except Exception:  # noqa: BLE001 - 头里没有 IPP 时回落到声明值
            pass
    return declared


def _order_along_normal(datasets):
    """按 IOP 法向的投影升序排列层。

    不能只按 InstanceNumber 排：实例号与层位置**并不总是同向**。真实数据里
    就有实例号随 z 递减采集的序列（公开 CTA 数据集即如此）。一旦数组顺序与
    `MaskGeometry.normal` 相反，`lps_to_voxel` 会把体数据内部的点算成负帧号：
    报错是「种子点落在体数据之外」，但行列号完全正确，只有帧号整体取反——
    这种错在界面上看起来像"点歪了"，极难归因。

    排序后 datasets[0] 的 ImagePositionPatient 就是体素原点，帧索引沿 normal
    递增，与 lps_to_voxel / voxel_to_lps 的假设一致。
    """
    if len(datasets) < 2:
        return list(datasets)
    iop = getattr(datasets[0], "ImageOrientationPatient", None)
    if not iop or len(iop) != 6:
        # 没有方向余弦就没法定义"升序"，保持调用方给的顺序而不是猜
        return list(datasets)
    col_dir = np.array([float(v) for v in iop[:3]])
    row_dir = np.array([float(v) for v in iop[3:6]])
    normal = np.cross(col_dir, row_dir)

    def projection(ds) -> float:
        ipp = getattr(ds, "ImagePositionPatient", None)
        if ipp is None:
            return 0.0
        return float(np.dot(np.array([float(v) for v in ipp]), normal))

    return sorted(datasets, key=projection)


def load_volume(study_instance_uid: str, series_instance_uid: str | None = None) -> Volume:
    """从归档里装配一个体数据。找不到完整几何时抛 SeriesNotFound，不做兜底猜测。"""
    study = archive.get_study(study_instance_uid)
    if study is None:
        raise SeriesNotFound(f"归档中没有 study {study_instance_uid}")

    candidates = list(study.series.values())
    if series_instance_uid:
        candidates = [s for s in candidates if s.series_instance_uid == series_instance_uid]
    # 只接受横断面可堆叠的断层序列：SEG / SR / 报告没有像素，混进来会静默错位
    candidates = [s for s in candidates if s.modality in ("CT", "MR") and s.instances]
    if not candidates:
        raise SeriesNotFound("该 study 下没有可用的断层序列（CT / MR）")

    series = max(candidates, key=lambda s: len(s.instances))
    records = sorted(series.instances, key=lambda r: (r.instance_number, r.meta.get("ImagePositionPatient", [])))

    datasets = []
    for record in records:
        ds = archive.read_instance_dataset(record)
        if "PixelData" not in ds:
            continue
        datasets.append(ds)
    if not datasets:
        raise SeriesNotFound("该序列没有任何实例携带像素数据")

    # 顺序必须与 IOP 法向同向，否则帧索引与物理方向相反（见 _order_along_normal）
    datasets = _order_along_normal(datasets)

    slice_mm = _slice_spacing(datasets)
    pixel_spacing = getattr(datasets[0], "PixelSpacing", None)
    ipp = getattr(datasets[0], "ImagePositionPatient", None)
    iop = getattr(datasets[0], "ImageOrientationPatient", None)
    if not pixel_spacing or not ipp or not iop or not slice_mm:
        raise SeriesNotFound("序列缺少 PixelSpacing / ImagePositionPatient / ImageOrientationPatient")

    frames = []
    for ds in datasets:
        arr = ds.pixel_array.astype(np.float32)
        slope = float(getattr(ds, "RescaleSlope", 1) or 1)
        intercept = float(getattr(ds, "RescaleIntercept", 0) or 0)
        frames.append(arr * slope + intercept)

    hu = np.stack(frames, axis=0)
    geometry = MaskGeometry(
        rows=int(hu.shape[1]),
        cols=int(hu.shape[2]),
        frames=int(hu.shape[0]),
        spacing=(float(pixel_spacing[0]), float(pixel_spacing[1]), float(slice_mm)),
        origin=(float(ipp[0]), float(ipp[1]), float(ipp[2])),
        # IOP 前三个值 = 列索引方向，后三个值 = 行索引方向（DICOM C.7.6.2.1.1）
        col_dir=(float(iop[0]), float(iop[1]), float(iop[2])),
        row_dir=(float(iop[3]), float(iop[4]), float(iop[5])),
    )
    return Volume(
        hu=hu,
        geometry=geometry,
        sop_uids=[str(ds.SOPInstanceUID) for ds in datasets],
        series_instance_uid=series.series_instance_uid,
        modality=series.modality,
    )


def _dilate6(region: np.ndarray) -> np.ndarray:
    """6 邻域膨胀。用切片而不是 np.roll：roll 会环绕，把对侧边界连成一片。"""
    out = region.copy()
    out[1:, :, :] |= region[:-1, :, :]
    out[:-1, :, :] |= region[1:, :, :]
    out[:, 1:, :] |= region[:, :-1, :]
    out[:, :-1, :] |= region[:, 1:, :]
    out[:, :, 1:] |= region[:, :, :-1]
    out[:, :, :-1] |= region[:, :, 1:]
    return out


def grow_from_seed(
    volume: Volume,
    seed_lps: tuple[float, float, float],
    *,
    hu_tolerance: float = DEFAULT_HU_TOLERANCE,
    radius_mm: float = DEFAULT_RADIUS_MM,
) -> GrowResult:
    """以种子点为中心、HU 容差内做三维区域生长。

    只在种子周围的窗口内生长：整卷迭代在 512×512×128 上要几十秒，
    而分割目标通常只占几个厘米。窗口外的体素一律视为背景 —— 这一点
    会在 `stats.grow_radius_mm` 里说明，避免把"窗口边界"误读成"结构边界"。
    """
    geom = volume.geometry
    seed_voxel = geom.lps_to_voxel(seed_lps)
    i, j, k = (int(round(v)) for v in seed_voxel)
    frames, rows, cols = volume.hu.shape
    if not (0 <= i < rows and 0 <= j < cols and 0 <= k < frames):
        raise ValueError(
            f"种子点 {seed_lps} 落在体数据之外（体素 {seed_voxel}，"
            f"范围 行<{rows} 列<{cols} 帧<{frames}）"
        )

    seed_hu = float(volume.hu[k, i, j])
    hu_low = seed_hu - float(hu_tolerance)
    hu_high = seed_hu + float(hu_tolerance)

    # 半径换算成体素时取三轴最小值：宁可窗口偏紧，也不要按毫米假装各向同性
    spacing_min = min(geom.spacing)
    radius_vox = int(min(MAX_RADIUS_VOXELS, max(4, round(radius_mm / spacing_min))))
    r = (min(radius_vox, rows), min(radius_vox, cols), min(radius_vox, frames))
    i0, i1 = max(0, i - r[0]), min(rows, i + r[0] + 1)
    j0, j1 = max(0, j - r[1]), min(cols, j + r[1] + 1)
    k0, k1 = max(0, k - r[2]), min(frames, k + r[2] + 1)

    window = volume.hu[k0:k1, i0:i1, j0:j1]
    band = (window >= hu_low) & (window <= hu_high)

    region = np.zeros_like(band)
    region[k - k0, i - i0, j - j0] = True
    iterations = 0
    while iterations < MAX_ITERATIONS:
        grown = _dilate6(region) & band
        iterations += 1
        if np.array_equal(grown, region):
            break
        region = grown

    mask = np.zeros_like(volume.hu, dtype=bool)
    mask[k0:k1, i0:i1, j0:j1] = region
    voxel_count = int(region.sum())
    stats = {
        "seed_hu": round(seed_hu, 1),
        "hu_window": [round(hu_low, 1), round(hu_high, 1)],
        "iterations": iterations,
        "voxel_count": voxel_count,
        "grow_radius_mm": round(radius_mm, 1),
        "window_shape": [int(v) for v in band.shape],
        "touches_window_edge": bool(_touches_edge(region)),
        "method": "seed-region-growing-v1",
    }
    return GrowResult(
        mask=mask,
        seed_hu=seed_hu,
        hu_low=hu_low,
        hu_high=hu_high,
        iterations=iterations,
        bbox_voxels=int(band.size),
        stats=stats,
    )


def _touches_edge(region: np.ndarray) -> bool:
    """区域是否顶到了生长窗口边界。

    顶到边界意味着分割可能被窗口截断——体积会偏小，而界面看不出来。
    这个标志必须暴露给调用方，让"分割不完整"成为可观察状态。
    """
    if region.size == 0:
        return False
    return bool(
        region[0].any() or region[-1].any()
        or region[:, 0].any() or region[:, -1].any()
        or region[:, :, 0].any() or region[:, :, -1].any()
    )
