"""
main — FastAPI 应用入口：路由定义、鉴权中间件、模板渲染、后台定时任务

调用链总览：
  本模块是唯一的业务入口，所有 HTTP 请求都经这里分发。
  本模块 → auth.py（鉴权中间件、登录/登出/验证码）
  本模块 → gitlab_auth.py（GitLab 凭据检测）
  本模块 → repositories.py（所有页面数据查询与写库，间接调用 git_release / release_draft / db）
  本模块 → config.py（settings 配置读取）

路由分组：
  登录鉴权：/、/login、/auth/captcha、/logout、/permission
  首页：/home
  任务列表：/tasks
  发布确认：/confirm、/confirm/publish
  发布记录：/records、/records/{batch_id}
  配置管理：/config 及其子路由（GitLab凭据、Git仓库、元数据同步等）
  用户管理：/users
  Git 代码管理：/git

全局行为：
  - 鉴权中间件(require_authenticated_session)：白名单路径免登录，其余需有效 rc_session Cookie
  - 后台定时任务(git_auto_refresh_loop)：每 N 秒刷新全部 Git 提交缓存
  - 模板渲染使用 Jinja2Templates，静态文件挂载到 /static
"""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import auth
from app.config import settings
from app.gitlab_auth import GitLabLoginError, check_default_project_access
from app.mock_data import (
    ENV_LABELS as MOCK_ENV_LABELS,
    NAV_ITEMS as MOCK_NAV_ITEMS,
    PROJECT_SPACES as MOCK_PROJECT_SPACES,
    mock_base_context,
    mock_confirm_context,
    mock_config_context,
    mock_git_context,
    mock_home_context,
    mock_records_context,
    mock_task_list_context,
    mock_users_context,
    pagination_meta as mock_pagination_meta,
)
from app.repositories import (
    NAV_ITEMS,
    clear_datasource_mapping,
    check_gitlab_api_project,
    get_datasource_config_rows,
    environment_labels,
    get_datasource_mappings,
    get_datasource_options,
    get_datasource_resources,
    get_directory_sync_summary,
    get_git_info,
    get_git_diff_task_plan,
    get_gitlab_credential_view,
    get_git_repo_bindings,
    get_home_overview,
    get_latest_release_draft_preview,
    load_git_branches,
    get_project_mappings,
    get_project_mapping_rows,
    get_project_space_options,
    get_record_detail,
    get_latest_release_draft_model,
    get_release_draft_model,
    get_records,
    get_record_stats,
    get_source_project_spaces,
    get_task_stats,
    get_tasks,
    mapped_target_project_name,
    persist_release_draft,
    refresh_all_git_repos,
    refresh_git_repo_commits,
    record_gitlab_check_status,
    save_datasource_mapping,
    save_gitlab_credential,
    save_git_repo_binding,
    save_project_mapping,
    selected_project_space,
    simulate_release_from_draft,
    sync_config_metadata,
    sync_dtstack_metadata,
    toggle_datasource_mapping,
    toggle_project_mapping,
    get_users,
)


# app/ 目录，用于定位 templates/ 和 static/
BASE_DIR = Path(__file__).resolve().parent


def config_redirect(message: str, status: str = "", anchor: str = "") -> RedirectResponse:
    """构造带 config_message/config_status 查询串的 303 重定向到 /config。

    被配置管理下的所有 POST 路路由统一使用，避免在每个路由里重复拼 URL。
    """
    query = f"config_message={quote(message)}"
    if status:
        query += f"&config_status={quote(status)}"
    url = f"/config?{query}"
    if anchor:
        url += f"#{anchor}"
    return RedirectResponse(url=url, status_code=303)


async def git_auto_refresh_loop() -> None:
    """后台定时循环：每 git_auto_refresh_interval_seconds 秒刷新全部 Git 提交缓存。

    只维护 Web 元数据库中的 Git 提交缓存，不触发数栈发布 API。
    定时任务异常不影响页面请求；具体仓库错误会写入 rc_git_repo 刷新状态。
    被 lifespan 启动时创建为 asyncio.Task。
    """
    interval = settings.git_auto_refresh_interval_seconds
    if interval <= 0:
        return
    while True:
        await asyncio.sleep(interval)
        try:
            refresh_all_git_repos()
        except Exception:
            # 定时任务异常不影响页面请求；具体仓库错误会写入 rc_git_repo 刷新状态。
            pass


