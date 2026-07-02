"""
git_release — Git 发布范围识别：clone/pull/diff/commit 解析

调用链位置：
  本模块 ← repositories.py（间接调用，是 Git 侧所有操作的底层执行模块）
    - get_git_diff_task_plan → build_git_snapshot / build_changed_task_candidates
    - load_git_branches / validate_tracking_branch → list_remote_branches
    - refresh_git_repo_commits → refresh_repo_commit_history
    - saved_git_credential → 构造 GitCredential 传入本模块
  本模块不直接访问数据库，所有 rc_* 表的读写由 repositories 完成。

核心职责：
  1. clone/pull 项目空间绑定的 Git 仓库到本地缓存目录（ensure_repo）
  2. 计算上次成功发布 Commit 到当前 Commit 的变更文件（changed_files_between）
  3. 把变更文件解析为候选发布任务（build_changed_task_candidates）
  4. 刷新提交历史到 rc_git_commit（refresh_repo_commit_history，由 repositories 回写）
  5. 读取远端分支列表（list_remote_branches），供配置页校验跟踪分支

真实数栈任务创建/更新不在这里做，后续由发布执行服务接入生产 API。

对外暴露（被 repositories.py 导入）：
  - build_git_snapshot(repo, last_success_commit, refresh, credential) → GitSnapshot
  - build_changed_task_candidates(changed_files, forced_module_type) → list[dict]
  - list_remote_branches(repo_url, credential) → (ok, branches, error)
  - refresh_repo_commit_history(repo, credential, limit) → dict
  - GitRepo / GitSnapshot / GitCredential : 数据类
  - normalize_module_type / module_label_from_type : 模块类型工具函数
"""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from app.config import settings


# 项目根目录，用于拼接相对路径的 git_workspace_root
ROOT = Path(__file__).resolve().parents[1]
# 第一版只识别 .sql 文件；.json/.yaml/.jar 等后续再做配置化
SQL_EXTENSIONS = {".sql"}
# 路径中包含这些关键词时识别为实时任务
REALTIME_MARKERS = {"realtime", "real-time", "stream", "streaming", "flink", "实时"}
# 路径中包含这些关键词时识别为离线任务
OFFLINE_MARKERS = {"offline", "batch", "scheduler", "离线"}


@dataclass(frozen=True)
class GitRepo:
    """Git 仓库绑定信息，从 rc_git_repo 表读出后传入本模块。"""
    id: int
    repo_url: str
    default_branch: str
    current_branch: str
    module_type: str = ""


@dataclass(frozen=True)
class GitSnapshot:
    """Git 差异快照，包含本地工作树路径、基准/最新 Commit、变更文件列表和状态消息。

    被 repositories.get_git_diff_task_plan 使用，作为任务列表页的 Git 上下文。
    """
    worktree: Path | None
    last_success_commit: str
    current_commit: str
    changed_files: list[str]
    status: str
    message: str


@dataclass(frozen=True)
class GitCredential:
    """Git HTTP 认证凭据（用户名 + 密码），从 rc_gitlab_credential 解密后传入。

    通过 git_askpass_env 临时脚本传递给 git clone/fetch/pull，密码不出现在命令行参数。
    """
    username: str
    password: str


def cache_root() -> Path:
    """返回 git 仓库缓存根目录。

    若 git_workspace_root 是相对路径，则拼接到项目根下；
    绝对路径直接使用。被 local_repo_path / ensure_repo 调用。
    """
    root = Path(settings.git_workspace_root)
    if not root.is_absolute():
        root = ROOT / root
    return root


def slugify(value: str) -> str:
    """把字符串清洗成路径安全 slug（只保留字母数字和 ._-），截断 80 字符。

    被 local_repo_path 调用，用于构造仓库本地缓存目录名。
    """
    slug = re.sub(r"[^0-9A-Za-z._-]+", "-", value).strip("-")
    return slug[:80] or "repo"


def run_git(args: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> tuple[bool, str, str]:
    """核心 git 子进程封装：执行 git 命令并返回 (是否成功, stdout, stderr)。

    超时取 settings.git_operation_timeout_seconds（默认 20 秒）。
    环境变量合并 os.environ + 传入的 env（通常含 GIT_ASKPASS 等凭据变量）。
    被 list_remote_branches / ensure_repo / commit_exists / current_commit /
    tracked_sql_files_at_head / changed_files_between / commit_history 调用。
    """
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            check=False,
            capture_output=True,
            text=True,
            timeout=settings.git_operation_timeout_seconds,
            env={**os.environ, **(env or {})},
        )
    except Exception as exc:
        return False, "", str(exc)
    return completed.returncode == 0, completed.stdout.strip(), completed.stderr.strip()


