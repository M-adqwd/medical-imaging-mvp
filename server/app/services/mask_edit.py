"""SEG 掩膜编辑：按 LPS 盒裁切、形态学平滑（开 / 闭运算）。

为什么几何一律不变：编辑只改"哪些体素属于该分割"，不重建栅格。
Rows / Columns / Frames / origin / spacing / 方向全部沿用源 SEG —— 一旦
重采样，编辑后的分割与原序列、与已建立的规划引用就不再是同一个坐标系，
而界面上看不出来：体积数字照常、叠加位置大致对得上，量的却是别处。

这条约束的代价是"裁切只能做零化"（把盒外的体素置 0，不缩小数组），
换来的是 `MaskGeometry` 完全不动的可验证性。真要缩小数组，那是重采样，
属于另一件事，需要显式的几何对齐步骤，不能藏在"编辑"按钮后面。

两个操作都返回 (mask, stats)：stats 必须能回放到界面上，否则用户无法判断
"平滑得很厉害"到底是几个毫米的核。
"""

from __future__ import annotations

import numpy as np

from app.dicom.mask import MaskGeometry

# 单轴核半径上限（体素）。开运算的核超过这个量级，小器官 / 细血管会被
# 整个吃掉，而结果是一份"看起来正常"的空壳 —— 宁可拒绝，也不要静默清空。
MAX_KERNEL_VOXELS = 12


def _require_mask(mask: np.ndarray | None, geom: MaskGeometry | None) -> None:
    if mask is None or geom is None:
        raise ValueError("该分割没有像素数据或缺少空间定位，无法编辑")
    if mask.shape != (geom.frames, geom.rows, geom.cols):
        raise ValueError(
            f"分割像素维度 {mask.shape} 与几何声明 "
            f"{(geom.frames, geom.rows, geom.cols)} 不一致，拒绝在错位的栅格上编辑"
        )


def _as_lps(point) -> tuple[float, float, float]:
    values = [float(v) for v in point]
    if len(values) != 3:
        raise ValueError("坐标必须是 [x, y, z] 三个分量")
    if not all(np.isfinite(values)):
        raise ValueError("坐标包含 NaN / Inf")
    return (values[0], values[1], values[2])


def box_from_center(center, radius_mm: float) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """以点为中心、边长 2×radius 的立方体（LPS 毫米）。"""
    if not (radius_mm > 0):
        raise ValueError("radius_mm 必须大于 0")
    cx, cy, cz = _as_lps(center)
    r = float(radius_mm)
    return (cx - r, cy - r, cz - r), (cx + r, cy + r, cz + r)


def crop_slices(
    geom: MaskGeometry,
    min_lps,
    max_lps,
) -> tuple[slice, slice, slice]:
    """LPS 盒 → 体素索引切片（帧, 行, 列）。

    盒与体数据正交轴对齐，所以只要把 8 个角投影到 row / col / normal 上取极值，
    不需要求逆矩阵；这也让"盒完全落在体数据外"能自然退化成一个空切片，
    由调用方判空，而不是悄悄返回全图。
    """
    lo = _as_lps(min_lps)
    hi = _as_lps(max_lps)
    if any(a >= b for a, b in zip(lo, hi)):
        raise ValueError("裁切盒的 min 必须严格小于 max（三轴都要满足）")

    corners = [
        (x, y, z)
        for x in (lo[0], hi[0])
        for y in (lo[1], hi[1])
        for z in (lo[2], hi[2])
    ]
    # lps_to_voxel 返回 (行, 列, 帧)
    idx = np.array([geom.lps_to_voxel(c) for c in corners], dtype=np.float64)
    low = np.floor(idx.min(axis=0)).astype(int)
    high = np.ceil(idx.max(axis=0)).astype(int) + 1

    rows, cols, frames = geom.rows, geom.cols, geom.frames
    row0, row1 = max(0, low[0]), min(rows, high[0])
    col0, col1 = max(0, low[1]), min(cols, high[1])
    fr0, fr1 = max(0, low[2]), min(frames, high[2])
    # 空区间统一表示为 (0, 0)，避免 start > stop 时 numpy 的负步长语义
    if row1 <= row0:
        row0 = row1 = 0
    if col1 <= col0:
        col0 = col1 = 0
    if fr1 <= fr0:
        fr0 = fr1 = 0
    return slice(fr0, fr1), slice(row0, row1), slice(col0, col1)


def crop_to_box(
    mask: np.ndarray,
    geom: MaskGeometry,
    min_lps,
    max_lps,
) -> tuple[np.ndarray, dict]:
    """保留盒内体素，盒外一律置 0（不改变数组形状与几何）。"""
    _require_mask(mask, geom)
    fs, rs, cs = crop_slices(geom, min_lps, max_lps)

    out = np.zeros_like(mask, dtype=bool)
    out[fs, rs, cs] = mask[fs, rs, cs]

    before = int(mask.sum())
    after = int(out.sum())
    stats = {
        "operation": "crop",
        "box_lps_mm": {
            "min": [round(float(v), 2) for v in min_lps],
            "max": [round(float(v), 2) for v in max_lps],
        },
        "voxels_before": before,
        "voxels_after": after,
        "removed_voxels": before - after,
        # 盒顶到体数据边界时，裁切结果可能只是"被视野截断"，不是用户想要的范围。
        # 必须转成 Python bool：numpy 标量不能直接进 JSON 列（seg_meta）
        "touches_volume_edge": bool(
            int(fs.start) == 0
            or int(rs.start) == 0
            or int(cs.start) == 0
            or int(fs.stop) == geom.frames
            or int(rs.stop) == geom.rows
            or int(cs.stop) == geom.cols
        ),
    }
    return out, stats


