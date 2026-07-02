"""
auth — Web 登录鉴权与生产数栈 Cookie 会话托管

调用链位置：
  本模块 ← main.py（鉴权中间件 require_authenticated_session、
            路由 login_page/login_submit/logout/captcha 等直接调用）

核心职责：
  1. 从生产数栈 UIC 拉验证码图片 + SM2 公钥（DtStackAuthClient.fetch_captcha）
  2. 用前端 SM2 加密后的密码密文 + 验证码登录生产数栈（DtStackAuthClient.login）
  3. 把生产 Cookie 用 Fernet 加密落库 rc_auth_session，对外发本系统 rc_session Cookie
  4. 后续请求凭 rc_session 校验/续期/登出（AuthSessionStore）

登录完整流程（对应页面操作）：
  ① 用户访问 /auth/captcha → get_captcha_payload() → fetch_captcha → 拿验证码图片 + SM2 公钥
  ② 前端用公钥执行 sm2.doEncrypt(password) + 拼接 04 → 生成 password_ciphertext
  ③ 用户提交 /login → login_to_production() → dtstack_client.login → POST 生产登录接口
  ④ 登录成功 → session_store.create_session → 生产 Cookie 加密落库 rc_auth_session
  ⑤ 下发本系统 rc_session Cookie（httponly/samesite/lax），浏览器不接触生产 Cookie
  ⑥ 后续请求 → 中间件 require_authenticated_session → get_current_session → 校验/续期

对外暴露：
  - SESSION_COOKIE_NAME / REMEMBER_USERNAME_COOKIE : Cookie 名称常量
  - AuthError : 异常类
  - get_captcha_payload() : 获取验证码 + SM2 公钥
  - login_to_production(username, encrypted_password, verify_code, key) : 登录生产数栈
  - get_current_session(request) : 从请求中获取有效会话
  - logout(session_id) : 登出，软失效 rc_auth_session
  - public_path(path) : 判断路径是否免登录
  - AuthSession / LoginSession 等数据类
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
import random
import re
import secrets
import time
from typing import Any
from urllib.parse import urljoin

from fastapi import Request

from app.config import settings
from app.db import execute_write, fetch_one

# 本系统会话 Cookie 名称，浏览器只保存此 Cookie，不接触生产 Cookie
SESSION_COOKIE_NAME = "rc_session"
# "记住我"功能保存的用户名 Cookie，30 天有效
REMEMBER_USERNAME_COOKIE = "rc_username"


class AuthError(RuntimeError):
    """登录链路中的可展示错误。"""


@dataclass(frozen=True)
class ProductionEnvironment:
    """当前登录数栈环境信息，从 rc_environment 表读取。"""
    id: int
    env_type: str
    env_name: str
    base_url: str


@dataclass(frozen=True)
class CaptchaPayload:
    """验证码获取结果，包含验证码 key、图片 base64、内容类型和 SM2 公钥。

    被 /auth/captcha 路由返回给前端，前端用 public_key 加密密码。
    """
    key: str
    image_base64: str
    content_type: str
    public_key: str


@dataclass(frozen=True)
class ProductionCookieBundle:
    """生产数栈登录成功后的 Cookie 包，包含用户名、环境 ID、Cookie 字典和过期时间。

    由 DtStackAuthClient.login 返回，传入 AuthSessionStore.create_session 加密落库。
    """
    username: str
    prod_env_id: int
    base_url: str
    cookies: dict[str, str]
    expires_at: datetime | None


@dataclass(frozen=True)
class AuthSession:
    """本系统有效会话信息，从 rc_auth_session 解密后构造。

    被 main.py 中间件 require_authenticated_session 挂到 request.state.auth_session，
    下游路由通过 request.state.auth_session.username 取用户名。
    """
    username: str
    prod_env_id: int
    cookies: dict[str, str]


@dataclass(frozen=True)
class LoginSession:
    """登录成功后的会话信息，包含明文 session_id（只下发到 Cookie，不入库）、用户名和过期时间。

    由 AuthSessionStore.create_session 返回，main.py 用它写 rc_session Cookie。
    """
    session_id: str
    username: str
    expires_at: datetime


def utcnow() -> datetime:
    """返回当前 UTC 时间。被 AuthSessionStore 各方法用来判断过期和刷新时间。"""
    return datetime.utcnow()


def parse_datetime(value: Any) -> datetime | None:
    """把数据库中的 datetime/str 值统一解析为 datetime 对象。"""
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def public_path(path: str) -> bool:
    """判断请求路径是否免登录（白名单）。

    白名单：/static/* 前缀、/、/login、/auth/captcha、/permission、/logout、/favicon.ico。
    被 main.py 鉴权中间件 require_authenticated_session 调用。
    """
    if path.startswith("/static/"):
        return True
    return path in {"/", "/login", "/auth/captcha", "/permission", "/logout", "/favicon.ico"}


def get_auth_environment_type() -> str:
    """读取登录环境类型。

    优先级：
      1. `.env` / 环境变量 AUTH_ENV_TYPE
      2. rc_app_setting.auth_env_type
      3. 空串（由下游按测试 → 生产回退）
    """
    configured = str(settings.auth_env_type or "").strip().lower()
    if configured in {"test", "prod"}:
        return configured
    row = fetch_one(
        """
        SELECT setting_value
        FROM rc_app_setting
        WHERE setting_key = 'auth_env_type'
        LIMIT 1
        """
    )
    value = str((row or {}).get("setting_value") or "").strip().lower()
    return value if value in {"test", "prod"} else ""


def get_production_environment() -> ProductionEnvironment:
    """读取当前启用的登录环境配置。

    顺序：
      1. 若 rc_app_setting.auth_env_type 已配置，则优先使用该环境类型
      2. 未配置时默认尝试 test
      3. test 不存在时自动回退到 prod
    """
    preferred = get_auth_environment_type()
    candidate_types = [preferred] if preferred else ["test", "prod"]
    if preferred == "test":
        candidate_types.append("prod")
    elif preferred == "prod":
        candidate_types.append("test")

    row = None
    selected_env_type = ""
    for env_type in candidate_types:
        if not env_type:
            continue
        row = fetch_one(
            """
            SELECT id, env_type, env_name, base_url
            FROM rc_environment
            WHERE env_type = :env_type AND is_enabled = 1
            ORDER BY id
            LIMIT 1
            """,
            {"env_type": env_type},
        )
        if row:
            selected_env_type = env_type
            break

    if not row:
        fallback_base_url = str(settings.auth_fallback_base_url or "").strip().rstrip("/")
        if fallback_base_url:
            fallback_env_type = preferred or "test"
            fallback_env_name = str(settings.auth_env_name or "测试环境").strip() or "测试环境"
            return ProductionEnvironment(
                id=0,
                env_type=fallback_env_type,
                env_name=fallback_env_name,
                base_url=fallback_base_url,
            )
        tried = " -> ".join([item for item in candidate_types if item]) or "test/prod"
        raise AuthError(f"未找到启用的登录环境配置（尝试顺序：{tried}），请先在元数据库中维护。")
    return ProductionEnvironment(
        id=int(row["id"]),
        env_type=str(row.get("env_type") or selected_env_type),
        env_name=str(row.get("env_name") or selected_env_type),
        base_url=str(row["base_url"]).rstrip("/"),
    )


class DtStackAuthClient:
    """与生产数栈 UIC 系统交互：拉验证码、获取 SM2 公钥、提交登录。

    被模块级单例 dtstack_client 使用，main.py 通过顶层函数间接调用。
    核心方法：
      - fetch_captcha(env) → 拉验证码图片 + SM2 公钥
      - login(username, encrypted_password, verify_code, key) → 登录生产数栈
      - verify_cookies(cookies) → 校验 Cookie 是否仍有效（当前只判 dt_token + DT_SESSION_ID）
    """
    def __init__(self) -> None:
        # 生产验证码可能依赖同一 HTTP 会话中的临时 Cookie；这里按验证码 key
        # 短暂缓存，提交登录时复用，避免把生产 Cookie 暴露给浏览器。
        self._captcha_cookies: dict[str, tuple[dict[str, str], datetime]] = {}

    def remember_captcha_cookies(self, key: str, cookies: dict[str, str]) -> None:
        """缓存验证码请求产生的临时 Cookie，10 分钟有效，供后续 login 复用。

        被 fetch_captcha 调用（获取验证码后存入缓存）。
        """
        self.cleanup_captcha_cookies()
        self._captcha_cookies[key] = (cookies, utcnow() + timedelta(minutes=10))

    def pop_captcha_cookies(self, key: str) -> dict[str, str]:
        """取出并删除验证码阶段的临时 Cookie，供 login 复用到生产登录请求。

        被 login 调用（提交登录时还原验证码 Cookie，保证验证码和登录在同一会话）。
        """
        self.cleanup_captcha_cookies()
        stored = self._captcha_cookies.pop(key, None)
        return stored[0] if stored else {}

    def cleanup_captcha_cookies(self) -> None:
        """清理过期的验证码 Cookie 缓存。被 remember / pop 自动调用。"""
        now = utcnow()
        expired_keys = [key for key, (_, expires_at) in self._captcha_cookies.items() if expires_at <= now]
        for key in expired_keys:
            self._captcha_cookies.pop(key, None)

    def _requests(self):
        """懒加载 requests 库，缺失时抛 AuthError。"""
        try:
            import requests
        except ImportError as exc:  # pragma: no cover - 真实登录环境依赖
            raise AuthError("缺少 requests 依赖，请先安装 requirements.txt。") from exc
        return requests

    def _new_session(self, base_url: str):
        """创建带数栈 UIC 特定 headers 的 requests.Session。

        关键 headers：Origin/Referer 指向 base_url/uic/，X-Custom-Header: dtuic
        被 fetch_captcha / login 调用。
        """
        requests = self._requests()
        session = requests.Session()
        session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
            ),
            "Origin": base_url,
            "Referer": f"{base_url}/uic/",
            "X-Custom-Header": "dtuic",
        })
        return session

    def generate_key(self) -> str:
        """生成验证码 key：毫秒时间戳 + 6 位随机数。被 fetch_captcha 调用。"""
        return f"{int(time.time() * 1000)}{random.randint(100000, 999999)}"

    def fetch_captcha(self, env: ProductionEnvironment) -> CaptchaPayload:
        """从生产数栈拉取验证码图片和 SM2 公钥。

        流程：
          1. 生成验证码 key
          2. GET uic/api/v2/account/login/gen-captcha（带 key 参数）→ 获取验证码图片二进制
          3. GET uic/api/v2/account/login/get-publi-key → 获取 SM2 公钥
          4. remember_captcha_cookies 存下本次 Session 的 Cookie（供后续 login 复用）
          5. 图片 base64 编码返回

        被 get_captcha_payload() 调用，结果返回给 /auth/captcha 路由。
        """
        key = self.generate_key()
        session = self._new_session(env.base_url)
        resp = session.get(
            urljoin(env.base_url + "/", "uic/api/v2/account/login/gen-captcha"),
            params={"r": str(random.random()), "key": key},
            timeout=10,
        )
        resp.raise_for_status()
        # 获取 SM2 公钥（前端用此公钥加密密码）
        public_key = self.get_public_key(session, env.base_url)
        # 把验证码请求的 Cookie 缓存起来，登录提交时复用同一会话
        self.remember_captcha_cookies(key, session.cookies.get_dict())
        content_type = resp.headers.get("content-type", "image/jpeg").split(";", 1)[0]
        image_base64 = base64.b64encode(resp.content).decode("ascii")
        return CaptchaPayload(key=key, image_base64=image_base64, content_type=content_type, public_key=public_key)

    def get_public_key(self, session: Any, base_url: str) -> str:
        """从生产数栈获取 SM2 登录公钥（注意原接口拼写是 publi-key，不是 public-key）。

        被 fetch_captcha 内部调用，公钥交给前端加密密码。
        """
        resp = session.get(urljoin(base_url + "/", "uic/api/v2/account/login/get-publi-key"), timeout=10)
        resp.raise_for_status()
        payload = resp.json()
        if not payload.get("success") or not payload.get("data"):
            raise AuthError("获取生产登录公钥失败。")
        return str(payload["data"])

    def validate_encrypted_password(self, encrypted_password: str) -> None:
        """校验前端 SM2 加密后的密码密文格式：必须以 04 开头且为 130+ 位十六进制。

        SM2 加密本身在前端用 public_key 做，后端只校验密文格式不参与加密。
        被 login 调用。
        """
        if not encrypted_password.startswith("04") or not re.fullmatch(r"[0-9a-fA-F]{130,}", encrypted_password):
            raise AuthError("登录请求中的密码密文格式无效，请刷新页面后重试。")

    def cookie_expires_at(self, session: Any) -> datetime | None:
        """从 requests.Session 的 Cookie 中提取最早过期时间，作为会话过期时间。

        被 login 调用，确定 ProductionCookieBundle.expires_at。
        """
        expires_values = [cookie.expires for cookie in session.cookies if cookie.expires]
        if not expires_values:
            return None
        return datetime.utcfromtimestamp(min(expires_values))

    def login(self, username: str, encrypted_password: str, verify_code: str, key: str) -> ProductionCookieBundle:
        """登录生产数栈：校验密文 → 获取生产环境 → 还原验证码 Cookie → POST 登录接口 → 校验核心 Cookie。

        流程：
          1. validate_encrypted_password 校验密文格式
          2. get_production_environment 获取生产环境 base_url
          3. pop_captcha_cookies(key) 还原验证码阶段的 Cookie
          4. POST uic/api/v2/account/login（form-urlencoded: username/password/verify_code/key）
          5. 校验响应 Cookie 里同时有 dt_token 和 DT_SESSION_ID → 否则抛 AuthError
          6. 返回 ProductionCookieBundle（含生产 cookies + 过期时间）

        被 login_to_production() 调用。
        """
        self.validate_encrypted_password(encrypted_password)
        env = get_production_environment()
        session = self._new_session(env.base_url)
        # 还原验证码阶段的 Cookie，保证验证码和登录在同一 HTTP 会话
        session.cookies.update(self.pop_captcha_cookies(key))
        resp = session.post(
            urljoin(env.base_url + "/", "uic/api/v2/account/login"),
            data={
                "username": username,
                "password": encrypted_password,
                "verify_code": verify_code,
                "key": key,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
            timeout=10,
        )
        resp.raise_for_status()
        cookies = session.cookies.get_dict()
        # 核心判断：数栈登录成功必须同时下发 dt_token 和 DT_SESSION_ID
        if "dt_token" not in cookies or "DT_SESSION_ID" not in cookies:
            raise AuthError(f"{env.env_name} 登录失败，请检查账号、密码和验证码。")
        return ProductionCookieBundle(
            username=username,
            prod_env_id=env.id,
            base_url=env.base_url,
            cookies=cookies,
            expires_at=self.cookie_expires_at(session),
        )

    def verify_cookies(self, cookies: dict[str, str]) -> bool:
        """校验生产 Cookie 是否仍有效。当前阶段先不调用登录后验证接口，只判核心 Cookie 是否在。

        和独立登录脚本保持一致：只要 dt_token 和 DT_SESSION_ID 都存在就认为有效。
        后续接入真实发布 API 时，再补充轻量登录态校验接口。
        """
        # 当前阶段先不调用登录后验证接口，和独立登录脚本保持一致：
        # 只要生产登录接口下发核心 Cookie，就认为本地会话可复用。
        return "dt_token" in cookies and "DT_SESSION_ID" in cookies


class AuthSessionStore:
    """本系统会话（rc_auth_session）的落库、校验、续期和登出管理。

    核心设计：
      - 明文 session_id 不入库，只存 SHA-256 哈希（session_id_hash）
      - 生产 Cookie 用 Fernet 加密后存入 cookie_ciphertext
      - 浏览器只保存本系统 rc_session Cookie，不接触生产 Cookie
      - 会话校验超过 auth_validation_interval_minutes（默认 10 分钟）时重新验证

    注意：本类 _fernet 的密钥派生逻辑与 secret_crypto.fernet_key 完全一致，
    但未复用 secret_crypto，修改密钥规则时需同步更新两处。
    """
    def session_hash(self, session_id: str) -> str:
        """对明文 session_id 做 SHA-256 摘要，库里只存哈希不存明文。"""
        return hashlib.sha256(session_id.encode("utf-8")).hexdigest()

    def _fernet(self):
        """派生 Fernet 加密实例，密钥逻辑与 secret_crypto.fernet_key 一致。

        规则：COOKIE_ENCRYPTION_KEY 经 urlsafe_b64decode 后 32 字节 → 直接用；
        否则 SHA-256 哈希后再 urlsafe_b64encode → 生成 32 字节 key。
        """
        try:
            from cryptography.fernet import Fernet
        except ImportError as exc:  # pragma: no cover - 真实登录环境依赖
            raise AuthError("缺少 cryptography 依赖，请先安装 requirements.txt。") from exc

        raw_key = settings.cookie_encryption_key
        if not raw_key:
            raise AuthError("请先在 .env 中配置 COOKIE_ENCRYPTION_KEY。")
        try:
            decoded = base64.urlsafe_b64decode(raw_key.encode("utf-8"))
            fernet_key = raw_key.encode("utf-8") if len(decoded) == 32 else None
        except Exception:
            fernet_key = None
        if fernet_key is None:
            fernet_key = base64.urlsafe_b64encode(hashlib.sha256(raw_key.encode("utf-8")).digest())
        return Fernet(fernet_key)

    def encrypt_cookies(self, cookies: dict[str, str]) -> str:
        """把生产 Cookie 字典 JSON 序列化后 Fernet 加密，返回密文字符串。

        被 create_session 调用，密文写入 rc_auth_session.cookie_ciphertext。
        """
        payload = json.dumps(cookies, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return self._fernet().encrypt(payload).decode("utf-8")

    def decrypt_cookies(self, ciphertext: str) -> dict[str, str]:
        """把 rc_auth_session.cookie_ciphertext 中的密文 Fernet 解密，返回 Cookie 字典。

        被 get_valid_session 调用，解密后用于后续校验和续期判断。
        """
        payload = self._fernet().decrypt(ciphertext.encode("utf-8"))
        return json.loads(payload.decode("utf-8"))

    def create_session(self, bundle: ProductionCookieBundle) -> LoginSession:
        """登录成功后创建本系统会话：生成 session_id → 加密 Cookie → INSERT rc_auth_session。

        关键设计：
          - 明文 session_id 用 secrets.token_urlsafe(32) 生成，只通过返回值交给路由写 Cookie
          - 库里只存 session_id_hash（SHA-256），不存明文，防止泄露
          - 过期时间优先取生产 Cookie 的实际过期时间，否则按 auth_session_ttl_hours 计算

        被 login_to_production() 调用。
        """
        session_id = secrets.token_urlsafe(32)  # 明文 session_id，只下发到 Cookie，不入库
        expires_at = bundle.expires_at or (utcnow() + timedelta(hours=settings.auth_session_ttl_hours))
        execute_write(
            """
            INSERT INTO rc_auth_session(
              session_id_hash, username, prod_env_id, cookie_ciphertext,
              cookie_expires_at, last_validated_at, is_active
            )
            VALUES (
              :session_id_hash, :username, :prod_env_id, :cookie_ciphertext,
              :cookie_expires_at, :last_validated_at, 1
            )
            """,
            {
                "session_id_hash": self.session_hash(session_id),
                "username": bundle.username,
                "prod_env_id": bundle.prod_env_id,
                "cookie_ciphertext": self.encrypt_cookies(bundle.cookies),
                "cookie_expires_at": expires_at,
                "last_validated_at": utcnow(),
            },
        )
        return LoginSession(session_id=session_id, username=bundle.username, expires_at=expires_at)

    def deactivate_session(self, session_id: str) -> None:
        """软失效会话：UPDATE rc_auth_session SET is_active=0，不删行。

        被 logout() 调用，也被 get_valid_session 在过期/校验失败时调用。
        """
        execute_write(
            """
            UPDATE rc_auth_session
            SET is_active = 0
            WHERE session_id_hash = :session_id_hash
            """,
            {"session_id_hash": self.session_hash(session_id)},
        )

    def get_valid_session(self, session_id: str, client: DtStackAuthClient) -> AuthSession | None:
        """从 rc_auth_session 读取有效会话：查库 → 验过期 → 解密 Cookie → 校验/续期。

        流程：
          1. 按 session_id_hash 查 rc_auth_session（is_active=1）
          2. cookie_expires_at 已过期 → deactivate + 返回 None
          3. decrypt_cookies 解出生产 Cookie
          4. last_validated_at 超过 auth_validation_interval_minutes（默认 10 分钟）
             → verify_cookies 校验 → 失效则 deactivate + 返回 None
             → 有效则 UPDATE last_validated_at 刷新校验时间
          5. 返回 AuthSession（username + prod_env_id + cookies）

        被 get_current_session() 菜用，也是中间件 require_authenticated_session 的核心逻辑。
        """
        row = fetch_one(
            """
            SELECT session_id_hash, username, prod_env_id, cookie_ciphertext,
                   cookie_expires_at, last_validated_at
            FROM rc_auth_session
            WHERE session_id_hash = :session_id_hash AND is_active = 1
            LIMIT 1
            """,
            {"session_id_hash": self.session_hash(session_id)},
        )
        if not row:
            return None

        # 检查会话是否过期
        expires_at = parse_datetime(row.get("cookie_expires_at"))
        if expires_at and expires_at <= utcnow():
            self.deactivate_session(session_id)
            return None

        # 解密生产 Cookie
        cookies = self.decrypt_cookies(str(row["cookie_ciphertext"]))

        # 超过校验间隔则重新验证 Cookie 有效性
        last_validated_at = parse_datetime(row.get("last_validated_at"))
        stale_after = timedelta(minutes=settings.auth_validation_interval_minutes)
        if not last_validated_at or utcnow() - last_validated_at >= stale_after:
            if not client.verify_cookies(cookies):
                self.deactivate_session(session_id)
                return None
            # 校验通过，刷新 last_validated_at
            execute_write(
                "UPDATE rc_auth_session SET last_validated_at = :last_validated_at WHERE session_id_hash = :session_id_hash",
                {"last_validated_at": utcnow(), "session_id_hash": row["session_id_hash"]},
            )

        return AuthSession(username=str(row["username"]), prod_env_id=int(row["prod_env_id"]), cookies=cookies)


# 模块级单例，main.py 通过顶层函数间接使用
dtstack_client = DtStackAuthClient()
session_store = AuthSessionStore()


def get_captcha_payload() -> CaptchaPayload:
    """获取生产数栈验证码 + SM2 公钥。

    调用链：get_captcha_payload → dtstack_client.fetch_captcha → get_production_environment → requests HTTP
    被 main.py /auth/captcha 路由调用，结果返回给前端展示验证码图片。
    """
    return dtstack_client.fetch_captcha(get_production_environment())


def login_to_production(username: str, encrypted_password: str, verify_code: str, key: str) -> LoginSession:
    """登录生产数栈并创建本系统会话。

    调用链：login_to_production → dtstack_client.login（POST 生产登录）
           → session_store.create_session（加密 Cookie 落库 rc_auth_session）
    被 main.py /login POST 路由调用，成功后写 rc_session Cookie。
    """
    bundle = dtstack_client.login(username=username, encrypted_password=encrypted_password, verify_code=verify_code, key=key)
    return session_store.create_session(bundle)


def get_current_session(request: Request) -> AuthSession | None:
    """从请求的 rc_session Cookie 中获取有效会话。

    调用链：get_current_session → session_store.get_valid_session → rc_auth_session 查库 + 校验
    被 main.py 鉴权中间件 require_authenticated_session 调用。
    无 Cookie 或会话失效返回 None，中间件会渲染 permission.html (403)。
    """
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_id:
        return None
    return session_store.get_valid_session(session_id, dtstack_client)


def logout(session_id: str | None) -> None:
    """登出：软失效 rc_auth_session（is_active=0），删除浏览器 Cookie 由 main.py 处理。

    被 main.py /logout POST 路由调用。
    """
    if session_id:
        session_store.deactivate_session(session_id)