@contextmanager
def git_askpass_env(credential: GitCredential | None):
    """通过临时 askpass 脚本传递 GitLab 凭据，避免密码出现在命令行参数里。

    无凭据时只设置 GIT_TERMINAL_PROMPT=0（禁止交互式密码输入）。
    有凭据时写临时 .sh 脚本（chmod 0o700），yield 含 GIT_ASKPASS/GIT_TERMINAL_PROMPT/
    RC_GIT_USERNAME/RC_GIT_PASSWORD 的 env，finally 里 os.unlink 清理。

    被 list_remote_branches / ensure_repo 调用，包裹 git clone/fetch/pull/ls-remote 操作。
    """
    if credential is None or not credential.username or not credential.password:
        yield {"GIT_TERMINAL_PROMPT": "0"}
        return

    script_path = ""
    try:
        # 写临时 askpass 脚本，根据 Git 提问（Username/Password）输出对应凭据
        with tempfile.NamedTemporaryFile("w", delete=False, prefix="rc-git-askpass-", suffix=".sh") as handle:
            script_path = handle.name
            handle.write(
                "#!/bin/sh\n"
                "case \"$1\" in\n"
                "*Username*) printf '%s\\n' \"$RC_GIT_USERNAME\" ;;\n"
                "*Password*) printf '%s\\n' \"$RC_GIT_PASSWORD\" ;;\n"
                "*) printf '%s\\n' \"$RC_GIT_PASSWORD\" ;;\n"
                "esac\n"
            )
        os.chmod(script_path, 0o700)
        yield {
            "GIT_ASKPASS": script_path,
            "GIT_TERMINAL_PROMPT": "0",
            "RC_GIT_USERNAME": credential.username,
            "RC_GIT_PASSWORD": credential.password,
        }
    finally:
        # 退出上下文后立即删除临时脚本，不留凭据痕迹
        if script_path:
            try:
                os.unlink(script_path)
            except OSError:
                pass


def parse_remote_branches(output: str) -> list[str]:
    """解析 git ls-remote --heads 输出，提取 refs/heads/ 后的分支名，去重保序。

    被 list_remote_branches 调用。
    """
    branches: list[str] = []
    for line in output.splitlines():
        if "refs/heads/" not in line:
            continue
        branch = line.rsplit("refs/heads/", 1)[-1].strip()
        if branch:
            branches.append(branch)
    return list(dict.fromkeys(branches))


def list_remote_branches(repo_url: str, credential: GitCredential | None = None) -> tuple[bool, list[str], str]:
    """在 git_askpass_env 内执行 git ls-remote --heads，获取远端真实分支列表。

    被 repositories.load_git_branches / validate_tracking_branch 调用，
    用于配置页"读取远端分支"和校验跟踪分支是否存在于远端。
    """
    with git_askpass_env(credential) as env:
        ok, output, error = run_git(["ls-remote", "--heads", repo_url], env=env)
    return ok, parse_remote_branches(output) if ok else [], error


def local_repo_path(repo: GitRepo) -> Path:
    """决定本地仓库路径：file:// 前缀→expanduser；本地路径存在→直接用；否则 cache_root/{id}-{slugify(url)}。

    被 ensure_repo 调用。
    """
    repo_url = repo.repo_url.strip()
    if repo_url.startswith("file://"):
        return Path(repo_url.removeprefix("file://")).expanduser()

    path = Path(repo_url).expanduser()
    if path.exists():
        return path

    return cache_root() / f"{repo.id}-{slugify(repo_url)}"


