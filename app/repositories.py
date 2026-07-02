"""
repositories — Web 元数据库读写入口（所有页面数据的核心交汇层）

调用链位置：
  本模块 ← main.py（所有路由处理函数通过 import 调用本模块的 get_*/save_*/refresh_*/sync_* 函数）
  本模块 → db.py（所有 rc_* 表的读写、数栈元数据库只读查询）
  本模块 → git_release.py（Git clone/pull/diff/commit 解析）
  本模块 → release_draft.py（发布草稿校验项生成与汇总）
  本模块 → secret_crypto.py（GitLab 密码加解密）

核心职责：
  1. 统一封装页面需要的查询、数栈元数据库只读同步、发布草稿持久化和非 API 阶段记录生成
  2. 数栈业务 API 暂不在本模块调用，避免页面演示阶段误写生产任务

涉及数据库表（按功能分组）：
  自身库（读写）：
    - rc_environment, rc_project_space, rc_project_mapping    — 环境与项目映射
    - rc_datasource_mapping, rc_datasource_resource            — 数据源映射与资源缓存
    - rc_project_directory                                      — 目录树缓存
    - rc_git_repo, rc_git_commit, rc_gitlab_credential          — Git 仓库与 GitLab 凭据
    - rc_release_draft, rc_release_draft_task, rc_release_validation, rc_task_artifact — 发布草稿
    - rc_release_batch, rc_release_task, rc_release_step_log    — 发布记录
    - rc_task_config_snapshot, rc_datasource_usage, rc_task_publish_binding — 配置快照与绑定
    - rc_user, rc_auth_session, rc_operation_log, rc_app_setting — 用户、会话、审计、配置

  数栈元数据库（只读，通过 fetch_dtstack_all）：
    - streamapp.rdos_stream_task, ide.rdos_batch_task           — 实时/离线已提交任务
    - ide.rdos_project                                           — 项目空间
    - streamapp.rdos_data_source_center, ide.rdos_batch_data_source_center, dt_pub_service.dsc_info — 数据源
    - ide.rdos_batch_catalogue                                    — 目录树
    - dt_pub_service.uic_user                                    — 用户

函数分组概览（每组标注行号范围，方便定位）：
  (a) 通用辅助 / 能力探测          — db_badge, schema_hint, gitlab_schema_hint, NAV_ITEMS
  (b) GitLab 全局凭据              — get_gitlab_credential_view, save_gitlab_credential, get_saved_gitlab_config,
                                    record_gitlab_check_status, saved_git_credential, load_git_branches,
                                    validate_tracking_branch
  (c) 环境/表自举                 — ensure_datasource_resource_table, ensure_environment
  (d) 项目空间（源侧）            — get_source_project_spaces, synced_project_space_ids,
                                    selected_project_space
  (e) 数栈元数据同步               — sync_project_spaces_from_dtstack, sync_datasources_from_dtstack,
                                    build_relative_paths, sync_project_directories_from_dtstack,
                                    sync_dtstack_metadata
  (f) Git 仓库/项目映射辅助       — git_repo_for_project, get_last_success_commit, has_project_mapping,
                                    directory_status_for_task, datasource_status_for_project,
                                    release_status_by_source_id, fake_git_task_id
  (g) 任务列表查询                — get_release_tasks, get_dtstack_submitted_tasks,
                                    get_dtstack_task_detail, merge_git_candidates_with_metadata,
                                    get_git_diff_task_plan, get_tasks
  (h) 发布草稿持久化              — persist_release_draft, get_release_draft_model
  (i) 发布记录（非 API 阶段）     — simulate_release_from_draft, get_records, get_record_detail,
                                    get_record_stats
  (j) 首页看板                    — get_home_tasks, filter_home_tasks, get_home_task_preview,
                                    get_datasource_resource_summary, get_daily_release_chart,
                                    get_home_overview, get_task_stats
  (k) 数据源映射与资源            — get_datasource_mappings, get_datasource_resources,
                                    get_datasource_options
  (l) 项目映射与选项              — get_project_mappings, get_project_space_options,
                                    get_directory_sync_summary
  (m) Git 仓库绑定与刷新          — get_git_repo_bindings, save_git_repo_binding, repo_by_id,
                                    refresh_git_repo_commits, refresh_all_git_repos, get_git_info
  (n) 用户                        — get_users

对外暴露：约 30 个函数被 main.py 直接 import（见 main.py:15-45 的 import 清单）
"""
import hashlib
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from app.config import settings
from app.db import (
    execute_insert,
    execute_write,
    fetch_all,
    fetch_dtstack_all,
    fetch_one,
    fetch_prod_metadata_all,
    fetch_test_metadata_all,
    is_database_configured,
    is_dtstack_metadata_configured,
    is_prod_metadata_configured,
    is_test_metadata_configured,
)
from app.git_release import (
    GitCredential,
    GitRepo,
    build_changed_task_candidates,
    build_git_snapshot,
    latest_commit_info_for_file,
    list_remote_branches,
    module_label_from_type,
    normalize_module_type,
    refresh_repo_commit_history,
)
from app.gitlab_api import GitLabApiCredential, GitLabApiError, GitLabApiService
from app.release_draft import build_validation_items, now_text, summarize_validation_status
from app.secret_crypto import decrypt_secret, encrypt_secret



# ──── (a) 通用辅助 / 能力探测 ────
# NAV_ITEMS: 导航栏配置，被 main.py base_context 使用
# db_badge: 当前数据源标签（数栈元数据库/MySQL/未配置）
# schema_hint / gitlab_schema_hint: 把建表异常包装成"请先执行 DDL"提示

NAV_ITEMS = [
    {"label": "首页", "path": "/home", "key": "home", "icon": "⌂"},
    {"label": "任务列表", "path": "/tasks", "key": "tasks", "icon": "▦"},
    {"label": "发布确认", "path": "/confirm", "key": "confirm", "icon": "▣"},
    {"label": "发布记录", "path": "/records", "key": "records", "icon": "▤"},
    {"label": "配置管理", "path": "/config", "key": "config", "icon": "◎"},
    {"label": "用户管理", "path": "/users", "key": "users", "icon": "♙"},
    {"label": "Git 代码管理", "path": "/git", "key": "git", "icon": "⌬"},
]


def db_badge() -> str:
    if is_test_metadata_configured() and is_prod_metadata_configured():
        return "测试/生产元数据库"
    if is_dtstack_metadata_configured():
        return "测试元数据库"
    return "MySQL" if is_database_configured() else "未配置"


def sql_literal_list(values: tuple[str, ...] | list[str]) -> str:
    """把内部固定值拼成 SQL IN (...) 片段，避免 text() 里元组参数不展开。"""
    quoted = []
    for value in values:
        escaped = str(value).replace("'", "''")
        quoted.append(f"'{escaped}'")
    return ", ".join(quoted) if quoted else "''"


def project_space_option_label(project_name: str, module_type: str) -> str:
    """生成项目空间下拉展示名：正文保留原始项目名，下拉选项补充离线/实时标识。"""
    module_label = module_label_from_type(normalize_module_type(module_type)) or "未标识"
    return f"{project_name}（{module_label}）"


def metadata_project_space_key(project_space_code: str | None, module_type: str | None) -> str:
    """把数栈项目空间原始 ID + 模块类型合成稳定键，避免离线/实时同 ID 相互覆盖。"""
    normalized_module = normalize_module_type(str(module_type or ""))
    raw_code = str(project_space_code or "").strip()
    return f"{normalized_module}:{raw_code}" if normalized_module and raw_code else ""


def ensure_project_space_unique_key() -> None:
    """兼容旧库结构：项目空间唯一键必须包含 project_type，才能区分离线/实时同 raw ID。"""
    try:
        indexes = fetch_all("SHOW INDEX FROM rc_project_space")
    except Exception:
        return
    grouped: dict[str, list[dict[str, object]]] = {}
    for row in indexes:
        grouped.setdefault(str(row.get("Key_name") or ""), []).append(row)

    new_key = grouped.get("uk_rc_project_space_env_type_space", [])
    if [str(item.get("Column_name") or "") for item in sorted(new_key, key=lambda item: int(item.get("Seq_in_index") or 0))] == ["env_id", "project_type", "project_space_id"]:
        return

    old_key = grouped.get("uk_rc_project_space_env_space", [])
    old_columns = [str(item.get("Column_name") or "") for item in sorted(old_key, key=lambda item: int(item.get("Seq_in_index") or 0))]
    if old_columns == ["env_id", "project_space_id"]:
        execute_write("ALTER TABLE rc_project_space DROP INDEX uk_rc_project_space_env_space")

    try:
        execute_write("ALTER TABLE rc_project_space ADD UNIQUE KEY uk_rc_project_space_env_type_space (env_id, project_type, project_space_id)")
    except Exception as exc:
        if "Duplicate key name" not in str(exc):
            raise


def schema_hint(exc: Exception) -> str:
    return f"发布草稿表不可用，请先执行 docs/sql/release_console_metadata_v2.sql。原始错误：{exc}"


def gitlab_schema_hint(exc: Exception) -> str:
    return f"GitLab 配置表不可用，请先执行 docs/sql/release_console_metadata_v2.sql。原始错误：{exc}"



# ──── (b) GitLab 全局凭据 ────
# 管理 rc_gitlab_credential 表：保存/读取/检测 GitLab 登录凭据
# 凭据密码使用 secret_crypto.encrypt_secret 加密后入库
# 被调用：main.py 的 /config/gitlab/save、/config/gitlab/test、gitlab_auth.default_gitlab_config

def get_gitlab_credential_row() -> dict[str, object] | None:
    try:
        return fetch_one(
            """
            SELECT id, base_url, username, password_ciphertext, auth_mode,
                   token_ciphertext, git_api_last_check_status, git_api_last_check_message,
                   git_api_last_checked_at, last_check_status, last_check_message, last_checked_at
            FROM rc_gitlab_credential
            WHERE credential_key = 'global' AND is_enabled = 1
            ORDER BY id DESC
            LIMIT 1
            """
        )
    except Exception:
        return None


def get_gitlab_credential_view() -> dict[str, str]:
    row = get_gitlab_credential_row() or {}
    return {
        "base_url": str(row.get("base_url") or settings.gitlab_base_url),
        "username": str(row.get("username") or ""),
        "auth_mode": str(row.get("auth_mode") or "password"),
        "last_check_status": str(row.get("last_check_status") or "未检测"),
        "last_check_message": str(row.get("last_check_message") or ""),
        "last_checked_at": str(row.get("last_checked_at") or "-"),
        "git_api_last_check_status": str(row.get("git_api_last_check_status") or "未检测"),
        "git_api_last_check_message": str(row.get("git_api_last_check_message") or ""),
        "git_api_last_checked_at": str(row.get("git_api_last_checked_at") or "-"),
        "has_password": "已保存" if row.get("password_ciphertext") else "未保存",
        "has_token": "已保存" if row.get("token_ciphertext") else "未保存",
    }


def save_gitlab_credential(
    base_url: str,
    username: str,
    password: str,
    auth_mode: str = "password",
    token: str = "",
) -> dict[str, str]:
    base_url = base_url.rstrip("/")
    if not base_url or not username:
        return {"status": "failed", "message": "请填写 GitLab 地址和用户名。"}
    normalized_auth_mode = "token" if auth_mode == "token" else "password"

    existing = get_gitlab_credential_row() or {}
    password_ciphertext = str(existing.get("password_ciphertext") or "")
    token_ciphertext = str(existing.get("token_ciphertext") or "")
    if password:
        password_ciphertext = encrypt_secret(password)
    if token:
        token_ciphertext = encrypt_secret(token)
    if not password_ciphertext and normalized_auth_mode == "password":
        return {"status": "failed", "message": "首次保存 GitLab 凭据时必须填写密码。"}
    if not token_ciphertext and normalized_auth_mode == "token":
        return {"status": "failed", "message": "当前认证方式为 Token，请填写 GitLab Token。"}

    try:
        execute_write(
            """
            INSERT INTO rc_gitlab_credential(
              credential_key, base_url, username, password_ciphertext, auth_mode,
              token_ciphertext, is_enabled
            )
            VALUES (
              'global', :base_url, :username, :password_ciphertext, :auth_mode,
              :token_ciphertext, 1
            )
            ON DUPLICATE KEY UPDATE
              base_url = VALUES(base_url),
              username = VALUES(username),
              password_ciphertext = VALUES(password_ciphertext),
              auth_mode = VALUES(auth_mode),
              token_ciphertext = VALUES(token_ciphertext),
              is_enabled = 1,
              updated_at = CURRENT_TIMESTAMP
            """,
            {
                "base_url": base_url,
                "username": username,
                "password_ciphertext": password_ciphertext,
                "auth_mode": normalized_auth_mode,
                "token_ciphertext": token_ciphertext,
            },
        )
    except Exception as exc:
        return {"status": "failed", "message": gitlab_schema_hint(exc)}
    return {"status": "success", "message": "GitLab 凭据已保存。"}


def get_saved_gitlab_config() -> dict[str, str]:
    row = get_gitlab_credential_row()
    if row and (row.get("password_ciphertext") or row.get("token_ciphertext")):
        return {
            "base_url": str(row.get("base_url") or settings.gitlab_base_url),
            "username": str(row.get("username") or ""),
            "password": decrypt_secret(str(row.get("password_ciphertext") or "")) if row.get("password_ciphertext") else "",
            "auth_mode": str(row.get("auth_mode") or "password"),
            "token": decrypt_secret(str(row.get("token_ciphertext") or "")) if row.get("token_ciphertext") else "",
        }
    if settings.gitlab_username and settings.gitlab_password:
        return {
            "base_url": settings.gitlab_base_url,
            "username": settings.gitlab_username,
            "password": settings.gitlab_password,
            "auth_mode": "password",
            "token": "",
        }
    raise RuntimeError("请先在配置页保存 GitLab 全局凭据。")


def record_gitlab_check_status(status: str, message: str) -> None:
    try:
        execute_write(
            """
            UPDATE rc_gitlab_credential
            SET last_check_status = :status,
                last_check_message = :message,
                last_checked_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE credential_key = 'global' AND is_enabled = 1
            """,
            {"status": status, "message": message[:1024]},
        )
    except Exception:
        pass


def record_gitlab_api_check_status(status: str, message: str) -> None:
    """记录 GitLab API 检测结果，供配置页展示 Token 主链路状态。"""
    try:
        execute_write(
            """
            UPDATE rc_gitlab_credential
            SET git_api_last_check_status = :status,
                git_api_last_check_message = :message,
                git_api_last_checked_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE credential_key = 'global' AND is_enabled = 1
            """,
            {"status": status, "message": message[:1024]},
        )
    except Exception:
        pass


def saved_git_credential() -> GitCredential | None:
    try:
        config = get_saved_gitlab_config()
    except Exception:
        return None
    return GitCredential(username=config["username"], password=config["password"])


def saved_gitlab_api_credential() -> GitLabApiCredential:
    """读取已保存的 GitLab API Token 凭据。"""
    config = get_saved_gitlab_config()
    if str(config.get("auth_mode") or "password") != "token":
        raise RuntimeError("当前 GitLab 认证方式不是 Token，请先在配置页切换为 Token 并保存。")
    token = str(config.get("token") or "")
    if not token:
        raise RuntimeError("请先在配置页保存 GitLab Token。")
    return GitLabApiCredential(
        base_url=str(config.get("base_url") or settings.gitlab_base_url),
        token=token,
        timeout_seconds=settings.gitlab_login_timeout_seconds,
    )


def normalize_git_repo_url(repo_url: str) -> str:
    """把 GitLab 项目页 URL 规范成可 clone 的仓库地址，避免页面 URL/仓库 URL 混用。"""
    normalized = (repo_url or "").strip()
    if not normalized:
        return ""
    if normalized.startswith("http://") or normalized.startswith("https://"):
        normalized = normalized.rstrip("/")
        if not normalized.endswith(".git"):
            normalized = f"{normalized}.git"
    return normalized


def gitlab_api_service() -> GitLabApiService:
    """构造 GitLab API 客户端，供分支读取、项目解析和提交刷新复用。"""
    return GitLabApiService(saved_gitlab_api_credential())


def load_git_branches(repo_url: str) -> dict[str, object]:
    if not repo_url:
        return {"success": False, "message": "请先填写仓库地址。", "branches": []}
    normalized_repo_url = normalize_git_repo_url(repo_url)
    try:
        service = gitlab_api_service()
        project = service.resolve_project(normalized_repo_url)
        branches = service.list_branches(str(project.get("project_id") or project.get("project_path") or ""))
        record_gitlab_api_check_status("success", "GitLab API 分支读取成功。")
    except (RuntimeError, GitLabApiError) as exc:
        record_gitlab_api_check_status("failed", str(exc))
        return {"success": False, "message": str(exc), "branches": []}
    if not branches:
        return {"success": False, "message": "未读取到远端分支。", "branches": []}
    return {"success": True, "message": "分支读取完成。", "branches": branches}


def check_gitlab_api_project(project_url: str) -> dict[str, object]:
    """用 Token 主链路检测 GitLab API 是否能访问指定项目。"""
    try:
        service = gitlab_api_service()
        project = service.resolve_project(project_url)
        branches = service.list_branches(str(project.get("project_id") or project.get("project_path") or ""))
        message = f"GitLab API 访问正常，项目 {project.get('project_path') or '-'} 共读取到 {len(branches)} 个分支。"
        record_gitlab_api_check_status("success", message)
        return {"success": True, "message": message}
    except Exception as exc:
        message = str(exc)
        record_gitlab_api_check_status("failed", message)
        return {"success": False, "message": message}


