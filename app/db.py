"""
db — MySQL 数据库连接与查询封装

调用链位置：
  本模块 ← repositories.py（所有 rc_* 表读写、数栈元数据库只读查询）
  本模块 ← auth.py（rc_auth_session 读写）

核心设计：
  - 维护三类数据库连接：自身元数据库(DATABASE_URL)、测试数栈元数据库、生产数栈元数据库
  - 自身库可读写（fetch_all / fetch_one / execute_write / execute_insert）
  - 数栈库只读（fetch_dtstack_all），未配置时回退到自身库
  - 全裸 SQL（sqlalchemy.text），无 ORM；读用 connect()（无显式事务），写用 begin()（事务上下文）
  - 引擎按 URL 缓存在 _ENGINES 字典，懒加载 SQLAlchemy

对外暴露：
  - is_database_configured / is_test_metadata_configured / is_prod_metadata_configured : 配置探测
  - get_engine : 获取/创建 SQLAlchemy 引擎
  - fetch_all / fetch_one : 自身库只读查询
  - fetch_test_metadata_all / fetch_prod_metadata_all / fetch_dtstack_all : 数栈库只读查询
  - fetch_all_from : 通用只读查询（指定 URL）
  - execute_write : 自身库写操作（事务），返回影响行数
  - execute_insert : 自身库插入（事务），返回自增 ID
"""
from collections.abc import Iterable
from typing import Any

from app.config import normalize_database_url, settings

# 按 URL 缓存 SQLAlchemy 引擎实例，同一 URL 只创建一次
_ENGINES: dict[str, Any] = {}


