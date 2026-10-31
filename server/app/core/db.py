from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


_settings = get_settings()
_is_sqlite = _settings.database_url.startswith("sqlite")

engine = create_engine(
    _settings.database_url,
    echo=_settings.debug,
    future=True,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db() -> None:
    import app.models  # noqa: F401  确保所有映射类被导入并注册到 metadata

    if _is_sqlite:
        db_file = _settings.database_url.split("///", 1)[-1]
        if db_file and db_file != ":memory:":
            Path(db_file).parent.mkdir(parents=True, exist_ok=True)

    Base.metadata.create_all(bind=engine)
    _migrate_columns()
    _backfill_coord_geometry()


# 增量列迁移：create_all 只建新表，不会给已存在的表补列。
# 新增字段在这里登记，启动时按「缺哪列补哪列」的方式补齐。
_COLUMN_ADDITIONS: dict[str, dict[str, str]] = {
    "idempotency_records": {
        "response_binary_b64": "TEXT",
        "response_media_type": "VARCHAR(120)",
        "response_filename": "VARCHAR(200)",
    },
    "plans": {
        "risk_basis": "VARCHAR(200)",
        "anatomical_distance_mm": "FLOAT",
    },
}


def _migrate_columns() -> None:
    """为已存在的表补齐新增列（SQLite / PostgreSQL 通用）。"""
    import sqlalchemy as sa

    with engine.begin() as conn:
        inspector = sa.inspect(conn)
        existing_tables = set(inspector.get_table_names())
        for table, columns in _COLUMN_ADDITIONS.items():
            if table not in existing_tables:
                continue
            present = {c["name"] for c in inspector.get_columns(table)}
            for column, ddl in columns.items():
                if column in present:
                    continue
                conn.execute(sa.text(f'ALTER TABLE {table} ADD COLUMN {column} {ddl}'))


# 坐标契约 §3.3：geometry 历史写法一次性回填为规范值 patient_lps_mm。
# 写路径已做「写入即归一」，这里只负责存量。
_BACKFILL_GEOMETRY_SQL: dict[str, tuple[str, str]] = {
    # dialect → (回填语句, 残留断言语句)
    "sqlite": (
        "UPDATE measurements SET coord_ref = json_replace(coord_ref, '$.geometry', "
        "'patient_lps_mm') WHERE json_extract(coord_ref, '$.geometry') IN "
        "('frame_of_reference', 'volume_world')",
        "SELECT count(*) FROM measurements WHERE json_extract(coord_ref, '$.geometry') "
        "IS NOT NULL AND json_extract(coord_ref, '$.geometry') <> 'patient_lps_mm'",
    ),
    "postgresql": (
        "UPDATE measurements SET coord_ref = jsonb_set(coord_ref, '{geometry}', "
        "'\"patient_lps_mm\"') WHERE coord_ref ->> 'geometry' IN "
        "('frame_of_reference', 'volume_world')",
        "SELECT count(*) FROM measurements WHERE coord_ref ->> 'geometry' IS NOT NULL "
        "AND coord_ref ->> 'geometry' <> 'patient_lps_mm'",
    ),
}


def _backfill_coord_geometry() -> None:
    import sqlalchemy as sa

    dialect = engine.dialect.name
    statements = _BACKFILL_GEOMETRY_SQL.get(dialect)
    if statements is None:
        return
    update_sql, assert_sql = statements
    with engine.begin() as conn:
        inspector = sa.inspect(conn)
        if "measurements" not in set(inspector.get_table_names()):
            return
        conn.execute(sa.text(update_sql))
        remaining = conn.execute(sa.text(assert_sql)).scalar() or 0
    if remaining:
        # 回填没收敛就是缺陷，不能静默放行 —— 残留的旧写法会让下游读到未归一的值
        raise RuntimeError(
            f"coord_ref.geometry 回填未完成，仍有 {remaining} 行非规范值"
        )


def create_all() -> None:
    """供脚本显式建表（幂等）。"""
    init_db()


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI 依赖。事务边界由调用方（路由 / 服务层）负责。"""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()