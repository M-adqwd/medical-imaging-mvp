"""二值掩膜 ↔ DICOM SEG：写像素、写空间定位、读回几何、算解剖距离。

为什么单独一个模块：契约 §0.1 规定 SEG 是分割的**唯一事实源**。这句约束的
真实含义是——几何（像素间距 / 层间距 / 方位 / 原点）必须落在 SEG 自己身上，
不能让下游回头去查原始序列再"大致对上"。否则同一份分割在不同端上会被解释
成不同位置，而界面上不会出现任何异常：数字照常显示，只是量的是别的地方。

`build_seg_header` 的产出是元数据骨架（无 Rows/Columns/PixelData），只能
表示"这里有一份分割"；本模块的 `build_seg_from_mask` 才让 SEG 真的承载像素。
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pydicom
from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian

SEG_STORAGE_UID = "1.2.840.10008.5.1.4.1.1.66.4"  # Segmentation Storage
CT_IMAGE_STORAGE_UID = "1.2.840.10008.5.1.4.1.1.2"

# 私有块：记录"这份像素是谁产出的"。不给来源贴标签的 SEG，下游无法判断
# 它能不能用于临床判断 —— 算法产出与医师勾画在标准字段里长得很像。
PRIVATE_CREATOR = "MIIS SEG"
TAG_PRODUCER = 0x01
TAG_METHOD = 0x02
TAG_CLINICAL_USE = 0x03
TAG_NOTICE = 0x04

CLINICAL_NOTICE_ALGORITHMIC = "算法自动生成，未经医师勾画或核准，不得用于临床诊断"


def _code(value: str, scheme: str, meaning: str) -> Dataset:
    ds = Dataset()
    ds.CodeValue = value
    ds.CodingSchemeDesignator = scheme
    ds.CodeMeaning = meaning
    return ds


# ============================ 几何 ============================


def _cross(a: tuple[float, float, float], b: tuple[float, float, float]) -> tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _dot(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


@dataclass(frozen=True)
class MaskGeometry:
    """SEG 体素栅格 → 患者 LPS 毫米的仿射参数（坐标契约 §3：patient_lps_mm）。

    voxel(i, j, k) = origin + i·spacing[0]·row_dir + j·spacing[1]·col_dir + k·spacing[2]·normal

    i 是**行**索引、j 是**列**索引、k 是帧（层）索引。

    方向命名按 DICOM 现实而不是按直觉：
    `ImageOrientationPatient` 的**前**三个值是"列索引增大"的方向，后三个才是
    "行索引增大"的方向；`PixelSpacing[0]` 是行间距、`PixelSpacing[1]` 是列间距。
    这里把两组容易互换的东西绑死——行索引配 spacing[0] 配 row_dir。
    写反了在 0.7×0.7 的各向同性数据上完全看不出来，换成 0.7×0.9 就整体偏一个
    间距因子，而界面照常显示一个像样的数字。
    """

    rows: int
    cols: int
    frames: int
    spacing: tuple[float, float, float]  # (行间距, 列间距, 层间距)，mm
    origin: tuple[float, float, float]   # 体素 (0,0,0) 的 LPS
    row_dir: tuple[float, float, float]  # 行索引增大方向（IOP 后三个值）
    col_dir: tuple[float, float, float]  # 列索引增大方向（IOP 前三个值）

    @property
    def normal(self) -> tuple[float, float, float]:
        """帧索引增大方向 = 列方向 × 行方向。"""
        return _cross(self.col_dir, self.row_dir)

    def lps_to_voxel(self, point: tuple[float, float, float]) -> tuple[float, float, float]:
        """LPS 毫米 → 连续体素坐标。row_dir/col_dir/normal 正交，故用投影而不是求逆。"""
        d = (point[0] - self.origin[0], point[1] - self.origin[1], point[2] - self.origin[2])
        return (
            _dot(d, self.row_dir) / self.spacing[0],
            _dot(d, self.col_dir) / self.spacing[1],
            _dot(d, self.normal) / self.spacing[2],
        )

    def voxel_to_lps(self, i: float, j: float, k: float) -> tuple[float, float, float]:
        n = self.normal
        return (
            self.origin[0] + i * self.spacing[0] * self.row_dir[0]
            + j * self.spacing[1] * self.col_dir[0] + k * self.spacing[2] * n[0],
            self.origin[1] + i * self.spacing[0] * self.row_dir[1]
            + j * self.spacing[1] * self.col_dir[1] + k * self.spacing[2] * n[1],
            self.origin[2] + i * self.spacing[0] * self.row_dir[2]
            + j * self.spacing[1] * self.col_dir[2] + k * self.spacing[2] * n[2],
        )


# ============================ 写：掩膜 → SEG ============================


def build_seg_from_mask(
    *,
    seg_uid: str,
    study_instance_uid: str,
    series_instance_uid: str | None,
    patient_id: str,
    patient_name: str,
    segment_name: str,
    algorithm: str,
    mask: np.ndarray,
    spacing: tuple[float, float, float],
    origin: tuple[float, float, float],
    row_dir: tuple[float, float, float] = (0.0, 1.0, 0.0),
    col_dir: tuple[float, float, float] = (1.0, 0.0, 0.0),
    referenced_sop_uids: list[str] | None = None,
    clinical_use: bool = False,
    producer: str = "",
    method: str = "",
    study_date: str | None = None,
) -> bytes:
    """把 (帧, 行, 列) 的二值掩膜编码为带 PixelData 的 BINARY SEG。

    mask 的 dtype 不做假设：非零即属于该分割。
    referenced_sop_uids 按帧顺序给出源图像的 SOPInstanceUID；缺项时该帧不写
    DerivationImageSequence —— 宁可少写引用，也不编一个指向别处的引用。
    """
    arr = np.asarray(mask)
    if arr.ndim != 3:
        raise ValueError(f"mask 必须是 (帧, 行, 列) 三维数组，当前 {arr.ndim} 维")
    frames, rows, cols = arr.shape
    if frames < 1 or rows < 1 or cols < 1:
        raise ValueError(f"mask 维度非法：{arr.shape}")

    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = SEG_STORAGE_UID
    meta.MediaStorageSOPInstanceUID = seg_uid
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.ImplementationClassUID = "1.2.826.0.1.3680043.10.1337.1"

    ds = FileDataset(None, {}, file_meta=meta, preamble=b"\0" * 128)
    ds.is_little_endian = True
    ds.is_implicit_VR = False

    ds.SOPClassUID = SEG_STORAGE_UID
    ds.SOPInstanceUID = seg_uid
    ds.Modality = "SEG"
    ds.SeriesInstanceUID = series_instance_uid or f"{seg_uid}.1"
    ds.SeriesNumber = 9100
    ds.SeriesDescription = f"SEG {segment_name}"
    ds.InstanceNumber = 1
    ds.ImageType = ["DERIVED", "PRIMARY"]
    ds.StudyInstanceUID = study_instance_uid
    ds.StudyDate = study_date or datetime.now().strftime("%Y%m%d")
    ds.StudyTime = datetime.now().strftime("%H%M%S")
    ds.ContentDate = ds.StudyDate
    ds.ContentTime = ds.StudyTime
    ds.Manufacturer = "MIIS"
    # DICOM 默认字符集是 ISO-IR 100（西欧），中文标签会变成一串问号 ——
    # 分割名与患者姓名都是中文，必须显式声明 UTF-8。
    ds.SpecificCharacterSet = "ISO_IR 192"
    ds.PatientName = patient_name
    ds.PatientID = patient_id
    ds.PatientBirthDate = ""
    ds.PatientSex = ""

    ds.ContentLabel = "SEGMENTATION"
    ds.ContentDescription = "Algorithmic Segmentation" if not clinical_use else "Segmentation"
    ds.ContentCreatorName = algorithm

    # ---------- 分割元信息 ----------
    ds.SegmentationType = "BINARY"
    ds.SegmentsOverlap = "NO"
    ds.LossyImageCompression = "00"
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 1
    ds.BitsStored = 1
    ds.HighBit = 0
    ds.PixelRepresentation = 0
    ds.SegmentationFractionalType = "NONE"
    ds.Rows = rows
    ds.Columns = cols
    ds.NumberOfFrames = frames

    segment = Dataset()
    segment.SegmentNumber = 1
    segment.SegmentLabel = segment_name[:64]
    # 算法产出与医师勾画必须可区分：SEMIAUTOMATIC 是"算法算、人给种子"，
    # 写成 AUTOMATIC 会让下游以为这是端到端的自动结果。
    segment.SegmentAlgorithmType = (
        "MANUAL" if (not algorithm or algorithm == "merged") else "SEMIAUTOMATIC"
    )
    segment.SegmentAlgorithmName = (algorithm.split(":")[-1] if ":" in algorithm else algorithm)[:64]
    segment.SegmentedPropertyCategoryCodeSequence = [_code("T-D0050", "SRT", "Tissue")]
    segment.SegmentedPropertyTypeCodeSequence = [_code("T-D4000", "SRT", "Mass")]
    ds.SegmentSequence = [segment]

    # ---------- 共享功能组：像素尺寸 + 方位 ----------
    normal = _cross(tuple(col_dir), tuple(row_dir))
    orientation = Dataset()
    # IOP 前三个值 = 列索引增大方向，后三个值 = 行索引增大方向
    orientation.ImageOrientationPatient = [float(v) for v in (*col_dir, *row_dir)]

    measures = Dataset()
    measures.PixelSpacing = [float(spacing[0]), float(spacing[1])]
    measures.SliceThickness = float(spacing[2])
    measures.SpacingBetweenSlices = float(spacing[2])

    shared = Dataset()
    shared.PixelMeasuresSequence = [measures]
    shared.PlaneOrientationSequence = [orientation]
    ds.SharedFunctionalGroupsSequence = [shared]

    # ---------- 逐帧功能组：位置 + 派生来源 ----------
    per_frame = []
    refs = list(referenced_sop_uids or [])
    for k in range(frames):
        pos = (
            origin[0] + k * spacing[2] * normal[0],
            origin[1] + k * spacing[2] * normal[1],
            origin[2] + k * spacing[2] * normal[2],
        )

        frame_content = Dataset()
        frame_content.StackID = "1"
        frame_content.InStackPositionNumber = k + 1
        frame_content.DimensionIndexValues = [1, k + 1]

        plane_position = Dataset()
        plane_position.ImagePositionPatient = [float(v) for v in pos]

        seg_id = Dataset()
        seg_id.ReferencedSegmentNumber = 1

        item = Dataset()
        item.FrameContentSequence = [frame_content]
        item.PlanePositionSequence = [plane_position]
        item.SegmentIdentificationSequence = [seg_id]

        if k < len(refs) and refs[k]:
            derivation = Dataset()
            derivation.DerivationCodeSequence = [_code("113076", "DCM", "Segmentation")]
            source = Dataset()
            source.ReferencedSOPClassUID = CT_IMAGE_STORAGE_UID
            source.ReferencedSOPInstanceUID = refs[k]
            derivation.SourceImageSequence = [source]
            item.DerivationImageSequence = [derivation]

        per_frame.append(item)
    ds.PerFrameFunctionalGroupsSequence = per_frame

    # ---------- 维度组织（读 SEG 的软件靠它把帧还原成体数据） ----------
    dim_org_uid = f"{seg_uid}.2"
    org = Dataset()
    org.DimensionOrganizationUID = dim_org_uid
    ds.DimensionOrganizationSequence = [org]

    stack_pointer = Dataset()
    stack_pointer.DimensionOrganizationUID = dim_org_uid
    stack_pointer.DimensionIndexPointer = (0x0020, 0x9056)  # StackID
    stack_pointer.FunctionalGroupPointer = (0x0020, 0x9111)  # FrameContentSequence

    pos_pointer = Dataset()
    pos_pointer.DimensionOrganizationUID = dim_org_uid
    pos_pointer.DimensionIndexPointer = (0x0020, 0x9057)  # InStackPositionNumber
    pos_pointer.FunctionalGroupPointer = (0x0020, 0x9111)

    ds.DimensionIndexSequence = [stack_pointer, pos_pointer]

    # ---------- 像素：位打包，帧优先 ----------
    flat = (arr.reshape(-1) != 0).astype(np.uint8)
    packed = np.packbits(flat, bitorder="little").tobytes()
    if len(packed) % 2:
        packed += b"\x00"
    ds.PixelData = packed

    # ---------- 来源自曝 ----------
    block = ds.private_block(0x000B, PRIVATE_CREATOR, create=True)
    block.add_new(TAG_PRODUCER, "LO", producer or algorithm)
    block.add_new(TAG_METHOD, "LO", method or algorithm)
    block.add_new(TAG_CLINICAL_USE, "CS", "YES" if clinical_use else "NO")
    block.add_new(TAG_NOTICE, "LO", "" if clinical_use else CLINICAL_NOTICE_ALGORITHMIC)

    buf = io.BytesIO()
    pydicom.dcmwrite(buf, ds, enforce_file_format=True)
    return buf.getvalue()


# ============================ 读：几何 / 掩膜 ============================


def read_seg(seg_bytes: bytes | None) -> pydicom.dataset.Dataset | None:
    """解析 SEG。不做 stop_before_pixels —— 那样"PixelData" in ds 恒为 False。"""
    if not seg_bytes:
        return None
    try:
        return pydicom.dcmread(io.BytesIO(seg_bytes))
    except Exception:  # noqa: BLE001 - 解析失败按无像素处理，交由上层显式判定
        return None


def seg_geometry(seg_bytes: bytes | None) -> MaskGeometry | None:
    """读回 SEG 自带的空间定位。缺任何一项都返回 None —— 不猜、不填默认值。

    没有 geometry 的 SEG 照样能显示、能算体素数，但**无法把分割与患者坐标对上**，
    任何"到分割的距离"都会退化成对某个不确定位置的距离。
    """
    ds = read_seg(seg_bytes)
    if ds is None:
        return None
    rows = int(getattr(ds, "Rows", 0) or 0)
    cols = int(getattr(ds, "Columns", 0) or 0)
    frames = int(getattr(ds, "NumberOfFrames", 0) or 0)
    if not (rows and cols and frames):
        return None

    spacing: tuple[float, float, float] | None = None
    row_dir: tuple[float, float, float] | None = None
    col_dir: tuple[float, float, float] | None = None

    for shared in getattr(ds, "SharedFunctionalGroupsSequence", None) or []:
        for measures in getattr(shared, "PixelMeasuresSequence", None) or []:
            ps = getattr(measures, "PixelSpacing", None)
            if ps is not None and len(ps) >= 2:
                slice_mm = getattr(measures, "SpacingBetweenSlices", None) or getattr(
                    measures, "SliceThickness", None
                )
                try:
                    spacing = (float(ps[0]), float(ps[1]), float(slice_mm))
                except (TypeError, ValueError):
                    spacing = None
        for orient in getattr(shared, "PlaneOrientationSequence", None) or []:
            iop = getattr(orient, "ImageOrientationPatient", None)
            if iop is not None and len(iop) >= 6:
                # IOP 前三个值对应列索引方向，后三个值对应行索引方向
                col_dir = (float(iop[0]), float(iop[1]), float(iop[2]))
                row_dir = (float(iop[3]), float(iop[4]), float(iop[5]))

    if spacing is None or row_dir is None or col_dir is None:
        return None
    if min(spacing) <= 0:
        return None

    origin: tuple[float, float, float] | None = None
    for frame in getattr(ds, "PerFrameFunctionalGroupsSequence", None) or []:
        for plane in getattr(frame, "PlanePositionSequence", None) or []:
            ipp = getattr(plane, "ImagePositionPatient", None)
            if ipp is not None and len(ipp) == 3:
                origin = (float(ipp[0]), float(ipp[1]), float(ipp[2]))
                break
        if origin is not None:
            break

    if origin is None:
        return None
    return MaskGeometry(
        rows=rows, cols=cols, frames=frames, spacing=spacing, origin=origin,
        row_dir=row_dir, col_dir=col_dir,
    )


def seg_mask_array(seg_bytes: bytes | None) -> np.ndarray | None:
    """解出 (帧, 行, 列) 布尔掩膜。解不出来返回 None，不返回全零。"""
    ds = read_seg(seg_bytes)
    if ds is None or "PixelData" not in ds:
        return None
    try:
        arr = ds.pixel_array
    except Exception:  # noqa: BLE001
        return None
    arr = np.asarray(arr)
    if arr.ndim == 2:  # 单帧 SEG 可能被压成二维
        arr = arr[np.newaxis, ...]
    return arr != 0


# ============================ 布尔运算 ============================

MERGE_OPERATIONS = ("union", "intersection", "difference")


def _same_grid(a: MaskGeometry, b: MaskGeometry) -> bool:
    """两个 SEG 是否落在同一体素栅格上。

    布尔运算是逐体素的与或非。shape 不同直接无意义；原点/间距/方位差一点点，
    结果就会整体错位——而这种错位在界面上完全看不出来（掩膜照样是块状结构），
    所以这里用严格容差，不做"差不多就对齐"的猜测。
    """
    if (a.rows, a.cols, a.frames) != (b.rows, b.cols, b.frames):
        return False
    for x, y in zip(a.spacing, b.spacing):
        if abs(x - y) > 1e-4:
            return False
    for x, y in zip(a.origin, b.origin):
        if abs(x - y) > 1e-3:
            return False
    for x, y in zip((*a.row_dir, *a.col_dir), (*b.row_dir, *b.col_dir)):
        if abs(x - y) > 1e-6:
            return False
    return True


def merge_masks(
    seg_bytes_list: list[bytes | None], operation: str
) -> tuple[np.ndarray, MaskGeometry]:
    """对多个 SEG 的掩膜做逐体素布尔运算，返回 (掩膜, 栅格几何)。

    只支持同栅格。跨栅格需要重采样（不同层厚/不同重建），那是另一件事——
    静默重采样出一个"看起来对"的合并结果，比明确报错危险得多。
    """
    if operation not in MERGE_OPERATIONS:
        raise ValueError(f"不支持的布尔运算 {operation}，可选 {', '.join(MERGE_OPERATIONS)}")
    if len(seg_bytes_list) < 2:
        raise ValueError("布尔运算至少需要两个 SEG")

    masks: list[np.ndarray] = []
    geoms: list[MaskGeometry] = []
    for idx, raw in enumerate(seg_bytes_list):
        mask = seg_mask_array(raw)
        geom = seg_geometry(raw)
        if mask is None:
            raise ValueError(f"第 {idx + 1} 个 SEG 没有像素数据，布尔运算无从下手")
        if geom is None:
            raise ValueError(f"第 {idx + 1} 个 SEG 缺少空间定位信息，无法确认栅格是否一致")
        if mask.shape != (geom.frames, geom.rows, geom.cols):
            raise ValueError(f"第 {idx + 1} 个 SEG 的像素形状与几何声明不一致")
        masks.append(mask)
        geoms.append(geom)

    base = geoms[0]
    for idx, other in enumerate(geoms[1:], start=2):
        if not _same_grid(base, other):
            raise ValueError(
                f"第 {idx} 个 SEG 与第 1 个不在同一体素栅格，跨栅格布尔运算需要重采样（当前不支持）"
            )

    result = masks[0].copy()
    for mask in masks[1:]:
        if operation == "union":
            result |= mask
        elif operation == "intersection":
            result &= mask
        else:  # difference：以第一个为被减数
            result &= ~mask
    return result, base


# ============================ 解剖距离 ============================


def _boundary_surface_points(mask: np.ndarray, geom: MaskGeometry, limit: int = 400_000):
    """掩膜的外表面体素中心（LPS 毫米）。

    面 = 掩膜减去 6 邻域腐蚀，即"与外部相邻的那一层"。
    只取表面而不是全部体素：距离是到**结构边界**的距离，取内部体素会把
    贴边的靶点算成 0；而全部体素的点数在 512³ 上会拖垮请求。
    """
    padded = np.pad(mask, 1, mode="constant", constant_values=False)
    core = padded[1:-1, 1:-1, 1:-1]
    eroded = core.copy()
    for axis in range(3):
        for shift in (-1, 1):
            eroded &= np.roll(padded, shift, axis=axis)[1:-1, 1:-1, 1:-1]
    surface = core & ~eroded

    idx = np.argwhere(surface)
    if idx.size == 0:
        return np.zeros((0, 3), dtype=np.float64)
    if len(idx) > limit:
        idx = idx[:: math.ceil(len(idx) / limit)]

    # mask 形状是 (帧, 行, 列)：idx[:,0]=帧 k，idx[:,1]=行 i，idx[:,2]=列 j
    frame = idx[:, 0].astype(np.float64)
    row = idx[:, 1].astype(np.float64)
    col = idx[:, 2].astype(np.float64)
    n = geom.normal
    pts = np.empty((len(idx), 3), dtype=np.float64)
    for axis in range(3):
        pts[:, axis] = (
            geom.origin[axis]
            + row * geom.spacing[0] * geom.row_dir[axis]
            + col * geom.spacing[1] * geom.col_dir[axis]
            + frame * geom.spacing[2] * n[axis]
        )
    return pts


def _point_segment_distance(points: np.ndarray, a, b) -> np.ndarray:
    """点到线段的距离（向量化）。"""
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    ab = b - a
    denom = float(ab @ ab)
    if denom == 0.0:
        return np.linalg.norm(points - a, axis=1)
    t = ((points - a) @ ab) / denom
    t = np.clip(t, 0.0, 1.0)
    closest = a + t[:, None] * ab
    return np.linalg.norm(points - closest, axis=1)


def min_distance_to_segment(
    seg_bytes: bytes | None,
    a_lps: tuple[float, float, float],
    b_lps: tuple[float, float, float],
) -> tuple[float | None, str]:
    """入路轴线到分割外表面的最小距离（mm），返回 (距离, 依据)。

    这就是 `safe_distance` 想表达、但一直没有算的那个量。返回 None 表示算不了，
    调用方必须显式处理，不能把它当成 0 或某个默认等级。
    """
    geom = seg_geometry(seg_bytes)
    if geom is None:
        return None, "分割缺少空间定位信息（PixelMeasures / PlanePosition），无法定位到患者坐标"

    mask = seg_mask_array(seg_bytes)
    if mask is None or not mask.any():
        return None, "分割为空（无像素或全为背景），没有可测量的解剖边界"

    if mask.shape != (geom.frames, geom.rows, geom.cols):
        return None, (
            f"分割像素维度 {mask.shape} 与几何声明 "
            f"{(geom.frames, geom.rows, geom.cols)} 不一致"
        )

    if _axis_hits_mask(mask, geom, a_lps, b_lps):
        return 0.0, "入路轴线穿过了分割区域（最小距离为 0）"

    surface = _boundary_surface_points(mask, geom)
    if surface.size == 0:
        return None, "分割没有外表面体素，无法计算距离"
    distances = _point_segment_distance(surface, a_lps, b_lps)
    return float(distances.min()), "按入路轴线到分割外表面的最小距离分级"


def _axis_hits_mask(mask, geom: MaskGeometry, a, b) -> bool:
    """入路轴线是否穿入掩膜内部。

    穿进结构的路径谈"距结构 3mm"没有意义，必须判成距离 0。
    """
    av = geom.lps_to_voxel(a)
    bv = geom.lps_to_voxel(b)
    length = math.dist(av, bv)
    steps = max(2, int(math.ceil(length * 2)) + 1)  # 每半个体素至少采样一次
    frames, rows, cols = mask.shape
    for t in np.linspace(0.0, 1.0, steps):
        i = av[0] + (bv[0] - av[0]) * t
        j = av[1] + (bv[1] - av[1]) * t
        k = av[2] + (bv[2] - av[2]) * t
        ii, jj, kk = int(round(i)), int(round(j)), int(round(k))
        if 0 <= ii < rows and 0 <= jj < cols and 0 <= kk < frames and mask[kk, ii, jj]:
            return True
    return False


def point_inside_mask(seg_bytes: bytes | None, point: tuple[float, float, float]) -> bool | None:
    """点在分割内？返回 None 表示判断不了（无像素 / 无几何）。

    这个判断存在的理由：当靶点本身就在分割结构里，"到分割表面的最小距离"
    恒为 0，据此判成"高风险"是把靶区当成了需要回避的结构 —— 方向反了。
    必须先区分"路径穿过靶区（预期）"和"路径穿过回避结构（危险）"。
    """
    geom = seg_geometry(seg_bytes)
    mask = seg_mask_array(seg_bytes)
    if geom is None or mask is None:
        return None
    row, col, frame = geom.lps_to_voxel(point)
    i, j, k = int(round(row)), int(round(col)), int(round(frame))
    frames, rows, cols = mask.shape
    if not (0 <= i < rows and 0 <= j < cols and 0 <= k < frames):
        return False
    return bool(mask[k, i, j])
