"""
config — 全局配置单例，从 .env / 环境变量读取所有运行参数

调用链位置：
  本模块 ← 所有其他模块（通过 from app.config import settings 读取配置项）
  本模块 ← db.py、auth.py、git_release.py、repositories.py 等

对外暴露：
  - settings : Settings 不可变单例，包含所有配置项
  - parse_bool / parse_int / normalize_database_url : 辅助解析函数，仅供本模块内部使用
"""
from dataclasses import dataclass
import os
import re
from urllib.parse import quote

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - 本地未安装依赖时仍允许基础配置加载。
    def load_dotenv() -> None:
        return None


# 启动时加载 .env 文件，缺失 python-dotenv 则忽略（不影响 os.getenv）
load_dotenv()


def parse_bool(value: str | None, default: bool = False) -> bool:
    """将环境变量字符串解析为布尔值。'1'/'true'/'yes'/'on' 视为 True，其余为 False。"""
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def parse_int(value: str | None, default: int) -> int:
    """将环境变量字符串解析为整数，解析失败时返回默认值。"""
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def normalize_database_url(database_url: str | None) -> str | None:
    """规范化 MySQL 连接 URL。

    兼容手工误填的 mysql+pymysql:user//:password@host 格式（多了一个冒号），
    将其修正为标准 mysql+pymysql://user:password@host 格式。
    标准格式直接放行。
    """
    if not database_url:
        return None

    database_url = database_url.strip()
    if database_url.startswith("mysql+pymysql://"):
        return database_url

    # 兼容本地手工误填的 mysql+pymysql:user//:password@host 格式。
    legacy_match = re.match(r"^mysql\+pymysql:([^/]+)//:([^@]+)@(.+)$", database_url)
    if legacy_match:
        username, password, host_and_db = legacy_match.groups()
        return f"mysql+pymysql://{quote(username)}:{quote(password, safe='')}@{host_and_db}"

    return database_url


@dataclass(frozen=True)
class Settings:
    """全局不可变配置，所有值从 .env 或环境变量读取。

    关键配置项说明：
      - database_url : 发布台自身元数据库连接（读写 rc_* 表）
      - dtstack_test_metadata_database_url : 测试数栈元数据库只读连接
      - dtstack_prod_metadata_database_url : 生产数栈元数据库只读连接
      - cookie_encryption_key : Fernet 加密密钥，用于 GitLab 密码和 auth Cookie 加密
      - git_workspace_root : git clone 缓存目录，相对路径则拼到项目根下
      - git_auto_refresh_interval_seconds : 后台定时刷新 Git 提交的间隔秒数，设为 0 关闭
      - metadata_auto_sync_interval_seconds : 后台定时同步测试/生产元数据的间隔秒数，设为 0 关闭
      - gitlab_base_url / gitlab_default_project_url : GitLab 地址与默认检测项目
    """
    database_url: str | None = normalize_database_url(os.getenv("DATABASE_URL") or None)
    # 兼容历史配置：若未拆分测试/生产连接，则测试侧继续回退旧的 DTSTACK_METADATA_DATABASE_URL。
    dtstack_test_metadata_database_url: str | None = normalize_database_url(
        os.getenv("DTSTACK_TEST_METADATA_DATABASE_URL")
        or os.getenv("DTSTACK_METADATA_DATABASE_URL")
        or None
    )
    dtstack_prod_metadata_database_url: str | None = normalize_database_url(
        os.getenv("DTSTACK_PROD_METADATA_DATABASE_URL") or None
    )
    # 历史兼容字段，供尚未迁移完的代码继续读取测试元数据库配置。
    dtstack_metadata_database_url: str | None = dtstack_test_metadata_database_url
    database_required: bool = parse_bool(os.getenv("DATABASE_REQUIRED"))
    cookie_encryption_key: str | None = os.getenv("COOKIE_ENCRYPTION_KEY") or None
    auth_session_ttl_hours: int = parse_int(os.getenv("AUTH_SESSION_TTL_HOURS"), 8)
    auth_cookie_secure: bool = parse_bool(os.getenv("AUTH_COOKIE_SECURE"))
    auth_validation_interval_minutes: int = parse_int(os.getenv("AUTH_VALIDATION_INTERVAL_MINUTES"), 10)
    auth_env_type: str = (os.getenv("AUTH_ENV_TYPE") or "test").strip().lower()
    auth_env_name: str = (os.getenv("AUTH_ENV_NAME") or "测试环境").strip()
    git_workspace_root: str = os.getenv("GIT_WORKSPACE_ROOT") or ".cache/git-repos"
    git_operation_timeout_seconds: int = parse_int(os.getenv("GIT_OPERATION_TIMEOUT_SECONDS"), 20)
    git_auto_refresh_interval_seconds: int = parse_int(os.getenv("GIT_AUTO_REFRESH_INTERVAL_SECONDS"), 600)
    metadata_auto_sync_interval_seconds: int = parse_int(os.getenv("METADATA_AUTO_SYNC_INTERVAL_SECONDS"), 1800)
    auth_fallback_base_url: str = (os.getenv("AUTH_FALLBACK_BASE_URL") or "http://192.168.35.119/").rstrip("/")
    gitlab_base_url: str = (os.getenv("GITLAB_BASE_URL") or "http://jnbygitlab.jnby.com").rstrip("/")
    gitlab_username: str | None = os.getenv("GITLAB_USERNAME") or None
    gitlab_password: str | None = os.getenv("GITLAB_PASSWORD") or None
    gitlab_login_timeout_seconds: int = parse_int(os.getenv("GITLAB_LOGIN_TIMEOUT_SECONDS"), 10)
    gitlab_default_project_url: str = (
        os.getenv("GITLAB_DEFAULT_PROJECT_URL")
        or "http://jnbygitlab.jnby.com/hangwenjie405085/independent_pj"
    )
    use_mock_data: bool = parse_bool(os.getenv("USE_MOCK_DATA"))


# 全局配置单例，其他模块通过 from app.config import settings 使用
settings = Settings()
