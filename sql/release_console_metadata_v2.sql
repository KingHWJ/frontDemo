-- 数栈发布台第一版非 API 阶段补充表
-- 使用方式：在 Web 元数据库中手动执行。本脚本不会由应用自动执行。

-- 数据源映射升级：从文本替换逐步过渡到测试数据源 ID -> 生产数据源 ID。
-- 若字段已存在，重复执行本段会报重复字段；已建库环境请按实际情况跳过已存在字段。
ALTER TABLE rc_datasource_mapping
  ADD COLUMN source_datasource_resource_id BIGINT DEFAULT NULL COMMENT '测试环境数据源资源 ID' AFTER project_mapping_id,
  ADD COLUMN target_datasource_resource_id BIGINT DEFAULT NULL COMMENT '生产环境数据源资源 ID' AFTER source_datasource_resource_id,
  ADD INDEX idx_rc_datasource_mapping_source_resource (source_datasource_resource_id),
  ADD INDEX idx_rc_datasource_mapping_target_resource (target_datasource_resource_id);

CREATE TABLE IF NOT EXISTS rc_gitlab_credential (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  credential_key VARCHAR(64) NOT NULL DEFAULT 'global' COMMENT '第一版固定 global',
  base_url VARCHAR(512) NOT NULL,
  username VARCHAR(128) NOT NULL,
  password_ciphertext TEXT NOT NULL COMMENT '使用 COOKIE_ENCRYPTION_KEY 加密后的 GitLab 密码',
  last_check_status VARCHAR(32) NOT NULL DEFAULT '未检测',
  last_check_message VARCHAR(1024) DEFAULT NULL,
  last_checked_at DATETIME DEFAULT NULL,
  is_enabled TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_rc_gitlab_credential_key (credential_key),
  INDEX idx_rc_gitlab_credential_enabled (is_enabled)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='GitLab 全局凭据';

-- Git 仓库刷新状态补充。已建库环境如果字段已存在，请跳过重复字段。
ALTER TABLE rc_git_repo
  ADD COLUMN last_refresh_status VARCHAR(32) NOT NULL DEFAULT '未刷新' AFTER latest_commit_message,
  ADD COLUMN last_refresh_message VARCHAR(1024) DEFAULT NULL AFTER last_refresh_status,
  ADD COLUMN last_refreshed_at DATETIME DEFAULT NULL AFTER last_refresh_message;

-- 推荐将提交唯一键调整为 repo_id + commit_id，避免不同仓库存在相同 commit 时冲突。
-- 已存在 uk_rc_git_commit_id 的环境请先手工 DROP INDEX uk_rc_git_commit_id ON rc_git_commit;
-- ALTER TABLE rc_git_commit ADD UNIQUE KEY uk_rc_git_commit_repo_commit (repo_id, commit_id);

CREATE TABLE IF NOT EXISTS rc_project_directory (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  env_id BIGINT NOT NULL,
  project_space_id BIGINT NOT NULL,
  directory_id VARCHAR(128) NOT NULL COMMENT '数栈目录 ID',
  parent_directory_id VARCHAR(128) DEFAULT NULL COMMENT '父目录 ID',
  directory_name VARCHAR(256) NOT NULL,
  relative_path VARCHAR(1024) NOT NULL COMMENT '项目空间内相对目录路径',
  module_type VARCHAR(32) NOT NULL DEFAULT 'offline' COMMENT 'offline/stream',
  is_enabled TINYINT(1) NOT NULL DEFAULT 1,
  last_synced_at DATETIME DEFAULT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_rc_project_directory_source (project_space_id, directory_id, module_type),
  INDEX idx_rc_project_directory_path (project_space_id, relative_path(255), is_enabled),
  CONSTRAINT fk_rc_project_directory_env FOREIGN KEY (env_id) REFERENCES rc_environment(id),
  CONSTRAINT fk_rc_project_directory_project FOREIGN KEY (project_space_id) REFERENCES rc_project_space(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='项目空间目录树缓存';

CREATE TABLE IF NOT EXISTS rc_task_artifact_policy (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  module_type VARCHAR(32) NOT NULL COMMENT '实时/离线',
  task_type_label VARCHAR(64) NOT NULL,
  artifact_required TINYINT(1) NOT NULL DEFAULT 1,
  allowed_artifact_kinds VARCHAR(256) NOT NULL DEFAULT 'manifest',
  auto_export_allowed TINYINT(1) NOT NULL DEFAULT 0,
  description VARCHAR(512) DEFAULT NULL,
  is_enabled TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_rc_task_artifact_policy_type (module_type, task_type_label)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='任务类型 Git 发布文件策略';

CREATE TABLE IF NOT EXISTS rc_task_artifact (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  repo_id BIGINT DEFAULT NULL,
  commit_id VARCHAR(128) DEFAULT NULL,
  project_space_id BIGINT DEFAULT NULL,
  task_name VARCHAR(256) NOT NULL,
  module_type VARCHAR(32) NOT NULL,
  task_type_label VARCHAR(64) NOT NULL,
  git_path VARCHAR(1024) NOT NULL,
  git_directory VARCHAR(1024) DEFAULT NULL,
  artifact_kind VARCHAR(32) NOT NULL,
  parse_status VARCHAR(32) NOT NULL DEFAULT '已解析',
  parse_message VARCHAR(1024) DEFAULT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_rc_task_artifact_repo_commit (repo_id, commit_id),
  INDEX idx_rc_task_artifact_project_task (project_space_id, module_type, task_name),
  CONSTRAINT fk_rc_task_artifact_repo FOREIGN KEY (repo_id) REFERENCES rc_git_repo(id),
  CONSTRAINT fk_rc_task_artifact_project FOREIGN KEY (project_space_id) REFERENCES rc_project_space(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Git 变更任务文件解析结果';

CREATE TABLE IF NOT EXISTS rc_release_draft (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  draft_code VARCHAR(128) NOT NULL UNIQUE,
  source_project_space_id BIGINT DEFAULT NULL,
  source_project_name VARCHAR(128) NOT NULL,
  repo_id BIGINT DEFAULT NULL,
  base_commit VARCHAR(128) DEFAULT NULL,
  head_commit VARCHAR(128) DEFAULT NULL,
  changed_file_count INT NOT NULL DEFAULT 0,
  task_count INT NOT NULL DEFAULT 0,
  scan_status VARCHAR(32) NOT NULL DEFAULT '待确认',
  created_by VARCHAR(128) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_rc_release_draft_project_time (source_project_space_id, created_at),
  INDEX idx_rc_release_draft_status (scan_status),
  CONSTRAINT fk_rc_release_draft_project FOREIGN KEY (source_project_space_id) REFERENCES rc_project_space(id),
  CONSTRAINT fk_rc_release_draft_repo FOREIGN KEY (repo_id) REFERENCES rc_git_repo(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='发布草稿';

CREATE TABLE IF NOT EXISTS rc_release_draft_task (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  draft_id BIGINT NOT NULL,
  task_key VARCHAR(256) NOT NULL,
  source_task_id VARCHAR(128) DEFAULT NULL,
  target_task_id VARCHAR(128) DEFAULT NULL,
  task_name VARCHAR(256) NOT NULL,
  module_type VARCHAR(32) NOT NULL,
  task_type_label VARCHAR(64) NOT NULL,
  project_space_name VARCHAR(128) DEFAULT NULL,
  git_path VARCHAR(1024) DEFAULT NULL,
  git_directory VARCHAR(1024) DEFAULT NULL,
  artifact_kind VARCHAR(32) DEFAULT NULL,
  release_file_status VARCHAR(32) NOT NULL DEFAULT '已就绪',
  metadata_status VARCHAR(32) NOT NULL DEFAULT '未匹配',
  directory_status VARCHAR(32) NOT NULL DEFAULT '未校验',
  datasource_status VARCHAR(32) NOT NULL DEFAULT '未校验',
  submitted_at DATETIME DEFAULT NULL,
  submitter VARCHAR(128) DEFAULT NULL,
  release_status VARCHAR(32) NOT NULL DEFAULT '未发布',
  is_selected TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_rc_release_draft_task_draft (draft_id),
  INDEX idx_rc_release_draft_task_status (release_status),
  CONSTRAINT fk_rc_release_draft_task_draft FOREIGN KEY (draft_id) REFERENCES rc_release_draft(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='发布草稿任务明细';

CREATE TABLE IF NOT EXISTS rc_task_config_snapshot (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  draft_task_id BIGINT DEFAULT NULL,
  env_type VARCHAR(32) NOT NULL COMMENT 'test/prod_before/prod_after',
  source_task_id VARCHAR(128) DEFAULT NULL,
  target_task_id VARCHAR(128) DEFAULT NULL,
  code_hash VARCHAR(128) DEFAULT NULL,
  code_text MEDIUMTEXT DEFAULT NULL,
  config_json JSON DEFAULT NULL,
  schedule_json JSON DEFAULT NULL,
  params_json JSON DEFAULT NULL,
  resource_json JSON DEFAULT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_rc_task_config_snapshot_task (draft_task_id, env_type),
  CONSTRAINT fk_rc_task_config_snapshot_draft_task FOREIGN KEY (draft_task_id) REFERENCES rc_release_draft_task(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='任务配置快照';

CREATE TABLE IF NOT EXISTS rc_datasource_usage (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  draft_task_id BIGINT DEFAULT NULL,
  source_datasource_id VARCHAR(128) DEFAULT NULL,
  source_datasource_name VARCHAR(256) DEFAULT NULL,
  datasource_type VARCHAR(64) DEFAULT NULL,
  field_path VARCHAR(512) DEFAULT NULL,
  mapping_id BIGINT DEFAULT NULL,
  check_status VARCHAR(32) NOT NULL DEFAULT '未校验',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_rc_datasource_usage_task (draft_task_id),
  CONSTRAINT fk_rc_datasource_usage_draft_task FOREIGN KEY (draft_task_id) REFERENCES rc_release_draft_task(id),
  CONSTRAINT fk_rc_datasource_usage_mapping FOREIGN KEY (mapping_id) REFERENCES rc_datasource_mapping(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='任务数据源使用点';

CREATE TABLE IF NOT EXISTS rc_release_validation (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  draft_id BIGINT NOT NULL,
  draft_task_id BIGINT DEFAULT NULL,
  check_key VARCHAR(64) NOT NULL,
  check_name VARCHAR(128) NOT NULL,
  check_status VARCHAR(32) NOT NULL,
  is_blocking TINYINT(1) NOT NULL DEFAULT 1,
  message VARCHAR(1024) DEFAULT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_rc_release_validation_draft (draft_id),
  INDEX idx_rc_release_validation_task (draft_task_id),
  CONSTRAINT fk_rc_release_validation_draft FOREIGN KEY (draft_id) REFERENCES rc_release_draft(id),
  CONSTRAINT fk_rc_release_validation_draft_task FOREIGN KEY (draft_task_id) REFERENCES rc_release_draft_task(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='发布前校验结果';

CREATE TABLE IF NOT EXISTS rc_task_publish_binding (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  source_project_space_id BIGINT NOT NULL,
  target_project_space_id BIGINT NOT NULL,
  module_type VARCHAR(32) NOT NULL,
  source_task_id VARCHAR(128) NOT NULL,
  target_task_id VARCHAR(128) NOT NULL,
  task_name VARCHAR(256) NOT NULL,
  is_enabled TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_rc_task_publish_binding_task (source_project_space_id, module_type, source_task_id),
  INDEX idx_rc_task_publish_binding_target (target_project_space_id, target_task_id),
  CONSTRAINT fk_rc_task_publish_binding_source_project FOREIGN KEY (source_project_space_id) REFERENCES rc_project_space(id),
  CONSTRAINT fk_rc_task_publish_binding_target_project FOREIGN KEY (target_project_space_id) REFERENCES rc_project_space(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='测试任务到生产任务绑定';

CREATE TABLE IF NOT EXISTS rc_release_step_log (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  batch_id BIGINT DEFAULT NULL,
  draft_id BIGINT DEFAULT NULL,
  draft_task_id BIGINT DEFAULT NULL,
  step_key VARCHAR(64) NOT NULL,
  step_name VARCHAR(128) NOT NULL,
  step_status VARCHAR(32) NOT NULL,
  request_summary VARCHAR(1024) DEFAULT NULL,
  response_summary VARCHAR(1024) DEFAULT NULL,
  error_message VARCHAR(2048) DEFAULT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_rc_release_step_log_batch (batch_id),
  INDEX idx_rc_release_step_log_draft (draft_id),
  CONSTRAINT fk_rc_release_step_log_batch FOREIGN KEY (batch_id) REFERENCES rc_release_batch(id),
  CONSTRAINT fk_rc_release_step_log_draft FOREIGN KEY (draft_id) REFERENCES rc_release_draft(id),
  CONSTRAINT fk_rc_release_step_log_draft_task FOREIGN KEY (draft_task_id) REFERENCES rc_release_draft_task(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='发布执行步骤日志';

-- 初始化策略建议：
-- INSERT INTO rc_task_artifact_policy(module_type, task_type_label, artifact_required, allowed_artifact_kinds, auto_export_allowed, description)
-- VALUES
-- ('离线', 'SQL', 0, 'sql', 0, '离线 SQL 可以直接由 Git SQL 文件识别'),
-- ('离线', '数据同步', 1, 'manifest,json,yaml', 0, '数据同步必须人工提交发布配置文件'),
-- ('实时', '实时任务', 1, 'manifest,json,yaml', 0, '实时任务必须人工提交发布配置文件');
ALTER TABLE rc_git_repo ADD COLUMN module_type VARCHAR(32) NULL COMMENT '绑定模块类型：offline/stream';


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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='测试/生产任务元数据缓存';


ALTER TABLE rc_project_space
  ADD COLUMN last_synced_at DATETIME DEFAULT NULL AFTER is_enabled,
  ADD INDEX idx_rc_project_space_env_type_enabled (env_id, project_type, is_enabled);


ALTER TABLE rc_project_mapping
  ADD COLUMN mapping_status VARCHAR(32) NOT NULL DEFAULT 'pending' COMMENT 'pending/confirmed' AFTER target_catalogue_id,
  ADD COLUMN match_rule VARCHAR(64) DEFAULT NULL COMMENT 'same_name/manual' AFTER mapping_status,
  ADD COLUMN confirmed_at DATETIME DEFAULT NULL AFTER match_rule,
  ADD COLUMN last_synced_at DATETIME DEFAULT NULL AFTER confirmed_at,
  ADD INDEX idx_rc_project_mapping_status (mapping_status, is_enabled);

ALTER TABLE rc_datasource_mapping
  ADD COLUMN mapping_status VARCHAR(32) NOT NULL DEFAULT 'pending' COMMENT 'pending/confirmed' AFTER target_value,
  ADD COLUMN match_rule VARCHAR(64) DEFAULT NULL COMMENT 'same_name/manual' AFTER mapping_status,
  ADD COLUMN confirmed_at DATETIME DEFAULT NULL AFTER match_rule,
  ADD COLUMN last_synced_at DATETIME DEFAULT NULL AFTER confirmed_at,
  ADD INDEX idx_rc_datasource_mapping_status (mapping_status, is_enabled);

ALTER TABLE rc_release_task
  ADD INDEX idx_rc_release_task_source_task (source_task_id),
  ADD INDEX idx_rc_release_task_task_name (task_name);

ALTER TABLE rc_datasource_resource
  ADD INDEX idx_rc_datasource_resource_lookup (project_space_id, env_id, source_module, is_enabled);

ALTER TABLE rc_git_commit
  DROP INDEX uk_rc_git_commit_id;
ALTER TABLE rc_git_commit
  ADD UNIQUE KEY uk_rc_git_commit_repo_commit (repo_id, commit_id);


ALTER TABLE rc_datasource_mapping
  ADD COLUMN source_datasource_resource_id BIGINT DEFAULT NULL COMMENT '测试环境数据源资源 ID' AFTER project_mapping_id,
  ADD COLUMN target_datasource_resource_id BIGINT DEFAULT NULL COMMENT '生产环境数据源资源 ID' AFTER source_datasource_resource_id,
  ADD INDEX idx_rc_datasource_mapping_source_resource (source_datasource_resource_id),
  ADD INDEX idx_rc_datasource_mapping_target_resource (target_datasource_resource_id);


ALTER TABLE rc_git_repo
  ADD COLUMN last_refresh_status VARCHAR(32) NOT NULL DEFAULT '未刷新' AFTER latest_commit_message,
  ADD COLUMN last_refresh_message VARCHAR(1024) DEFAULT NULL AFTER last_refresh_status,
  ADD COLUMN last_refreshed_at DATETIME DEFAULT NULL AFTER last_refresh_message;

ALTER TABLE rc_git_repo
  ADD COLUMN module_type VARCHAR(32) NULL COMMENT '绑定模块类型：offline/stream';