def validate_tracking_branch(
    repo_url: str,
    branch: str,
    credential: GitCredential | None = None,
) -> tuple[bool, list[str], str]:
    """校验跟踪分支来自远端真实分支，优先走 GitLab API。"""
    normalized_repo_url = normalize_git_repo_url(repo_url)
    try:
        service = gitlab_api_service()
        project = service.resolve_project(normalized_repo_url)
        branches = service.list_branches(str(project.get("project_id") or project.get("project_path") or ""))
    except Exception as api_exc:
        # API 未就绪时保留本地 Git/HTTP 兜底，避免把旧能力完全打断。
        ok, branches, error = list_remote_branches(normalized_repo_url, credential)
        if not ok:
            return False, [], f"读取远端分支失败：{api_exc if str(api_exc) else error or repo_url}"
    if branch not in branches:
        branch_list = "、".join(branches) if branches else "无"
        return False, branches, f"跟踪分支 {branch} 不存在于远端仓库。当前远端分支：{branch_list}。"
    return True, branches, "跟踪分支校验通过。"



# ──── (c) 环境/表自举 ────
# 确保发布台自身元数据库的基础表和行存在（应用不自动建表，这些仅做补充保障）

def ensure_datasource_resource_table() -> None:
    execute_write(
        """
        CREATE TABLE IF NOT EXISTS rc_datasource_resource (
          id BIGINT PRIMARY KEY AUTO_INCREMENT,
          env_id BIGINT NOT NULL,
          project_space_id BIGINT DEFAULT NULL,
          project_space_code VARCHAR(128) DEFAULT NULL,
          project_space_name VARCHAR(128) DEFAULT NULL,
          datasource_id VARCHAR(128) NOT NULL,
          datasource_name VARCHAR(256) NOT NULL,
          datasource_type VARCHAR(64) NOT NULL,
          schema_name VARCHAR(128) DEFAULT NULL,
          datasource_key VARCHAR(512) NOT NULL,
          connectivity_status VARCHAR(32) NOT NULL DEFAULT 'unknown',
          source_module VARCHAR(32) NOT NULL DEFAULT 'stream',
          is_meta TINYINT(1) NOT NULL DEFAULT 0,
          is_enabled TINYINT(1) NOT NULL DEFAULT 1,
          last_synced_at DATETIME DEFAULT NULL,
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          UNIQUE KEY uk_rc_datasource_resource_source (
            env_id, project_space_code, datasource_id, source_module
          ),
          INDEX idx_rc_datasource_resource_env (env_id, is_enabled),
          INDEX idx_rc_datasource_resource_project (project_space_id),
          INDEX idx_rc_datasource_resource_name (datasource_name),
          CONSTRAINT fk_rc_datasource_resource_env FOREIGN KEY (env_id) REFERENCES rc_environment(id),
          CONSTRAINT fk_rc_datasource_resource_project FOREIGN KEY (project_space_id) REFERENCES rc_project_space(id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='数栈数据源资源缓存'
        """
    )


def ensure_environment(env_type: str, env_code: str, env_name: str) -> int:
    rows = fetch_all(
        """
        SELECT id
        FROM rc_environment
        WHERE env_type = :env_type AND is_enabled = 1
        ORDER BY id
        LIMIT 1
        """,
        {"env_type": env_type},
    )
    if rows:
        return int(rows[0]["id"])

    execute_write(
        """
        INSERT INTO rc_environment(env_code, env_name, env_type, base_url, is_enabled)
        VALUES (:env_code, :env_name, :env_type, '', 1)
        ON DUPLICATE KEY UPDATE env_name = VALUES(env_name), env_type = VALUES(env_type), is_enabled = 1
        """,
        {"env_code": env_code, "env_name": env_name, "env_type": env_type},
    )
    created = fetch_all("SELECT id FROM rc_environment WHERE env_code = :env_code", {"env_code": env_code})
    if not created:
        raise RuntimeError("无法创建 Web 元数据库环境记录。")
    return int(created[0]["id"])


def environment_labels() -> dict[str, str]:
    """读取平台库中的测试/生产环境名称，页面统一从这里展示，不再写死文案。"""
    rows = fetch_all(
        """
        SELECT env_type, env_name
        FROM rc_environment
        WHERE is_enabled = 1
          AND env_type IN ('test', 'prod')
        ORDER BY id
        """
    )
    labels = {str(row["env_type"]): str(row["env_name"]) for row in rows}
    return {
        "source": labels.get("test", "测试环境"),
        "target": labels.get("prod", "生产环境"),
    }


def ensure_task_metadata_table() -> None:
    """补齐 rc_task_metadata 表，保障双环境任务缓存可写。"""
    execute_write(
        """
        CREATE TABLE IF NOT EXISTS rc_task_metadata (
          id BIGINT PRIMARY KEY AUTO_INCREMENT,
          env_id BIGINT NOT NULL COMMENT '关联 rc_environment.id',
          project_space_id BIGINT NOT NULL COMMENT '关联 rc_project_space.id',
          project_space_code VARCHAR(128) NOT NULL COMMENT '数栈项目空间原始 ID',
          project_space_name VARCHAR(128) NOT NULL COMMENT '项目空间名称',
          module_type VARCHAR(32) NOT NULL COMMENT 'offline/stream',
          task_id VARCHAR(128) NOT NULL COMMENT '数栈任务 ID',
          task_name VARCHAR(256) NOT NULL COMMENT '任务名称',
          task_type INT DEFAULT NULL COMMENT '数栈原始 task_type',
          task_type_label VARCHAR(64) DEFAULT NULL COMMENT '页面展示任务类型',
          node_pid VARCHAR(128) DEFAULT NULL COMMENT '任务所属目录 ID',
          submit_status VARCHAR(32) DEFAULT NULL COMMENT '提交状态缓存',
          submitter_id VARCHAR(128) DEFAULT NULL COMMENT '提交人 ID',
          submitter_name VARCHAR(128) DEFAULT NULL COMMENT '提交人名称',
          submitted_at DATETIME DEFAULT NULL COMMENT '提交时间',
          is_deleted TINYINT(1) NOT NULL DEFAULT 0,
          is_enabled TINYINT(1) NOT NULL DEFAULT 1,
          last_synced_at DATETIME DEFAULT NULL,
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          UNIQUE KEY uk_rc_task_metadata_env_task (env_id, module_type, task_id),
          INDEX idx_rc_task_metadata_project (project_space_id, module_type, is_enabled),
          INDEX idx_rc_task_metadata_name (project_space_id, module_type, task_name),
          INDEX idx_rc_task_metadata_submit (submitted_at),
          CONSTRAINT fk_rc_task_metadata_env FOREIGN KEY (env_id) REFERENCES rc_environment(id),
          CONSTRAINT fk_rc_task_metadata_project FOREIGN KEY (project_space_id) REFERENCES rc_project_space(id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='测试/生产任务元数据缓存'
        """
    )


def app_setting_value(setting_key: str) -> str:
    row = fetch_one(
        """
        SELECT setting_value
        FROM rc_app_setting
        WHERE setting_key = :setting_key
        LIMIT 1
        """,
        {"setting_key": setting_key},
    )
    return str(row.get("setting_value") or "") if row else ""


def upsert_app_setting(setting_key: str, setting_value: str, description: str) -> None:
    execute_write(
        """
        INSERT INTO rc_app_setting(setting_key, setting_value, description)
        VALUES (:setting_key, :setting_value, :description)
        ON DUPLICATE KEY UPDATE
          setting_value = VALUES(setting_value),
          description = VALUES(description),
          updated_at = CURRENT_TIMESTAMP
        """,
        {
            "setting_key": setting_key,
            "setting_value": setting_value,
            "description": description,
        },
    )


def cleanup_legacy_demo_data_once() -> None:
    """一次性清理旧 seed/demo 残留，避免真实页面仍读到演示数据。"""
    marker_key = "cleanup_demo_seed_v2"
    if app_setting_value(marker_key) == "done":
        return

    demo_env_codes = ("prod_env_mock",)
    demo_project_names = ("实时任务测试空间", "离线任务测试空间", "公共测试空间", "生产环境实时空间", "生产环境离线空间", "公共生产空间")
    demo_usernames = ("test_user", "data_admin")
    env_codes_sql = sql_literal_list(demo_env_codes)
    project_names_sql = sql_literal_list(demo_project_names)
    usernames_sql = sql_literal_list(demo_usernames)

    execute_write(
        f"""
        DELETE FROM rc_release_step_log
        WHERE batch_id IN (
            SELECT id
            FROM rc_release_batch
            WHERE batch_code LIKE 'BATCH_202405%'
               OR batch_code LIKE 'SIM_%'
               OR batch_name LIKE '%demo%'
               OR batch_name LIKE '%非 API 发布确认%'
               OR source_project_name IN ({project_names_sql})
               OR target_project_name IN ({project_names_sql})
        )
        """
    )
    execute_write(
        f"""
        DELETE FROM rc_release_task
        WHERE source_task_id LIKE 'demo_%'
           OR source_task_id LIKE 'git:%'
           OR task_name LIKE 'demo_%'
           OR submitter IN ({usernames_sql})
        """
    )
    execute_write(
        f"""
        DELETE FROM rc_release_batch
        WHERE batch_code LIKE 'BATCH_202405%'
           OR batch_code LIKE 'SIM_%'
           OR batch_name LIKE '%demo%'
           OR batch_name LIKE '%非 API 发布确认%'
           OR source_project_name IN ({project_names_sql})
           OR target_project_name IN ({project_names_sql})
        """
    )
    execute_write(
        """
        DELETE FROM rc_datasource_mapping
        WHERE description = 'demo seed'
           OR source_pattern LIKE 'jdbc:mysql://10.10.1.23%'
           OR target_value LIKE 'jdbc:mysql://10.20.1.23%'
           OR source_pattern IN ('ods_test', 'kafka-test-9092', 'redis-test-6379', 'hive_test')
           OR target_value IN ('ods_prod', 'kafka-prod-9092', 'redis-prod-6379', 'hive_prod')
        """
    )
    execute_write(
        f"""
        DELETE FROM rc_project_mapping
        WHERE source_project_name IN ({project_names_sql})
           OR target_project_name IN ({project_names_sql})
        """
    )
    execute_write(
        """
        DELETE FROM rc_git_commit
        WHERE repo_id IN (
            SELECT id
            FROM rc_git_repo
            WHERE current_branch = 'release/demo'
               OR repo_url LIKE '%release/demo%'
        )
        """
    )
    execute_write(
        """
        DELETE FROM rc_git_repo
        WHERE current_branch = 'release/demo'
           OR repo_url LIKE '%release/demo%'
        """
    )
    execute_write(
        """
        DELETE FROM rc_operation_log
        WHERE object_type = 'demo_seed'
           OR operation_type = 'seed_demo_data'
        """
    )
    execute_write(
        f"""
        DELETE FROM rc_user
        WHERE username IN ({usernames_sql})
           OR credential_key IN ('web_test_user', 'web_data_admin')
        """
    )
    execute_write(
        """
        DELETE FROM rc_app_setting
        WHERE setting_key = 'default_project'
           OR description LIKE '%demo%'
        """
    )
    execute_write(
        f"""
        DELETE FROM rc_project_space
        WHERE env_id IN (
            SELECT id FROM rc_environment WHERE env_code IN ({env_codes_sql})
        )
           OR project_name IN ({project_names_sql})
        """
    )
    execute_write(
        f"""
        DELETE FROM rc_environment
        WHERE env_code IN ({env_codes_sql})
        """
    )
    upsert_app_setting(marker_key, "done", "旧 demo/seed 残留数据已完成一次性清理")



# ──── (d) 项目空间（源侧） ────
# 从 rc_project_space/rc_environment/rc_git_repo/rc_datasource_resource 读取测试项目空间列表
# 被 main.py base_context / 任务列表页 / 配置页项目空间选择器使用

def get_source_project_spaces() -> list[dict[str, str]]:
    rows = fetch_all(
        """
        SELECT ps.id, ps.project_name, ps.project_space_id, ps.project_type,
               repo.id AS repo_id, repo.repo_url, repo.default_branch, repo.current_branch,
               repo.module_type AS repo_module_type,
               CASE
                 WHEN ps.project_type = 'offline' THEN '离线'
                 WHEN ps.project_type IN ('realtime', 'stream') THEN '实时'
                 ELSE '未标识'
               END AS module_label,
               CASE
                 WHEN ps.project_type = 'offline' THEN 'offline'
                 WHEN ps.project_type IN ('realtime', 'stream') THEN 'realtime'
                 ELSE 'unknown'
               END AS module_tone
        FROM rc_project_space ps
        JOIN rc_environment env ON ps.env_id = env.id
        LEFT JOIN rc_git_repo repo ON repo.project_space_id = ps.id AND repo.is_current = 1
        WHERE ps.is_enabled = 1 AND env.is_enabled = 1 AND env.env_type = 'test'
        ORDER BY
          CASE WHEN ps.project_type = 'offline' THEN 0 ELSE 1 END,
          ps.project_name,
          ps.id
        """
    )
    for row in rows:
        row["option_label"] = project_space_option_label(
            str(row.get("project_name") or ""),
            str(row.get("project_type") or ""),
        )
    return rows


def synced_project_space_ids(env_id: int) -> dict[str, int]:
    rows = fetch_all(
        """
        SELECT id, project_space_id, project_type
        FROM rc_project_space
        WHERE env_id = :env_id AND is_enabled = 1
        """,
        {"env_id": env_id},
    )
    project_ids: dict[str, int] = {}
    for row in rows:
        key = metadata_project_space_key(row.get("project_space_id"), row.get("project_type"))
        if key:
            project_ids[key] = int(row["id"])
    return project_ids


def project_space_code(module_type: str, project_id: object) -> str:
    normalized = normalize_module_type(str(module_type))
    raw_id = str(project_id or "").strip()
    prefix = normalized or "stream"
    return f"{prefix}:{raw_id}" if raw_id else ""


def raw_project_id(project_space_code_value: str | None) -> str:
    value = str(project_space_code_value or "").strip()
    if ":" in value:
        return value.split(":", 1)[1]
    return value



# ──── (e) 数栈元数据同步（只读数栈库 → 写自身库） ────
# 核心入口：sync_dtstack_metadata()，被 main.py /config/sync-metadata 路由调用
# 同步模式：先软删（is_enabled=0）→ 再从数栈库拉数据 upsert
# 读取数栈库：ide.rdos_project / streamapp.rdos_data_source_center / dt_pub_service.dsc_info 等

def metadata_fetcher(env_type: str):
    return fetch_prod_metadata_all if env_type == "prod" else fetch_test_metadata_all


def sync_project_spaces_from_metadata(env_id: int, env_type: str) -> int:
    execute_write(
        """
        UPDATE rc_project_space
        SET is_enabled = 0, updated_at = CURRENT_TIMESTAMP
        WHERE env_id = :env_id
        """,
        {"env_id": env_id},
    )
    rows = metadata_fetcher(env_type)(
        """
        SELECT project_space_id, project_name, project_code, project_type
        FROM (
            SELECT CAST(project.id AS CHAR) AS project_space_id,
                   project.project_name,
                   project.project_Identifier AS project_code,
                   'offline' AS project_type
            FROM ide.rdos_project project
            WHERE project.is_deleted = 0
              AND project.status = 1
              AND LOWER(project.project_name) NOT LIKE '%meta%'
              AND project.project_name NOT LIKE '%元数据%'
            UNION ALL
            SELECT CAST(project.id AS CHAR) AS project_space_id,
                   project.project_name,
                   project.project_Identifier AS project_code,
                   'realtime' AS project_type
            FROM streamapp.rdos_project project
            WHERE project.is_deleted = 0
              AND project.status = 1
              AND LOWER(project.project_name) NOT LIKE '%meta%'
              AND project.project_name NOT LIKE '%元数据%'
        ) project_space
        ORDER BY project_type, project_name
        """
    )
    for row in rows:
        execute_write(
            """
            INSERT INTO rc_project_space(
              env_id, project_code, project_name, project_space_id, project_type, is_enabled, last_synced_at
            )
            VALUES (
              :env_id, :project_code, :project_name, :project_space_id, :project_type, 1, CURRENT_TIMESTAMP
            )
            ON DUPLICATE KEY UPDATE
              project_code = VALUES(project_code),
              project_name = VALUES(project_name),
              project_type = VALUES(project_type),
              is_enabled = 1,
              last_synced_at = CURRENT_TIMESTAMP,
              updated_at = CURRENT_TIMESTAMP
            """,
            {
                "env_id": env_id,
                "project_code": row.get("project_code") or str(row["project_space_id"]),
                "project_name": row["project_name"],
                "project_space_id": row["project_space_id"],
                "project_type": row["project_type"],
            },
        )
    return len(rows)


