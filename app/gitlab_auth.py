"""
gitlab_auth — GitLab Web 登录与项目访问检测

调用链位置：
  本模块 ← main.py（直接调用 check_default_project_access，
            用于 /config/gitlab/test 和 /config/gitlab/test-project 路由）
  本模块 ← main.py（直接引用 GitLabLoginError 异常类）
  本模块 → repositories.py（default_gitlab_config 回调 get_saved_gitlab_config，
            读取 rc_gitlab_credential 中加密保存的凭据）

核心流程：
  1. 从配置页保存的 GitLab 凭据（或 .env 回退）构造 GitLabLoginConfig
  2. 用 requests.Session 访问 /users/sign_in 页面，解析 authenticity_token
  3. POST 登录表单（username/password），获取登录态 Session
  4. 用登录态 Session 访问目标项目 URL，判断 200/403/404/重定向回登录页
  5. 结果脱敏（sanitize_message）后返回 GitLabProjectAccessResult

对外暴露：
  - GitLabLoginError : 异常类，被 main.py 捕获展示
  - check_default_project_access(project_url) : 顶层入口，检测 GitLab 项目访问权限
"""
from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlparse

from app.config import settings


class GitLabLoginError(RuntimeError):
    """GitLab 登录链路中的可展示错误。被 main.py 捕获后展示给用户。"""


class AuthenticityTokenParser(HTMLParser):
    """从 GitLab 登录页 HTML 中提取 authenticity_token，用于 POST 登录表单。

    被 extract_authenticity_token 调用，也在 GitLabLoginService.login 内部使用。
    """
    def __init__(self) -> None:
        super().__init__()
        self.token = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # 只提取 name="authenticity_token" 的 input 元素的 value 属性
        if tag != "input" or self.token:
            return
        attr_map = {key: value or "" for key, value in attrs}
        if attr_map.get("name") == "authenticity_token":
            self.token = attr_map.get("value", "")


class FlashMessageParser(HTMLParser):
    """提取 GitLab 登录页上的失败提示，避免只返回泛化错误。

    被 extract_flash_message 调用，也在 GitLabLoginService.login 内部使用。
    监听的 CSS class：flash-alert / flash-error / alert-danger / gl-alert-danger。
    """

    FLASH_CLASS_NAMES = {"flash-alert", "flash-error", "alert-danger", "gl-alert-danger"}

    def __init__(self) -> None:
        super().__init__()
        self._capture_depth = 0  # 嵌套深度计数器，>0 表示正在采集 flash 内容
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # 如果已在采集深度内，继续计数
        if self._capture_depth:
            self._capture_depth += 1
            return
        # 检查是否匹配 flash CSS class
        attr_map = {key: value or "" for key, value in attrs}
        class_names = set(attr_map.get("class", "").split())
        if class_names & self.FLASH_CLASS_NAMES:
            self._capture_depth = 1

    def handle_data(self, data: str) -> None:
        # 只在采集深度内收集文本
        if self._capture_depth and data.strip():
            self._parts.append(data.strip())

    def handle_endtag(self, tag: str) -> None:
        if self._capture_depth:
            self._capture_depth -= 1

    @property
    def message(self) -> str:
        # 合并所有采集到的文本片段，压缩多余空格
        return " ".join(" ".join(self._parts).split())


@dataclass(frozen=True)
class GitLabLoginConfig:
    """GitLab 登录配置，包含地址、账号密码和超时时间。

    被 default_gitlab_config() 构造，传入 GitLabLoginService。
    """
    base_url: str
    username: str
    password: str
    timeout_seconds: int = 10


@dataclass(frozen=True)
class GitLabProjectAccessResult:
    """GitLab 项目访问检测结果，包含登录状态和项目权限信息。

    被 check_default_project_access 返回，main.py 用来展示检测结果。
    """
    login_success: bool
    project_accessible: bool
    project_url: str
    final_url: str
    project_path: str
    status_code: int
    message: str


def extract_authenticity_token(html: str) -> str:
    """从 GitLab 登录页 HTML 中提取 authenticity_token。

    被 GitLabLoginService.login 调用，提取的 token 用于 POST 登录表单。
    """
    parser = AuthenticityTokenParser()
    parser.feed(html)
    return parser.token


def extract_flash_message(html: str) -> str:
    """从 GitLab 登录页 HTML 中提取失败提示信息。

    被 GitLabLoginService.login 调用，登录失败时提取具体错误原因。
    """
    parser = FlashMessageParser()
    parser.feed(html)
    return parser.message


