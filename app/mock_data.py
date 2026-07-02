"""
mock_data — Frontend development mock data module.

Provides fake data for all Jinja2 templates without database dependency.
When config.settings.use_mock_data=True, routes switch to calling these functions.
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta

# ---- helpers ----

_TODAY = date.today()
r = random.Random(42)  # deterministic randomness


def _d(offset: int = 0) -> str:
    """Return YYYY-MM-DD string for today + offset days."""
    return (_TODAY + timedelta(days=offset)).isoformat()


def _dt(offset: int = 0, hour: int = 9, minute: int = 0) -> str:
    """Return YYYY-MM-DD HH:MM:SS string."""
    d = _TODAY + timedelta(days=offset)
    return f"{d.isoformat()} {hour:02d}:{minute:02d}:00"


def _project_spaces():
    """Shared mock project spaces list."""
    return [
        {
            "id": 1,
            "project_name": "数栈实时平台-实时",
            "project_space_id": "10001",
            "project_type": "stream",
            "repo_id": 1,
            "repo_url": "http://jnbygitlab.jnby.com/dtstack/realtime-platform.git",
            "default_branch": "main",
            "current_branch": "main",
            "repo_module_type": "stream",
            "module_label": "实时",
            "module_tone": "realtime",
            "option_label": "数栈实时平台-实时 (实时)",
        },
        {
            "id": 2,
            "project_name": "数据仓库-离线",
            "project_space_id": "10002",
            "project_type": "offline",
            "repo_id": 2,
            "repo_url": "http://jnbygitlab.jnby.com/dtstack/data-warehouse.git",
            "default_branch": "master",
            "current_branch": "master",
            "repo_module_type": "offline",
            "module_label": "离线",
            "module_tone": "offline",
            "option_label": "数据仓库-离线 (离线)",
        },
        {
            "id": 3,
            "project_name": "用户画像-实时",
            "project_space_id": "10003",
            "project_type": "stream",
            "repo_id": 3,
            "repo_url": "http://jnbygitlab.jnby.com/dtstack/user-profile.git",
            "default_branch": "dev",
            "current_branch": "dev",
            "repo_module_type": "stream",
            "module_label": "实时",
            "module_tone": "realtime",
            "option_label": "用户画像-实时 (实时)",
        },
        {
            "id": 4,
            "project_name": "报表中心-离线",
            "project_space_id": "10004",
            "project_type": "offline",
            "repo_id": 4,
            "repo_url": "http://jnbygitlab.jnby.com/dtstack/report-center.git",
            "default_branch": "main",
            "current_branch": "main",
            "repo_module_type": "offline",
            "module_label": "离线",
            "module_tone": "offline",
            "option_label": "报表中心-离线 (离线)",
        },
        {
            "id": 5,
            "project_name": "风控引擎-实时",
            "project_space_id": "10005",
            "project_type": "stream",
            "repo_id": None,
            "repo_url": None,
            "default_branch": None,
            "current_branch": None,
            "repo_module_type": None,
            "module_label": "未标识",
            "module_tone": "unknown",
            "option_label": "风控引擎-实时 (未标识)",
        },
    ]

PROJECT_SPACES = _project_spaces()

# ---- nav items ----

NAV_ITEMS = [
    {"label": "首页",     "path": "/home",    "key": "home",    "icon": "⌂"},
    {"label": "任务列表", "path": "/tasks",  "key": "tasks",   "icon": "▦"},
    {"label": "发布确认", "path": "/confirm", "key": "confirm", "icon": "▣"},
    {"label": "发布记录", "path": "/records", "key": "records", "icon": "▤"},
    {"label": "配置管理", "path": "/config",  "key": "config",  "icon": "◎"},
    {"label": "用户管理", "path": "/users",   "key": "users",   "icon": "♙"},
    {"label": "Git 代码管理", "path": "/git", "key": "git", "icon": "⌬"},
]

# ---- env labels ----

ENV_LABELS = {"source": "测试环境", "target": "生产环境"}

# ---- base context ----

def mock_base_context(active, project_space_id=None):
    """Mock data for base_context()."""
    project_spaces = PROJECT_SPACES
    current = project_spaces[0]
    if project_space_id:
        for ps in project_spaces:
            if str(ps["id"]) == str(project_space_id):
                current = ps
                break
    show = active in {"tasks", "confirm"}
    return {
        "nav_items": NAV_ITEMS,
        "active": active,
        "current_project": current["project_name"] if show else "全局视图",
        "project_spaces": project_spaces if show else [],
        "selected_project_space": current if show else {},
        "show_project_space_switcher": show,
        "user_name": "admin",
        "user_role": "超级管理员",
    }


# ---- pagination ----

def pagination_meta(total, page_size=10):
    return {
        "total": total,
        "page_size": page_size,
        "page_count": max(1, (total + page_size - 1) // page_size),
    }


# ---- tasks (shared) ----

STATUSES = ["未发布", "已发布", "发布失败"]
MODULES = ["实时", "离线"]
TASK_TYPES = ["SparkSQL", "数据同步", "Flink SQL", "Shell", "Python"]
SUBMITTERS = ["张三", "李四", "王五", "赵六", "admin"]
PROJECT_NAMES = [ps["project_name"] for ps in PROJECT_SPACES]

TASK_NAMES = [
    "ods_order_daily_sync", "dwd_user_behavior_etl", "ads_sales_report",
    "dim_product_info_load", "dws_traffic_summary", "ods_member_import",
    "ads_conversion_funnel", "dim_region_mapping", "dwd_payment_detail",
    "ads_daily_kpi_board", "ods_inventory_snapshot", "dws_user_segment",
    "dim_date_lookup", "dwd_click_stream", "ads_campaign_roi",
    "ods_log_raw_ingest", "dws_merchant_stat", "dim_category_tree",
    "dwd_refund_record", "ads_realtime_gmv", "ods_coupon_sync",
    "dwd_search_keyword", "dim_store_hierarchy", "dws_order_path",
    "ads_user_lifetime_value",
]


def _make_task(i):
    status = STATUSES[i % 3 if i < 20 else 0]
    module = MODULES[i % 2]
    ps = PROJECT_SPACES[i % len(PROJECT_SPACES)]
    git_name = TASK_NAMES[i].replace("_", "_")
    submitted_at = _dt(-(i % 14), r.randint(8, 18), r.randint(0, 59))
    return {
        "id": str(i + 1),
        "source_task_id": str(20000 + i),
        "name": TASK_NAMES[i],
        "module_type": "stream" if module == "实时" else "offline",
        "task_type_label": TASK_TYPES[i % len(TASK_TYPES)],
        "module": module,
        "project_space": ps["project_name"],
        "submitted_at": submitted_at,
        "submitter": SUBMITTERS[i % len(SUBMITTERS)],
        "status": status,
        "node_pid": str(1000 + i),
        "git_path": f"tasks/{module}/{TASK_NAMES[i]}.sql",
        "git_directory": f"tasks/{module}/",
        "artifact_kind": "sql",
        "release_file_status": "已就绪" if status == "已发布" else ("发布失败" if status == "发布失败" else "未发布"),
        "required_artifact": "sql",
        "metadata_status": "已匹配" if i % 3 != 0 else "Git侧待匹配",
        "directory_status": "已匹配" if i % 2 == 0 else "未匹配",
        "datasource_status": "已匹配" if i % 4 != 0 else "未匹配",
    }


def _make_tasks(count=25):
    return [_make_task(i) for i in range(count)]


_TASKS = _make_tasks()


# ---- records (shared) ----

BATCH_STATUSES = ["成功", "失败", "进行中", "校验通过", "校验阻断"]

def _make_record(i):
    status = BATCH_STATUSES[i % 3]
    offset = -(i * 2 + 1)
    start = _dt(offset, r.randint(8, 18), r.randint(0, 59))
    end = _dt(offset, r.randint(18, 23), r.randint(0, 59)) if status != "进行中" else "-"
    return {
        "batch_db_id": i + 1,
        "id": f"REL-{_TODAY.strftime('%Y%m%d')}-{i+1:04d}",
        "task_name": f"{TASK_NAMES[i % len(TASK_NAMES)]} (批#{i+1})",
        "source_env": "测试环境",
        "target_env": "生产环境",
        "start": start,
        "end": end,
        "status": status,
    }


def _make_records(count=15):
    return [_make_record(i) for i in range(count)]


_RECORDS = _make_records()


# ---------- mock context functions ----------

# ---- /home ----

def mock_home_context(home_view="all"):
    home_view_tabs = [
        {"key": "all", "label": "全部"},
        {"key": "realtime", "label": "实时"},
        {"key": "offline", "label": "离线"},
        {"key": "project", "label": "项目空间"},
    ]
    total_tasks = len(_TASKS)
    unpublished = sum(1 for t in _TASKS if t["status"] == "未发布")
    published = sum(1 for t in _TASKS if t["status"] == "已发布")
    failed = sum(1 for t in _TASKS if t["status"] == "发布失败")
    home_stats = [
        {"label": "总任务数", "value": str(total_tasks), "tone": "blue", "icon": "▣", "desc": "所有项目空间"},
        {"label": "已发布", "value": str(published), "tone": "green", "icon": "✓", "desc": "发布成功任务"},
        {"label": "未发布", "value": str(unpublished), "tone": "orange", "icon": "▰", "desc": "待发布任务"},
        {"label": "发布失败", "value": str(failed), "tone": "red", "icon": "×", "desc": "发布失败任务"},
    ]
    visible_spaces = PROJECT_SPACES
    if home_view == "realtime":
        visible_spaces = [p for p in PROJECT_SPACES if p["project_type"] == "stream"]
    elif home_view == "offline":
        visible_spaces = [p for p in PROJECT_SPACES if p["project_type"] == "offline"]
    days = 7
    chart = []
    for d in range(days - 1, -1, -1):
        total = r.randint(3, 20)
        success = r.randint(1, total)
        failed_cnt = total - success
        chart.append({
            "date": _d(-d),
            "label": (_TODAY - timedelta(days=d)).strftime("%m-%d"),
            "total": total,
            "success": success,
            "failed": failed_cnt,
            "height": min(100, total * 5),
        })
    datasource_summary = [
        {"source_module": "实时", "project_space_id": ps["id"], "project_space_name": ps["project_name"], "datasource_count": r.randint(3, 15)}
        for ps in PROJECT_SPACES
    ]
    return {
        "home_view": home_view,
        "home_view_tabs": home_view_tabs,
        "home_stats": home_stats,
        "records": _RECORDS[:5],
        "tasks": _TASKS[:8],
        "project_spaces_overview": visible_spaces,
        "datasource_summary": datasource_summary,
        "daily_release_chart": chart,
        "last_metadata_sync_at": _dt(0, 8, 30),
    }


# ---- /tasks ----

def mock_task_list_context(project_space_id=None):
    ps_id = int(project_space_id) if project_space_id else 1
    current = PROJECT_SPACES[0]
    for ps in PROJECT_SPACES:
        if ps["id"] == ps_id:
            current = ps
            break
    tasks = [t for t in _TASKS if t["project_space"] == current["project_name"]]
    if not tasks:
        tasks = _TASKS
    total = len(_TASKS)
    stats = [
        {"key": "total", "filter": "", "label": "总任务数", "value": str(total), "tone": "blue", "icon": "▣"},
        {"key": "unpublished", "filter": "未发布", "label": "未发布", "value": str(sum(1 for t in _TASKS if t["status"]=="未发布")), "tone": "orange", "icon": "▰"},
        {"key": "published", "filter": "已发布", "label": "发布成功", "value": str(sum(1 for t in _TASKS if t["status"]=="已发布")), "tone": "green", "icon": "✓"},
        {"key": "failed", "filter": "发布失败", "label": "发布失败", "value": str(sum(1 for t in _TASKS if t["status"]=="发布失败")), "tone": "red", "icon": "×"},
    ]
    git_context = {
        "status": "success",
        "message": "",
        "repo_url": current["repo_url"] or "未配置",
        "branch": current["current_branch"] or current["default_branch"] or "-",
        "module_type": current["project_type"],
        "module_label": current["module_label"],
        "worktree": "",
        "last_success_commit": "a1b2c3d4e5f6",
        "current_commit": "f6e5d4c3b2a1",
        "changed_files": [t["git_path"] for t in tasks],
        "changed_file_count": len(tasks),
        "task_candidate_count": len(tasks),
        "refresh_git": True,
    }
    return {
        "stats": stats,
        "tasks": tasks,
        "pagination": pagination_meta(len(tasks)),
        "project_spaces": PROJECT_SPACES,
        "selected_project_space": current,
        "git_context": git_context,
        "draft_info": {"draft_id": "1", "status": "success", "message": ""},
    }


# ---- /confirm ----

def mock_confirm_context(step=1, project_space_id=None):
    ps_id = int(project_space_id) if project_space_id else 1
    current = PROJECT_SPACES[0]
    for ps in PROJECT_SPACES:
        if ps["id"] == ps_id:
            current = ps
            break
    step_i = min(4, max(1, int(step or 1)))
    all_tasks = _TASKS[:8]
    selected = all_tasks[:3]
    selected_ids = ",".join(str(t["id"]) for t in selected)
    draft = {
        "id": 1,
        "scan_status": "success",
        "base_commit": "a1b2c3d4e5f6",
        "head_commit": "f6e5d4c3b2a1",
    }
    validation_groups = {}
    if step_i == 2:
        for t in selected:
            tid = str(t["id"])
            validation_groups[tid] = [
                {"check_name": "元数据缓存校验", "status": "通过", "message": "任务元数据已同步", "is_blocking": False, "draft_task_id": int(t["id"]), "check_key": "metadata"},
                {"check_name": "Git 文件可用性", "status": "通过", "message": "Git 文件可读取", "is_blocking": False, "draft_task_id": int(t["id"]), "check_key": "git_file"},
                {"check_name": "项目空间映射", "status": "通过", "message": "已配置生产项目空间映射", "is_blocking": True, "draft_task_id": int(t["id"]), "check_key": "project_mapping"},
                {"check_name": "数据源映射", "status": "通过" if int(t["id"]) % 3 != 0 else "失败", "message": "数据源映射已配置", "is_blocking": True, "draft_task_id": int(t["id"]), "check_key": "datasource"},
            ]
    mappings = [
        {"source": "test_mysql_warehouse", "target": "prod_mysql_warehouse", "type": "MySQL", "source_project_name": current["project_name"], "target_project_name": "生产-数据仓库", "mapping_status": "confirmed", "match_rule": "same_name", "connectivity_status": "connected", "last_synced_at": _dt(0, 8, 0), "source_resource_id": 101, "target_resource_id": 201, "connected": 1, "id": 1},
        {"source": "test_redis_cache", "target": "prod_redis_cache", "type": "Redis", "source_project_name": current["project_name"], "target_project_name": "生产-数据仓库", "mapping_status": "confirmed", "match_rule": "same_name", "connectivity_status": "connected", "last_synced_at": _dt(0, 8, 0), "source_resource_id": 102, "target_resource_id": 202, "connected": 1, "id": 2},
        {"source": "test_kafka_stream", "target": "prod_kafka_stream", "type": "Kafka", "source_project_name": current["project_name"], "target_project_name": "生产-数据仓库", "mapping_status": "confirmed", "match_rule": "same_name", "connectivity_status": "connected", "last_synced_at": _dt(0, 8, 0), "source_resource_id": 103, "target_resource_id": 203, "connected": 1, "id": 3},
        {"source": "test_hbase_store", "target": "", "type": "HBase", "source_project_name": current["project_name"], "target_project_name": "", "mapping_status": "pending", "match_rule": "manual", "connectivity_status": "unknown", "last_synced_at": _dt(0, 8, 0), "source_resource_id": 104, "target_resource_id": 0, "connected": 0, "id": 4},
    ]
    return {
        "step": step_i,
        "all_tasks": all_tasks,
        "tasks": selected,
        "selected_task_ids": selected_ids,
        "selected_task_id_list": selected_ids.split(","),
        "mappings": mappings if step_i == 2 else [],
        "draft": draft,
        "draft_id": "1",
        "env_labels": ENV_LABELS,
        "target_project_name": "生产-数据仓库",
        "validation_groups": validation_groups if step_i == 2 else {},
    }


# ---- /records & /records/{batch_id} ----

def mock_records_context(keyword="", batch_id=None):
    records = _RECORDS
    if keyword:
        records = [r for r in records if keyword.lower() in r["task_name"].lower()]
    total_r = len(records)
    stats = [
        {"label": "总发布次数", "value": str(total_r), "tone": "blue", "icon": "▣"},
        {"label": "成功次数", "value": str(sum(1 for r in records if r["status"] == "成功")), "tone": "green", "icon": "✓"},
        {"label": "失败次数", "value": str(sum(1 for r in records if r["status"] == "失败")), "tone": "red", "icon": "×"},
    ]
    record_detail = {"record": {}, "tasks": [], "logs": []}
    if batch_id:
        bid = int(batch_id)
        rec = _RECORDS[bid - 1] if 1 <= bid <= len(_RECORDS) else _RECORDS[0]
        record_detail = {
            "record": {
                "batch_code": rec["id"],
                "release_status": rec["status"],
                "batch_name": rec["task_name"],
                "source_env_name": "测试环境",
                "target_env_name": "生产环境",
                "source_project_name": "数栈实时平台-实时",
                "task_count": 3,
                "success_count": 2 if rec["status"] == "失败" else 3,
                "failed_count": 1 if rec["status"] == "失败" else 0,
                "git_commit_id": "a1b2c3d4e5f6",
                "publisher": "admin",
                "started_at": rec["start"],
                "finished_at": rec["end"],
                "failure_reason": "数据源映射缺失" if rec["status"] == "失败" else None,
            },
            "tasks": [
                {"task_name": TASK_NAMES[0], "module": "实时", "submitter": "张三", "sql_repo_path": f"tasks/实时/{TASK_NAMES[0]}.sql", "status": "成功" if rec["status"] != "失败" else "成功", "failure_reason": None},
                {"task_name": TASK_NAMES[1], "module": "离线", "submitter": "李四", "sql_repo_path": f"tasks/离线/{TASK_NAMES[1]}.sql", "status": "成功" if rec["status"] != "失败" else "成功", "failure_reason": None},
                {"task_name": TASK_NAMES[2], "module": "实时", "submitter": "王五", "sql_repo_path": f"tasks/实时/{TASK_NAMES[2]}.sql", "status": "失败" if rec["status"] == "失败" else "成功", "failure_reason": "数据源映射缺失" if rec["status"] == "失败" else None},
            ],
            "logs": [
                {"step_name": "发布前校验", "status": "通过", "request_summary": None, "response_summary": "所有校验项通过", "error_message": None},
                {"step_name": "元数据同步", "status": "通过", "request_summary": None, "response_summary": "元数据快照已同步", "error_message": None},
                {"step_name": "生产任务发布", "status": "通过" if rec["status"] == "成功" else "校验阻断", "request_summary": None, "response_summary": "任务已提交至生产" if rec["status"] == "成功" else "数据源映射校验阻断", "error_message": None if rec["status"] == "成功" else "目标数据源未配置映射"},
            ],
        }
    return {
        "records": records,
        "keyword": keyword,
        "stats": stats,
        "pagination": pagination_meta(len(records)),
        "record_detail": record_detail,
    }

# ---- /config ----

def mock_config_context():
    datasource_types = ["MySQL", "Redis", "Kafka", "HBase", "Elasticsearch", "PostgreSQL", "MongoDB", "Cassandra"]
    ds_mappings = []
    for i in range(8):
        src_name = f"test_ds_{['mysql','redis','kafka','hbase','es','pg','mongo','cassandra'][i]}"
        tgt_name = f"prod_ds_{['mysql','redis','kafka','hbase','es','pg','mongo','cassandra'][i]}"
        ds_type = datasource_types[i % len(datasource_types)]
        has_target = i < 6
        connected = i < 5
        enabled = 1 if i < 5 else 0
        ds_mappings.append({
            "row_id": str(i + 1),
            "source": src_name,
            "type": ds_type,
            "source_project_name": PROJECT_SPACES[i % len(PROJECT_SPACES)]["project_name"],
            "source_module": "实时" if i % 2 == 0 else "离线",
            "target_resource_id": str(200 + i + 1) if has_target else "",
            "target": tgt_name if has_target else "",
            "mapping_id": str(i + 1) if has_target else "",
            "mapping_status": "confirmed" if has_target else "pending",
            "mapping_enabled": enabled,
            "connectivity_status": "connected" if connected else ("failed" if has_target else "unknown"),
            "last_synced_at": _dt(0, 8, 0),
            "connected": 1 if connected else 0,
        })
    ds_mapping_stats = {
        "total": len(ds_mappings),
        "enabled": sum(1 for m in ds_mappings if m["mapping_enabled"] == 1 and m["target"]),
        "pending": sum(1 for m in ds_mappings if not m["target"]),
        "connected": sum(1 for m in ds_mappings if m["connectivity_status"] == "connected"),
        "failed": sum(1 for m in ds_mappings if m["connectivity_status"] == "failed"),
    }

    ds_resources = []
    for i in range(10):
        is_prod = i >= 5
        ds_resources.append({
            "id": i + 1,
            "name": f"{'prod' if is_prod else 'test'}_ds_{['mysql','redis','kafka','hbase','es','pg','mongo','cassandra','neo4j','druid'][i]}",
            "type": datasource_types[i % len(datasource_types)],
            "source_module": "实时" if i % 2 == 0 else "离线",
            "project_space_code": f"PS{10001 + i}",
            "project_space_id": (i % 5) + 1,
            "project_space": PROJECT_SPACES[i % len(PROJECT_SPACES)]["project_name"],
            "env_type": "prod" if is_prod else "test",
            "connectivity_status": "connected" if i % 3 != 0 else "failed",
            "last_synced_at": _dt(0, 8, 0),
        })

    project_mappings = []
    for i in range(4):
        src = PROJECT_SPACES[i]
        tgt_id = i + 1
        enabled = 1 if i < 3 else 0
        project_mappings.append({
            "row_id": tgt_id,
            "source_project_space_id": str(src["id"]),
            "source_space": src["project_name"],
            "source_type": src["project_type"],
            "source_type_label": src["module_label"],
            "source_env": "测试环境",
            "mapping_id": str(tgt_id),
            "status": "confirmed" if enabled else "pending",
            "mapping_enabled": str(enabled),
            "match_rule": "same_name",
            "last_synced_at": _dt(0, 8, 0),
            "target_env": "生产环境",
            "target_project_space_id": str(100 + tgt_id),
            "target_space": f"生产-{src['project_name']}" if enabled else "",
        })
    pm_stats = {
        "total": len(project_mappings),
        "enabled": sum(1 for m in project_mappings if m["mapping_enabled"] == "1" and m["target_space"]),
        "pending": sum(1 for m in project_mappings if not m["target_space"]),
        "offline": sum(1 for m in project_mappings if m["source_type"] == "offline"),
        "stream": sum(1 for m in project_mappings if m["source_type"] != "offline"),
    }

    def _opt_list(env_type):
        opts = []
        for i, ps in enumerate(PROJECT_SPACES):
            opts.append({
                "id": str(ps["id"] + (100 if env_type == "prod" else 0)),
                "name": ps["project_name"],
                "project_type": ps["project_type"],
                "label": f"{ps['project_name']} ({ps['module_label']})",
            })
        return opts

    project_space_options = {"source": _opt_list("test"), "target": _opt_list("prod")}

    ds_options_source = []
    ds_options_target = []
    for i in range(6):
        ds_options_source.append({
            "id": str(i + 1),
            "name": f"test_ds_{['mysql','redis','kafka','hbase','es','pg'][i]}",
            "type": datasource_types[i],
            "project_space": PROJECT_SPACES[i % len(PROJECT_SPACES)]["project_name"],
            "source_module": "实时" if i % 2 == 0 else "离线",
            "connectivity_status": "connected",
            "label": f"test_ds_{['mysql','redis','kafka','hbase','es','pg'][i]} (测试环境)",
        })
        ds_options_target.append({
            "id": str(100 + i + 1),
            "name": f"prod_ds_{['mysql','redis','kafka','hbase','es','pg'][i]}",
            "type": datasource_types[i],
            "project_space": PROJECT_SPACES[i % len(PROJECT_SPACES)]["project_name"],
            "source_module": "实时" if i % 2 == 0 else "离线",
            "connectivity_status": "connected" if i < 5 else "failed",
            "label": f"prod_ds_{['mysql','redis','kafka','hbase','es','pg'][i]} (生产环境)",
        })
    datasource_options = {"source": ds_options_source, "target": ds_options_target}

    ds_mapping_records = [
        {
            "id": i + 1,
            "source": f"test_ds_{['mysql','redis','kafka','hbase','es','pg','mongo','cassandra'][i]}",
            "target": f"prod_ds_{['mysql','redis','kafka','hbase','es','pg','mongo','cassandra'][i]}" if i < 6 else "",
            "type": datasource_types[i % len(datasource_types)],
            "source_project_name": PROJECT_SPACES[i % len(PROJECT_SPACES)]["project_name"],
            "target_project_name": f"生产-{PROJECT_SPACES[i % len(PROJECT_SPACES)]['project_name']}" if i < 6 else "",
            "mapping_status": "confirmed" if i < 6 else "pending",
            "match_rule": "same_name" if i < 6 else "manual",
            "connectivity_status": "connected" if i < 5 else ("failed" if i == 5 else "unknown"),
            "last_synced_at": _dt(0, 8, 0),
            "source_resource_id": i + 1,
            "target_resource_id": 100 + i + 1 if i < 6 else 0,
            "connected": 1 if i < 5 else 0,
        }
        for i in range(8)
    ]

    git_bindings = []
    for ps in PROJECT_SPACES:
        has_repo = ps["repo_id"] is not None
        git_bindings.append({
            "project_space_id": ps["id"],
            "project_name": ps["project_name"],
            "project_type": ps["project_type"],
            "repo_id": ps["repo_id"],
            "repo_url": ps["repo_url"],
            "default_branch": ps["default_branch"],
            "current_branch": ps["current_branch"],
            "repo_module_type": ps["repo_module_type"],
            "git_provider": "gitlab" if has_repo else None,
            "gitlab_project_id": ps["repo_id"],
            "gitlab_project_path": ps["repo_url"].replace("http://jnbygitlab.jnby.com/", "").replace(".git", "") if has_repo else None,
            "api_sync_mode": 0,
            "module_label": ps["module_label"],
            "module_type_value": "stream" if ps["project_type"] == "stream" else ("offline" if ps["project_type"] == "offline" else ""),
            "latest_commit_id": "a1b2c3d4e5f6789012345678901234567890abcd" if has_repo else None,
            "latest_commit_time": _dt(-1, 18, 30) if has_repo else None,
            "latest_commit_author": "张三" if has_repo else None,
            "latest_commit_message": "feat: 更新数据处理逻辑" if has_repo else None,
            "last_refresh_status": "success" if has_repo else None,
            "last_refresh_message": "OK" if has_repo else None,
            "last_refreshed_at": _dt(-1, 18, 31) if has_repo else None,
            "is_current": 1 if has_repo else 0,
        })

    return {
        "env_labels": ENV_LABELS,
        "project_mappings": project_mappings,
        "project_mapping_stats": pm_stats,
        "project_space_options": project_space_options,
        "datasource_mappings": ds_mappings,
        "datasource_mapping_stats": ds_mapping_stats,
        "datasource_mapping_records": ds_mapping_records,
        "datasource_options": datasource_options,
        "datasource_resources": ds_resources,
        "directory_sync": {
            "directory_count": "42",
            "task_count": str(len(_TASKS)),
            "datasource_count": "16",
            "last_synced_at": _dt(0, 8, 30),
            "status": "已同步",
        },
        "git_bindings": git_bindings,
        "sync_message": "",
        "config_message": "",
        "config_status": "",
        "gitlab_credential": {
            "base_url": "http://jnbygitlab.jnby.com",
            "username": "admin",
            "auth_mode": "password",
            "last_check_status": "未检测",
            "last_check_message": "",
            "last_checked_at": "-",
            "git_api_last_check_status": "未检测",
            "git_api_last_check_message": "",
            "git_api_last_checked_at": "-",
            "has_password": "已保存",
            "has_token": "未保存",
        },
        "gitlab_project_url": "",
        "gitlab_check_message": "",
        "gitlab_check_status": "",
    }


# ---- /users ----

def mock_users_context():
    users = [
        {"username": "admin", "role": "超级管理员", "created_at": _dt(-30, 10, 0), "status": "启用"},
        {"username": "zhangsan", "role": "发布管理员", "created_at": _dt(-25, 14, 30), "status": "启用"},
        {"username": "lisi", "role": "开发者", "created_at": _dt(-20, 9, 15), "status": "启用"},
        {"username": "wangwu", "role": "开发者", "created_at": _dt(-15, 11, 0), "status": "启用"},
        {"username": "zhaoliu", "role": "只读用户", "created_at": _dt(-10, 16, 45), "status": "启用"},
        {"username": "qianqi", "role": "开发者", "created_at": _dt(-5, 8, 20), "status": "禁用"},
    ]
    return {"users": users, "pagination": pagination_meta(len(users))}


# ---- /git ----

def mock_git_context():
    repo = {
        "repo_url": "http://jnbygitlab.jnby.com/dtstack/realtime-platform.git",
        "default_branch": "main",
        "current_branch": "main",
        "latest_commit_id": "a1b2c3d4e5f6789012345678901234567890abcd",
        "latest_commit_time": _dt(-1, 18, 30),
        "latest_commit_author": "张三",
        "latest_commit_message": "feat: 优化实时任务调度逻辑",
    }
    commits = []
    for i in range(12):
        commit_id = f"{r.randint(0,9)}{r.randint(0,9)}{r.randint(0,9)}{r.randint(0,9)}{r.randint(0,9)}{r.randint(0,9)}{r.randint(0,9)}{r.randint(0,9)}{r.randint(0,9)}{r.randint(0,9)}"
        commits.append({
            "commit": commit_id,
            "time": _dt(-i - 1, r.randint(8, 22), r.randint(0, 59)),
            "author": SUBMITTERS[i % len(SUBMITTERS)],
            "message": ["feat: 新增实时ETL任务配置", "fix: 修复离线任务调度异常", "refactor: 重构数据源连接池", "docs: 更新发布操作手册", "chore: 升级依赖版本"][i % 5],
            "files": r.randint(1, 8),
        })
    bindings = []
    for ps in PROJECT_SPACES:
        if ps["repo_id"]:
            bindings.append({
                "project_name": ps["project_name"],
                "module_label": ps["module_label"],
                "module_type_value": "stream" if ps["project_type"] == "stream" else "offline",
                "repo_url": ps["repo_url"] or "-",
                "current_branch": ps["current_branch"] or "-",
                "latest_commit_id": "a1b2c3d4e5f6789012345678901234567890abcd",
            })
    return {
        "git": {"repo": repo, "commits": commits, "bindings": bindings},
        "pagination": pagination_meta(len(commits)),
    }