def ensure_repo(repo: GitRepo, refresh: bool, credential: GitCredential | None = None) -> tuple[Path | None, str]:
    """clone/pull 主体：确保本地有仓库工作树，返回 (工作树路径, 错误消息)。

    流程：
      - 路径不存在且 refresh=True → git clone（优先 partial clone --filter=blob:none，
        老 GitLab 不支持时回退普通单分支 clone）
      - 路径存在且 refresh=True → git checkout + git pull --ff-only
      - 最后 git rev-parse --is-inside-work-tree 校验
      - 失败返回错误消息字符串

    被 build_git_snapshot / refresh_repo_commit_history 调用。
    """
    path = local_repo_path(repo)
    branch = repo.current_branch or repo.default_branch or "main"

    if not path.exists():
        if not refresh:
            return None, "Git 仓库尚未拉取，点击「刷新 Git 差异」后会 clone/pull 主分支。"
        path.parent.mkdir(parents=True, exist_ok=True)
        with git_askpass_env(credential) as env:
            # 大仓库场景优先使用 partial clone，只下载提交和目录树；老 GitLab 不支持时回退普通单分支 clone。
            ok, _, error = run_git(
                ["clone", "--filter=blob:none", "--branch", branch, "--single-branch", repo.repo_url, str(path)],
                env=env,
            )
            if not ok:
                shutil.rmtree(path, ignore_errors=True)
                # partial clone 失败后回退到普通单分支 clone
                ok, _, error = run_git(["clone", "--branch", branch, "--single-branch", repo.repo_url, str(path)], env=env)
        if not ok:
            return None, f"Git clone 失败：{error or repo.repo_url}"
    elif refresh:
        # 仓库已存在，切换到跟踪分支并拉取最新
        ok, _, error = run_git(["checkout", branch], cwd=path)
        if not ok:
            return path, f"Git checkout {branch} 失败：{error}"
        with git_askpass_env(credential) as env:
            ok, _, error = run_git(["pull", "--ff-only"], cwd=path, env=env)
        if not ok:
            return path, f"Git pull 失败：{error}"

    # 校验是否是有效 Git 仓库
    ok, _, error = run_git(["rev-parse", "--is-inside-work-tree"], cwd=path)
    if not ok:
        return None, f"不是有效 Git 仓库：{error or path}"
    return path, ""


def commit_exists(worktree: Path, commit_id: str) -> bool:
    """判断指定 commit 是否在本地仓库内（git cat-file -e）。

    被 changed_files_between / build_git_snapshot 调用，用于判断基准 Commit 是否可用。
    """
    if not commit_id:
        return False
    ok, _, _ = run_git(["cat-file", "-e", f"{commit_id}^{{commit}}"], cwd=worktree)
    return ok


def current_commit(worktree: Path) -> str:
    """返回当前分支 HEAD Commit ID（git rev-parse HEAD）。

    被 build_git_snapshot 调用，作为最新 Commit 与上次成功 Commit 对比。
    """
    ok, output, _ = run_git(["rev-parse", "HEAD"], cwd=worktree)
    return output if ok else ""


def sql_files_from_output(output: str) -> list[str]:
    """从 git diff / ls-tree 输出中过滤出后缀属于 SQL_EXTENSIONS 的文件路径。

    被 changed_files_between / tracked_sql_files_at_head 调用。
    """
    return [
        line.strip()
        for line in output.splitlines()
        if line.strip() and Path(line.strip()).suffix.lower() in SQL_EXTENSIONS
    ]


def tracked_sql_files_at_head(worktree: Path) -> list[str]:
    """读取当前分支 HEAD 下所有被追踪的 SQL 文件（git ls-tree -r --name-only HEAD）。

    被 changed_files_between 在首次发布场景（无基准 Commit）时调用。
    """
    ok, output, _ = run_git(["ls-tree", "-r", "--name-only", "HEAD"], cwd=worktree)
    return sql_files_from_output(output) if ok else []


def latest_commit_info_for_file(worktree: Path, file_path: str) -> dict[str, str]:
    """读取单个文件最近一次 Git 提交人和时间，任务未匹配数栈时用它做回退展示。"""
    ok, output, _ = run_git(["log", "-1", "--pretty=format:%ci%x1f%an", "HEAD", "--", file_path], cwd=worktree)
    if not ok or not output:
        return {"submitted_at": "-", "submitter": "-"}
    committed_at, _, author = output.partition("\x1f")
    return {
        "submitted_at": committed_at[:19] if committed_at else "-",
        "submitter": author or "-",
    }


def changed_files_between(
    worktree: Path,
    last_success_commit: str,
    latest_commit: str,
    has_release_base: bool | None = None,
) -> list[str]:
    """计算待发布 SQL 文件。

    首次发布没有基准 Commit，不能用 `git show HEAD`，否则 merge commit 场景容易得到空结果。
    这里直接读取当前分支完整文件树，把全部 SQL 任务视为待发布。

    被 build_git_snapshot 调用。参数 has_release_base 可预先传入避免重复校验。
    """
    if has_release_base is None:
        has_release_base = bool(last_success_commit and latest_commit and commit_exists(worktree, last_success_commit))
    if has_release_base:
        # 有基准 Commit → git diff --name-only 上次..当前
        ok, output, _ = run_git(["diff", "--name-only", f"{last_success_commit}..{latest_commit}"], cwd=worktree)
        return sql_files_from_output(output) if ok else []

    # 无基准 Commit（首次发布）→ 读取 HEAD 下全部 SQL 文件
    return tracked_sql_files_at_head(worktree)