def sync_datasources_from_metadata(env_id: int, env_type: str) -> int:
    ensure_datasource_resource_table()
    execute_write(
        """
        UPDATE rc_datasource_resource
        SET is_enabled = 0, updated_at = CURRENT_TIMESTAMP
        WHERE env_id = :env_id
        """,
        {"env_id": env_id},
    )
    rows = metadata_fetcher(env_type)(
        """
        SELECT
               CAST(dsc.id AS CHAR) AS datasource_id,
               dsc.data_name AS datasource_name,
               dsc.data_type AS datasource_type,
               dsc.schema_name,
               CASE WHEN dsc.status = 1 THEN 'connected' ELSE 'failed' END AS connectivity_status,
               dsc.is_meta
        FROM dt_pub_service.dsc_info dsc
        WHERE dsc.is_deleted = 0
          AND dsc.is_meta = 0
          AND LOWER(dsc.data_name) NOT LIKE '%meta%'
          AND dsc.data_name NOT LIKE '%元数据%'
          AND LOWER(dsc.data_type) NOT LIKE '%meta%'
        ORDER BY dsc.data_name, dsc.id
        """
    )
    synced_count = 0
    for row in rows:
        execute_write(
            """
            INSERT INTO rc_datasource_resource(
              env_id, project_space_id, project_space_code, project_space_name,
              datasource_id, datasource_name, datasource_type, schema_name, datasource_key,
              connectivity_status, source_module, is_meta, is_enabled, last_synced_at
            )
            VALUES (
              :env_id, :project_space_id, :project_space_code, :project_space_name,
              :datasource_id, :datasource_name, :datasource_type, :schema_name, :datasource_key,
              :connectivity_status, :source_module, :is_meta, 1, CURRENT_TIMESTAMP
            )
            ON DUPLICATE KEY UPDATE
              project_space_id = VALUES(project_space_id),
              project_space_name = VALUES(project_space_name),
              datasource_name = VALUES(datasource_name),
              datasource_type = VALUES(datasource_type),
              schema_name = VALUES(schema_name),
              datasource_key = VALUES(datasource_key),
              connectivity_status = VALUES(connectivity_status),
              is_meta = VALUES(is_meta),
              is_enabled = 1,
              last_synced_at = CURRENT_TIMESTAMP,
              updated_at = CURRENT_TIMESTAMP
            """,
            {
                "env_id": env_id,
                "project_space_id": None,
                "project_space_code": "",
                "project_space_name": "",
                "datasource_id": row["datasource_id"],
                "datasource_name": row["datasource_name"],
                "datasource_type": row["datasource_type"],
                "schema_name": row.get("schema_name") or "",
                "datasource_key": row["datasource_name"],
                "connectivity_status": row.get("connectivity_status") or "unknown",
                "source_module": "global",
                "is_meta": int(row.get("is_meta") or 0),
            },
        )
        synced_count += 1
    return synced_count


def build_relative_paths(rows: list[dict[str, object]]) -> dict[str, str]:
    by_id = {str(row["directory_id"]): row for row in rows}
    cache: dict[str, str] = {}

    def resolve(directory_id: str) -> str:
        if directory_id in cache:
            return cache[directory_id]
        row = by_id.get(directory_id)
        if not row:
            return ""
        parent_id = str(row.get("parent_directory_id") or "")
        name = str(row.get("directory_name") or "").strip("/")
        if not parent_id or parent_id == "-1" or parent_id not in by_id:
            cache[directory_id] = name
            return name
        parent = resolve(parent_id)
        cache[directory_id] = f"{parent}/{name}" if parent else name
        return cache[directory_id]

    for row in rows:
        resolve(str(row["directory_id"]))
    return cache


def sync_project_directories_from_metadata(env_id: int, env_type: str) -> int:
    project_ids = synced_project_space_ids(env_id)
    execute_write(
        """
        UPDATE rc_project_directory
        SET is_enabled = 0, updated_at = CURRENT_TIMESTAMP
        WHERE env_id = :env_id
        """,
        {"env_id": env_id},
    )
    rows = metadata_fetcher(env_type)(
        """
        SELECT *
        FROM (
            SELECT CAST(id AS CHAR) AS directory_id,
                   CAST(project_id AS CHAR) AS project_space_code,
                   CAST(node_pid AS CHAR) AS parent_directory_id,
                   node_name AS directory_name,
                   'offline' AS module_type
            FROM ide.rdos_batch_catalogue
            WHERE is_deleted = 0
              AND catalogue_type = 0
            UNION ALL
            SELECT CAST(id AS CHAR) AS directory_id,
                   CAST(project_id AS CHAR) AS project_space_code,
                   CAST(node_pid AS CHAR) AS parent_directory_id,
                   node_name AS directory_name,
                   'stream' AS module_type
            FROM streamapp.rdos_stream_catalogue
            WHERE is_deleted = 0
        ) catalogue
        ORDER BY project_space_code, directory_id
        """
    )
    rows_by_project: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        project_key = metadata_project_space_key(row.get("project_space_code"), row.get("module_type"))
        if project_key:
            rows_by_project.setdefault(project_key, []).append(row)

    synced_count = 0
    for project_key, project_rows in rows_by_project.items():
        project_space_id = project_ids.get(project_key)
        if project_space_id is None:
            continue
        relative_paths = build_relative_paths(project_rows)
        for row in project_rows:
            directory_id = str(row["directory_id"])
            execute_write(
                """
                INSERT INTO rc_project_directory(
                  env_id, project_space_id, directory_id, parent_directory_id,
                  directory_name, relative_path, module_type, is_enabled, last_synced_at
                )
                VALUES (
                  :env_id, :project_space_id, :directory_id, :parent_directory_id,
                  :directory_name, :relative_path, :module_type, 1, CURRENT_TIMESTAMP
                )
                ON DUPLICATE KEY UPDATE
                  parent_directory_id = VALUES(parent_directory_id),
                  directory_name = VALUES(directory_name),
                  relative_path = VALUES(relative_path),
                  module_type = VALUES(module_type),
                  is_enabled = 1,
                  last_synced_at = CURRENT_TIMESTAMP,
                  updated_at = CURRENT_TIMESTAMP
                """,
                {
                    "env_id": env_id,
                    "project_space_id": project_space_id,
                    "directory_id": directory_id,
                    "parent_directory_id": row.get("parent_directory_id") or "",
                    "directory_name": row.get("directory_name") or "",
                    "relative_path": relative_paths.get(directory_id) or row.get("directory_name") or "",
                    "module_type": row.get("module_type") or "offline",
                },
            )
            synced_count += 1
    return synced_count


def sync_task_metadata_from_metadata(env_id: int, env_type: str) -> int:
    """同步测试/生产两侧任务元数据到 rc_task_metadata，供页面和 Git 结果合并使用。"""
    ensure_task_metadata_table()
    project_ids = synced_project_space_ids(env_id)
    execute_write(
        """
        UPDATE rc_task_metadata
        SET is_enabled = 0, updated_at = CURRENT_TIMESTAMP
        WHERE env_id = :env_id
        """,
        {"env_id": env_id},
    )
    rows = metadata_fetcher(env_type)(
        """
        SELECT *
        FROM (
            SELECT
              'offline' AS module_type,
              CAST(batch.id AS CHAR) AS task_id,
              batch.name AS task_name,
              batch.task_type,
              CASE batch.task_type
                  WHEN -1 THEN '虚节点'
                  WHEN 0 THEN 'SparkSQL'
                  WHEN 1 THEN 'Spark'
                  WHEN 2 THEN '数据同步'
                  WHEN 3 THEN 'PySpark'
                  WHEN 4 THEN 'R'
                  WHEN 5 THEN '深度学习'
                  WHEN 6 THEN 'Python'
                  WHEN 7 THEN 'Shell'
                  WHEN 8 THEN '机器学习'
                  WHEN 9 THEN 'HadoopMR'
                  WHEN 10 THEN '工作流'
                  WHEN 12 THEN 'CarbonSQL'
                  WHEN 13 THEN 'Notebook'
                  WHEN 14 THEN '算法实验'
                  WHEN 15 THEN 'Libra SQL'
                  WHEN 16 THEN 'Kylin'
                  WHEN 17 THEN 'HiveSQL'
                  ELSE CONCAT('离线类型', CAST(batch.task_type AS CHAR))
              END AS task_type_label,
              CAST(batch.project_id AS CHAR) AS project_space_code,
              COALESCE(project.project_name, CONCAT('项目ID ', batch.project_id)) AS project_space_name,
              CAST(batch.node_pid AS CHAR) AS node_pid,
              CAST(batch.modify_user_id AS CHAR) AS submitter_id,
              COALESCE(user.full_name, user.username, CAST(batch.modify_user_id AS CHAR), '-') AS submitter_name,
              batch.gmt_modified AS submitted_at,
              CAST(batch.submit_status AS CHAR) AS submit_status,
              batch.is_deleted
            FROM ide.rdos_batch_task batch
            LEFT JOIN ide.rdos_project project ON project.id = batch.project_id AND project.is_deleted = 0
            LEFT JOIN dt_pub_service.uic_user user ON user.id = batch.modify_user_id AND user.is_deleted = 'N'
            WHERE batch.is_deleted = 0
            UNION ALL
            SELECT
              'stream' AS module_type,
              CAST(stream.task_id AS CHAR) AS task_id,
              stream.name AS task_name,
              stream.task_type,
              CASE stream.task_type
                  WHEN 0 THEN 'SQL'
                  WHEN 1 THEN 'MR'
                  ELSE CONCAT('实时类型', CAST(stream.task_type AS CHAR))
              END AS task_type_label,
              CAST(stream.project_id AS CHAR) AS project_space_code,
              COALESCE(project.project_name, CONCAT('项目ID ', stream.project_id)) AS project_space_name,
              CAST(stream.node_pid AS CHAR) AS node_pid,
              CAST(stream.modify_user_id AS CHAR) AS submitter_id,
              COALESCE(user.full_name, user.username, CAST(stream.modify_user_id AS CHAR), '-') AS submitter_name,
              stream.gmt_modified AS submitted_at,
              CAST(stream.submit_state AS CHAR) AS submit_status,
              stream.is_deleted
            FROM streamapp.rdos_stream_task stream
            LEFT JOIN streamapp.rdos_project project ON project.id = stream.project_id AND project.is_deleted = 0
            LEFT JOIN dt_pub_service.uic_user user ON user.id = stream.modify_user_id AND user.is_deleted = 'N'
            WHERE stream.is_deleted = 0
        ) task_meta
        ORDER BY project_space_code, module_type, task_name
        """
    )
    synced_count = 0
    for row in rows:
        project_space_code = str(row.get("project_space_code") or "")
        project_key = metadata_project_space_key(project_space_code, row.get("module_type"))
        project_space_id = project_ids.get(project_key)
        if project_space_id is None:
            continue
        execute_write(
            """
            INSERT INTO rc_task_metadata(
              env_id, project_space_id, project_space_code, project_space_name,
              module_type, task_id, task_name, task_type, task_type_label, node_pid,
              submit_status, submitter_id, submitter_name, submitted_at,
              is_deleted, is_enabled, last_synced_at
            )
            VALUES (
              :env_id, :project_space_id, :project_space_code, :project_space_name,
              :module_type, :task_id, :task_name, :task_type, :task_type_label, :node_pid,
              :submit_status, :submitter_id, :submitter_name, :submitted_at,
              :is_deleted, 1, CURRENT_TIMESTAMP
            )
            ON DUPLICATE KEY UPDATE
              project_space_id = VALUES(project_space_id),
              project_space_code = VALUES(project_space_code),
              project_space_name = VALUES(project_space_name),
              task_name = VALUES(task_name),
              task_type = VALUES(task_type),
              task_type_label = VALUES(task_type_label),
              node_pid = VALUES(node_pid),
              submit_status = VALUES(submit_status),
              submitter_id = VALUES(submitter_id),
              submitter_name = VALUES(submitter_name),
              submitted_at = VALUES(submitted_at),
              is_deleted = VALUES(is_deleted),
              is_enabled = 1,
              last_synced_at = CURRENT_TIMESTAMP,
              updated_at = CURRENT_TIMESTAMP
            """,
            {
                "env_id": env_id,
                "project_space_id": project_space_id,
                "project_space_code": project_space_code,
                "project_space_name": row.get("project_space_name") or "",
                "module_type": row.get("module_type") or "stream",
                "task_id": row.get("task_id") or "",
                "task_name": row.get("task_name") or "",
                "task_type": row.get("task_type"),
                "task_type_label": row.get("task_type_label") or "",
                "node_pid": row.get("node_pid") or "",
                "submit_status": row.get("submit_status") or "",
                "submitter_id": row.get("submitter_id") or "",
                "submitter_name": row.get("submitter_name") or "-",
                "submitted_at": row.get("submitted_at"),
                "is_deleted": int(row.get("is_deleted") or 0),
            },
        )
        synced_count += 1
    return synced_count


def generate_project_mapping_candidates() -> int:
    """按同名 + 同模块为测试/生产项目空间生成候选映射，不覆盖已确认结果。"""
    rows = fetch_all(
        """
        SELECT
          source.id AS source_project_space_id,
          source.project_name AS source_project_name,
          source.project_type AS source_project_type,
          source_env.id AS source_env_id,
          source_env.env_name AS source_env_name,
          target.id AS target_project_space_id,
          target.project_name AS target_project_name,
          target_env.id AS target_env_id,
          target_env.env_name AS target_env_name
        FROM rc_project_space source
        JOIN rc_environment source_env ON source_env.id = source.env_id AND source_env.env_type = 'test'
        JOIN rc_project_space target
          ON target.project_name = source.project_name
         AND target.project_type = source.project_type
        JOIN rc_environment target_env ON target_env.id = target.env_id AND target_env.env_type = 'prod'
        WHERE source.is_enabled = 1
          AND target.is_enabled = 1
        ORDER BY source.project_name, source.project_type
        """
    )
    generated = 0
    for row in rows:
        execute_write(
            """
            INSERT INTO rc_project_mapping(
              source_env_id, source_env_name, source_project_space_id, source_project_name,
              target_env_id, target_env_name, target_project_space_id, target_project_name,
              mapping_status, match_rule, last_synced_at, is_enabled
            )
            VALUES (
              :source_env_id, :source_env_name, :source_project_space_id, :source_project_name,
              :target_env_id, :target_env_name, :target_project_space_id, :target_project_name,
              'pending', 'same_name', CURRENT_TIMESTAMP, 1
            )
            ON DUPLICATE KEY UPDATE
              source_env_name = VALUES(source_env_name),
              source_project_name = VALUES(source_project_name),
              target_env_name = VALUES(target_env_name),
              target_project_name = VALUES(target_project_name),
              last_synced_at = CURRENT_TIMESTAMP,
              is_enabled = 1,
              mapping_status = CASE
                  WHEN mapping_status = 'confirmed' THEN mapping_status
                  ELSE 'pending'
              END,
              match_rule = CASE
                  WHEN mapping_status = 'confirmed' THEN match_rule
                  ELSE 'same_name'
              END,
              updated_at = CURRENT_TIMESTAMP
            """,
            row,
        )
        generated += 1
    return generated


def generate_datasource_mapping_candidates() -> int:
    """按已映射项目空间范围内的数据源同名 + 同类型生成候选映射。"""
    rows = fetch_all(
        """
        SELECT
          mapping.id AS project_mapping_id,
          source_resource.id AS source_resource_id,
          target_resource.id AS target_resource_id,
          source_resource.datasource_type,
          source_resource.datasource_key AS source_value,
          target_resource.datasource_key AS target_value
        FROM rc_project_mapping mapping
        JOIN rc_project_space source_project ON source_project.id = mapping.source_project_space_id
        JOIN rc_project_space target_project ON target_project.id = mapping.target_project_space_id
        JOIN rc_environment source_env ON source_env.id = source_project.env_id AND source_env.env_type = 'test'
        JOIN rc_environment target_env ON target_env.id = target_project.env_id AND target_env.env_type = 'prod'
        JOIN rc_datasource_resource source_resource
          ON source_resource.project_space_id = source_project.id
         AND source_resource.is_enabled = 1
         AND source_resource.is_meta = 0
        JOIN rc_datasource_resource target_resource
          ON target_resource.project_space_id = target_project.id
         AND target_resource.is_enabled = 1
         AND target_resource.is_meta = 0
         AND target_resource.datasource_name = source_resource.datasource_name
         AND target_resource.datasource_type = source_resource.datasource_type
        WHERE mapping.is_enabled = 1
        ORDER BY mapping.id, source_resource.datasource_name
        """
    )
    generated = 0
    for row in rows:
        execute_write(
            """
            INSERT INTO rc_datasource_mapping(
              project_mapping_id, source_datasource_resource_id, target_datasource_resource_id,
              datasource_type, source_pattern, target_value,
              mapping_status, match_rule, connectivity_status, last_synced_at, is_enabled
            )
            VALUES (
              :project_mapping_id, :source_resource_id, :target_resource_id,
              :datasource_type, :source_value, :target_value,
              'pending', 'same_name', 'unknown', CURRENT_TIMESTAMP, 1
            )
            ON DUPLICATE KEY UPDATE
              target_value = VALUES(target_value),
              last_synced_at = CURRENT_TIMESTAMP,
              is_enabled = 1,
              mapping_status = CASE
                  WHEN mapping_status = 'confirmed' THEN mapping_status
                  ELSE 'pending'
              END,
              match_rule = CASE
                  WHEN mapping_status = 'confirmed' THEN match_rule
                  ELSE 'same_name'
              END,
              updated_at = CURRENT_TIMESTAMP
            """,
            row,
        )
        generated += 1
    return generated