def project_path_from_url(project_url: str) -> str:
    """从项目 URL 中提取 group/project 路径（去掉 .git 后缀）。

    被 GitLabLoginService.check_project_access 调用，用于在页面内容中匹配项目。
    """
    parsed = urlparse(project_url)
    return parsed.path.strip("/").removesuffix(".git")


def page_contains_project(page_text: str, project_path: str) -> bool:
    """GitLab 页面结构会随版本变化，第一版只校验路径或项目名任一命中。

    被 GitLabLoginService.check_project_access 调用，判断项目页面是否包含目标项目信息。
    """
    if not project_path:
        return False
    project_name = project_path.split("/")[-1]
    return project_path in page_text or project_name in page_text


def sanitize_message(message: str, config: GitLabLoginConfig) -> str:
    """把错误信息中的 username/password 替换为 ***，防止敏感信息暴露给页面。

    被 GitLabLoginService._result / check_project_access 调用。
    """
    sanitized = message
    for secret in [config.password, config.username]:
        if secret:
            sanitized = sanitized.replace(secret, "***")
    return sanitized


def default_gitlab_config() -> GitLabLoginConfig:
    """构造 GitLab 登录配置，优先取配置页保存的凭据，回退到 .env。

    调用链：check_default_project_access → default_gitlab_config → repositories.get_saved_gitlab_config
    凭据来源优先级：rc_gitlab_credential 表（加密密码）→ .env → 抛错提示先配置
    """
    try:
        # 回调 repositories，从 rc_gitlab_credential 读取已保存的加密凭据
        from app.repositories import get_saved_gitlab_config

        saved = get_saved_gitlab_config()
        return GitLabLoginConfig(
            base_url=str(saved.get("base_url") or settings.gitlab_base_url),
            username=str(saved["username"]),
            password=str(saved["password"]),
            timeout_seconds=settings.gitlab_login_timeout_seconds,
        )
    except Exception:
        pass

    # 回退到 .env 中的 GitLab 账号密码
    if not settings.gitlab_username or not settings.gitlab_password:
        raise GitLabLoginError("请先在配置页保存 GitLab 全局凭据。")
    return GitLabLoginConfig(
        base_url=settings.gitlab_base_url,
        username=settings.gitlab_username,
        password=settings.gitlab_password,
        timeout_seconds=settings.gitlab_login_timeout_seconds,
    )