def build_git_snapshot(
    repo: GitRepo | None,
    last_success_commit: str,
    refresh: bool,
    credential: GitCredential | None = None,
) -> GitSnapshot:
    """顶层入口：为项目空间构建 Git 差异快照。

    流程：ensure_repo → current_commit → commit_exists → changed_files_between → 生成 GitSnapshot

    被 repositories.get_git_diff_task_plan 调用，结果作为任务列表页的 git_context 上下文。
    repo 为 None → 返回 warning 快照（项目空间未配置 Git 仓库）。
    """
    if repo is None:
        return GitSnapshot(None, last_success_commit, "", [], "warning", "当前项目空间未配置 Git 仓库。")

    worktree, error = ensure_repo(repo, refresh, credential)
    if error:
        return GitSnapshot(worktree, last_success_commit, "", [], "warning", error)
    if worktree is None:
        return GitSnapshot(None, last_success_commit, "", [], "warning", "Git 仓库不可用。")

    latest_commit = current_commit(worktree)
    has_release_base = bool(last_success_commit and latest_commit and commit_exists(worktree, last_success_commit))
    changed_files = changed_files_between(worktree, last_success_commit, latest_commit, has_release_base)
    # 根据是否有基准 Commit 生成不同的中文消息
    if not last_success_commit:
        message = "首次发布：当前分支全部 SQL 文件均视为待发布。" if changed_files else "首次发布：当前分支没有可识别 SQL 发布文件。"
    elif not has_release_base:
        message = "上次成功发布 Commit 不在当前仓库，已按首次发布处理。"
    else:
        message = "Git 差异读取完成。" if changed_files else "Git 差异读取完成：未发现 SQL 变更文件。"
    return GitSnapshot(worktree, last_success_commit, latest_commit, changed_files, "success", message)


def commit_history(worktree: Path, limit: int = 200) -> list[dict[str, str | int]]:
    """从本地仓库读取提交历史（git log），对每条 commit 再统计变更文件数（git show --name-only）。

    被 refresh_repo_commit_history 调用。失败时 raise RuntimeError。
    返回 dict 列表，字段：commit_id / committed_at / author / message / changed_files。
    """
    ok, output, error = run_git(
        ["log", f"--max-count={limit}", "--pretty=format:%H%x1f%ci%x1f%an%x1f%s"],
        cwd=worktree,
    )
    if not ok:
        raise RuntimeError(error or "Git log 读取失败。")

    commits: list[dict[str, str | int]] = []
    for line in output.splitlines():
        # 按 \x1f 分隔出 commit_id/时间/作者/消息
        parts = line.split("\x1f", 3)
        if len(parts) != 4:
            continue
        commit_id, committed_at, author, message = parts
        # 对每条 commit 统计变更文件数
        ok_files, files_output, _ = run_git(["show", "--name-only", "--format=", commit_id], cwd=worktree)
        changed_files = len([item for item in files_output.splitlines() if item.strip()]) if ok_files else 0
        commits.append({
            "commit_id": commit_id,
            "committed_at": committed_at[:19],
            "author": author,
            "message": message,
            "changed_files": changed_files,
        })
    return commits


def refresh_repo_commit_history(
    repo: GitRepo,
    credential: GitCredential | None = None,
    limit: int = 200,
) -> dict[str, object]:
    """顶层入口：刷新仓库提交历史，返回含最新 Commit 信息的 dict。

    流程：ensure_repo(refresh=True) → commit_history → 取第一条作为 latest_commit

    被 repositories.refresh_git_repo_commits 调用，结果回写到 rc_git_commit 和 rc_git_repo。
    """
    worktree, error = ensure_repo(repo, True, credential)
    if error or worktree is None:
        return {"status": "failed", "message": error or "Git 仓库不可用。", "commits": []}

    commits = commit_history(worktree, limit)
    latest = commits[0] if commits else {}
    return {
        "status": "success",
        "message": "提交记录刷新完成。",
        "latest_commit_id": latest.get("commit_id", ""),
        "latest_commit_time": latest.get("committed_at", ""),
        "latest_commit_author": latest.get("author", ""),
        "latest_commit_message": latest.get("message", ""),
        "commits": commits,
    }


def detect_module(parts: list[str]) -> str:
    """按路径片段匹配实时/离线关键词，返回"离线"/"实时"，默认"实时"。

    被 parse_changed_task_file 调用（当 forced_module_type 为空时按路径推断）。
    """
    lowered = {part.lower() for part in parts}
    if lowered & OFFLINE_MARKERS:
        return "离线"
    if lowered & REALTIME_MARKERS:
        return "实时"
    return "实时"