def sync_dtstack_metadata() -> dict[str, int | str]:
    """同步总入口：测试/生产双环境元数据只读拉取 → 平台库快照更新 → 自动生成候选映射。"""
    ensure_project_space_unique_key()
    if not is_test_metadata_configured():
        raise RuntimeError("请先配置 DTSTACK_TEST_METADATA_DATABASE_URL 后再同步测试元数据。")
    if not is_prod_metadata_configured():
        raise RuntimeError("请先配置 DTSTACK_PROD_METADATA_DATABASE_URL 后再同步生产元数据。")

    cleanup_legacy_demo_data_once()

    test_env_id = ensure_environment("test", "test_env", "测试环境")
    prod_env_id = ensure_environment("prod", "prod_env", "生产环境")

    test_project_count = sync_project_spaces_from_metadata(test_env_id, "test")
    prod_project_count = sync_project_spaces_from_metadata(prod_env_id, "prod")
    test_task_count = sync_task_metadata_from_metadata(test_env_id, "test")
    prod_task_count = sync_task_metadata_from_metadata(prod_env_id, "prod")
    test_datasource_count = sync_datasources_from_metadata(test_env_id, "test")
    prod_datasource_count = sync_datasources_from_metadata(prod_env_id, "prod")
    test_directory_count = sync_project_directories_from_metadata(test_env_id, "test")
    prod_directory_count = sync_project_directories_from_metadata(prod_env_id, "prod")
    repair_git_repo_project_space_links()
    repair_release_draft_project_space_links()
    project_mapping_count = generate_project_mapping_candidates()
    datasource_mapping_count = generate_datasource_mapping_candidates()

    upsert_app_setting("last_metadata_sync_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "最近一次测试/生产元数据同步时间")
    return {
        "project_count": test_project_count + prod_project_count,
        "task_count": test_task_count + prod_task_count,
        "datasource_count": test_datasource_count + prod_datasource_count,
        "directory_count": test_directory_count + prod_directory_count,
        "project_mapping_count": project_mapping_count,
        "datasource_mapping_count": datasource_mapping_count,
        "message": (
            f"同步完成：测试项目空间 {test_project_count} 个、生产项目空间 {prod_project_count} 个，"
            f"测试任务 {test_task_count} 个、生产任务 {prod_task_count} 个，"
            f"测试数据源 {test_datasource_count} 个、生产数据源 {prod_datasource_count} 个，"
            f"项目映射候选 {project_mapping_count} 条、数据源映射候选 {datasource_mapping_count} 条。"
        ),
    }


def sync_config_metadata() -> dict[str, int | str]:
    """配置页轻量同步入口。

    当前配置页主要依赖项目空间和全局数据源，不需要每次都全量刷新任务缓存和目录树。
    这里仅同步：
      - 测试/生产项目空间
      - 测试/生产全局数据源
      - 自动生成项目映射候选、数据源映射候选
    """
    ensure_project_space_unique_key()
    if not is_test_metadata_configured():
        raise RuntimeError("请先配置 DTSTACK_TEST_METADATA_DATABASE_URL 后再同步测试元数据。")
    if not is_prod_metadata_configured():
        raise RuntimeError("请先配置 DTSTACK_PROD_METADATA_DATABASE_URL 后再同步生产元数据。")

    cleanup_legacy_demo_data_once()

    test_env_id = ensure_environment("test", "test_env", "测试环境")
    prod_env_id = ensure_environment("prod", "prod_env", "生产环境")

    test_project_count = sync_project_spaces_from_metadata(test_env_id, "test")
    prod_project_count = sync_project_spaces_from_metadata(prod_env_id, "prod")
    test_datasource_count = sync_datasources_from_metadata(test_env_id, "test")
    prod_datasource_count = sync_datasources_from_metadata(prod_env_id, "prod")
    repair_git_repo_project_space_links()
    repair_release_draft_project_space_links()
    project_mapping_count = generate_project_mapping_candidates()
    datasource_mapping_count = generate_datasource_mapping_candidates()

    upsert_app_setting("last_metadata_sync_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "最近一次配置页轻量元数据同步时间")
    return {
        "project_count": test_project_count + prod_project_count,
        "task_count": 0,
        "datasource_count": test_datasource_count + prod_datasource_count,
        "directory_count": 0,
        "project_mapping_count": project_mapping_count,
        "datasource_mapping_count": datasource_mapping_count,
        "message": (
            f"轻量同步完成：测试项目空间 {test_project_count} 个、生产项目空间 {prod_project_count} 个，"
            f"测试数据源 {test_datasource_count} 个、生产数据源 {prod_datasource_count} 个，"
            f"项目映射候选 {project_mapping_count} 条、数据源映射候选 {datasource_mapping_count} 条。"
        ),
    }


def selected_project_space(project_space_id: str | None = None) -> dict[str, str]:
    project_spaces = get_source_project_spaces()
    if not project_spaces:
        return {}
    if project_space_id:
        for project_space in project_spaces:
            if str(project_space["id"]) == str(project_space_id):
                return project_space
    return project_spaces[0]



# ──── (f) Git 仓库/项目映射辅助 ────
# 读取 rc_git_repo/rc_release_batch/rc_project_mapping 等辅助函数
# 为任务列表查询提供 Git 上下文和映射状态

def git_repo_for_project(project_space: dict[str, str]) -> GitRepo | None:
    repo_id = project_space.get("repo_id")
    repo_url = project_space.get("repo_url")
    if not repo_id or not repo_url:
        return None
    return GitRepo(
        id=int(repo_id),
        repo_url=str(repo_url),
        default_branch=str(project_space.get("default_branch") or "main"),
        current_branch=str(project_space.get("current_branch") or project_space.get("default_branch") or "main"),
        module_type=normalize_module_type(str(project_space.get("project_type") or project_space.get("repo_module_type") or "")),
    )


def get_last_success_commit(project_space_id: str | int | None) -> str:
    if not project_space_id:
        return ""
    rows = fetch_all(
        """
        SELECT draft.head_commit AS git_commit_id
        FROM rc_release_draft draft
        WHERE draft.source_project_space_id = :project_space_id
          AND draft.head_commit IS NOT NULL
          AND draft.head_commit <> ''
        ORDER BY draft.created_at DESC, draft.id DESC
        LIMIT 1
        """,
        {"project_space_id": project_space_id},
    )
    return str(rows[0]["git_commit_id"]) if rows else ""


def has_project_mapping(project_space: dict[str, str]) -> bool:
    if not project_space.get("id"):
        return False
    rows = fetch_all(
        """
        SELECT id
        FROM rc_project_mapping
        WHERE source_project_space_id = :project_space_id
          AND is_enabled = 1
        LIMIT 1
        """,
        {"project_space_id": project_space["id"]},
    )
    return bool(rows)


def project_space_module_type(project_space_id: str | int | None) -> str:
    """项目空间模块类型统一以元数据为准，配置页不再让用户手工改离线/实时。"""
    if not project_space_id:
        return ""
    row = fetch_one(
        """
        SELECT project_type
        FROM rc_project_space
        WHERE id = :project_space_id
          AND is_enabled = 1
        LIMIT 1
        """,
        {"project_space_id": project_space_id},
    )
    return normalize_module_type(str(row.get("project_type") or "")) if row else ""


def mapped_target_project_name(project_space_id: str | int | None) -> str:
    if not project_space_id:
        return "-"
    row = fetch_one(
        """
        SELECT target_project_name
        FROM rc_project_mapping
        WHERE source_project_space_id = :project_space_id
          AND is_enabled = 1
        ORDER BY
          CASE WHEN mapping_status = 'confirmed' THEN 0 ELSE 1 END,
          id
        LIMIT 1
        """,
        {"project_space_id": project_space_id},
    )
    return str(row.get("target_project_name") or "-") if row else "-"


def directory_status_for_task(
    project_space: dict[str, str],
    task_detail: dict[str, str] | None = None,
    git_directory: str = "",
) -> str:
    """优先通过任务元数据里的 node_pid 反查真实目录，再判断测试/生产层级是否一致。"""
    if not project_space.get("id"):
        return "已匹配"

    if task_detail and task_detail.get("node_pid"):
        rows = fetch_all(
            """
            SELECT source_dir.id AS source_directory_id, target_dir.id AS target_directory_id
            FROM rc_project_mapping mapping
            JOIN rc_project_directory source_dir
              ON source_dir.project_space_id = mapping.source_project_space_id
             AND source_dir.directory_id = :node_pid
             AND source_dir.module_type = :module_type
             AND source_dir.is_enabled = 1
            JOIN rc_project_directory target_dir
              ON target_dir.project_space_id = mapping.target_project_space_id
             AND target_dir.relative_path = source_dir.relative_path
             AND target_dir.module_type = source_dir.module_type
             AND target_dir.is_enabled = 1
            WHERE mapping.source_project_space_id = :project_space_id
              AND mapping.is_enabled = 1
            LIMIT 1
            """,
            {
                "project_space_id": project_space["id"],
                "node_pid": task_detail["node_pid"],
                "module_type": task_detail.get("module_type") or "stream",
            },
        )
        return "已匹配" if rows else "不一致"

    if not git_directory:
        return "已匹配"
    rows = fetch_all(
        """
        SELECT source_dir.id AS source_directory_id, target_dir.id AS target_directory_id
        FROM rc_project_mapping mapping
        JOIN rc_project_directory source_dir
          ON source_dir.project_space_id = mapping.source_project_space_id
         AND source_dir.relative_path = :relative_path
         AND source_dir.is_enabled = 1
        JOIN rc_project_directory target_dir
          ON target_dir.project_space_id = mapping.target_project_space_id
         AND target_dir.relative_path = source_dir.relative_path
         AND target_dir.is_enabled = 1
        WHERE mapping.source_project_space_id = :project_space_id
          AND mapping.is_enabled = 1
        LIMIT 1
        """,
        {"project_space_id": project_space["id"], "relative_path": git_directory},
    )
    return "已匹配" if rows else "不一致"


def datasource_status_for_project(project_space_id: str | int | None) -> str:
    if not project_space_id:
        return "缺失"
    rows = fetch_all(
        """
        SELECT COUNT(*) AS mapping_count
        FROM rc_datasource_mapping mapping
        JOIN rc_project_mapping project_mapping ON project_mapping.id = mapping.project_mapping_id
        WHERE project_mapping.source_project_space_id = :project_space_id
          AND project_mapping.is_enabled = 1
          AND mapping.is_enabled = 1
        """,
        {"project_space_id": project_space_id},
    )
    return "已匹配" if int(rows[0]["mapping_count"] or 0) > 0 else "缺失"


def release_status_by_source_id() -> dict[str, str]:
    rows = fetch_all(
        """
        SELECT source_task_id, release_status
        FROM rc_release_task
        WHERE source_task_id IS NOT NULL
        """
    )
    return {str(row["source_task_id"]): str(row["release_status"]) for row in rows}


def fake_git_task_id(candidate: dict[str, str]) -> str:
    """Git 侧跑通阶段暂不查数栈任务 ID，用文件路径生成稳定占位 ID。"""
    module_type = candidate.get("module_type") or ("offline" if candidate.get("module") == "离线" else "stream")
    digest = hashlib.sha1(str(candidate.get("git_path") or candidate.get("name") or "").encode("utf-8")).hexdigest()[:12]
    return f"git:{module_type}:{digest}"


def build_git_api_snapshot(
    repo: GitRepo | None,
    project_space: dict[str, str] | None,
    last_success_commit: str,
    refresh_git: bool,
) -> dict[str, object]:
    """优先使用 GitLab API 生成任务页 Git 快照；首次发布保留本地 Git 兜底。"""
    if repo is None or not project_space:
        return {
            "status": "warning",
            "message": "当前项目空间未配置 Git 仓库。",
            "repo_url": repo.repo_url if repo else "",
            "branch": repo.current_branch if repo else "",
            "module_type": repo.module_type if repo else "",
            "module_label": module_label_from_type(repo.module_type) if repo else "",
            "worktree": "",
            "last_success_commit": last_success_commit or "-",
            "current_commit": "-",
            "changed_files": [],
            "changed_file_count": 0,
            "task_candidate_count": 0,
            "refresh_git": refresh_git,
            "changed_by_api": False,
        }

    repo_row = fetch_one(
        """
        SELECT gitlab_project_id, gitlab_project_path, api_sync_mode
        FROM rc_git_repo
        WHERE id = :repo_id
        LIMIT 1
        """,
        {"repo_id": repo.id},
    ) or {}
    project_ref = str(repo_row.get("gitlab_project_id") or repo_row.get("gitlab_project_path") or "")
    if int(repo_row.get("api_sync_mode") or 0) == 1 and project_ref:
        try:
            service = gitlab_api_service()
            commits = service.list_commits(project_ref, repo.current_branch, page=1, per_page=1)
            current_commit = str(commits[0]["commit_id"]) if commits else ""
            changed_files: list[str] = []
            message = ""
            changed_by_api = False
            if last_success_commit and current_commit:
                compare = service.compare_commits(project_ref, last_success_commit, current_commit)
                changed_files = [path for path in compare.get("changed_files") or [] if str(path).lower().endswith(".sql")]
                changed_by_api = True
                message = "GitLab API 差异读取完成。" if changed_files else "GitLab API 差异读取完成：未发现 SQL 变更文件。"
            else:
                fallback = build_git_snapshot(repo, last_success_commit, refresh_git, saved_git_credential())
                changed_files = fallback.changed_files
                current_commit = fallback.current_commit
                message = (
                    "首次发布暂未配置基准 Commit，已使用本地 Git 兜底读取当前分支全部 SQL 文件。"
                    if changed_files else
                    "首次发布暂未配置基准 Commit，当前分支没有可识别 SQL 文件。"
                )
            return {
                "status": "success",
                "message": message,
                "repo_url": repo.repo_url,
                "branch": repo.current_branch,
                "module_type": repo.module_type,
                "module_label": module_label_from_type(repo.module_type),
                "worktree": "",
                "last_success_commit": last_success_commit or "-",
                "current_commit": current_commit or "-",
                "changed_files": changed_files,
                "changed_file_count": len(changed_files),
                "task_candidate_count": 0,
                "refresh_git": refresh_git,
                "changed_by_api": changed_by_api,
            }
        except Exception as exc:
            fallback = build_git_snapshot(repo, last_success_commit, refresh_git, saved_git_credential())
            return {
                "status": fallback.status,
                "message": f"GitLab API 读取失败，已切换本地 Git 兜底：{exc}",
                "repo_url": repo.repo_url,
                "branch": repo.current_branch,
                "module_type": repo.module_type,
                "module_label": module_label_from_type(repo.module_type),
                "worktree": str(fallback.worktree or ""),
                "last_success_commit": fallback.last_success_commit or "-",
                "current_commit": fallback.current_commit or "-",
                "changed_files": fallback.changed_files,
                "changed_file_count": len(fallback.changed_files),
                "task_candidate_count": 0,
                "refresh_git": refresh_git,
                "changed_by_api": False,
            }

    fallback = build_git_snapshot(repo, last_success_commit, refresh_git, saved_git_credential())
    return {
        "status": fallback.status,
        "message": fallback.message,
        "repo_url": repo.repo_url,
        "branch": repo.current_branch,
        "module_type": repo.module_type,
        "module_label": module_label_from_type(repo.module_type),
        "worktree": str(fallback.worktree or ""),
        "last_success_commit": fallback.last_success_commit or "-",
        "current_commit": fallback.current_commit or "-",
        "changed_files": fallback.changed_files,
        "changed_file_count": len(fallback.changed_files),
        "task_candidate_count": 0,
        "refresh_git": refresh_git,
        "changed_by_api": False,
    }



# ──── (g) 任务列表查询 ────
# 核心入口：get_git_diff_task_plan()，被 main.py /tasks 路由调用
# 串起：get_source_project_spaces → selected_project_space → get_last_success_commit
#      → git_repo_for_project → build_git_snapshot → build_changed_task_candidates
#      → merge_git_candidates_with_metadata
# 返回：{project_spaces, selected_project_space, tasks, git_context}

def get_release_tasks() -> list[dict[str, str]]:
    return fetch_all(
        """
        SELECT task.source_task_id AS id, task.source_task_id, task.task_name AS name,
               '-' AS task_type_label, task.task_type AS module,
               COALESCE(batch.source_project_name, '-') AS project_space,
               NULL AS project_id, task.source_submit_time AS submitted_at, task.submitter,
               task.release_status AS status
        FROM rc_release_task task
        LEFT JOIN rc_release_batch batch ON task.batch_id = batch.id
        ORDER BY task.source_submit_time DESC
        """
    )


def get_task_metadata_rows(project_space_db_id: str | None = None, module_type: str | None = None) -> list[dict[str, str]]:
    """只读平台库中的测试环境任务缓存，供首页和 Git 扫描结果匹配复用。"""
    params = {
        "project_space_id": project_space_db_id or "",
        "module_type": normalize_module_type(module_type) or "",
    }
    return fetch_all(
        """
        SELECT
          CONCAT(meta.module_type, ':', meta.task_id) AS id,
          CONCAT(meta.module_type, ':', meta.task_id) AS source_task_id,
          meta.task_name AS name,
          meta.task_type_label,
          CASE
            WHEN meta.module_type = 'offline' THEN '离线'
            WHEN meta.module_type = 'stream' THEN '实时'
            ELSE meta.module_type
          END AS module,
          meta.project_space_name AS project_space,
          meta.project_space_code AS project_id,
          COALESCE(DATE_FORMAT(meta.submitted_at, '%Y-%m-%d %H:%i:%s'), '-') AS submitted_at,
          COALESCE(NULLIF(meta.submitter_name, ''), '-') AS submitter,
          meta.node_pid,
          meta.module_type,
          meta.submit_status
        FROM rc_task_metadata meta
        JOIN rc_environment env ON env.id = meta.env_id
        WHERE env.env_type = 'test'
          AND meta.is_enabled = 1
          AND meta.is_deleted = 0
          AND (:project_space_id = '' OR CAST(meta.project_space_id AS CHAR) = :project_space_id)
          AND (:module_type = '' OR meta.module_type = :module_type)
        ORDER BY meta.submitted_at DESC, meta.id DESC
        """,
        params,
    )


def get_dtstack_submitted_tasks() -> list[dict[str, str]]:
    rows = get_task_metadata_rows()
    statuses = release_status_by_source_id()
    for row in rows:
        raw_id = str(row.get("source_task_id") or "")
        row["status"] = statuses.get(raw_id, statuses.get(str(row.get("id") or ""), "未发布"))
    return rows


def get_dtstack_task_detail(task_name: str, module_type: str, project_space_id: str | None = None) -> dict[str, str]:
    normalized_module = normalize_module_type(module_type)
    rows = get_task_metadata_rows(project_space_id, normalized_module)
    matched_rows = [row for row in rows if str(row.get("name") or "") == task_name]
    if matched_rows:
        row = matched_rows[0]
        return {
            "id": str(row["id"]),
            "source_task_id": str(row["source_task_id"]),
            "name": str(row["name"]),
            "task_type_label": str(row["task_type_label"]),
            "module": str(row["module"]),
            "project_space": str(row["project_space"]),
            "project_id": str(row["project_id"]),
            "submitted_at": str(row["submitted_at"]),
            "submitter": str(row["submitter"]),
            "node_pid": str(row.get("node_pid") or ""),
            "module_type": str(row.get("module_type") or normalized_module),
        }
    return {}


def merge_git_candidates_with_metadata(
    candidates: list[dict[str, str]],
    project_space: dict[str, str],
) -> list[dict[str, str]]:
    statuses = release_status_by_source_id()
    try:
        datasource_status = datasource_status_for_project(project_space.get("id"))
    except Exception:
        datasource_status = "缺失"
    tasks: list[dict[str, str]] = []
    for candidate in candidates:
        detail = get_dtstack_task_detail(
            str(candidate["name"]),
            str(candidate.get("module_type") or ""),
            str(project_space.get("id") or ""),
        ) if is_database_configured() else {}
        try:
            directory_status = directory_status_for_task(project_space, detail, candidate.get("git_directory", ""))
        except Exception:
            directory_status = "不一致"
        source_task_id = str(detail.get("source_task_id") or fake_git_task_id(candidate))
        tasks.append({
            "id": str(detail.get("id") or source_task_id),
            "source_task_id": source_task_id,
            "name": str(detail.get("name") or candidate["name"]),
            "module_type": str(candidate.get("module_type") or ("offline" if candidate.get("module") == "离线" else "stream")),
            "task_type_label": str(detail.get("task_type_label") or candidate["task_type_label"]),
            "module": str(detail.get("module") or candidate["module"]),
            "project_space": str(detail.get("project_space") or project_space.get("project_name") or "-"),
            "submitted_at": str(detail.get("submitted_at") or candidate.get("submitted_at") or "-"),
            "submitter": str(detail.get("submitter") or candidate.get("submitter") or "-"),
            "status": statuses.get(source_task_id, "未发布"),
            "node_pid": str(detail.get("node_pid") or ""),
            "git_path": candidate["git_path"],
            "git_directory": candidate.get("git_directory", ""),
            "artifact_kind": candidate.get("artifact_kind", ""),
            "release_file_status": candidate.get("release_file_status", "已就绪"),
            "required_artifact": candidate.get("required_artifact", ""),
            "metadata_status": "已匹配" if detail else "Git侧待匹配",
            "directory_status": directory_status,
            "datasource_status": datasource_status,
        })
    return tasks


def get_git_diff_task_plan(project_space_id: str | None = None, refresh_git: bool = False) -> dict[str, object]:
    """任务确认页主查询：串起项目空间 → Git 差异 → 候选任务 → 元数据合并。

    调用链：get_source_project_spaces → selected_project_space → get_last_success_commit
           → git_repo_for_project → build_git_snapshot → build_changed_task_candidates
           → merge_git_candidates_with_metadata
    被 main.py /tasks 路由调用，返回 {project_spaces, selected_project_space, tasks, git_context}。
    """
    project_spaces = get_source_project_spaces()
    project_space = selected_project_space(project_space_id)
    last_success_commit = get_last_success_commit(project_space.get("id")) if project_space else ""
    repo = git_repo_for_project(project_space) if project_space else None
    git_context = build_git_api_snapshot(repo, project_space, last_success_commit, refresh_git)
    commit_info_by_file: dict[str, dict[str, str]] = {}
    if git_context.get("worktree"):
        worktree_path = Path(str(git_context.get("worktree")))
        for file_path in git_context.get("changed_files") or []:
            commit_info_by_file[str(file_path)] = latest_commit_info_for_file(worktree_path, str(file_path))
    candidates = build_changed_task_candidates(
        list(git_context.get("changed_files") or []),
        repo.module_type if repo else "",
        commit_info_by_file,
    )
    tasks = merge_git_candidates_with_metadata(candidates, project_space) if project_space else []
    git_context["task_candidate_count"] = len(candidates)
    return {
        "project_spaces": project_spaces,
        "selected_project_space": project_space,
        "tasks": tasks,
        "git_context": git_context,
    }



# ──── (h) 发布草稿持久化 ────
# 核心入口：persist_release_draft()，被 main.py /tasks 路由（refresh=1 时）调用
# 流程：INSERT rc_release_draft → 循环 INSERT rc_task_artifact + rc_release_draft_task
#      → release_draft.build_validation_items → INSERT rc_release_validation
#      → release_draft.summarize_validation_status → UPDATE rc_release_draft.scan_status
# 另有 get_release_draft_model() 供确认页读取草稿

def persist_release_draft(task_plan: dict[str, object], username: str) -> dict[str, object]:
    """把一次 Git 扫描结果保存为发布草稿，后续确认页只读取 Web 元数据库。"""
    project_space = task_plan.get("selected_project_space") or {}
    git_context = task_plan.get("git_context") or {}
    tasks = task_plan.get("tasks") or []
    if not isinstance(project_space, dict) or not isinstance(git_context, dict) or not isinstance(tasks, list):
        return {"draft_id": "", "status": "校验阻断", "message": "发布草稿数据结构异常。"}

    try:
        mapping_exists = has_project_mapping(project_space)
        draft_code = f"DRAFT_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        draft_id = execute_insert(
            """
            INSERT INTO rc_release_draft(
              draft_code, source_project_space_id, source_project_name, repo_id,
              base_commit, head_commit, changed_file_count, task_count, scan_status, created_by
            )
            VALUES (
              :draft_code, :source_project_space_id, :source_project_name, :repo_id,
              :base_commit, :head_commit, :changed_file_count, :task_count, :scan_status, :created_by
            )
            """,
            {
                "draft_code": draft_code,
                "source_project_space_id": project_space.get("id"),
                "source_project_name": project_space.get("project_name") or "",
                "repo_id": project_space.get("repo_id"),
                "base_commit": git_context.get("last_success_commit") or "",
                "head_commit": git_context.get("current_commit") or "",
                "changed_file_count": git_context.get("changed_file_count") or 0,
                "task_count": len(tasks),
                "scan_status": git_context.get("status") or "warning",
                "created_by": username,
            },
        )

        all_validation_items: list[dict[str, object]] = []
        for task in tasks:
            if not isinstance(task, dict):
                continue
            execute_insert(
                """
                INSERT INTO rc_task_artifact(
                  repo_id, commit_id, project_space_id, task_name, module_type, task_type_label,
                  git_path, git_directory, artifact_kind, parse_status, parse_message
                )
                VALUES (
                  :repo_id, :commit_id, :project_space_id, :task_name, :module_type, :task_type_label,
                  :git_path, :git_directory, :artifact_kind, :parse_status, :parse_message
                )
                """,
                {
                    "repo_id": project_space.get("repo_id"),
                    "commit_id": git_context.get("current_commit") or "",
                    "project_space_id": project_space.get("id"),
                    "task_name": task.get("name") or "",
                    "module_type": task.get("module_type") or "",
                    "task_type_label": task.get("task_type_label") or "",
                    "git_path": task.get("git_path") or "",
                    "git_directory": task.get("git_directory") or "",
                    "artifact_kind": task.get("artifact_kind") or "",
                    "parse_status": "已解析" if task.get("release_file_status") == "已就绪" else "缺少发布文件",
                    "parse_message": task.get("required_artifact") or "",
                },
            )
            draft_task_id = execute_insert(
                """
                INSERT INTO rc_release_draft_task(
                  draft_id, task_key, source_task_id, task_name, module_type, task_type_label,
                  project_space_name, git_path, git_directory, artifact_kind, release_file_status,
                  metadata_status, directory_status, datasource_status, submitted_at, submitter, release_status, is_selected
                )
                VALUES (
                  :draft_id, :task_key, :source_task_id, :task_name, :module_type, :task_type_label,
                  :project_space_name, :git_path, :git_directory, :artifact_kind, :release_file_status,
                  :metadata_status, :directory_status, :datasource_status, :submitted_at, :submitter, :release_status, 1
                )
                """,
                {
                    "draft_id": draft_id,
                    "task_key": task.get("id") or "",
                    "source_task_id": task.get("source_task_id") or "",
                    "task_name": task.get("name") or "",
                    "module_type": task.get("module_type") or "",
                    "task_type_label": task.get("task_type_label") or "",
                    "project_space_name": task.get("project_space") or "",
                    "git_path": task.get("git_path") or "",
                    "git_directory": task.get("git_directory") or "",
                    "artifact_kind": task.get("artifact_kind") or "",
                    "release_file_status": task.get("release_file_status") or "",
                    "metadata_status": task.get("metadata_status") or "",
                    "directory_status": task.get("directory_status") or "",
                    "datasource_status": task.get("datasource_status") or "",
                    "submitted_at": None if task.get("submitted_at") == "-" else task.get("submitted_at"),
                    "submitter": task.get("submitter") or "",
                    "release_status": task.get("status") or "未发布",
                },
            )
            validation_items = build_validation_items(task, has_project_mapping=mapping_exists)
            all_validation_items.extend(validation_items)
            for item in validation_items:
                execute_write(
                    """
                    INSERT INTO rc_release_validation(
                      draft_id, draft_task_id, check_key, check_name, check_status, is_blocking, message
                    )
                    VALUES (
                      :draft_id, :draft_task_id, :check_key, :check_name, :check_status, :is_blocking, :message
                    )
                    """,
                    {
                        "draft_id": draft_id,
                        "draft_task_id": draft_task_id,
                        "check_key": item["check_key"],
                        "check_name": item["check_name"],
                        "check_status": item["status"],
                        "is_blocking": 1 if item["is_blocking"] else 0,
                        "message": item["message"],
                    },
                )

        status = summarize_validation_status(all_validation_items)
        execute_write(
            """
            UPDATE rc_release_draft
            SET scan_status = :scan_status
            WHERE id = :draft_id
            """,
            {"scan_status": status, "draft_id": draft_id},
        )
        return {"draft_id": draft_id, "draft_code": draft_code, "status": status, "message": "发布草稿已生成。"}
    except Exception as exc:
        return {"draft_id": "", "status": "校验阻断", "message": schema_hint(exc)}


def get_release_draft_model(draft_id: str | None) -> dict[str, object]:
    if not draft_id:
        return {"draft": {}, "tasks": [], "validations": [], "validation_groups": {}}
    draft_rows = fetch_all(
        """
        SELECT *
        FROM rc_release_draft
        WHERE id = :draft_id
        LIMIT 1
        """,
        {"draft_id": draft_id},
    )
    if not draft_rows:
        return {"draft": {}, "tasks": [], "validations": [], "validation_groups": {}}

    tasks = fetch_all(
        """
        SELECT id, task_key AS source_id, source_task_id, task_name AS name, module_type,
               CASE
                 WHEN module_type = 'offline' THEN '离线'
                 WHEN module_type IN ('stream', 'realtime') THEN '实时'
                 ELSE module_type
               END AS module,
               task_type_label, project_space_name AS project_space, git_path, git_directory,
               artifact_kind, release_file_status, metadata_status, directory_status,
               datasource_status,
               COALESCE(DATE_FORMAT(submitted_at, '%Y-%m-%d %H:%i:%s'), '-') AS submitted_at,
               COALESCE(NULLIF(submitter, ''), '-') AS submitter,
               release_status AS status
        FROM rc_release_draft_task
        WHERE draft_id = :draft_id
        ORDER BY id
        """,
        {"draft_id": draft_id},
    )
    validations = fetch_all(
        """
        SELECT draft_task_id, check_key, check_name, check_status AS status, is_blocking, message
        FROM rc_release_validation
        WHERE draft_id = :draft_id
        ORDER BY draft_task_id, id
        """,
        {"draft_id": draft_id},
    )
    groups: dict[str, list[dict[str, object]]] = {}
    for item in validations:
        groups.setdefault(str(item["draft_task_id"]), []).append(item)
    return {"draft": draft_rows[0], "tasks": tasks, "validations": validations, "validation_groups": groups}


def get_release_draft_preview(draft_id: str | None) -> dict[str, object]:
    """任务列表页只需要草稿头和任务快照，不加载校验明细，减少项目空间切换耗时。"""
    if not draft_id:
        return {"draft": {}, "tasks": []}
    draft_rows = fetch_all(
        """
        SELECT *
        FROM rc_release_draft
        WHERE id = :draft_id
        LIMIT 1
        """,
        {"draft_id": draft_id},
    )
    if not draft_rows:
        return {"draft": {}, "tasks": []}
    tasks = fetch_all(
        """
        SELECT id, task_key AS source_id, source_task_id, task_name AS name, module_type,
               CASE
                 WHEN module_type = 'offline' THEN '离线'
                 WHEN module_type IN ('stream', 'realtime') THEN '实时'
                 ELSE module_type
               END AS module,
               task_type_label, project_space_name AS project_space, git_path, git_directory,
               artifact_kind, release_file_status, metadata_status, directory_status,
               datasource_status,
               COALESCE(DATE_FORMAT(submitted_at, '%Y-%m-%d %H:%i:%s'), '-') AS submitted_at,
               COALESCE(NULLIF(submitter, ''), '-') AS submitter,
               release_status AS status
        FROM rc_release_draft_task
        WHERE draft_id = :draft_id
        ORDER BY id
        """,
        {"draft_id": draft_id},
    )
    return {"draft": draft_rows[0], "tasks": tasks}


def get_latest_release_draft_model(project_space_db_id: str | int | None) -> dict[str, object]:
    if not project_space_db_id:
        return {"draft": {}, "tasks": [], "validations": [], "validation_groups": {}}
    rows = fetch_all(
        """
        SELECT id
        FROM rc_release_draft
        WHERE source_project_space_id = :project_space_id
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        {"project_space_id": project_space_db_id},
    )
    if not rows:
        return {"draft": {}, "tasks": [], "validations": [], "validation_groups": {}}
    return get_release_draft_model(str(rows[0]["id"]))


def get_latest_release_draft_preview(project_space_db_id: str | int | None) -> dict[str, object]:
    if not project_space_db_id:
        return {"draft": {}, "tasks": []}
    rows = fetch_all(
        """
        SELECT id
        FROM rc_release_draft
        WHERE source_project_space_id = :project_space_id
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        {"project_space_id": project_space_db_id},
    )
    if not rows:
        return {"draft": {}, "tasks": []}
    return get_release_draft_preview(str(rows[0]["id"]))


def get_tasks(project_space_id: str | None = None, refresh_git: bool = False) -> list[dict[str, str]]:
    plan = get_git_diff_task_plan(project_space_id, refresh_git)
    tasks = plan["tasks"]
    if isinstance(tasks, list):
        return tasks
    return []



# ──── (j) 首页看板 ────
# 核心入口：get_home_overview()，被 main.py /home 路由调用
# 按 all/realtime/offline/project 四种视图组装 stats 卡片 + tasks + records + daily_release_chart

def get_home_tasks() -> list[dict[str, str]]:
    try:
        rows = get_dtstack_submitted_tasks()
        return rows or get_release_tasks()
    except Exception:
        return get_release_tasks()


def filter_home_tasks(tasks: list[dict[str, str]], home_view: str) -> list[dict[str, str]]:
    module_filter = {"realtime": "实时", "offline": "离线"}.get(home_view)
    if module_filter:
        return [task for task in tasks if task.get("module") == module_filter]
    return tasks


def get_home_task_preview(home_view: str) -> list[dict[str, str]]:
    return filter_home_tasks(get_home_tasks(), home_view)[:8]


def get_datasource_resource_summary() -> list[dict[str, str]]:
    return fetch_all(
        """
        SELECT resource.source_module,
               resource.project_space_id,
               resource.project_space_name,
               COUNT(DISTINCT resource.datasource_id) AS datasource_count
        FROM rc_datasource_resource resource
        JOIN rc_environment env ON resource.env_id = env.id
        WHERE resource.is_enabled = 1
          AND resource.is_meta = 0
          AND env.is_enabled = 1
          AND resource.project_space_id IS NOT NULL
        GROUP BY resource.source_module, resource.project_space_id, resource.project_space_name
        ORDER BY resource.project_space_name, resource.source_module
        """
    )


def record_date_value(value: object) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    return text[:10] if len(text) >= 10 else ""


def get_daily_release_chart(records: list[dict[str, str]]) -> list[dict[str, str | int]]:
    grouped: dict[str, dict[str, int]] = {}
    for record in records:
        day = record_date_value(record.get("start"))
        if not day:
            continue
        stats = grouped.setdefault(day, {"total": 0, "success": 0, "failed": 0})
        stats["total"] += 1
        if record.get("status") == "成功":
            stats["success"] += 1
        elif record.get("status") == "失败":
            stats["failed"] += 1

    if not grouped:
        today = date.today()
        for offset in range(6, -1, -1):
            day = (today - timedelta(days=offset)).isoformat()
            grouped[day] = {"total": 0, "success": 0, "failed": 0}

    days = sorted(grouped.keys())[-7:]
    max_total = max([grouped[day]["total"] for day in days] or [1]) or 1
    chart = []
    for day in days:
        total = grouped[day]["total"]
        chart.append({
            "date": day,
            "label": day[5:],
            "total": total,
            "success": grouped[day]["success"],
            "failed": grouped[day]["failed"],
            "height": max(8, round(total / max_total * 100)) if total else 8,
        })
    return chart


def get_home_overview(home_view: str) -> dict[str, object]:
    """首页总览：按 all/realtime/offline/project 四种视图组装 stats 卡片 + tasks + records + 柱状图。

    被 main.py /home 路由调用，返回含 home_view/home_view_tabs/home_stats/tasks/records/
    project_spaces/datasource_summary/daily_release_chart 的 dict。
    """
    view = home_view if home_view in {"all", "realtime", "offline", "project"} else "all"
    project_spaces = get_source_project_spaces()
    records = get_records()
    all_tasks = get_home_tasks()
    filtered_tasks = filter_home_tasks(all_tasks, view)
    tasks = filtered_tasks[:8]
    datasource_summary = get_datasource_resource_summary()

    def is_realtime_space(space: dict[str, str]) -> bool:
        value = str(space.get("repo_module_type") or space.get("project_type") or space.get("module_tone") or "").lower()
        return value in {"stream", "realtime"}

    def is_offline_space(space: dict[str, str]) -> bool:
        value = str(space.get("repo_module_type") or space.get("project_type") or space.get("module_tone") or "").lower()
        return value == "offline"

    if view == "realtime":
        visible_project_spaces = [space for space in project_spaces if is_realtime_space(space)]
    elif view == "offline":
        visible_project_spaces = [space for space in project_spaces if is_offline_space(space)]
    else:
        visible_project_spaces = project_spaces

    project_space_count = len(visible_project_spaces)
    repo_count = len([space for space in visible_project_spaces if space.get("repo_url")])
    realtime_task_count = len([task for task in all_tasks if task.get("module") == "实时"])
    offline_task_count = len([task for task in all_tasks if task.get("module") == "离线"])
    visible_task_count = len(filtered_tasks)
    realtime_datasource_count = sum(
        int(row["datasource_count"]) for row in datasource_summary if row["source_module"] == "stream"
    )
    offline_datasource_count = sum(
        int(row["datasource_count"]) for row in datasource_summary if row["source_module"] == "offline"
    )
    success_count = len([record for record in records if record["status"] == "成功"])
    last_sync_at = app_setting_value("last_metadata_sync_at") or "-"

    if view == "realtime":
        stats = [
            {"label": "实时任务", "value": str(visible_task_count), "tone": "blue", "icon": "▣", "desc": "只统计实时模块"},
            {"label": "实时数据源", "value": str(realtime_datasource_count), "tone": "green", "icon": "◎", "desc": "按项目空间归属"},
            {"label": "项目空间", "value": str(project_space_count), "tone": "orange", "icon": "⌬", "desc": "每个空间对应仓库"},
            {"label": "发布成功", "value": str(success_count), "tone": "green", "icon": "✓", "desc": "全部发布记录"},
        ]
    elif view == "offline":
        stats = [
            {"label": "离线任务", "value": str(visible_task_count), "tone": "blue", "icon": "▣", "desc": "只统计离线模块"},
            {"label": "离线数据源", "value": str(offline_datasource_count), "tone": "green", "icon": "◎", "desc": "按项目空间归属"},
            {"label": "项目空间", "value": str(project_space_count), "tone": "orange", "icon": "⌬", "desc": "每个空间对应仓库"},
            {"label": "发布成功", "value": str(success_count), "tone": "green", "icon": "✓", "desc": "全部发布记录"},
        ]
    elif view == "project":
        stats = [
            {"label": "项目空间", "value": str(project_space_count), "tone": "blue", "icon": "⌬", "desc": "测试侧可发布空间"},
            {"label": "已配仓库", "value": str(repo_count), "tone": "green", "icon": "⌁", "desc": "空间与 Git 仓库"},
            {"label": "实时数据源", "value": str(realtime_datasource_count), "tone": "orange", "icon": "◎", "desc": "stream 模块"},
            {"label": "离线数据源", "value": str(offline_datasource_count), "tone": "orange", "icon": "▤", "desc": "offline 模块"},
        ]
    else:
        stats = [
            {"label": "项目空间", "value": str(project_space_count), "tone": "blue", "icon": "⌬", "desc": "每个空间对应 Git 仓库"},
            {"label": "实时任务", "value": str(realtime_task_count), "tone": "green", "icon": "▣", "desc": "实时模块"},
            {"label": "离线任务", "value": str(offline_task_count), "tone": "orange", "icon": "▤", "desc": "离线模块"},
            {"label": "发布成功", "value": str(success_count), "tone": "green", "icon": "✓", "desc": "全部发布记录"},
        ]

    return {
        "home_view": view,
        "home_view_tabs": [
            {"key": "all", "label": "全部"},
            {"key": "realtime", "label": "实时"},
            {"key": "offline", "label": "离线"},
            {"key": "project", "label": "项目空间"},
        ],
        "home_stats": stats,
        "records": records[:5],
        "tasks": tasks,
        "project_spaces": visible_project_spaces,
        "datasource_summary": datasource_summary,
        "daily_release_chart": get_daily_release_chart(records),
        "project_space_count": project_space_count,
        "last_metadata_sync_at": last_sync_at,
    }


def get_task_stats(tasks: list[dict[str, str]]) -> list[dict[str, str]]:
    success = len([task for task in tasks if task["status"] == "已发布"])
    failed = len([task for task in tasks if "失败" in task["status"]])
    unpublished = len([task for task in tasks if task["status"] == "未发布"])
    return [
        {"key": "total", "filter": "", "label": "总任务数", "value": str(len(tasks)), "tone": "blue", "icon": "▣"},
        {"key": "unpublished", "filter": "未发布", "label": "未发布", "value": str(unpublished), "tone": "orange", "icon": "▰"},
        {"key": "published", "filter": "已发布", "label": "发布成功", "value": str(success), "tone": "green", "icon": "✓"},
        {"key": "failed", "filter": "发布失败", "label": "发布失败", "value": str(failed), "tone": "red", "icon": "×"},
    ]



# ──── (k) 数据源映射与资源 ────
# 读取 rc_datasource_mapping + rc_datasource_resource，展示数据源映射和下拉选项
# 被 main.py /confirm 和 /config 页面调用

def get_datasource_mappings() -> list[dict[str, str]]:
    return fetch_all(
        """
        SELECT
          mapping.id,
          COALESCE(source_resource.datasource_key, mapping.source_pattern) AS source,
          COALESCE(target_resource.datasource_key, mapping.target_value) AS target,
          mapping.datasource_type AS type,
          COALESCE(source_project.project_name, '-') AS source_project_name,
          COALESCE(target_project.project_name, '-') AS target_project_name,
          COALESCE(mapping.mapping_status, 'pending') AS mapping_status,
          COALESCE(mapping.match_rule, '-') AS match_rule,
          COALESCE(mapping.connectivity_status, 'unknown') AS connectivity_status,
          COALESCE(DATE_FORMAT(mapping.last_synced_at, '%Y-%m-%d %H:%i:%s'), '-') AS last_synced_at,
          mapping.source_datasource_resource_id AS source_resource_id,
          mapping.target_datasource_resource_id AS target_resource_id,
          CASE WHEN mapping.connectivity_status = 'connected' THEN 1 ELSE 0 END AS connected
        FROM rc_datasource_mapping mapping
        LEFT JOIN rc_datasource_resource source_resource ON source_resource.id = mapping.source_datasource_resource_id
        LEFT JOIN rc_datasource_resource target_resource ON target_resource.id = mapping.target_datasource_resource_id
        LEFT JOIN rc_project_mapping project_mapping ON project_mapping.id = mapping.project_mapping_id
        LEFT JOIN rc_project_space source_project ON source_project.id = project_mapping.source_project_space_id
        LEFT JOIN rc_project_space target_project ON target_project.id = project_mapping.target_project_space_id
        WHERE mapping.is_enabled = 1
        ORDER BY
          CASE mapping.mapping_status
            WHEN 'confirmed' THEN 0
            WHEN 'pending' THEN 1
            ELSE 2
          END,
          mapping.sort_order,
          mapping.id
        """
    )


def get_datasource_config_rows() -> list[dict[str, str]]:
    """配置页数据源映射表专用数据源行。

    口径说明：
      - 左侧固定取测试环境真实数据源缓存 rc_datasource_resource
      - 右侧目标映射优先读 rc_datasource_mapping 已保存结果
      - 未映射的数据源也必须展示出来，供页面继续补充生产环境映射
    """
    source_rows = fetch_all(
        """
        SELECT
          source_resource.id AS row_id,
          COALESCE(NULLIF(source_resource.datasource_key, ''), source_resource.datasource_name) AS source,
          source_resource.datasource_type AS type,
          source_resource.project_space_name AS source_project_name,
          source_resource.source_module,
          source_resource.connectivity_status,
          COALESCE(DATE_FORMAT(source_resource.last_synced_at, '%Y-%m-%d %H:%i:%s'), '-') AS last_synced_at
        FROM rc_datasource_resource source_resource
        JOIN rc_environment source_env
          ON source_env.id = source_resource.env_id
         AND source_env.env_type = 'test'
         AND source_env.is_enabled = 1
        WHERE source_resource.is_enabled = 1
          AND source_resource.is_meta = 0
          AND LOWER(source_resource.datasource_name) NOT LIKE '%meta%'
          AND source_resource.datasource_name NOT LIKE '%元数据%'
        ORDER BY
          CASE source_resource.source_module
            WHEN 'offline' THEN 0
            ELSE 1
          END,
          source_resource.project_space_name,
          source_resource.datasource_name
        """
    )
    mapping_rows = fetch_all(
        """
        SELECT
          mapping.id,
          mapping.source_datasource_resource_id AS source_resource_id,
          mapping.target_datasource_resource_id AS target_resource_id,
          mapping.mapping_status,
          mapping.match_rule,
          mapping.is_enabled,
          mapping.connectivity_status,
          COALESCE(DATE_FORMAT(mapping.last_synced_at, '%Y-%m-%d %H:%i:%s'), '-') AS last_synced_at,
          COALESCE(target_resource.datasource_key, mapping.target_value) AS target
        FROM rc_datasource_mapping mapping
        LEFT JOIN rc_datasource_resource target_resource
          ON target_resource.id = mapping.target_datasource_resource_id
        WHERE mapping.id IN (
          SELECT latest_mapping.id
          FROM (
            SELECT MAX(id) AS id
            FROM rc_datasource_mapping
            GROUP BY source_datasource_resource_id
          ) latest_mapping
        )
        """
    )
    mapping_by_source_id = {
        str(row.get("source_resource_id")): row
        for row in mapping_rows
        if row.get("source_resource_id")
    }
    merged_rows: list[dict[str, str]] = []
    for row in source_rows:
        mapping = mapping_by_source_id.get(str(row["row_id"])) or {}
        connectivity_status = str(mapping.get("connectivity_status") or row.get("connectivity_status") or "unknown")
        merged_rows.append({
            "row_id": str(row["row_id"]),
            "source": str(row.get("source") or ""),
            "type": str(row.get("type") or ""),
            "source_project_name": str(row.get("source_project_name") or ""),
            "source_module": str(row.get("source_module") or ""),
            "target_resource_id": str(mapping.get("target_resource_id") or ""),
            "target": str(mapping.get("target") or ""),
            "mapping_id": str(mapping.get("id") or ""),
            "mapping_status": str(mapping.get("mapping_status") or "pending"),
            "mapping_enabled": int(mapping.get("is_enabled") or 0) if mapping else 0,
            "connectivity_status": connectivity_status,
            "last_synced_at": str(mapping.get("last_synced_at") or row.get("last_synced_at") or "-"),
            "connected": 1 if connectivity_status == "connected" else 0,
        })
    return merged_rows



# ──── (i) 发布记录（非 API 阶段） ────
# 核心：simulate_release_from_draft()，被 main.py /confirm/publish 路由调用
# 当前阶段只生成发布记录和步骤日志，不创建/更新生产数栈任务
# 写入：rc_release_batch + rc_release_task + rc_release_step_log

def get_records(keyword: str = "") -> list[dict[str, str]]:
    """发布记录列表统一从平台库读取，支持关键字过滤，不触发额外扫描。"""
    return fetch_all(
        """
        SELECT
          id AS batch_db_id,
          batch_code AS id,
          batch_name AS task_name,
          source_env_name AS source_env,
          target_env_name AS target_env,
          started_at AS start,
          finished_at AS end,
          release_status AS status
        FROM rc_release_batch
        WHERE (
          :keyword = ''
          OR batch_code LIKE CONCAT('%', :keyword, '%')
          OR batch_name LIKE CONCAT('%', :keyword, '%')
          OR source_project_name LIKE CONCAT('%', :keyword, '%')
          OR publisher LIKE CONCAT('%', :keyword, '%')
        )
        ORDER BY started_at DESC, id DESC
        """,
        {"keyword": keyword.strip()},
    )


def get_record_detail(batch_id: str | None) -> dict[str, object]:
    if not batch_id:
        return {"record": {}, "tasks": [], "logs": []}
    records = fetch_all(
        """
        SELECT *
        FROM rc_release_batch
        WHERE id = :batch_id OR batch_code = :batch_id
        LIMIT 1
        """,
        {"batch_id": batch_id},
    )
    if not records:
        return {"record": {}, "tasks": [], "logs": []}
    record = records[0]
    tasks = fetch_all(
        """
        SELECT task_name, task_type AS module, submitter, release_status AS status, failure_reason, sql_repo_path
        FROM rc_release_task
        WHERE batch_id = :batch_id
        ORDER BY id
        """,
        {"batch_id": record["id"]},
    )
    try:
        logs = fetch_all(
            """
            SELECT step_name, step_status AS status, request_summary, response_summary, error_message, created_at
            FROM rc_release_step_log
            WHERE batch_id = :batch_id
            ORDER BY id
            """,
            {"batch_id": record["id"]},
        )
    except Exception:
        logs = []
    return {"record": record, "tasks": tasks, "logs": logs}


def simulate_release_from_draft(draft_id: str, selected_task_ids: list[str], username: str) -> dict[str, object]:
    """生成非 API 阶段发布记录，不调用生产数栈创建/更新/提交接口。

    当前阶段只生成发布记录和步骤日志，写入 rc_release_batch / rc_release_task / rc_release_step_log，
    不会创建、更新或提交生产数栈任务。后续接入真实发布 API 时替换此函数。
    被 main.py /confirm/publish 路由调用。
    """
    model = get_release_draft_model(draft_id)
    draft = model["draft"]
    tasks = model["tasks"]
    validations = model["validations"]
    if not draft or not isinstance(tasks, list):
        return {"batch_id": "", "status": "校验阻断", "message": "发布草稿不存在。"}
    selected_set = {str(item) for item in selected_task_ids if item}
    selected_tasks = [task for task in tasks if not selected_set or str(task["id"]) in selected_set]
    selected_validation = [
        item for item in validations
        if not selected_set or str(item.get("draft_task_id")) in selected_set
    ]
    status = summarize_validation_status(selected_validation)
    now = now_text()
    batch_code = f"SIM_{datetime.now().strftime('%Y%m%d%H%M%S')}_{draft_id}"
    env_labels = environment_labels()
    batch_id = execute_insert(
        """
        INSERT INTO rc_release_batch(
          batch_code, batch_name, source_env_name, target_env_name, source_project_name,
          task_count, success_count, failed_count, release_status, git_commit_id,
          publisher, started_at, finished_at, failure_reason
        )
        VALUES (
          :batch_code, :batch_name, :source_env_name, :target_env_name, :source_project_name,
          :task_count, :success_count, :failed_count, :release_status, :git_commit_id,
          :publisher, :started_at, :finished_at, :failure_reason
        )
        """,
        {
            "batch_code": batch_code,
            "batch_name": f"{draft.get('source_project_name') or '项目空间'} 非 API 发布确认",
            "source_project_name": draft.get("source_project_name") or "",
            "task_count": len(selected_tasks),
            "success_count": 0 if status != "校验通过" else len(selected_tasks),
            "failed_count": len(selected_tasks) if status == "校验阻断" else 0,
            "release_status": status,
            "git_commit_id": draft.get("head_commit") or "",
            "source_env_name": env_labels["source"],
            "target_env_name": env_labels["target"],
            "publisher": username,
            "started_at": now,
            "finished_at": now,
            "failure_reason": "存在阻断校验项，未进入真实发布。" if status == "校验阻断" else "数栈 API 暂未接入，已生成待接 API 记录。",
        },
    )
    for task in selected_tasks:
        execute_write(
            """
            INSERT INTO rc_release_task(
              batch_id, source_task_id, task_name, task_type, source_submit_time,
              submitter, release_status, sql_repo_path, failure_reason
            )
            VALUES (
              :batch_id, :source_task_id, :task_name, :task_type, :source_submit_time,
              :submitter, :release_status, :sql_repo_path, :failure_reason
            )
            """,
            {
                "batch_id": batch_id,
                "source_task_id": task.get("source_task_id") or task.get("source_id") or str(task["id"]),
                "task_name": task.get("name") or "",
                "task_type": task.get("module") or "",
                "source_submit_time": None if task.get("submitted_at") == "-" else task.get("submitted_at"),
                "submitter": task.get("submitter") or username,
                "release_status": status,
                "sql_repo_path": task.get("git_path") or "",
                "failure_reason": "校验阻断，未执行发布。" if status == "校验阻断" else "待接数栈 API。",
            },
        )
    for step_key, step_name, step_status in [
        ("validation", "发布前校验", status),
        ("api_placeholder", "数栈 API 执行", "待接 API"),
        ("record", "写入发布记录", "通过"),
    ]:
        execute_write(
            """
            INSERT INTO rc_release_step_log(
              batch_id, draft_id, step_key, step_name, step_status, request_summary, response_summary
            )
            VALUES (
              :batch_id, :draft_id, :step_key, :step_name, :step_status, :request_summary, :response_summary
            )
            """,
            {
                "batch_id": batch_id,
                "draft_id": draft_id,
                "step_key": step_key,
                "step_name": step_name,
                "step_status": step_status,
                "request_summary": "非 API 阶段，仅基于 Web 元数据库校验。",
                "response_summary": "已记录模拟发布阶段。",
            },
        )
    return {"batch_id": batch_id, "batch_code": batch_code, "status": status, "message": "已生成非 API 阶段发布记录。"}


def get_record_stats(keyword: str = "") -> list[dict[str, str]]:
    row = fetch_one(
        """
        SELECT
          COUNT(*) AS total_count,
          SUM(CASE WHEN release_status = '成功' THEN 1 ELSE 0 END) AS success_count,
          SUM(CASE WHEN release_status = '失败' THEN 1 ELSE 0 END) AS failed_count
        FROM rc_release_batch
        WHERE (
          :keyword = ''
          OR batch_code LIKE CONCAT('%', :keyword, '%')
          OR batch_name LIKE CONCAT('%', :keyword, '%')
          OR source_project_name LIKE CONCAT('%', :keyword, '%')
          OR publisher LIKE CONCAT('%', :keyword, '%')
        )
        """,
        {"keyword": keyword.strip()},
    ) or {}
    return [
        {"label": "总发布次数", "value": str(row.get("total_count") or 0), "tone": "blue", "icon": "▣"},
        {"label": "成功次数", "value": str(row.get("success_count") or 0), "tone": "green", "icon": "✓"},
        {"label": "失败次数", "value": str(row.get("failed_count") or 0), "tone": "red", "icon": "×"},
    ]



# ──── (l) 项目映射与选项 ────
# 读取 rc_project_mapping + rc_project_space，展示测试→生产映射和项目空间下拉选项
# 被 main.py /config 页面调用

def get_project_mappings() -> list[dict[str, str]]:
    return fetch_all(
        """
        SELECT
               mapping.id,
               source_env.env_name AS source_env,
               mapping.source_project_name AS source_space,
               target_env.env_name AS target_env,
               mapping.target_project_name AS target_space,
               COALESCE(mapping.mapping_status, 'pending') AS status,
               COALESCE(mapping.match_rule, '-') AS match_rule,
               COALESCE(DATE_FORMAT(mapping.last_synced_at, '%Y-%m-%d %H:%i:%s'), '-') AS last_synced_at
        FROM rc_project_mapping mapping
        JOIN rc_environment source_env ON mapping.source_env_id = source_env.id
        JOIN rc_environment target_env ON mapping.target_env_id = target_env.id
        WHERE source_env.env_type = 'test' AND target_env.env_type = 'prod'
          AND mapping.is_enabled = 1
        ORDER BY
          CASE mapping.mapping_status
            WHEN 'confirmed' THEN 0
            WHEN 'pending' THEN 1
            ELSE 2
          END,
          mapping.id
        """
    )


def get_project_mapping_rows() -> list[dict[str, str]]:
    """配置页项目空间映射表专用行。

    口径说明：
      - 以测试环境项目空间为主表，保证每个测试空间都能出现在配置页
      - 已有映射优先展示数据库结果
      - 未写入映射表时，尝试按“同名 + 同模块”带出生产候选项目空间
    """
    rows = fetch_all(
        """
        SELECT
          source.id AS row_id,
          source.id AS source_project_space_id,
          source.project_name AS source_space,
          source.project_type AS source_type,
          CASE
            WHEN source.project_type = 'offline' THEN '离线'
            ELSE '实时'
          END AS source_type_label,
          source_env.env_name AS source_env,
          COALESCE(mapping.id, 0) AS mapping_id,
          COALESCE(mapping.mapping_status, 'pending') AS status,
          COALESCE(mapping.is_enabled, 1) AS mapping_enabled,
          COALESCE(mapping.match_rule, 'same_name') AS match_rule,
          COALESCE(DATE_FORMAT(mapping.last_synced_at, '%Y-%m-%d %H:%i:%s'), '-') AS last_synced_at,
          target_env.env_name AS target_env,
          COALESCE(mapping.target_project_space_id, target.id, 0) AS target_project_space_id,
          COALESCE(mapping.target_project_name, target.project_name, '') AS target_space
        FROM rc_project_space source
        JOIN rc_environment source_env
          ON source_env.id = source.env_id
         AND source_env.env_type = 'test'
         AND source_env.is_enabled = 1
        LEFT JOIN rc_project_mapping mapping
          ON mapping.id = (
               SELECT latest_mapping.id
               FROM rc_project_mapping latest_mapping
               WHERE latest_mapping.source_project_space_id = source.id
               ORDER BY latest_mapping.id DESC
               LIMIT 1
          )
        LEFT JOIN rc_project_space target
          ON target.id = COALESCE(mapping.target_project_space_id, (
               SELECT candidate.id
               FROM rc_project_space candidate
               JOIN rc_environment candidate_env
                 ON candidate_env.id = candidate.env_id
                AND candidate_env.env_type = 'prod'
                AND candidate_env.is_enabled = 1
               WHERE candidate.is_enabled = 1
                 AND candidate.project_name = source.project_name
                 AND candidate.project_type = source.project_type
               ORDER BY candidate.id
               LIMIT 1
          ))
        LEFT JOIN rc_environment target_env
          ON target_env.id = target.env_id
        WHERE source.is_enabled = 1
        ORDER BY
          CASE source.project_type
            WHEN 'offline' THEN 0
            ELSE 1
          END,
          source.project_name
        """
    )
    for row in rows:
        row["source_space"] = re.sub(r"[（(](离线|实时)[）)]$", "", str(row.get("source_space") or "")).strip()
        row["target_space"] = re.sub(r"[（(](离线|实时)[）)]$", "", str(row.get("target_space") or "")).strip()
    return rows


def get_project_space_options() -> dict[str, list[dict[str, str]]]:
    rows = fetch_all(
        """
        SELECT
               ps.id,
               ps.project_name AS name,
               ps.project_type,
               env.env_type AS env_type
        FROM rc_project_space ps
        JOIN rc_environment env ON ps.env_id = env.id
        WHERE ps.is_enabled = 1 AND env.is_enabled = 1
          AND ps.project_type IN ('realtime', 'stream', 'offline')
        ORDER BY ps.id
        """
    )
    source = [
        {
            "id": str(row["id"]),
            "name": str(row["name"]),
            "project_type": str(row["project_type"]),
            "label": project_space_option_label(
                str(row["name"]),
                str(row["project_type"]),
            ),
        }
        for row in rows
        if row["env_type"] in {"test", "dev", "pre"}
    ]
    target = [
        {
            "id": str(row["id"]),
            "name": str(row["name"]),
            "project_type": str(row["project_type"]),
            "label": project_space_option_label(
                str(row["name"]),
                str(row["project_type"]),
            ),
        }
        for row in rows
        if row["env_type"] == "prod"
    ]
    return {
        "source": source,
        "target": target,
    }


def get_project_space_detail(project_space_id: str, env_type: str | None = None) -> dict[str, object] | None:
    """按 ID 读取项目空间，用于配置页把展示名解析回真实主键。"""
    params = {"project_space_id": project_space_id}
    extra_sql = ""
    if env_type:
        params["env_type"] = env_type
        extra_sql = " AND env.env_type = :env_type "
    rows = fetch_all(
        f"""
        SELECT
               ps.id,
               ps.project_name,
               ps.project_type,
               ps.project_code,
               env.id AS env_id,
               env.env_type,
               env.env_name
        FROM rc_project_space ps
        JOIN rc_environment env ON env.id = ps.env_id
        WHERE ps.is_enabled = 1
          AND CAST(ps.id AS CHAR) = :project_space_id
          {extra_sql}
        LIMIT 1
        """,
        params,
    )
    return rows[0] if rows else None


def save_project_mapping(source_project_space_id: str, target_project_space_id: str) -> dict[str, str]:
    """保存项目空间映射到平台库，后续页面刷新直接读取真实 rc_project_mapping。"""
    source = get_project_space_detail(source_project_space_id, "test")
    target = get_project_space_detail(target_project_space_id, "prod")
    if not source or not target:
        return {"status": "failed", "message": "请选择有效的测试/生产项目空间。"}
    if str(source.get("project_type") or "") != str(target.get("project_type") or ""):
        return {"status": "failed", "message": "测试与生产项目空间的模块类型不一致，不能直接映射。"}

    existing = fetch_all(
        """
        SELECT id
        FROM rc_project_mapping
        WHERE source_project_space_id = :source_project_space_id
        ORDER BY is_enabled DESC, id DESC
        LIMIT 1
        """,
        {"source_project_space_id": source_project_space_id},
    )
    payload = {
        "source_env_id": source["env_id"],
        "source_env_name": source["env_name"],
        "source_project_space_id": source["id"],
        "source_project_name": source["project_name"],
        "target_env_id": target["env_id"],
        "target_env_name": target["env_name"],
        "target_project_space_id": target["id"],
        "target_project_name": target["project_name"],
    }
    if existing:
        execute_write(
            """
            UPDATE rc_project_mapping
            SET source_env_id = :source_env_id,
                source_env_name = :source_env_name,
                source_project_name = :source_project_name,
                target_env_id = :target_env_id,
                target_env_name = :target_env_name,
                target_project_space_id = :target_project_space_id,
                target_project_name = :target_project_name,
                mapping_status = 'confirmed',
                match_rule = 'manual',
                confirmed_at = CURRENT_TIMESTAMP,
                last_synced_at = CURRENT_TIMESTAMP,
                is_enabled = 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :id
            """,
            {**payload, "id": existing[0]["id"]},
        )
    else:
        execute_write(
            """
            INSERT INTO rc_project_mapping(
              source_env_id, source_env_name, source_project_space_id, source_project_name,
              target_env_id, target_env_name, target_project_space_id, target_project_name,
              mapping_status, match_rule, confirmed_at, last_synced_at, is_enabled
            )
            VALUES (
              :source_env_id, :source_env_name, :source_project_space_id, :source_project_name,
              :target_env_id, :target_env_name, :target_project_space_id, :target_project_name,
              'confirmed', 'manual', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1
            )
            """,
            payload,
        )
    return {"status": "success", "message": "项目空间映射已保存。"}


def toggle_project_mapping(source_project_space_id: str, enabled: bool) -> dict[str, str]:
    """启用/停用项目空间映射；停用后页面会回退显示候选态。"""
    affected = execute_write(
        """
        UPDATE rc_project_mapping
        SET is_enabled = :enabled,
            mapping_status = CASE WHEN :enabled = 1 THEN 'confirmed' ELSE 'disabled' END,
            last_synced_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE source_project_space_id = :source_project_space_id
        """,
        {
            "enabled": 1 if enabled else 0,
            "source_project_space_id": source_project_space_id,
        },
    )
    if affected <= 0:
        return {"status": "failed", "message": "未找到可更新的项目空间映射。"}
    return {"status": "success", "message": "项目空间映射已启用。" if enabled else "项目空间映射已停用。"}


def get_directory_sync_summary() -> dict[str, str]:
    rows = fetch_all(
        """
        SELECT
          COUNT(*) AS directory_count,
          MAX(last_synced_at) AS last_synced_at
        FROM rc_project_directory
        WHERE is_enabled = 1
        """
    )
    task_rows = fetch_all(
        """
        SELECT COUNT(*) AS task_count
        FROM rc_task_metadata
        WHERE is_enabled = 1
          AND is_deleted = 0
        """
    )
    datasource_rows = fetch_all(
        """
        SELECT COUNT(*) AS datasource_count
        FROM rc_datasource_resource
        WHERE is_enabled = 1
          AND is_meta = 0
        """
    )
    row = rows[0] if rows else {}
    return {
        "directory_count": str(row.get("directory_count") or 0),
        "task_count": str((task_rows[0] if task_rows else {}).get("task_count") or 0),
        "datasource_count": str((datasource_rows[0] if datasource_rows else {}).get("datasource_count") or 0),
        "last_synced_at": str(row.get("last_synced_at") or "-"),
        "status": "已同步" if row.get("directory_count") else "未同步",
    }



# ──── (m) Git 仓库绑定与刷新 ────
# 管理 rc_git_repo + rc_git_commit：保存绑定、刷新提交历史、定时刷新
# 核心入口：refresh_git_repo_commits() 和 refresh_all_git_repos()
# 调用 git_release.refresh_repo_commit_history 执行 git pull + log，结果回写 rc_git_commit 和 rc_git_repo

def get_git_repo_bindings() -> list[dict[str, str]]:
    return fetch_all(
        """
        SELECT
               ps.id AS project_space_id,
               ps.project_name,
               ps.project_type,
               repo.id AS repo_id,
               repo.repo_url,
               repo.default_branch,
               repo.current_branch,
               repo.module_type AS repo_module_type,
               repo.git_provider,
               repo.gitlab_project_id,
               repo.gitlab_project_path,
               repo.api_sync_mode,
               CASE
                 WHEN ps.project_type = 'offline' THEN '离线'
                 ELSE '实时'
               END AS module_label,
               CASE
                 WHEN ps.project_type = 'offline' THEN 'offline'
                 ELSE 'stream'
               END AS module_type_value,
               repo.latest_commit_id,
               repo.latest_commit_time,
               repo.latest_commit_author,
               repo.latest_commit_message,
               repo.last_refresh_status,
               repo.last_refresh_message,
               repo.last_refreshed_at,
               repo.is_current
        FROM rc_project_space ps
        LEFT JOIN rc_git_repo repo ON repo.project_space_id = ps.id AND repo.is_current = 1
        JOIN rc_environment env ON ps.env_id = env.id
        WHERE ps.is_enabled = 1
          AND env.env_type = 'test'
        ORDER BY
          CASE WHEN ps.project_type = 'offline' THEN 0 ELSE 1 END,
          ps.project_name
        """
    )


def repair_git_repo_project_space_links() -> None:
    """把历史上绑错到同名实时/离线空间的 Git 仓库记录重新挂回正确项目空间。"""
    rows = fetch_all(
        """
        SELECT
               repo.id AS repo_id,
               repo.module_type,
               source.id AS current_project_space_id,
               source.project_name,
               source.project_type,
               env.env_type
        FROM rc_git_repo repo
        JOIN rc_project_space source ON source.id = repo.project_space_id
        JOIN rc_environment env ON env.id = source.env_id
        WHERE repo.is_current = 1
          AND source.is_enabled = 1
          AND env.env_type = 'test'
        """
    )
    for row in rows:
        repo_module = normalize_module_type(str(row.get("module_type") or ""))
        project_module = normalize_module_type(str(row.get("project_type") or ""))
        if not repo_module or repo_module == project_module:
            continue
        candidate = fetch_one(
            """
            SELECT ps.id
            FROM rc_project_space ps
            JOIN rc_environment env ON env.id = ps.env_id
            WHERE env.env_type = 'test'
              AND ps.is_enabled = 1
              AND ps.project_name = :project_name
              AND (
                    (:repo_module = 'offline' AND ps.project_type = 'offline')
                 OR (:repo_module = 'stream' AND ps.project_type IN ('realtime', 'stream'))
              )
            ORDER BY ps.id
            LIMIT 1
            """,
            {"project_name": row.get("project_name") or "", "repo_module": repo_module},
        )
        if candidate and str(candidate.get("id") or "") != str(row.get("current_project_space_id") or ""):
            execute_write(
                """
                UPDATE rc_git_repo
                SET project_space_id = :project_space_id,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :repo_id
                """,
                {"project_space_id": candidate["id"], "repo_id": row["repo_id"]},
            )


def repair_release_draft_project_space_links(project_space_id: str | int | None = None) -> None:
    """按当前 Git 仓库绑定纠正历史草稿归属，避免旧草稿挂在错误项目空间下。"""
    params = {"project_space_id": str(project_space_id or "")}
    execute_write(
        """
        UPDATE rc_release_draft draft
        JOIN rc_git_repo repo
          ON repo.id = draft.repo_id
         AND repo.is_current = 1
        JOIN rc_project_space project_space
          ON project_space.id = repo.project_space_id
         AND project_space.is_enabled = 1
        JOIN rc_environment env
          ON env.id = project_space.env_id
         AND env.env_type = 'test'
        SET draft.source_project_space_id = project_space.id,
            draft.source_project_name = project_space.project_name
        WHERE draft.repo_id IS NOT NULL
          AND draft.source_project_space_id <> project_space.id
          AND (
                :project_space_id = ''
             OR CAST(draft.source_project_space_id AS CHAR) = :project_space_id
             OR CAST(project_space.id AS CHAR) = :project_space_id
          )
        """
        ,
        params,
    )


def save_git_repo_binding(project_space_id: str, repo_url: str, branch: str, module_type: str) -> dict[str, str]:
    normalized_module = normalize_module_type(module_type) or project_space_module_type(project_space_id)
    normalized_repo_url = normalize_git_repo_url(repo_url)
    if not project_space_id or not repo_url or not branch:
        return {"status": "failed", "message": "请填写项目空间、仓库地址和跟踪分支。"}
    if normalized_module not in {"offline", "stream"}:
        return {"status": "failed", "message": "未识别项目空间模块类型，请先同步真实项目空间元数据。"}
    credential = saved_git_credential()

    try:
        service = gitlab_api_service()
        project = service.resolve_project(normalized_repo_url)
        real_branches = service.list_branches(str(project.get("project_id") or project.get("project_path") or ""))
        record_gitlab_api_check_status("success", "GitLab API 项目解析成功。")
    except (RuntimeError, GitLabApiError) as exc:
        record_gitlab_api_check_status("failed", str(exc))
        return {"status": "failed", "message": str(exc)}

    if branch not in real_branches:
        branch_list = "、".join(real_branches) if real_branches else "无"
        return {"status": "failed", "message": f"跟踪分支 {branch} 不存在于远端仓库。当前远端分支：{branch_list}。"}

    existing = fetch_one(
        """
        SELECT id
        FROM rc_git_repo
        WHERE project_space_id = :project_space_id AND is_current = 1
        ORDER BY id DESC
        LIMIT 1
        """,
        {"project_space_id": project_space_id},
    )
    try:
        if existing:
            execute_write(
                """
                UPDATE rc_git_repo
                SET repo_url = :repo_url,
                    default_branch = :default_branch,
                    current_branch = :current_branch,
                    module_type = :module_type,
                    git_provider = 'gitlab',
                    gitlab_project_id = :gitlab_project_id,
                    gitlab_project_path = :gitlab_project_path,
                    api_sync_mode = 1,
                    is_current = 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :repo_id
                """,
                {
                    "project_space_id": project_space_id,
                    "repo_url": normalized_repo_url,
                    "default_branch": branch,
                    "current_branch": branch,
                    "module_type": normalized_module,
                    "gitlab_project_id": project.get("project_id"),
                    "gitlab_project_path": project.get("project_path") or "",
                    "repo_id": existing["id"],
                },
            )
        else:
            execute_write(
                """
                INSERT INTO rc_git_repo(
                  project_space_id, repo_url, default_branch, current_branch, module_type,
                  git_provider, gitlab_project_id, gitlab_project_path, api_sync_mode, is_current
                )
                VALUES (
                  :project_space_id, :repo_url, :default_branch, :current_branch, :module_type,
                  'gitlab', :gitlab_project_id, :gitlab_project_path, 1, 1
                )
                """,
                {
                    "project_space_id": project_space_id,
                    "repo_url": normalized_repo_url,
                    "default_branch": branch,
                    "current_branch": branch,
                    "module_type": normalized_module,
                    "gitlab_project_id": project.get("project_id"),
                    "gitlab_project_path": project.get("project_path") or "",
                },
            )
    except Exception as exc:
        return {"status": "failed", "message": f"保存 Git 仓库绑定失败：{exc}"}
    repair_release_draft_project_space_links(project_space_id)
    return {"status": "success", "message": "Git 仓库绑定已保存。"}


def repo_by_id(repo_id: str) -> dict[str, object] | None:
    return fetch_one(
        """
        SELECT id, project_space_id, repo_url, default_branch, current_branch, module_type,
               git_provider, gitlab_project_id, gitlab_project_path, api_sync_mode
        FROM rc_git_repo
        WHERE id = :repo_id AND is_current = 1
        """,
        {"repo_id": repo_id},
    )


def refresh_git_repo_commits(repo_id: str) -> dict[str, object]:
    """刷新指定仓库的提交历史，优先走 GitLab API，失败时保留本地 Git 兜底。"""
    repo_row = repo_by_id(repo_id)
    if not repo_row:
        return {"status": "failed", "message": "未找到可刷新的 Git 仓库配置。", "commit_count": 0}
    repo = GitRepo(
        id=int(repo_row["id"]),
        repo_url=str(repo_row["repo_url"]),
        default_branch=str(repo_row.get("default_branch") or "main"),
        current_branch=str(repo_row.get("current_branch") or repo_row.get("default_branch") or "main"),
        module_type=normalize_module_type(str(repo_row.get("module_type") or "")),
    )
    latest_commit_id = None
    latest_commit_time = None
    latest_commit_author = None
    latest_commit_message = None
    try:
        if int(repo_row.get("api_sync_mode") or 0) == 1 and (repo_row.get("gitlab_project_id") or repo_row.get("gitlab_project_path")):
            service = gitlab_api_service()
            project_ref = str(repo_row.get("gitlab_project_id") or repo_row.get("gitlab_project_path") or "")
            branches = service.list_branches(project_ref)
            if repo.current_branch not in branches:
                branch_list = "、".join(branches) if branches else "无"
                raise GitLabApiError(f"跟踪分支 {repo.current_branch} 不存在于远端仓库。当前远端分支：{branch_list}。")
            commits = service.list_commits(project_ref, repo.current_branch, page=1, per_page=100)
            result = {
                "status": "success",
                "message": "提交记录刷新完成（GitLab API）。",
                "latest_commit_id": commits[0]["commit_id"] if commits else "",
                "latest_commit_time": commits[0]["committed_at"] if commits else "",
                "latest_commit_author": commits[0]["author"] if commits else "",
                "latest_commit_message": commits[0]["message"] if commits else "",
                "commits": [
                    {
                        "commit_id": item["commit_id"],
                        "committed_at": item["committed_at"],
                        "author": item["author"],
                        "message": item["message"],
                        "changed_files": 0,
                    }
                    for item in commits
                ],
            }
            record_gitlab_api_check_status("success", "GitLab API 提交刷新成功。")
        else:
            credential = saved_git_credential()
            result = refresh_repo_commit_history(repo, credential)

        commits = result.get("commits") or []
        if result.get("status") == "success":
            for commit in commits:
                execute_write(
                    """
                    INSERT INTO rc_git_commit(
                      repo_id, commit_id, committed_at, author, commit_message, changed_files
                    )
                    VALUES (
                      :repo_id, :commit_id, :committed_at, :author, :commit_message, :changed_files
                    )
                    ON DUPLICATE KEY UPDATE
                      committed_at = VALUES(committed_at),
                      author = VALUES(author),
                      commit_message = VALUES(commit_message),
                      changed_files = VALUES(changed_files)
                    """,
                    {
                        "repo_id": repo.id,
                        "commit_id": commit["commit_id"],
                        "committed_at": commit["committed_at"],
                        "author": commit["author"],
                        "commit_message": commit["message"],
                        "changed_files": commit["changed_files"],
                    },
                )
        latest_commit_id = result.get("latest_commit_id") or None
        latest_commit_time = result.get("latest_commit_time") or None
        latest_commit_author = result.get("latest_commit_author") or None
        latest_commit_message = result.get("latest_commit_message") or None
        execute_write(
            """
            UPDATE rc_git_repo
            SET latest_commit_id = :latest_commit_id,
                latest_commit_time = :latest_commit_time,
                latest_commit_author = :latest_commit_author,
                latest_commit_message = :latest_commit_message,
                last_refresh_status = :last_refresh_status,
                last_refresh_message = :last_refresh_message,
                last_refreshed_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :repo_id
            """,
            {
                "repo_id": repo.id,
                "latest_commit_id": latest_commit_id,
                "latest_commit_time": latest_commit_time,
                "latest_commit_author": latest_commit_author,
                "latest_commit_message": latest_commit_message,
                "last_refresh_status": result.get("status") or "failed",
                "last_refresh_message": result.get("message") or "",
            },
        )
        return {
            "status": str(result.get("status") or "failed"),
            "message": str(result.get("message") or ""),
            "commit_count": len(commits) if isinstance(commits, list) else 0,
        }
    except Exception as exc:
        record_gitlab_api_check_status("failed", str(exc))
        execute_write(
            """
            UPDATE rc_git_repo
            SET last_refresh_status = 'failed',
                last_refresh_message = :message,
                last_refreshed_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :repo_id
            """,
            {"repo_id": repo.id, "message": str(exc)},
        )
        return {"status": "failed", "message": str(exc), "commit_count": 0}


def refresh_all_git_repos() -> dict[str, object]:
    """刷新所有已启用的 Git 仓库提交历史，返回汇总结果。

    被 main.py /config/git-repos/refresh-all 路由和后台定时任务 git_auto_refresh_loop 调用。
    遍历所有 is_current=1 的 rc_git_repo，逐个调 refresh_git_repo_commits。
    """
    repos = fetch_all(
        """
        SELECT id
        FROM rc_git_repo
        WHERE is_current = 1 AND repo_url <> ''
        ORDER BY id
        """
    )
    results = [refresh_git_repo_commits(str(repo["id"])) for repo in repos]
    success_count = len([item for item in results if item.get("status") == "success"])
    return {
        "status": "success" if success_count == len(results) else "partial",
        "message": f"刷新完成：成功 {success_count} 个，失败 {len(results) - success_count} 个。",
        "total": len(results),
        "success_count": success_count,
    }


def get_datasource_resources(project_space_id: str | None = None) -> list[dict[str, str]]:
    """读取平台库中的全局数据源缓存。

    当前配置页按环境级数据源展示，不再按项目空间拆分。
    `project_space_id` 参数保留仅为兼容旧调用，当前不参与过滤。
    """
    return fetch_all(
        """
        SELECT
               resource.id,
               COALESCE(NULLIF(resource.datasource_key, ''), resource.datasource_name) AS name,
               resource.datasource_type AS type,
               resource.source_module,
               resource.project_space_code,
               resource.project_space_id,
               resource.project_space_name AS project_space,
               env.env_type,
               resource.connectivity_status,
               resource.last_synced_at
        FROM rc_datasource_resource resource
        JOIN rc_environment env ON resource.env_id = env.id
        WHERE resource.is_enabled = 1
          AND resource.is_meta = 0
          AND env.is_enabled = 1
          AND LOWER(resource.datasource_name) NOT LIKE '%meta%'
          AND resource.datasource_name NOT LIKE '%元数据%'
        ORDER BY env.env_type, name, resource.id
        """
    )


def get_datasource_options(project_space_id: str | None = None) -> dict[str, list[dict[str, str]]]:
    resources = get_datasource_resources(project_space_id)
    mappings = get_datasource_mappings()
    def dedupe_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
        unique: list[dict[str, str]] = []
        seen: set[str] = set()
        for row in rows:
            key = row.get("id") or row.get("label") or row.get("name") or ""
            if key in seen:
                continue
            seen.add(key)
            unique.append(row)
        return unique

    source = [
        {
            "id": str(row["id"]),
            "name": str(row["name"]),
            "type": str(row["type"]),
            "project_space": str(row["project_space"]),
            "source_module": str(row["source_module"]),
            "connectivity_status": str(row["connectivity_status"]),
            "label": str(row["name"]),
        }
        for row in resources
        if row["env_type"] in {"test", "dev", "pre"}
    ]
    target = [
        {
            "id": str(row["id"]),
            "name": str(row["name"]),
            "type": str(row["type"]),
            "project_space": str(row["project_space"]),
            "source_module": str(row["source_module"]),
            "connectivity_status": str(row["connectivity_status"]),
            "label": str(row["name"]),
        }
        for row in resources
        if row["env_type"] == "prod"
    ]
    if not source:
        source = [{"id": "", "name": mapping["source"], "type": mapping["type"], "project_space": "", "source_module": "", "connectivity_status": "unknown", "label": mapping["source"]} for mapping in mappings]
    if not target:
        target = [{"id": "", "name": mapping["target"], "type": mapping["type"], "project_space": "", "source_module": "", "connectivity_status": "unknown", "label": mapping["target"]} for mapping in mappings if mapping.get("target")]
    return {
        "source": dedupe_rows(source),
        "target": dedupe_rows(target),
    }


def get_datasource_resource_detail(resource_id: str, env_type: str | None = None) -> dict[str, object] | None:
    """按资源 ID 读取数据源缓存，供配置页把下拉选择落到真实资源主键。"""
    params = {"resource_id": resource_id}
    extra_sql = ""
    if env_type:
        params["env_type"] = env_type
        extra_sql = " AND env.env_type = :env_type "
    rows = fetch_all(
        f"""
        SELECT
               resource.id,
               resource.project_space_id,
               resource.project_space_name,
               resource.datasource_name,
               resource.datasource_type,
               resource.datasource_key,
               resource.connectivity_status,
               env.id AS env_id,
               env.env_type
        FROM rc_datasource_resource resource
        JOIN rc_environment env ON env.id = resource.env_id
        WHERE resource.is_enabled = 1
          AND CAST(resource.id AS CHAR) = :resource_id
          {extra_sql}
        LIMIT 1
        """,
        params,
    )
    return rows[0] if rows else None


def save_datasource_mapping(source_resource_id: str, target_resource_id: str) -> dict[str, str]:
    """保存数据源映射到平台库，后续任务/确认页都只读这份正式映射。"""
    source = get_datasource_resource_detail(source_resource_id, "test")
    target = get_datasource_resource_detail(target_resource_id, "prod")
    if not source or not target:
        return {"status": "failed", "message": "请选择有效的测试/生产数据源。"}
    if str(source.get("datasource_type") or "") != str(target.get("datasource_type") or ""):
        return {"status": "failed", "message": "测试与生产数据源类型不一致，不能直接映射。"}

    existing = fetch_all(
        """
        SELECT id
        FROM rc_datasource_mapping
        WHERE source_datasource_resource_id = :source_resource_id
        ORDER BY is_enabled DESC, id DESC
        LIMIT 1
        """,
        {"source_resource_id": source_resource_id},
    )
    payload = {
        "project_mapping_id": None,
        "source_resource_id": source["id"],
        "target_resource_id": target["id"],
        "datasource_type": source["datasource_type"],
        "source_pattern": source.get("datasource_key") or source.get("datasource_name") or "",
        "target_value": target.get("datasource_key") or target.get("datasource_name") or "",
        "connectivity_status": target.get("connectivity_status") or "unknown",
    }
    if existing:
        execute_write(
            """
            UPDATE rc_datasource_mapping
            SET project_mapping_id = :project_mapping_id,
                target_datasource_resource_id = :target_resource_id,
                datasource_type = :datasource_type,
                source_pattern = :source_pattern,
                target_value = :target_value,
                mapping_status = 'confirmed',
                match_rule = 'manual',
                confirmed_at = CURRENT_TIMESTAMP,
                last_synced_at = CURRENT_TIMESTAMP,
                connectivity_status = :connectivity_status,
                is_enabled = 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :id
            """,
            {**payload, "id": existing[0]["id"]},
        )
    else:
        execute_write(
            """
            INSERT INTO rc_datasource_mapping(
              project_mapping_id, source_datasource_resource_id, target_datasource_resource_id,
              datasource_type, source_pattern, target_value,
              mapping_status, match_rule, confirmed_at, last_synced_at, connectivity_status, is_enabled
            )
            VALUES (
              :project_mapping_id, :source_resource_id, :target_resource_id,
              :datasource_type, :source_pattern, :target_value,
              'confirmed', 'manual', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :connectivity_status, 1
            )
            """,
            payload,
        )
    return {"status": "success", "message": "数据源映射已保存。", "connectivity_status": payload["connectivity_status"]}


def clear_datasource_mapping(source_resource_id: str) -> dict[str, str]:
    """清空指定测试数据源的生产映射，不删除测试侧资源缓存。"""
    affected = execute_write(
        """
        UPDATE rc_datasource_mapping
        SET is_enabled = 0,
            mapping_status = 'disabled',
            updated_at = CURRENT_TIMESTAMP
        WHERE source_datasource_resource_id = :source_resource_id
        """,
        {"source_resource_id": source_resource_id},
    )
    if affected <= 0:
        return {"status": "failed", "message": "未找到可清空的数据源映射。"}
    return {"status": "success", "message": "数据源映射已清空。"}


def toggle_datasource_mapping(source_resource_id: str, enabled: bool) -> dict[str, str]:
    """启用/停用数据源映射，供配置页直接维护映射是否参与后续发布。"""
    affected = execute_write(
        """
        UPDATE rc_datasource_mapping
        SET is_enabled = :enabled,
            mapping_status = CASE WHEN :enabled = 1 THEN 'confirmed' ELSE 'disabled' END,
            updated_at = CURRENT_TIMESTAMP
        WHERE source_datasource_resource_id = :source_resource_id
        """,
        {
            "enabled": 1 if enabled else 0,
            "source_resource_id": source_resource_id,
        },
    )
    if affected <= 0:
        return {"status": "failed", "message": "未找到可更新的数据源映射。"}
    return {"status": "success", "message": "数据源映射已启用。" if enabled else "数据源映射已停用。"}



# ──── (n) 用户 ────
# 读取 rc_user 列表，被 main.py /users 页面调用

def get_users() -> list[dict[str, str]]:
    return fetch_all(
        """
        SELECT username, role_name AS role, created_at, CASE WHEN is_enabled = 1 THEN '启用' ELSE '禁用' END AS status
        FROM rc_user
        ORDER BY id
        """
    )


def get_git_info() -> dict[str, str | list[dict[str, str]]]:
    repo_rows = fetch_all(
        """
        SELECT repo_url, default_branch, current_branch, latest_commit_id,
               latest_commit_time, latest_commit_author, latest_commit_message
        FROM rc_git_repo
        WHERE is_current = 1
        LIMIT 1
        """
    )
    repo = repo_rows[0] if repo_rows else {}
    commits = fetch_all(
        """
        SELECT commit_id AS commit, committed_at AS time, author,
               commit_message AS message, changed_files AS files
        FROM rc_git_commit
        ORDER BY committed_at DESC
        """
    )
    bindings = get_git_repo_bindings()
    return {"repo": repo, "commits": commits, "bindings": bindings}