async def metadata_auto_sync_loop() -> None:
    """后台定时同步测试/生产元数据快照，只写平台库，不在页面请求链路里执行。"""
    interval = settings.metadata_auto_sync_interval_seconds
    if interval <= 0:
        return
    while True:
        await asyncio.sleep(interval)
        try:
            sync_dtstack_metadata()
        except Exception:
            # 同步异常只影响本次定时任务，不阻断页面访问。
            pass


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """FastAPI 生命周期管理：启动时创建后台 Git 自动刷新任务，退出时 cancel。

    只有 git_auto_refresh_interval_seconds > 0 时才启动定时刷新。
    """
    tasks: list[asyncio.Task[Any]] = []
    if settings.git_auto_refresh_interval_seconds > 0:
        tasks.append(asyncio.create_task(git_auto_refresh_loop()))
    if settings.metadata_auto_sync_interval_seconds > 0:
        tasks.append(asyncio.create_task(metadata_auto_sync_loop()))
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()


# FastAPI 应用实例，挂载静态文件和 Jinja2 模板引擎
app = FastAPI(title="数栈实时任务发布台", version="0.4.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.middleware("http")
async def require_authenticated_session(request: Request, call_next):
    """全局鉴权中间件：白名单路径免登录，其余需有效 rc_session Cookie。

    流程：
      1. auth.public_path 判断是否在白名单（/static/*、/、/login 等）
      2. 白名单 → 直接放行
      3. 非 白名单 → auth.get_current_session 读 rc_session Cookie
      4. 无会话 → 渲染 permission.html (403)
      5. 有会话 → 挂到 request.state.auth_session，供下游路由取用

    use_mock_data 模式下跳过鉴权，方便前端开发调试。
    """
    if settings.use_mock_data:
        return await call_next(request)

    if auth.public_path(request.url.path):
        return await call_next(request)

    session = auth.get_current_session(request)
    if not session:
        return templates.TemplateResponse(
            request,
            "permission.html",
            {"request": request},
            status_code=403,
        )

    # 有效会话挂到 request.state，下游路由通过 request.state.auth_session.username 取用户名
    request.state.auth_session = session
    return await call_next(request)


def pagination_meta(total: int, page_size: int = 10) -> dict[str, int]:
    """由 total/page_size 算 page_count，返回分页元信息字典。被多个页面路由调用。"""
    page_count = max(1, (total + page_size - 1) // page_size)
    return {"total": total, "page_size": page_size, "page_count": page_count}


def confirm_selection(tasks: list[dict[str, Any]], selected_ids: str | None) -> list[dict[str, Any]]:
    """按 selected_ids（逗号串）过滤任务；未选则默认前 3 条。被 confirm 路由调用。"""
    requested_ids = [item for item in (selected_ids or "").split(",") if item]
    if not requested_ids:
        return tasks[:3]

    requested_set = set(requested_ids)
    selected = [task for task in tasks if str(task["id"]) in requested_set]
    return selected or tasks[:3]


def confirm_step(step: str | None) -> int:
    """把 step 参数限制在 1-4 之间（4 步发布流程）。被 confirm 路路由调用。"""
    try:
        value = int(step or 1)
    except ValueError:
        return 1
    return min(4, max(1, value))


def parse_form_value(data: dict[str, list[str]], key: str) -> str:
    """从 parse_qs 结果取单值并 strip。被 POST 路路由解析表单时调用。"""
    return (data.get(key) or [""])[0].strip()


def parse_form_values(data: dict[str, list[str]], key: str) -> list[str]:
    """从 parse_qs 结果取多值列表并 strip 过滤空串。被 confirm/publish 路由调用。"""
    return [item.strip() for item in data.get(key, []) if item.strip()]


def base_context(active: str, request: Request) -> dict[str, Any]:
    """构造所有页面通用上下文：导航栏、活跃页、项目空间切换器、用户信息。

    项目空间切换器只在 tasks/confirm 页显示，这两页需要确定发布来源空间与对应 Git 仓库。
    """
    session = getattr(request.state, "auth_session", None)
    show_project_space_switcher = active in {"tasks", "confirm"}
    project_spaces = get_source_project_spaces() if show_project_space_switcher else []
    current_project_space = (
        selected_project_space(request.query_params.get("project_space_id")) if show_project_space_switcher else {}
    )
    return {
        "nav_items": NAV_ITEMS,
        "active": active,
        "current_project": current_project_space.get("project_name") or "全局视图",
        "project_spaces": project_spaces,
        "selected_project_space": current_project_space,
        "show_project_space_switcher": show_project_space_switcher,
        "user_name": session.username if session else "admin",
        "user_role": "超级管理员",
    }


def login_context(request: Request, error: str = "", username: str = "") -> dict[str, Any]:
    """构造登录页上下文：request、error、username（回退读 rc_username Cookie）。"""
    return {
        "request": request,
        "error": error,
        "username": username or request.cookies.get(auth.REMEMBER_USERNAME_COOKIE, ""),
    }


# ──── 登录鉴权路由 ────


@app.get("/")
def index() -> RedirectResponse:
    """根路径重定向到 /home。"""
    return RedirectResponse(url="/home")


@app.get("/login")
def login_page(request: Request):
    """登录页：已登录则跳 /home；否则渲染 login.html。

    调用：auth.get_current_session（判断是否已登录）
    """
    if auth.get_current_session(request):
        return RedirectResponse(url="/home")
    return templates.TemplateResponse(request, "login.html", login_context(request))


@app.get("/auth/captcha")
def captcha():
    """获取生产数栈验证码图片 + SM2 公钥。

    调用：auth.get_captcha_payload → dtstack_client.fetch_captcha → requests HTTP
    返回 JSON：key、content_type、image_base64、public_key
    """
    try:
        payload = auth.get_captcha_payload()
    except Exception as exc:
        return JSONResponse({"success": False, "message": str(exc)}, status_code=502)
    return JSONResponse({
        "success": True,
        "key": payload.key,
        "content_type": payload.content_type,
        "image_base64": payload.image_base64,
        "public_key": payload.public_key,
    })


@app.post("/login")
async def login_submit(request: Request):
    """提交生产数栈登录：解析表单 → 校验密文格式 → 登录 → 写 Cookie → 跳转 /home。

    调用：auth.login_to_production（登录生产 → 加密 Cookie 落库 rc_auth_session）
    关键安全措施：禁止明文 password 字段（只接受 password_ciphertext）
    成功后写 rc_session Cookie（httponly/samesite/lax/secure 可配置），
    "记住我"额外写 rc_username Cookie（30 天）。
    """
    form = parse_qs((await request.body()).decode("utf-8"))
    username = parse_form_value(form, "username")
    encrypted_password = parse_form_value(form, "password_ciphertext")
    verify_code = parse_form_value(form, "verify_code")
    captcha_key = parse_form_value(form, "key")
    remember = parse_form_value(form, "remember") == "on"

    # 安全措施：禁止明文密码提交，前端必须用 SM2 加密后只传 password_ciphertext
    if "password" in form:
        return templates.TemplateResponse(
            request,
            "login.html",
            login_context(request, "登录请求不能提交明文密码，请刷新页面后重试。", username),
            status_code=400,
        )

    if not all([username, encrypted_password, verify_code, captcha_key]):
        return templates.TemplateResponse(
            request,
            "login.html",
            login_context(request, "请填写账号、密码和验证码。", username),
            status_code=400,
        )

    # 登录生产数栈 → 加密 Cookie 落库 → 返回 LoginSession
    try:
        login_session = auth.login_to_production(username, encrypted_password, verify_code, captcha_key)
    except Exception as exc:
        return templates.TemplateResponse(
            request,
            "login.html",
            login_context(request, str(exc), username),
            status_code=400,
        )

    # 登录成功：写本系统 rc_session Cookie，跳转到 /home
    response = RedirectResponse(url="/home", status_code=303)
    max_age = settings.auth_session_ttl_hours * 3600 if remember else None  # "记住我"时设置 max_age，否则浏览器关闭即失效
    response.set_cookie(
        auth.SESSION_COOKIE_NAME,
        login_session.session_id,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=settings.auth_cookie_secure,
    )
    if remember:
        response.set_cookie(auth.REMEMBER_USERNAME_COOKIE, username, max_age=30 * 24 * 3600, samesite="lax")
    else:
        response.delete_cookie(auth.REMEMBER_USERNAME_COOKIE)
    return response


@app.post("/logout")
def logout(request: Request):
    """登出：软失效 rc_auth_session → 删除 rc_session Cookie → 跳 /login。

    调用：auth.logout（UPDATE rc_auth_session SET is_active=0）
    """
    auth.logout(request.cookies.get(auth.SESSION_COOKIE_NAME))
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(auth.SESSION_COOKIE_NAME)
    return response


@app.get("/permission")
def permission_page(request: Request):
    """403 权限页：未登录访问业务页面时由鉴权中间件渲染此页。"""
    return templates.TemplateResponse(
        request,
        "permission.html",
        {"request": request},
        status_code=403,
    )


# ──── 首页路由 ────


@app.get("/home")
def home(request: Request):
    """首页/发布概览：按 view 参数（all/realtime/offline/project）展示统计卡片、最近任务和发布柱状图。

    调用：get_home_overview → 内部调 repositories 各函数
    """
    home_view = request.query_params.get("view") or "all"
    if settings.use_mock_data:
        context = mock_base_context("home")
        context["request"] = request
        context.update(mock_home_context(home_view))
        return templates.TemplateResponse(request, "home.html", context)
    overview = get_home_overview(home_view)
    context = base_context("home", request)
    context.update({
        "request": request,
        "home_view": overview["home_view"],
        "home_view_tabs": overview["home_view_tabs"],
        "home_stats": overview["home_stats"],
        "records": overview["records"],
        "tasks": overview["tasks"],
        "project_spaces_overview": overview["project_spaces"],
        "datasource_summary": overview["datasource_summary"],
        "daily_release_chart": overview["daily_release_chart"],
        "last_metadata_sync_at": overview["last_metadata_sync_at"],
    })
    return templates.TemplateResponse(request, "home.html", context)


# ──── 任务列表路由 ────


@app.get("/tasks")
def task_list(request: Request):
    """任务列表页：选项目空间 → 拉 Git 差异 → 展示待发布任务。

    调用链：get_git_diff_task_plan → repositories → build_git_snapshot + build_changed_task_candidates
    refresh=1 时 → persist_release_draft → 写草稿到 rc_release_draft/rc_release_draft_task/rc_release_validation
    """
    # 任务列表以"项目空间 + Git 差异"为入口；刷新时尝试写入发布草稿，供确认页复用。
    project_space_id = request.query_params.get("project_space_id")
    if settings.use_mock_data:
        context = mock_base_context("tasks", project_space_id)
        context["request"] = request
        context.update(mock_task_list_context(project_space_id))
        return templates.TemplateResponse(request, "task_list.html", context)
    refresh_git = request.query_params.get("refresh") == "1"
    draft_info = {"draft_id": request.query_params.get("draft_id", ""), "status": "", "message": ""}
    selected_space = selected_project_space(project_space_id)
    latest_draft_model = get_latest_release_draft_preview(selected_space.get("id")) if selected_space and not refresh_git else {}
    latest_draft = latest_draft_model.get("draft") if isinstance(latest_draft_model, dict) else {}
    if latest_draft and not refresh_git:
        tasks = latest_draft_model.get("tasks") or []
        task_plan = {
            "project_spaces": get_source_project_spaces(),
            "selected_project_space": selected_space,
            "tasks": tasks,
            "git_context": {
                "status": "success",
                "message": "",
                "repo_url": selected_space.get("repo_url") or "",
                "branch": selected_space.get("current_branch") or selected_space.get("default_branch") or "",
                "module_type": selected_space.get("repo_module_type") or "",
                "module_label": selected_space.get("module_label") or "",
                "worktree": "",
                "last_success_commit": latest_draft.get("base_commit") or "-",
                "current_commit": latest_draft.get("head_commit") or "-",
                "changed_files": [task.get("git_path") or "" for task in tasks if task.get("git_path")],
                "changed_file_count": len(tasks),
                "task_candidate_count": len(tasks),
                "refresh_git": False,
            },
        }
        draft_info = {
            "draft_id": str(latest_draft.get("id") or ""),
            "status": str(latest_draft.get("scan_status") or ""),
            "message": "",
        }
    elif selected_space and not refresh_git:
        # 普通切换项目空间时只读取平台库缓存，不主动触发 Git clone/diff。
        # 只有用户点击“刷新 Git 差异”时，才执行真正的 Git 扫描。
        tasks = []
        task_plan = {
            "project_spaces": get_source_project_spaces(),
            "selected_project_space": selected_space,
            "tasks": tasks,
            "git_context": {
                "status": "idle",
                "message": "当前项目空间暂无缓存草稿，请点击“刷新 Git 差异”生成待发布任务。",
                "repo_url": selected_space.get("repo_url") or "",
                "branch": selected_space.get("current_branch") or selected_space.get("default_branch") or "",
                "module_type": selected_space.get("project_type") or "",
                "module_label": selected_space.get("module_label") or "",
                "worktree": "",
                "last_success_commit": "-",
                "current_commit": "-",
                "changed_files": [],
                "changed_file_count": 0,
                "task_candidate_count": 0,
                "refresh_git": False,
            },
        }
        draft_info = {"draft_id": "", "status": "", "message": ""}
    else:
        task_plan = get_git_diff_task_plan(project_space_id, refresh_git)
        tasks = task_plan["tasks"]
        if refresh_git:
            # 只有主动刷新时才重新扫描 Git 并落草稿，普通打开页面直接读最近一次草稿。
            session = getattr(request.state, "auth_session", None)
            draft_info = persist_release_draft(task_plan, session.username if session else "admin")
        selected_space = task_plan["selected_project_space"]
    context = base_context("tasks", request)
    context.update({
        "request": request,
        "stats": get_task_stats(tasks),
        "tasks": tasks,
        "pagination": pagination_meta(len(tasks)),
        "project_spaces": task_plan["project_spaces"],
        "selected_project_space": selected_space,
        "git_context": task_plan["git_context"],
        "draft_info": draft_info,
    })
    return templates.TemplateResponse(request, "task_list.html", context)


# ──── 发布确认/草稿路由 ────


@app.get("/confirm")
def confirm(request: Request):
    """发布确认页：优先读草稿快照，无 draft_id 时回退旧页面逻辑。

    调用：get_release_draft_model（读 rc_release_draft/rc_release_draft_task/rc_release_validation）
         get_latest_release_draft_model（无 draft_id 时优先读最近草稿）
         get_datasource_mappings（展示数据源映射）
    """
    # 发布确认页不应再次触发 Git 扫描。
    # 优先规则：
    #   1. URL 带 draft_id → 直接读取指定草稿
    #   2. 未带 draft_id，但当前项目空间已有最近草稿 → 读取最近草稿
    #   3. 仍无草稿 → 展示空确认页，不再回退到 get_tasks() 触发 Git 扫描
    project_space_id = request.query_params.get("project_space_id")
    step = confirm_step(request.query_params.get("step"))
    if settings.use_mock_data:
        context = mock_base_context("confirm", project_space_id)
        context["request"] = request
        context.update(mock_confirm_context(step, project_space_id))
        return templates.TemplateResponse(request, "confirm.html", context)
    draft_id = request.query_params.get("draft_id")
    selected_space = selected_project_space(project_space_id)
    if draft_id:
        draft_model = get_release_draft_model(draft_id)
    elif selected_space:
        draft_model = get_latest_release_draft_model(selected_space.get("id"))
        draft_id = str((draft_model.get("draft") or {}).get("id") or "")
    else:
        draft_model = {}

    all_tasks = draft_model.get("tasks") if draft_model else []
    selected_tasks = confirm_selection(all_tasks, request.query_params.get("task_ids"))
    selected_task_ids = ",".join(str(task["id"]) for task in selected_tasks)
    step = confirm_step(request.query_params.get("step"))
    env_labels = environment_labels()
    context = base_context("confirm", request)
    context.update({
        "request": request,
        "step": step,
        "all_tasks": all_tasks,
        "tasks": selected_tasks,
        "selected_task_ids": selected_task_ids,
        "selected_task_id_list": selected_task_ids.split(",") if selected_task_ids else [],
        # 第 2 步才需要读取全量数据源映射，其他步骤避免额外查询。
        "mappings": get_datasource_mappings() if step == 2 else [],
        "draft": draft_model.get("draft", {}) if draft_model else {},
        "draft_id": draft_id or "",
        "env_labels": env_labels,
        "target_project_name": mapped_target_project_name(selected_space.get("id")) if selected_space else "-",
        # 第 2 步才展示校验项，其他步骤不加载校验分组。
        "validation_groups": draft_model.get("validation_groups", {}) if draft_model and step == 2 else {},
    })
    return templates.TemplateResponse(request, "confirm.html", context)


@app.post("/confirm/publish")
async def confirm_publish(request: Request):
    """执行发布确认：基于草稿生成发布记录（非 API 阶段，不调生产数栈 API）。

    调用：simulate_release_from_draft → 写 rc_release_batch/rc_release_task/rc_release_step_log
    当前阶段只生成发布记录和步骤日志，不创建或更新生产数栈任务。
    成功后跳转到发布记录详情页。
    """
    # 非 API 阶段只生成发布记录和步骤日志，不创建或更新生产数栈任务。
    form = parse_qs((await request.body()).decode("utf-8"))
    draft_id = parse_form_value(form, "draft_id")
    selected_task_ids = parse_form_values(form, "task_ids")
    session = getattr(request.state, "auth_session", None)
    result = simulate_release_from_draft(draft_id, selected_task_ids, session.username if session else "admin")
    batch_id = result.get("batch_id")
    if batch_id:
        return RedirectResponse(url=f"/records/{batch_id}", status_code=303)
    return RedirectResponse(url=f"/confirm?draft_id={quote(draft_id)}&step=2", status_code=303)


# ──── 发布记录路由 ────


@app.get("/records")
def records(request: Request):
    """发布记录列表页：展示所有发布批次、统计数据和分页。

    调用：get_records（读 rc_release_batch）、get_record_stats
    """
    keyword = request.query_params.get("keyword", "")
    if settings.use_mock_data:
        context = mock_base_context("records")
        context["request"] = request
        context.update(mock_records_context(keyword))
        return templates.TemplateResponse(request, "records.html", context)
    records_data = get_records(keyword)
    context = base_context("records", request)
    context.update({
        "request": request,
        "records": records_data,
        "keyword": keyword,
        "stats": get_record_stats(keyword),
        "pagination": pagination_meta(len(records_data)),
        "record_detail": {"record": {}, "tasks": [], "logs": []},
    })
    return templates.TemplateResponse(request, "records.html", context)


@app.get("/records/{batch_id}")
def record_detail(request: Request, batch_id: str):
    """发布记录详情页：展示指定批次的任务明细和步骤日志。

    调用：get_record_detail（读 rc_release_batch + rc_release_task + rc_release_step_log）
    """
    keyword = request.query_params.get("keyword", "")
    if settings.use_mock_data:
        context = mock_base_context("records")
        context["request"] = request
        context.update(mock_records_context(keyword, batch_id))
        return templates.TemplateResponse(request, "records.html", context)
    records_data = get_records(keyword)
    context = base_context("records", request)
    context.update({
        "request": request,
        "records": records_data,
        "keyword": keyword,
        "stats": get_record_stats(keyword),
        "pagination": pagination_meta(len(records_data)),
        "record_detail": get_record_detail(batch_id),
    })
    return templates.TemplateResponse(request, "records.html", context)


# ──── 配置管理路由 ────


@app.get("/config")
def config_page(request: Request):
    """配置管理页：聚合项目映射、数据源映射、Git 绑定、GitLab 凭据、目录同步等所有配置数据。

    调用：get_project_mappings / get_project_space_options / get_datasource_mappings /
         get_datasource_options / get_directory_sync_summary / get_git_repo_bindings /
         get_gitlab_credential_view
    """
    if settings.use_mock_data:
        context = mock_base_context("config")
        context["request"] = request
        context.update(mock_config_context())
        return templates.TemplateResponse(request, "config.html", context)
    env_labels = environment_labels()
    datasource_resources = get_datasource_resources()
    project_mappings = get_project_mapping_rows()
    datasource_mappings = get_datasource_config_rows()
    datasource_resources_payload = [
        {
            key: (
                value.isoformat(sep=" ") if hasattr(value, "isoformat")
                else value
            )
            for key, value in item.items()
        }
        for item in datasource_resources
    ]
    context = base_context("config", request)
    context.update({
        "request": request,
        "env_labels": env_labels,
        "project_mappings": project_mappings,
        "project_mapping_stats": {
            "total": len(project_mappings),
            "enabled": len([item for item in project_mappings if str(item.get("mapping_enabled", 1)) in {"1", "True", "true"} and item.get("target_space")]),
            "pending": len([item for item in project_mappings if not item.get("target_space") or str(item.get("status") or "") in {"pending", "disabled"}]),
            "offline": len([item for item in project_mappings if item.get("source_type") == "offline"]),
            "stream": len([item for item in project_mappings if item.get("source_type") != "offline"]),
        },
        "project_space_options": get_project_space_options(),
        "datasource_mappings": datasource_mappings,
        "datasource_mapping_stats": {
            "total": len(datasource_mappings),
            "enabled": len([item for item in datasource_mappings if int(item.get("mapping_enabled") or 0) == 1 and item.get("target")]),
            "pending": len([item for item in datasource_mappings if not item.get("target")]),
            "connected": len([item for item in datasource_mappings if item.get("connectivity_status") == "connected"]),
            "failed": len([item for item in datasource_mappings if item.get("connectivity_status") == "failed"]),
        },
        "datasource_mapping_records": get_datasource_mappings(),
        "datasource_options": get_datasource_options(),
        "datasource_resources": datasource_resources_payload,
        "directory_sync": get_directory_sync_summary(),
        "git_bindings": get_git_repo_bindings(),
        "sync_message": request.query_params.get("sync_message", ""),
        "config_message": request.query_params.get("config_message", ""),
        "config_status": request.query_params.get("config_status", ""),
        "gitlab_credential": get_gitlab_credential_view(),
        "gitlab_project_url": request.query_params.get("gitlab_project_url", settings.gitlab_default_project_url),
        "gitlab_check_message": request.query_params.get("gitlab_check_message", ""),
        "gitlab_check_status": request.query_params.get("gitlab_check_status", ""),
    })
    return templates.TemplateResponse(request, "config.html", context)


@app.post("/config/gitlab/save")
async def config_gitlab_save(request: Request):
    """保存 GitLab 全局凭据：支持用户名/密码与 Token 两种模式。

    调用：save_gitlab_credential → repositories → secret_crypto.encrypt_secret + db
    """
    form = parse_qs((await request.body()).decode("utf-8"))
    result = save_gitlab_credential(
        parse_form_value(form, "base_url") or settings.gitlab_base_url,
        parse_form_value(form, "username"),
        parse_form_value(form, "pass" + "word"),  # 拆开避免浏览器自动填充
        parse_form_value(form, "auth_mode") or "password",
        parse_form_value(form, "token"),
    )
    return config_redirect(str(result["message"]), str(result["status"]), "section-gitlab-creds")


@app.post("/config/gitlab/test")
async def config_gitlab_test(request: Request):
    """检测 GitLab 凭据：先保存 → 再用保存的凭据登录 GitLab 并访问默认项目 → 回写检测状态。

    调用：save_gitlab_credential → check_default_project_access → gitlab_auth → requests HTTP
         record_gitlab_check_status → UPDATE rc_gitlab_credential
    """
    form = parse_qs((await request.body()).decode("utf-8"))
    save_result = save_gitlab_credential(
        parse_form_value(form, "base_url") or settings.gitlab_base_url,
        parse_form_value(form, "username"),
        parse_form_value(form, "pass" + "word"),
        parse_form_value(form, "auth_mode") or "password",
        parse_form_value(form, "token"),
    )
    if save_result["status"] != "success":
        return config_redirect(str(save_result["message"]), str(save_result["status"]))

    auth_mode = parse_form_value(form, "auth_mode") or "password"
    if auth_mode == "token":
        result = check_gitlab_api_project(settings.gitlab_default_project_url)
        status = "success" if result["success"] else "failed"
        message = str(result["message"])
        return config_redirect(message, status, "section-gitlab-creds")

    # 用保存的凭据登录 GitLab 并检测默认项目访问权限
    try:
        result = check_default_project_access(settings.gitlab_default_project_url)
        status = "success" if result.login_success else "failed"
        message = result.message if result.login_success else f"GitLab 登录检测失败：{result.message}"
    except GitLabLoginError as exc:
        status = "failed"
        message = str(exc)
    # 回写检测状态到 rc_gitlab_credential
    record_gitlab_check_status(status, message)
    return config_redirect(message, status, "section-gitlab-creds")


@app.post("/config/git/branches")
async def config_git_branches(request: Request):
    """读取远端 Git 分支列表（git ls-remote --heads），返回 JSON。

    调用：load_git_branches → repositories → git_release.list_remote_branches → git 子进程
    """
    form = parse_qs((await request.body()).decode("utf-8"))
    result = load_git_branches(parse_form_value(form, "repo_url"))
    return JSONResponse(result)


@app.post("/config/git-repos/save")
async def config_git_repos_save(request: Request):
    """保存项目空间 Git 仓库绑定：校验跟踪分支 → 写入/更新 rc_git_repo。

    调用：save_git_repo_binding → repositories → git_release.validate_tracking_branch + db
    """
    form = parse_qs((await request.body()).decode("utf-8"))
    result = save_git_repo_binding(
        parse_form_value(form, "project_space_id"),
        parse_form_value(form, "repo_url"),
        parse_form_value(form, "branch"),
        parse_form_value(form, "module_type"),
    )
    return config_redirect(str(result["message"]), str(result["status"]), "section-git-bindings")


@app.post("/config/git-repos/refresh")
async def config_git_repos_refresh(request: Request):
    """刷新指定 Git 仓库的提交历史：clone/pull → 读取 commit log → 回写 rc_git_commit + rc_git_repo。

    调用：refresh_git_repo_commits → repositories → git_release.refresh_repo_commit_history + db
    """
    form = parse_qs((await request.body()).decode("utf-8"))
    result = refresh_git_repo_commits(parse_form_value(form, "repo_id"))
    return config_redirect(str(result["message"]), str(result["status"]), "section-git-bindings")


@app.post("/config/git-repos/refresh-all")
def config_git_repos_refresh_all():
    """刷新所有已启用 Git 仓库的提交历史。

    调用：refresh_all_git_repos → repositories → 遍历 rc_git_repo 逐个 refresh
    """
    result = refresh_all_git_repos()
    return config_redirect(str(result["message"]), str(result["status"]), "section-gitlab-creds")


@app.post("/config/project-mappings/save")
async def config_project_mapping_save(request: Request):
    """保存单条项目空间映射，配置页新增/编辑后直接写平台库。"""
    form = parse_qs((await request.body()).decode("utf-8"))
    result = save_project_mapping(
        parse_form_value(form, "source_project_space_id"),
        parse_form_value(form, "target_project_space_id"),
    )
    return JSONResponse(result)


@app.post("/config/project-mappings/toggle")
async def config_project_mapping_toggle(request: Request):
    """启用或停用单条项目空间映射。"""
    form = parse_qs((await request.body()).decode("utf-8"))
    enabled = parse_form_value(form, "enabled") not in {"0", "false", "False"}
    result = toggle_project_mapping(
        parse_form_value(form, "source_project_space_id"),
        enabled,
    )
    return JSONResponse(result)


@app.post("/config/datasource-mappings/save")
async def config_datasource_mapping_save(request: Request):
    """保存单条数据源映射，写 rc_datasource_mapping 并复用项目空间映射。"""
    form = parse_qs((await request.body()).decode("utf-8"))
    result = save_datasource_mapping(
        parse_form_value(form, "source_resource_id"),
        parse_form_value(form, "target_resource_id"),
    )
    return JSONResponse(result)


@app.post("/config/datasource-mappings/clear")
async def config_datasource_mapping_clear(request: Request):
    """清空单条数据源映射。"""
    form = parse_qs((await request.body()).decode("utf-8"))
    result = clear_datasource_mapping(parse_form_value(form, "source_resource_id"))
    return JSONResponse(result)


@app.post("/config/datasource-mappings/toggle")
async def config_datasource_mapping_toggle(request: Request):
    """启用或停用单条数据源映射。"""
    form = parse_qs((await request.body()).decode("utf-8"))
    enabled = parse_form_value(form, "enabled") not in {"0", "false", "False"}
    result = toggle_datasource_mapping(
        parse_form_value(form, "source_resource_id"),
        enabled,
    )
    return JSONResponse(result)


@app.post("/config/gitlab/test-project")
async def config_gitlab_test_project(request: Request):
    """检测指定 GitLab 项目访问权限：登录 GitLab → 访问项目页面 → 回写检测状态。

    调用：check_default_project_access → gitlab_auth → requests HTTP
         record_gitlab_check_status → UPDATE rc_gitlab_credential
    与 /config/gitlab/test 不同：这里检测用户指定的项目 URL，而非默认项目。
    """
    form = parse_qs((await request.body()).decode("utf-8"))
    project_url = parse_form_value(form, "project_url") or settings.gitlab_default_project_url
    try:
        result = check_default_project_access(project_url)
        status = "success" if result.project_accessible else "failed"
        message = (
            f"{result.message} 项目：{result.project_path or project_url}"
            if result.message else "GitLab 项目访问检测完成。"
        )
    except GitLabLoginError as exc:
        status = "failed"
        message = str(exc)
    # 回写检测状态到 rc_gitlab_credential
    record_gitlab_check_status(status, message)
    return RedirectResponse(
        url=(
            "/config?"
            f"gitlab_check_status={quote(status)}"
            f"&gitlab_check_message={quote(message)}"
            f"&gitlab_project_url={quote(project_url)}"
        ),
        status_code=303,
    )


@app.post("/config/sync-metadata")
def config_sync_metadata(request: Request):
    """配置页轻量同步：只同步项目空间和全局数据源，不做任务/目录全量扫描。

    调用：sync_config_metadata → repositories
    """
    try:
        result = sync_config_metadata()
        message = str(result["message"])
    except Exception as exc:
        message = f"同步失败：{exc}"
    return config_redirect(message, "success", "section-metasync")


# ──── 用户管理路由 ────


@app.get("/users")
def users(request: Request):
    """用户管理页：展示 rc_user 列表和分页。

    调用：get_users（读 rc_user）
    """
    if settings.use_mock_data:
        context = mock_base_context("users")
        context["request"] = request
        context.update(mock_users_context())
        return templates.TemplateResponse(request, "users.html", context)
    user_rows = get_users()
    context = base_context("users", request)
    context.update({
        "request": request,
        "users": user_rows,
        "pagination": pagination_meta(len(user_rows)),
    })
    return templates.TemplateResponse(request, "users.html", context)


# ──── Git 代码管理路由 ────


@app.get("/git")
def git_page(request: Request):
    """Git 代码管理页：展示当前仓库绑定和提交历史。

    调用：get_git_info → repositories → git_release.commit_history + db
    """
    if settings.use_mock_data:
        context = mock_base_context("git")
        context["request"] = request
        context.update(mock_git_context())
        return templates.TemplateResponse(request, "git.html", context)
    git = get_git_info()
    context = base_context("git", request)
    context.update({
        "request": request,
        "git": git,
        "pagination": pagination_meta(len(git["commits"])),
    })
    return templates.TemplateResponse(request, "git.html", context)


# ──── 旧路由兼容重定向 ────


@app.get("/config/env")
@app.get("/config/projects")
@app.get("/config/datasources")
@app.get("/settings")
def old_config_routes() -> RedirectResponse:
    """旧配置页路由兼容：全部 303 重定向到 /config。"""
    return RedirectResponse(url="/config")


@app.get("/ops")
@app.get("/logs")
def old_records_routes() -> RedirectResponse:
    """旧发布记录路由兼容：全部 303 重定向到 /records。"""
    return RedirectResponse(url="/records")
