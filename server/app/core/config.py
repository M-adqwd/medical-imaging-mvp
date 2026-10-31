from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "医学影像阅片与手术规划系统"
    api_prefix: str = "/api/v1"
    debug: bool = False

    database_url: str = "sqlite:///./miis.db"

    # 契约 §5.4：幂等键窗口（天）
    idempotency_ttl_days: int = 7
    # 契约 §1.7：Share 默认有效期（天）
    share_default_ttl_days: int = 7
    # 契约 §4：限流
    rate_limit_per_minute: int = 600

    # DICOM 事实源：本地 DICOMweb 网关地址（Orthanc）。为空时使用内置文件存储。
    dicomweb_base_url: str = ""
    dicomweb_verify_tls: bool = False
    dicom_store_dir: str = "./data/dicom"
    # 介质导入允许的根目录（光盘 / 移动存储挂载点）。留空表示不限制，
    # 生产环境应显式配置，避免任意本地路径被读入归档。
    dicom_import_roots: list[str] = []

    # AI 推理服务（MONAI Deploy）
    ai_base_url: str = ""
    ai_timeout_sec: int = 30
    # 本地真实推理（TotalSegmentator）。仅在 ai_base_url 为空时生效：
    # 显式配置了外部 AI 服务就优先用外部。开了但环境里没有 CLI 时，
    # /ai/infer 会明确 503，而不是退回桩——两种失败长得一样才最危险。
    ai_local_infer: bool = False
    # ModelForge（训练平台）网关地址。置空则 /ai/classify 明确拒绝，
    # 不退回桩：让阅片端「以为调了模型其实什么都没发生」是最坏的失败方式。
    modelforge_base_url: str = ""
    modelforge_token: str = ""
    # 令牌会过期（训练平台 TOKEN_TTL），因此除了静态 token 还允许用账号自动换票；
    # 两者都没有就明确报错，不能悄悄调用失败。
    modelforge_account: str = ""
    modelforge_password: str = ""
    modelforge_timeout_sec: int = 120

    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()