def _read_encrypted_app_setting(setting_key: str) -> str | None:
    """从平台库读取加密配置项，供测试/生产元数据库 URL 做落库兜底。"""
    if not settings.database_url:
        return None
    try:
        from sqlalchemy import text
        from app.secret_crypto import decrypt_secret
    except Exception:
        return None

    engine = get_engine(settings.database_url, required=False)
    if engine is None:
        return None
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT setting_value
                    FROM rc_app_setting
                    WHERE setting_key = :setting_key
                    LIMIT 1
                    """
                ),
                {"setting_key": setting_key},
            ).mappings().first()
    except Exception:
        return None
    if not row or not row.get("setting_value"):
        return None
    try:
        return normalize_database_url(decrypt_secret(str(row["setting_value"])))
    except Exception:
        return normalize_database_url(str(row["setting_value"]))


def current_test_metadata_database_url() -> str | None:
    """测试元数据库 URL：优先 `.env`，否则从 rc_app_setting 加密配置读取。"""
    return settings.dtstack_test_metadata_database_url or _read_encrypted_app_setting(
        "dtstack_test_metadata_database_url_ciphertext"
    )


def current_prod_metadata_database_url() -> str | None:
    """生产元数据库 URL：优先 `.env`，否则从 rc_app_setting 加密配置读取。"""
    return settings.dtstack_prod_metadata_database_url or _read_encrypted_app_setting(
        "dtstack_prod_metadata_database_url_ciphertext"
    )


def is_database_configured() -> bool:
    """判断发布台自身元数据库(DATABASE_URL)是否已配置。

    被 repositories.db_badge / sync_dtstack_metadata 等用来做能力探测。
    """
    return bool(settings.database_url)


def is_dtstack_metadata_configured() -> bool:
    """兼容旧逻辑：判断测试数栈元数据库是否已配置。

    被 repositories 里的同步函数判断是否可从数栈库拉数据。
    """
    return bool(current_test_metadata_database_url())


def is_test_metadata_configured() -> bool:
    """判断测试数栈元数据库是否已配置。"""
    return bool(current_test_metadata_database_url())


def is_prod_metadata_configured() -> bool:
    """判断生产数栈元数据库是否已配置。"""
    return bool(current_prod_metadata_database_url())


def get_engine(database_url: str | None = None, *, required: bool | None = None) -> Any:
    """获取或创建 SQLAlchemy 引擎实例，按 URL 缓存。

    参数：
      database_url : 指定连接 URL，默认取 settings.database_url
      required : 是否必须成功，默认取 settings.database_required

    池参数：pool_pre_ping=True(探活)、pool_size=1(单连接)、max_overflow=0、pool_recycle=1800(30分钟回收)
    被 fetch_all_from / execute_write / execute_insert 调用。
    """
    url = database_url if database_url is not None else settings.database_url
    must_exist = settings.database_required if required is None else required

    if not url:
        if must_exist:
            raise RuntimeError("必须配置数据库连接地址。")
        return None

    if url not in _ENGINES:
        try:
            from sqlalchemy import create_engine
        except ImportError as exc:
            if must_exist:
                raise RuntimeError("SQLAlchemy 未安装，无法读取 MySQL 元数据。") from exc
            return None

        # 懒加载：只在首次访问该 URL 时创建引擎
        _ENGINES[url] = create_engine(
            url,
            pool_pre_ping=True,  # 每次取连接前先 ping，避免拿到的连接已断开
            pool_size=1,         # 单连接池，适合 Web demo 低并发场景
            max_overflow=0,
            pool_recycle=1800,   # 30 分钟回收连接，避免 MySQL 8 小时断开问题
            future=True,
        )

    return _ENGINES[url]


def fetch_all_from(
    database_url: str | None,
    sql: str,
    params: dict[str, Any] | None = None,
    *,
    required: bool | None = None,
) -> list[dict[str, Any]]:
    """通用只读查询：在指定 URL 的数据库上执行裸 SQL，返回字典列表。

    异常降级策略：
      - 若 required=True 或指定了 database_url 或 settings.database_required=True → 直接抛异常
      - 否则吞掉异常返回空列表 []——这是"数栈元数据库未配置时页面不报错"的关键

    被 fetch_all / fetch_dtstack_all 内部调用。
    """
    try:
        from sqlalchemy import text
    except ImportError as exc:
        if settings.database_required:
            raise RuntimeError("SQLAlchemy 未安装，无法读取 MySQL 元数据。") from exc
        return []

    try:
        engine = get_engine(database_url, required=required)
        if engine is None:
            return []
        # 只读连接，不开显式事务
        with engine.connect() as conn:
            rows: Iterable[Any] = conn.execute(text(sql), params or {}).mappings()
            return [dict(row) for row in rows]
    except Exception:
        # 未配置或非必须时优雅降级，页面不会因为缺库而报错
        if required or database_url or settings.database_required:
            raise
        return []


def fetch_all(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """自身元数据库只读查询（DATABASE_URL）。

    被 repositories 里几乎所有 get_* 函数调用，读取 rc_* 表数据。
    """
    return fetch_all_from(settings.database_url, sql, params, required=settings.database_required)


def fetch_dtstack_all(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """兼容旧逻辑：读取测试数栈元数据库。

    URL 取 DTSTACK_TEST_METADATA_DATABASE_URL，未配置时回退到自身库(DATABASE_URL)。
    被 repositories 里的同步函数和任务列表查询调用，读取 streamapp/ide/dt_pub_service 表。
    """
    database_url = current_test_metadata_database_url() or settings.database_url
    return fetch_all_from(
        database_url,
        sql,
        params,
        required=bool(current_test_metadata_database_url()) or settings.database_required,
    )


def fetch_test_metadata_all(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """测试数栈元数据库只读查询。"""
    database_url = current_test_metadata_database_url() or settings.database_url
    return fetch_all_from(
        database_url,
        sql,
        params,
        required=bool(current_test_metadata_database_url()) or settings.database_required,
    )


def fetch_prod_metadata_all(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """生产数栈元数据库只读查询。"""
    database_url = current_prod_metadata_database_url() or settings.database_url
    return fetch_all_from(
        database_url,
        sql,
        params,
        required=bool(current_prod_metadata_database_url()) or settings.database_required,
    )


def fetch_one(sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """自身库查一行，取首条或返回 None。

    被 auth.get_production_environment / repositories 等单条查询场景调用。
    """
    rows = fetch_all(sql, params)
    return rows[0] if rows else None


def execute_write(sql: str, params: dict[str, Any] | None = None) -> int:
    """自身库写操作（UPDATE/DELETE 等），在事务中执行，返回影响行数。

    被 repositories 里所有 UPDATE/DELETE 操作调用（如软删、状态更新等）。
    使用 engine.begin() 事务上下文，异常自动回滚。
    """
    try:
        from sqlalchemy import text
    except ImportError as exc:
        raise RuntimeError("SQLAlchemy 未安装，无法写入 MySQL 元数据。") from exc

    engine = get_engine(settings.database_url, required=True)
    if engine is None:
        raise RuntimeError("未配置 DATABASE_URL，无法写入 MySQL 元数据。")

    # 事务上下文：异常自动回滚，正常提交
    with engine.begin() as conn:
        result = conn.execute(text(sql), params or {})
        return int(result.rowcount or 0)


def execute_insert(sql: str, params: dict[str, Any] | None = None) -> int:
    """执行单行插入并返回自增 ID，用于发布草稿、批次等 Web 元数据。

    被 repositories.persist_release_draft / simulate_release_from_draft 等调用，
    写入 rc_release_draft / rc_release_batch 等表后取回自增 ID。
    """
    try:
        from sqlalchemy import text
    except ImportError as exc:
        raise RuntimeError("SQLAlchemy 未安装，无法写入 MySQL 元数据。") from exc

    engine = get_engine(settings.database_url, required=True)
    if engine is None:
        raise RuntimeError("未配置 DATABASE_URL，无法写入 MySQL 元数据。")

    with engine.begin() as conn:
        result = conn.execute(text(sql), params or {})
        return int(result.lastrowid or 0)
