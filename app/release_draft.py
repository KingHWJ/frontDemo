"""
release_draft — 发布草稿的离线校验项生成与汇总

调用链位置：
  本模块 ← repositories.py
    - persist_release_draft 调 build_validation_items，为每个任务生成校验项后落库
    - simulate_release_from_draft 调 summarize_validation_status / now_text
  本模块不访问数据库、不调 git 子进程、不发 HTTP 请求，纯离线计算。

对外暴露：
  - build_validation_items(task, has_project_mapping) : 为单个任务生成 7 项校验
  - summarize_validation_status(items) : 从校验项列表汇总出总体状态
  - now_text() : 当前时间的格式化字符串
"""
from __future__ import annotations

from datetime import datetime
from typing import Any


def validation_item(
    check_key: str,
    check_name: str,
    status: str,
    message: str,
    *,
    is_blocking: bool = True,
) -> dict[str, Any]:
    """构造单个校验项字典。

    被 build_validation_items 内部调用 7 次，生成统一格式的校验结果。
    字段：check_key(校验键)、check_name(展示名)、status(通过/失败/待接API)、
         message(说明文本)、is_blocking(是否阻断发布)。
    """
    return {
        "check_key": check_key,
        "check_name": check_name,
        "status": status,
        "message": message,
        "is_blocking": is_blocking,
    }


def build_validation_items(task: dict[str, Any], *, has_project_mapping: bool) -> list[dict[str, Any]]:
    """根据已同步元数据做离线校验，不调用数栈任务发布 API。

    为单个任务生成 7 项校验：
      ① git_artifact   — 发布文件是否就绪（SQL 已就绪，非 SQL 标记"缺少发布文件"）
      ② metadata_match  — 测试任务元数据是否匹配到数栈已提交任务
      ③ project_mapping — 测试→生产项目空间映射是否存在
      ④ directory_mapping — 目录层级相对路径是否一致
      ⑤ datasource_mapping — 数据源映射是否完整
      ⑥ dependency      — 依赖关系（当前"待接 API"，不阻断）
      ⑦ schedule        — 调度与参数（当前"待接 API"，不阻断）

    被 repositories.persist_release_draft 调用。
    """
    items = [
        validation_item(
            "git_artifact",
            "Git 发布文件",
            "通过" if task.get("release_file_status") == "已就绪" else "失败",
            "发布文件已满足任务类型要求。"
            if task.get("release_file_status") == "已就绪"
            else f"缺少 {task.get('required_artifact') or 'manifest/export'} 发布文件。",
        ),
        validation_item(
            "metadata_match",
            "测试任务元数据",
            "通过" if task.get("metadata_status") == "已匹配" else "失败",
            "已匹配测试数栈任务。"
            if task.get("metadata_status") == "已匹配"
            else "Git 变更未匹配到测试数栈已提交任务。",
        ),
        validation_item(
            "project_mapping",
            "项目空间映射",
            "通过" if has_project_mapping else "失败",
            "已配置测试到生产项目空间映射。" if has_project_mapping else "缺少当前项目空间的生产映射。",
        ),
        validation_item(
            "directory_mapping",
            "目录层级一致",
            "通过" if task.get("directory_status", "已匹配") == "已匹配" else "失败",
            "测试和生产目录相对路径一致。"
            if task.get("directory_status", "已匹配") == "已匹配"
            else "目录层级未同步或相对路径不一致。",
        ),
        validation_item(
            "datasource_mapping",
            "数据源映射",
            "通过" if task.get("datasource_status", "已匹配") == "已匹配" else "失败",
            "已存在可用数据源映射。"
            if task.get("datasource_status", "已匹配") == "已匹配"
            else "任务使用的数据源缺少生产映射。",
        ),
        # 依赖关系和调度配置暂为"待接 API"占位，后续接入数栈 API 后补充真实校验
        validation_item(
            "dependency",
            "依赖关系",
            "待接 API",
            "本阶段只保留校验入口，后续接入数栈依赖查询。",
            is_blocking=False,
        ),
        validation_item(
            "schedule",
            "调度与参数",
            "待接 API",
            "本阶段只保留确认入口，后续接入数栈任务配置。",
            is_blocking=False,
        ),
    ]
    return items


def summarize_validation_status(items: list[dict[str, Any]]) -> str:
    """从校验项列表汇总出总体状态。

    规则：任一阻断项失败 → "校验阻断"；否则任一"待接 API" → "待接 API"；全部通过 → "校验通过"。

    被 repositories.persist_release_draft / simulate_release_from_draft 调用。
    """
    if any(item.get("is_blocking") and item.get("status") == "失败" for item in items):
        return "校验阻断"
    if any(item.get("status") == "待接 API" for item in items):
        return "待接 API"
    return "校验通过"


def now_text() -> str:
    """返回当前时间的格式化字符串（YYYY-MM-DD HH:MM:SS）。

    被 repositories.simulate_release_from_draft 调用，
    用于写入发布记录的时间字段。
    """
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
