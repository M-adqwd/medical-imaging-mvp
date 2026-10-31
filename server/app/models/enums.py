from __future__ import annotations

from enum import Enum


class CaseState(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    ARCHIVED = "archived"


class CaseSource(str, Enum):
    PACS = "pacs"
    UPLOAD = "upload"


class MeasurementType(str, Enum):
    LENGTH = "length"
    ANGLE = "angle"
    AREA = "area"
    VOLUME = "volume"
    FREE_TEXT = "free-text"
    ARROW = "arrow"


class MeasurementState(str, Enum):
    DRAFT = "draft"
    SAVED = "saved"
    WITHDRAWN = "withdrawn"


class SegmentState(str, Enum):
    DRAFT = "draft"
    SUGGESTED = "suggested"
    CONFIRMED = "confirmed"
    WITHDRAWN = "withdrawn"


class SegmentAlgorithm(str, Enum):
    MANUAL = "manual"
    MERGED = "merged"


class PlanState(str, Enum):
    DRAFT = "draft"
    SAVED = "saved"
    EXPORTED = "exported"
    WITHDRAWN = "withdrawn"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    # 底层 SEG 无像素数据时不得给出分级结论 —— 否则一个空壳 SEG 会被
    # 渲染成绿色「低风险」，属于临床上最危险的静默失效。
    UNKNOWN = "unknown"


class ReportState(str, Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    WITHDRAWN = "withdrawn"
    LOCKED = "locked"
    STALE_REF = "stale-ref"


class ShareScope(str, Enum):
    FULL = "full"
    CUSTOM = "custom"


class SharePermission(str, Enum):
    READONLY = "readonly"
    READONLY_COMMENT = "readonly+comment"


class MergeOperation(str, Enum):
    UNION = "union"
    INTERSECTION = "intersection"
    SUBTRACT = "subtract"


class AiJobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"