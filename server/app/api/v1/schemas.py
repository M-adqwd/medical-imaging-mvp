"""请求 / 响应模型。字段命名与契约文档 §1 / §3 保持一致。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ---------- Case ----------

class CaseCreate(BaseModel):
    patient_ref: str = Field(..., description="院内患者身份引用，本系统不存 PII 原文")
    studies: list[str] = Field(..., min_length=1)
    source: str = "pacs"
    modality: str | None = None
    title: str | None = Field(default=None, max_length=80)


class StudyRefOut(BaseModel):
    study_instance_uid: str
    series_count: int = 0
    date: str | None = None
    description: str | None = None
    modalities: list[str] = []


class CaseOut(BaseModel):
    case_id: str
    patient_ref: str
    patient_name: str | None = None
    title: str | None = None
    status: str
    source: str
    modality: str | None = None
    study_date: str | None = None
    owner_id: str
    participants: list[str] = []
    studies: list[StudyRefOut] = []
    created_at: datetime
    updated_at: datetime


class CaseListOut(BaseModel):
    total: int
    next_page: int | None = None
    items: list[CaseOut]


class CaseDetailOut(CaseOut):
    counts: dict[str, int] = {}
    recent_activity: list[dict] = []


# ---------- 坐标系契约（docs/COORDINATE-CONTRACT.md）----------
#
# 跨前后端只允许一种坐标系：参考坐标系 / LPS 毫米（契约 §1 的 C3）。
# 每组坐标必须自带 geometry 声明，否则体素索引或 RAS 被塞进来时系统不会报错，
# 只会产出一个空间位置错误、但长得完全正常的 SR。

GEOMETRY_PATIENT_LPS_MM = "patient_lps_mm"

# 历史写法 → 规范值。写入即归一（契约 §3.3），库里永远只留规范值。
# 不做长期别名映射表 —— 那只会持续腐化；这两个是一次性收敛用的。
LEGACY_GEOMETRY_ALIASES: dict[str, str] = {
    "frame_of_reference": GEOMETRY_PATIENT_LPS_MM,
    "volume_world": GEOMETRY_PATIENT_LPS_MM,
}

# PS3.3 C.18.9.1.2 GraphicType 的合法取值
GRAPHIC_TYPES: tuple[str, ...] = (
    "POINT",
    "MULTIPOINT",
    "POLYLINE",
    "POLYGON",
    "ELLIPSE",
    "ELLIPSOID",
)


def _check_geometry(value: str) -> str:
    """规范化 geometry。历史别名改写为规范值，其他值一律拒绝。

    不做「猜一个默认值」的兜底（契约 §9 X1）。错误体必须带上规范值，
    否则调用方不知道该填什么（断言 A2）。
    """
    normalized = value.strip()
    if normalized == GEOMETRY_PATIENT_LPS_MM:
        return GEOMETRY_PATIENT_LPS_MM
    if normalized in LEGACY_GEOMETRY_ALIASES:
        return LEGACY_GEOMETRY_ALIASES[normalized]
    raise ValueError(
        f"geometry 必须是 {GEOMETRY_PATIENT_LPS_MM!r}（参考坐标系 / LPS 毫米），"
        f"当前为 {value!r}；历史写法 {sorted(LEGACY_GEOMETRY_ALIASES)} 会被自动归一"
    )


class CoordRef(BaseModel):
    """测量坐标引用（契约 §4）。

    extra=forbid 是刻意的：
    - `series_instance_uid` 只允许出现在 MeasurementCreate 顶层，coord_ref 里
      再带一份就是第二份事实源（断言 A7）。
    - spacing / frame_of_reference_uid 属可派生数据，服务端只认影像头（契约 §6）。
      接收它们等于承认第二份事实源，故一律拒绝而不是"忽略后照用"（§9 X5）。
    """

    model_config = ConfigDict(extra="forbid")

    geometry: str
    graphic_type: str
    graphic_data: list[float]
    study_instance_uid: str
    sop_instance_uid: str

    @field_validator("geometry")
    @classmethod
    def _valid_geometry(cls, value: str) -> str:
        return _check_geometry(value)

    @field_validator("graphic_type")
    @classmethod
    def _check_graphic_type(cls, value: str) -> str:
        if value not in GRAPHIC_TYPES:
            raise ValueError(f"graphic_type 必须是 {list(GRAPHIC_TYPES)} 之一，当前为 {value!r}")
        return value

    @model_validator(mode="after")
    def _check_graphic_data(self) -> CoordRef:
        count = len(self.graphic_data)
        if count == 0:
            raise ValueError("graphic_data 不能为空")
        if count % 3 != 0:
            raise ValueError(
                f"graphic_data 长度必须是 3 的倍数（(x,y,z) 三元组），当前为 {count}"
            )
        if self.graphic_type == "POLYGON":
            # PS3.3 C.18.9.1.2：首尾顶点必须相同且各顶点共面。
            # 2 个点的 POLYGON 既不成环也不成面，属于非法 GraphicData。
            if count < 12 or self.graphic_data[:3] != self.graphic_data[-3:]:
                raise ValueError(
                    "POLYGON 必须闭合：至少 4 个顶点（12 个值）且首尾顶点相同，"
                    f"当前长度 {count}"
                )
        return self


class GeometryPoint(BaseModel):
    """自声明坐标系的空间点位（契约 §5）。

    safe_distance 就是两个点的欧氏距离。没有 geometry 声明时，体素索引被当成
    毫米算出的距离会差一个 spacing 因子，而界面会照常显示一个绿色数字。
    """

    model_config = ConfigDict(extra="forbid")

    geometry: str
    coordinates: list[float]

    @field_validator("geometry")
    @classmethod
    def _valid_geometry(cls, value: str) -> str:
        return _check_geometry(value)

    @field_validator("coordinates")
    @classmethod
    def _check_coordinates(cls, value: list[float]) -> list[float]:
        if len(value) != 3:
            raise ValueError(f"coordinates 必须是 3 个值 (x, y, z)，当前为 {len(value)}")
        return value


# ---------- Measurement ----------


class MeasurementCreate(BaseModel):
    study_instance_uid: str
    type: str
    value: float | None = None
    unit: str | None = None
    text: str | None = None
    coord_ref: CoordRef | None = None
    cross_series: bool = False
    series_instance_uid: str | None = None


class MeasurementUpdate(BaseModel):
    type: str | None = None
    value: float | None = None
    unit: str | None = None
    text: str | None = None
    coord_ref: CoordRef | None = None
    cross_series: bool | None = None


class MeasurementOut(BaseModel):
    sr_uid: str
    case_id: str
    study_instance_uid: str
    series_instance_uid: str | None = None
    type: str
    value: float | None = None
    unit: str
    text: str | None = None
    coord_ref: dict[str, Any] = {}
    cross_series: bool = False
    state: str
    creator_id: str
    created_at: datetime
    updated_at: datetime


# ---------- Segment ----------

class SegmentCreate(BaseModel):
    study_instance_uid: str
    segment_name: str = Field(..., max_length=60)
    algorithm: str = "manual"
    seg_content: str | None = Field(default=None, description="DICOM SEG base64")
    series_instance_uid: str | None = None


class SegmentMerge(BaseModel):
    seg_uids: list[str] = Field(..., min_length=2)
    result_name: str = Field(..., max_length=60)
    operation: str = "union"


class SegmentGrow(BaseModel):
    """种子点区域生长：本地可用的分割来源（Q-SEG-PIXELS 的第一条像素通路）。

    种子必须自声明坐标系（坐标契约 §5）：体素索引被当成 LPS 毫米时，
    生长会在离目标几十厘米的另一个位置开始，而结果看起来完全正常。
    """

    model_config = ConfigDict(extra="forbid")

    study_instance_uid: str
    seed: GeometryPoint
    segment_name: str = Field(default="区域生长", max_length=60)
    hu_tolerance: float = Field(default=8.0, gt=0, le=200)
    radius_mm: float = Field(default=60.0, gt=0, le=200)
    series_instance_uid: str | None = None


class SegmentCrop(BaseModel):
    """按 LPS 盒裁切分割。

    盒可以用 min/max 直接给，也可以给中心点 + 半径（界面走后者：
    十字线位置就是中心，医生不用心算包围盒）。
    """

    model_config = ConfigDict(extra="forbid")

    min_lps: list[float] | None = Field(default=None, description="盒的最小角 [x, y, z]，LPS 毫米")
    max_lps: list[float] | None = Field(default=None, description="盒的最大角 [x, y, z]，LPS 毫米")
    center: GeometryPoint | None = None
    radius_mm: float | None = Field(default=None, gt=0, le=300)
    result_name: str | None = Field(default=None, max_length=60)

    def resolve_box(self) -> tuple[list[float], list[float]]:
        """归一化成 (min, max)。两套写法同时给时以 min/max 为准，不做隐式合并。"""
        if self.min_lps is not None and self.max_lps is not None:
            return list(self.min_lps), list(self.max_lps)
        if self.center is not None and self.radius_mm is not None:
            cx, cy, cz = (float(v) for v in self.center.coordinates)
            r = float(self.radius_mm)
            return [cx - r, cy - r, cz - r], [cx + r, cy + r, cz + r]
        raise ValueError("裁切需要 min_lps/max_lps，或 center + radius_mm（二选一）")


class SegmentSmooth(BaseModel):
    """平滑分割。

    method=median（默认）：多数表决去毛刺，保留薄结构 —— 实测在真实 CTA 上
    保留 87% 体积，而 1mm 形态学开运算只保留 12%（薄壁被削穿）。
    method=morphology：显式的开 / 闭运算，力度更大，供"确实要去掉细小突起"时使用。
    """

    model_config = ConfigDict(extra="forbid")

    method: Literal["median", "morphology"] = "median"
    radius_mm: float = Field(default=1.0, gt=0, le=20)
    closing_mm: float = Field(default=0.0, ge=0, le=20)
    result_name: str | None = Field(default=None, max_length=60)


class SegmentVolumeOut(BaseModel):
    """病灶体积。

    status="unavailable" 时所有数值字段一律为 None —— 绝不返回 0，
    也不做「算不出来就给个默认值」的兜底。调用方必须先看 status。
    """

    status: str
    reason: str | None = None
    volume_mm3: float | None = None
    volume_ml: float | None = None
    voxel_count: int | None = None
    spacing_mm: list[float] | None = None


class SegmentOut(BaseModel):
    seg_uid: str
    case_id: str
    study_instance_uid: str
    segment_name: str
    algorithm: str
    base_seg_uids: list[Any] = []
    state: str
    confirmed_by: str | None = None
    version: int
    creator_id: str
    created_at: datetime
    updated_at: datetime
    # 是否携带真实像素数据。false 表示这是个元数据空壳，
    # 不能作为解剖学计算（安全距离 / 风险分级 / 体积）的依据。
    has_pixels: bool = False
    volume: SegmentVolumeOut = Field(
        default_factory=lambda: SegmentVolumeOut(status="unavailable", reason="not_computed")
    )


# ---------- Plan ----------

class PlanCreate(BaseModel):
    base_seg_uid: str
    base_seg_version: int
    entry_point: GeometryPoint
    target_point: GeometryPoint
    notes: str | None = None


class PlanUpdate(BaseModel):
    entry_point: GeometryPoint | None = None
    target_point: GeometryPoint | None = None
    notes: str | None = None


class PlanOut(BaseModel):
    plan_id: str
    case_id: str
    base_seg_uid: str
    base_seg_version: int
    entry_point: dict[str, Any]
    target_point: dict[str, Any]
    safe_distance: float | None = None
    # 入路轴线到分割外表面的最小距离（mm）。风险分级用的是它，不是 safe_distance。
    # 二者分开暴露：前端若把 safe_distance 当"安全距离"渲染，会把路径长度
    # 显示成安全裕度，方向恰好相反。
    anatomical_distance_mm: float | None = None
    angle: float | None = None
    risk_level: str
    # 分级依据。SEG 无像素时 risk_level=unknown，此字段说明原因，
    # 避免前端只看等级值就渲染成绿色「低风险」。
    risk_basis: str | None = None
    notes: str | None = None
    state: str
    version: int
    creator_id: str
    exported_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


# ---------- Report ----------

class ReportRef(BaseModel):
    kind: str
    uid: str
    version: int


class ReportCreate(BaseModel):
    template_id: str
    findings: str = ""
    impression: str = ""
    refs: list[ReportRef] = []


class ReportUpdate(BaseModel):
    findings: str | None = None
    impression: str | None = None
    refs: list[ReportRef] | None = None


class ReportOut(BaseModel):
    report_id: str
    case_id: str
    template_id: str
    findings: str
    impression: str
    refs: list[Any] = []
    state: str
    author_id: str
    reviewed_by: str | None = None
    submitted_at: datetime | None = None
    version: int
    created_at: datetime
    updated_at: datetime


# ---------- Share ----------

class ShareCreate(BaseModel):
    permission: str = "readonly"
    scope: str = "full"
    scope_refs: list[dict[str, Any]] = []
    expires_at: str | None = None
    ttl_days: int | None = Field(default=None, ge=1, le=90)


class ShareOut(BaseModel):
    share_id: str
    case_id: str
    scope: str
    scope_refs: list[Any] = []
    permission: str
    created_by: str
    expires_at: datetime
    revoked: bool
    view_count: int
    created_at: datetime


class ShareCreatedOut(ShareOut):
    url: str
    access_code: str = Field(..., description="仅在创建响应出现一次")


class CommentCreate(BaseModel):
    body: str
    anchor_type: str | None = None
    anchor_uid: str | None = None
    mention: str | None = None


class CommentOut(BaseModel):
    comment_id: str
    case_id: str
    body: str
    anchor_type: str | None = None
    anchor_uid: str | None = None
    mention: str | None = None
    closed: bool
    author_id: str
    created_at: datetime


# ---------- AI ----------

class AiInferRequest(BaseModel):
    model: str = "totalsegmentator/multi-structure"
    model_version: str = "2.0.0"
    study_instance_uid: str
    targets: list[str] = []
    callback: str = ""
    job_id: str


class AiCallback(BaseModel):
    """AI → 系统回调（契约 §2.4）。

    stats / error / segment_name 是 routers/ai.py 与契约示例实际使用的字段，
    schema 缺了它们会让回调直接 500。
    """

    job_id: str
    status: str
    seg_uid: str | None = None
    seg_content: str | None = None
    segment_name: str | None = None
    stats: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


# ---------- 介质导入 ----------


class DirectoryImportRequest(BaseModel):
    """从光盘 / 移动存储导入。path 为服务端可见的本地绝对路径。"""

    path: str = Field(..., min_length=1, description="介质挂载点或本地目录绝对路径")


class MediaImportOut(BaseModel):
    source: str
    mode: str = Field(description="dicomdir = 按 DICOMDIR 导入；filesystem = 按文件递归扫")
    dicomdir: str | None = None
    file_set_id: str = ""
    files_found: int = 0
    files_imported: int = 0
    files_failed: int = 0
    studies: int = 0
    series: int = 0
    instances: int = 0
    study_instance_uids: list[str] = []
    errors: list[str] = []
    segment_name: str | None = None
    stats: dict[str, Any] = {}
    error: dict[str, Any] | None = None