def _kernel_shape(radius_mm: float, geom: MaskGeometry) -> tuple[int, int, int]:
    """按毫米换算各轴核半径（帧, 行, 列），各向异性数据按各自间距折算。"""
    spacing_row, spacing_col, spacing_frame = geom.spacing
    r_mm = float(radius_mm or 0)
    if r_mm <= 0:
        raise ValueError("平滑半径必须大于 0")
    radii = (
        int(round(r_mm / spacing_frame)) if spacing_frame > 0 else 0,
        int(round(r_mm / spacing_row)) if spacing_row > 0 else 0,
        int(round(r_mm / spacing_col)) if spacing_col > 0 else 0,
    )
    if max(radii) > MAX_KERNEL_VOXELS:
        raise ValueError(
            f"平滑半径 {r_mm} mm 换算后达到 {max(radii)} 体素，超过上限 "
            f"{MAX_KERNEL_VOXELS}：继续放开会把小结构整个抹掉"
        )
    # 至少 1 体素：小于一个体素的半径在离散栅格上等价于不操作，
    # 而用户点了"平滑"却什么都没变，比报错更难判断
    return tuple(max(1, r) for r in radii)


def _ellipsoid(radii: tuple[int, int, int]) -> np.ndarray:
    rf, rr, rc = radii
    f, r, c = np.ogrid[-rf : rf + 1, -rr : rr + 1, -rc : rc + 1]
    return ((f / rf) ** 2 + (r / rr) ** 2 + (c / rc) ** 2) <= 1.0


def smooth_mask(
    mask: np.ndarray,
    geom: MaskGeometry,
    *,
    method: str = "median",
    radius_mm: float = 1.0,
    closing_mm: float = 0.0,
) -> tuple[np.ndarray, dict]:
    """平滑掩膜。两种方法，默认中值。

    为什么默认不是"开运算"（形态学平滑的教科书写法）：

    开运算要求结构元在整个邻域内都命中，等价于"至少 N 体素厚的地方才留下"。
    在真实增强 CTA 上实测：主动脉腔这份 4300 体素的分割，1mm 开运算后只剩
    527 体素（保留 12%）—— 管壁附近的薄连接与不规则边界被整片削掉，而界面
    上只会显示一个变小的体积数字，医生很难判断这是"平滑"还是"削坏了"。

    中值滤波（多数表决）去掉同样的毛刺，但保留薄片：同一份数据保留 87%。
    因此默认走中值；形态学开/闭仍保留，供"确实要去掉细小突起"的场景显式选择，
    并且把保留率写进 stats，让"平滑削掉了多少"成为可核对的事实而不是感觉。
    """
    _require_mask(mask, geom)
    from scipy import ndimage  # 局部导入：纯 numpy 路径（裁切）不依赖 scipy

    if method not in ("median", "morphology"):
        raise ValueError(f"不支持的平滑方法 {method}，可选 median / morphology")

    # 中值只需一个半径；形态学取开/闭中较大者定义核
    kernel_mm = max(float(radius_mm or 0), float(closing_mm or 0)) if method == "morphology" else float(radius_mm or 0)
    radii = _kernel_shape(kernel_mm, geom)
    structure = _ellipsoid(radii)

    out = np.asarray(mask, dtype=bool).copy()
    before = int(out.sum())
    if before == 0:
        raise ValueError("源分割为空（0 体素），没有可平滑的内容")

    if method == "median":
        # median_filter 对 bool 不支持，先转 uint8；footprint 是椭球，
        # 各轴半径按体素间距换算，各向异性数据不会在层方向过平滑。
        # mode="constant" 是关键：scipy 默认 'reflect' 会把边界体素镜像复制，
        # 贴在体数据边缘的孤立噪点因为"自己反射自己"而存活下来（实测如此）。
        # 体数据之外没有结构，必须是 0。
        filtered = ndimage.median_filter(
            out.astype(np.uint8), footprint=structure, mode="constant", cval=0
        )
        out = filtered > 0
    else:
        if radius_mm and radius_mm > 0:
            out = ndimage.binary_opening(out, structure=structure)
        if closing_mm and closing_mm > 0:
            out = ndimage.binary_closing(out, structure=structure)

    after = int(out.sum())
    stats = {
        "operation": "smooth",
        "method": method,
        "radius_mm": round(float(radius_mm or 0), 2),
        "closing_mm": round(float(closing_mm or 0), 2),
        "kernel_voxels": [int(v) for v in radii],
        "voxels_before": before,
        "voxels_after": after,
        "delta_voxels": after - before,
        # 保留率是判断"平滑是否削过头"的唯一客观指标
        "retention": round(after / before, 4),
        # 形态学开运算把主体吃光是真实风险：此时体积骤降，必须让调用方显式拒绝
        "erased": after == 0,
    }
    return out, stats