def normalize_module_type(value: str | None) -> str:
    """归一化模块类型为 "offline"/"stream"/""。

    被 parse_changed_task_file / module_label_from_type / repositories.save_git_repo_binding 调用。
    """
    raw = (value or "").strip().lower()
    if raw in {"offline", "batch", "离线"}:
        return "offline"
    if raw in {"stream", "realtime", "real-time", "实时"}:
        return "stream"
    return ""


def module_label_from_type(value: str | None) -> str:
    """normalize 后转中文标签"离线"/"实时"/""。被 parse_changed_task_file 调用。"""
    normalized = normalize_module_type(value)
    if normalized == "offline":
        return "离线"
    if normalized == "stream":
        return "实时"
    return ""


def task_name_from_path(path: Path) -> str:
    """取文件名去掉后缀作为任务名（path.stem）。被 parse_changed_task_file 调用。"""
    return path.stem


def detect_artifact_kind(path: Path) -> str:
    """根据文件后缀判断发布文件类型：.sql → "sql"，其余第一版返回 ""。被 parse_changed_task_file 调用。"""
    suffix = path.suffix.lower()
    if suffix in SQL_EXTENSIONS:
        return "sql"
    return ""


def detect_task_type_label(artifact_kind: str) -> str:
    """根据 artifact_kind 转换为任务类型标签："sql" → "SQL"。被 parse_changed_task_file 调用。"""
    return "SQL" if artifact_kind == "sql" else ""


def release_file_status(artifact_kind: str) -> tuple[str, str]:
    """判断发布文件是否就绪，返回 (状态, 缺失提示)。

    第一版先只认 SQL 文件；实时 Jar/manifest 等发布文件类型后续再做配置化。
    被 parse_changed_task_file 调用。
    """
    # 第一版先只认 SQL 文件；实时 Jar/manifest 等发布文件类型后续再做配置化。
    if artifact_kind != "sql":
        return "缺少发布文件", "SQL"
    return "已就绪", ""


def parse_changed_task_file(
    file_path: str,
    forced_module_type: str | None = None,
    commit_meta: dict[str, str] | None = None,
) -> dict[str, str] | None:
    """把单个变更文件解析为候选任务 dict。

    非 SQL 文件返回 None（第一版只处理 SQL）。
    字段：name/module/module_type/task_type_label/git_path/git_directory/
         artifact_kind/release_file_status/required_artifact

    被 build_changed_task_candidates 内部调用。
    """
    normalized = file_path.replace("\\", "/").strip("/")
    path = Path(normalized)
    # 非发布文件类型（第一版只认 SQL）→ 跳过
    artifact_kind = detect_artifact_kind(path)
    if not artifact_kind:
        return None

    name = task_name_from_path(path)
    if not name:
        return None

    # 模块类型：优先取强制指定值，否则按路径关键词推断
    module_type = normalize_module_type(forced_module_type)
    module = module_label_from_type(module_type) or detect_module(list(path.parts))
    module_type = module_type or ("offline" if module == "离线" else "stream")
    task_type_label = detect_task_type_label(artifact_kind)
    status, required_artifact = release_file_status(artifact_kind)
    return {
        "name": name,
        "module": module,
        "module_type": module_type,
        "task_type_label": task_type_label,
        "git_path": normalized,
        "git_directory": str(path.parent).replace("\\", "/") if str(path.parent) != "." else "",
        "artifact_kind": artifact_kind,
        "release_file_status": status,
        "required_artifact": required_artifact,
        "submitted_at": str((commit_meta or {}).get("submitted_at") or "-"),
        "submitter": str((commit_meta or {}).get("submitter") or "-"),
    }


def build_changed_task_candidates(
    changed_files: list[str],
    forced_module_type: str | None = None,
    commit_info_by_file: dict[str, dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    """顶层入口：对每个变更文件 parse_changed_task_file，按 (module_type, git_path) 去重。

    Git 文件路径是第一版唯一识别键；同名 SQL 任务仍保留多行，避免跨目录任务被吞掉。

    被 repositories.get_git_diff_task_plan 调用，结果合并到任务列表。
    """
    candidates_by_key: dict[tuple[str, str], dict[str, str]] = {}
    for file_path in changed_files:
        candidate = parse_changed_task_file(file_path, forced_module_type, (commit_info_by_file or {}).get(file_path))
        if not candidate:
            continue
        # Git 文件路径是第一版唯一识别键；同名 SQL 任务仍保留多行，避免跨目录任务被吞掉。
        key = (candidate["module_type"], candidate["git_path"])
        candidates_by_key[key] = candidate
    return list(candidates_by_key.values())