class GitLabLoginService:
    """通过 GitLab Web 登录页建立会话，并验证指定项目页面可访问。

    被 check_default_project_access 调用，是整个 GitLab 检测的核心执行类。
    主要方法：login() → 登录 GitLab；check_project_access() → 检测项目访问权限。
    """

    def __init__(self, config: GitLabLoginConfig, session_factory=None) -> None:
        self.config = config
        self._session_factory = session_factory  # 可注入自定义 Session（测试用）

    def _requests(self):
        """懒加载 requests 库，缺失时抛 GitLabLoginError。"""
        try:
            import requests
        except ImportError as exc:  # pragma: no cover - 真实环境依赖
            raise GitLabLoginError("缺少 requests 依赖，请先安装 requirements.txt。") from exc
        return requests

    def _new_session(self):
        """创建带浏览器式 headers 的 requests.Session，用于模拟浏览器访问 GitLab。

        被 self.login 调用，创建新会话后先 GET 登录页再 POST 登录表单。
        """
        if self._session_factory:
            return self._session_factory()
        session = self._requests().Session()
        session.headers.update({
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
            ),
        })
        return session

    def login(self):
        """执行 GitLab Web 登录流程：GET 登录页 → 提取 token → POST 登录表单 → 校验成功。

        返回已登录的 requests.Session，供后续 check_project_access 使用。
        失败时抛 GitLabLoginError，包含具体错误信息（从页面 flash 提示中提取）。
        """
        session = self._new_session()
        login_url = f"{self.config.base_url.rstrip('/')}/users/sign_in"

        # 第一步：GET 登录页，提取 authenticity_token
        login_page = session.get(login_url, timeout=self.config.timeout_seconds)
        login_page.raise_for_status()
        token = extract_authenticity_token(login_page.text)
        if not token:
            raise GitLabLoginError("GitLab 登录页缺少 authenticity_token，页面结构可能已变化。")

        # 第二步：POST 登录表单
        resp = session.post(
            login_url,
            data={
                "utf8": "✓",
                "authenticity_token": token,
                "user[login]": self.config.username,
                "user[password]": self.config.password,
                "user[remember_me]": "0",
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": login_url,
            },
            timeout=self.config.timeout_seconds,
            allow_redirects=True,
        )
        resp.raise_for_status()

        # 第三步：判断登录是否成功——如果最终 URL 仍含 /users/sign_in 则失败
        if "/users/sign_in" in str(getattr(resp, "url", "")):
            flash_message = extract_flash_message(getattr(resp, "text", "") or "")
            detail = (
                f"GitLab 登录失败：{flash_message}"
                if flash_message
                else "GitLab 登录失败，请检查账号密码或是否需要额外认证。"
            )
            raise GitLabLoginError(detail)
        return session

    def check_project_access(self, project_url: str) -> GitLabProjectAccessResult:
        """检测 GitLab 项目访问权限：先登录，再用登录态访问项目页面。

        判断逻辑：
          - 最终 URL 重定向回 /users/sign_in → 登录态失效
          - HTTP 404 → 项目不存在或无权限
          - HTTP 403 → 无项目访问权限
          - HTTP 非 200 → 其他访问失败
          - 页面内容不含项目路径 → 项目不可访问
          - 否则 → 项目可访问

        被 check_default_project_access 调用，结果返回给 main.py 展示。
        """
        project_path = project_path_from_url(project_url)
        try:
            session = self.login()
            resp = session.get(project_url, timeout=self.config.timeout_seconds, allow_redirects=True)
            final_url = str(getattr(resp, "url", project_url))
            status_code = int(getattr(resp, "status_code", 0))
            page_text = getattr(resp, "text", "") or ""
            login_redirected = "/users/sign_in" in final_url

            # 按不同 HTTP 状态和页面行为判断项目访问权限
            if login_redirected:
                return self._result(False, project_url, final_url, project_path, status_code, "GitLab 登录态失效，项目访问被重定向到登录页。")
            if status_code == 404:
                return self._result(False, project_url, final_url, project_path, status_code, "GitLab 项目不存在或当前账号无权限。")
            if status_code == 403:
                return self._result(False, project_url, final_url, project_path, status_code, "当前 GitLab 账号无项目访问权限。")
            if status_code != 200:
                return self._result(False, project_url, final_url, project_path, status_code, f"GitLab 项目访问失败，HTTP {status_code}。")
            if project_path and not page_contains_project(page_text, project_path):
                return self._result(False, project_url, final_url, project_path, status_code, "项目页面内容未匹配到目标项目路径。")

            # 登录成功 + 页面包含项目信息 → 项目可访问
            return GitLabProjectAccessResult(
                login_success=True,
                project_accessible=True,
                project_url=project_url,
                final_url=final_url,
                project_path=project_path,
                status_code=status_code,
                message="GitLab 登录成功，项目可访问。",
            )
        except Exception as exc:
            # 登录失败时返回 login_success=False 的结果，message 脱敏
            return GitLabProjectAccessResult(
                login_success=False,
                project_accessible=False,
                project_url=project_url,
                final_url="",
                project_path=project_path,
                status_code=0,
                message=sanitize_message(str(exc), self.config),
            )

    def _result(
        self,
        accessible: bool,
        project_url: str,
        final_url: str,
        project_path: str,
        status_code: int,
        message: str,
    ) -> GitLabProjectAccessResult:
        """构造 login_success=True 的检测结果，message 经 sanitize_message 脱敏。

        被 check_project_access 内部调用，统一构造"登录成功但项目可能不可访问"的结果。
        """
        return GitLabProjectAccessResult(
            login_success=True,
            project_accessible=accessible,
            project_url=project_url,
            final_url=final_url,
            project_path=project_path,
            status_code=status_code,
            message=sanitize_message(message, self.config),
        )


def check_default_project_access(project_url: str) -> GitLabProjectAccessResult:
    """GitLab 项目访问检测的顶层入口。

    调用链：check_default_project_access → default_gitlab_config → repositories.get_saved_gitlab_config
           → GitLabLoginService.check_project_access → login → requests HTTP

    被 main.py 的 /config/gitlab/test 和 /config/gitlab/test-project 路由直接调用。
    """
    return GitLabLoginService(default_gitlab_config()).check_project_access(project_url)
