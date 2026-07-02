"""
gitlab_api — GitLab REST API 主链路封装

调用链位置：
  本模块 ← repositories.py（Git 分支读取、仓库解析、提交刷新、任务差异 compare）

核心职责：
  1. 统一用 GitLab API 获取项目、分支、提交历史和提交差异
  2. 只暴露标准化后的 Python dict，不把 requests 细节泄露给上层
  3. 把 401/403/404/429/超时等错误翻译成页面可直接展示的中文提示

说明：
  - 第一阶段 API 认证仅支持 PRIVATE-TOKEN
  - 用户名/密码仍由 gitlab_auth.py 负责网页登录检测，不在这里复用
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, urlparse

from app.config import settings


class GitLabApiError(RuntimeError):
    """GitLab API 链路中的业务化错误，允许直接展示到页面。"""


@dataclass(frozen=True)
class GitLabApiCredential:
    """GitLab API 认证信息。"""

    base_url: str
    token: str
    timeout_seconds: int = 10


def project_path_from_repo_url(repo_url: str) -> str:
    """把 Git 仓库/项目页面 URL 解析成 GitLab project path。"""
    parsed = urlparse((repo_url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return ""
    return parsed.path.strip("/").removesuffix(".git").strip("/")


def api_project_path(project_path: str) -> str:
    """把 group/project 转成 GitLab API 可用的 projects/:id 片段。"""
    return quote(project_path, safe="")


class GitLabApiService:
    """GitLab REST API 客户端。"""

    def __init__(self, credential: GitLabApiCredential) -> None:
        self.credential = credential

    def _requests(self):
        try:
            import requests
        except ImportError as exc:  # pragma: no cover
            raise GitLabApiError("缺少 requests 依赖，请先安装 requirements.txt。") from exc
        return requests

    def _headers(self) -> dict[str, str]:
        return {
            "PRIVATE-TOKEN": self.credential.token,
            "Accept": "application/json",
        }

    def _api_url(self, path: str) -> str:
        base_url = self.credential.base_url.rstrip("/")
        return f"{base_url}/api/v4{path}"

    def _request(self, method: str, path: str, *, params: dict[str, object] | None = None) -> object:
        requests = self._requests()
        try:
            response = requests.request(
                method,
                self._api_url(path),
                headers=self._headers(),
                params=params,
                timeout=self.credential.timeout_seconds or settings.gitlab_login_timeout_seconds,
            )
        except requests.Timeout as exc:
            raise GitLabApiError("GitLab API 请求超时，请稍后重试。") from exc
        except requests.RequestException as exc:
            raise GitLabApiError(f"GitLab API 请求失败：{exc}") from exc

        if response.status_code == 401:
            raise GitLabApiError("GitLab Token 无效或已过期，请重新保存。")
        if response.status_code == 403:
            raise GitLabApiError("当前 Token 没有访问该 GitLab 资源的权限。")
        if response.status_code == 404:
            raise GitLabApiError("GitLab 项目不存在，或当前 Token 无法访问该项目。")
        if response.status_code == 429:
            raise GitLabApiError("GitLab API 请求过于频繁，请稍后再试。")
        if response.status_code >= 400:
            try:
                payload = response.json()
            except Exception:
                payload = response.text
            raise GitLabApiError(f"GitLab API 请求失败：{response.status_code} {payload}")

        try:
            return response.json()
        except ValueError as exc:
            raise GitLabApiError("GitLab API 返回了无法解析的响应。") from exc

    def resolve_project(self, repo_url: str) -> dict[str, object]:
        """由仓库 URL 解析并验证 GitLab 项目。"""
        project_path = project_path_from_repo_url(repo_url)
        if not project_path:
            raise GitLabApiError("仓库地址格式不正确，无法解析 GitLab 项目路径。")
        payload = self._request("GET", f"/projects/{api_project_path(project_path)}")
        if not isinstance(payload, dict):
            raise GitLabApiError("GitLab 项目解析结果异常。")
        return {
            "project_id": payload.get("id"),
            "project_path": str(payload.get("path_with_namespace") or project_path),
            "default_branch": str(payload.get("default_branch") or ""),
            "web_url": str(payload.get("web_url") or ""),
            "ssh_url_to_repo": str(payload.get("ssh_url_to_repo") or ""),
            "http_url_to_repo": str(payload.get("http_url_to_repo") or ""),
        }

    def list_branches(self, project_id_or_path: str | int) -> list[str]:
        """读取 GitLab 项目的真实分支列表。"""
        payload = self._request(
            "GET",
            f"/projects/{api_project_path(str(project_id_or_path))}/repository/branches",
            params={"per_page": 100},
        )
        if not isinstance(payload, list):
            raise GitLabApiError("GitLab 分支列表响应异常。")
        branches = [str(item.get("name") or "").strip() for item in payload if isinstance(item, dict)]
        return [branch for branch in branches if branch]

    def list_commits(
        self,
        project_id_or_path: str | int,
        branch: str,
        page: int = 1,
        per_page: int = 50,
    ) -> list[dict[str, object]]:
        """读取指定分支的提交历史。"""
        payload = self._request(
            "GET",
            f"/projects/{api_project_path(str(project_id_or_path))}/repository/commits",
            params={"ref_name": branch, "page": page, "per_page": per_page},
        )
        if not isinstance(payload, list):
            raise GitLabApiError("GitLab 提交历史响应异常。")
        commits: list[dict[str, object]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            commits.append(
                {
                    "commit_id": str(item.get("id") or ""),
                    "short_id": str(item.get("short_id") or ""),
                    "title": str(item.get("title") or ""),
                    "message": str(item.get("message") or item.get("title") or ""),
                    "author": str(item.get("author_name") or "-"),
                    "author_email": str(item.get("author_email") or ""),
                    "committed_at": str(item.get("committed_date") or "")[:19].replace("T", " "),
                }
            )
        return [item for item in commits if item["commit_id"]]

    def compare_commits(self, project_id_or_path: str | int, from_sha: str, to_sha: str) -> dict[str, object]:
        """比较两个 commit 之间的差异，返回变更文件及 compare 元信息。"""
        if not to_sha:
            raise GitLabApiError("缺少目标 commit，无法读取 Git 差异。")
        if not from_sha:
            raise GitLabApiError("缺少基准 commit，无法直接使用 compare API。")
        payload = self._request(
            "GET",
            f"/projects/{api_project_path(str(project_id_or_path))}/repository/compare",
            params={"from": from_sha, "to": to_sha},
        )
        if not isinstance(payload, dict):
            raise GitLabApiError("GitLab compare 响应异常。")
        diffs = payload.get("diffs") if isinstance(payload.get("diffs"), list) else []
        commits = payload.get("commits") if isinstance(payload.get("commits"), list) else []
        changed_files: list[str] = []
        for item in diffs:
            if not isinstance(item, dict):
                continue
            path = str(item.get("new_path") or item.get("old_path") or "").strip()
            if path:
                changed_files.append(path)
        return {
            "compare_timeout": bool(payload.get("compare_timeout")),
            "compare_same_ref": bool(payload.get("compare_same_ref")),
            "commit_count": int(payload.get("commit_count") or 0),
            "changed_files": list(dict.fromkeys(changed_files)),
            "commits": commits,
        }
