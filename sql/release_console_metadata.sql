CREATE TABLE IF NOT EXISTS rc_environment (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  env_code VARCHAR(64) NOT NULL UNIQUE COMMENT '环境编码',
  env_name VARCHAR(128) NOT NULL COMMENT '环境名称',
  env_type VARCHAR(32) NOT NULL COMMENT 'test/prod/dev/pre',
  base_url VARCHAR(512) NOT NULL COMMENT '数栈 Base URL',
  credential_key VARCHAR(128) DEFAULT NULL COMMENT '凭据标识，不保存明文密码',
  is_enabled TINYINT(1) NOT NULL DEFAULT 1,
  connectivity_status VARCHAR(32) NOT NULL DEFAULT 'unknown',
  last_checked_at DATETIME DEFAULT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_rc_environment_type (env_type),
  INDEX idx_rc_environment_enabled (is_enabled)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='数栈环境配置';

CREATE TABLE IF NOT EXISTS rc_project_space (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  env_id BIGINT NOT NULL,
  project_code VARCHAR(128) DEFAULT NULL,
  project_name VARCHAR(128) NOT NULL,
  project_space_id VARCHAR(128) NOT NULL,
  project_type VARCHAR(32) NOT NULL DEFAULT 'realtime',
  is_enabled TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_rc_project_space_env_space (env_id, project_space_id),
  INDEX idx_rc_project_space_name (project_name),
  CONSTRAINT fk_rc_project_space_env FOREIGN KEY (env_id) REFERENCES rc_environment(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='项目空间元数据';

CREATE TABLE IF NOT EXISTS rc_project_mapping (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  source_env_id BIGINT NOT NULL,
  source_env_name VARCHAR(128) NOT NULL,
  source_project_space_id BIGINT NOT NULL,
  source_project_name VARCHAR(128) NOT NULL,
  target_env_id BIGINT NOT NULL,
  target_env_name VARCHAR(128) NOT NULL,
  target_project_space_id BIGINT NOT NULL,
  target_project_name VARCHAR(128) NOT NULL,
  target_catalogue_id VARCHAR(128) DEFAULT NULL COMMENT '目标任务开发目录 ID',
  is_enabled TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_rc_project_mapping_pair (source_project_space_id, target_project_space_id),
  INDEX idx_rc_project_mapping_enabled (is_enabled),
  CONSTRAINT fk_rc_project_mapping_source_env FOREIGN KEY (source_env_id) REFERENCES rc_environment(id),
  CONSTRAINT fk_rc_project_mapping_target_env FOREIGN KEY (target_env_id) REFERENCES rc_environment(id),
  CONSTRAINT fk_rc_project_mapping_source_project FOREIGN KEY (source_project_space_id) REFERENCES rc_project_space(id),
  CONSTRAINT fk_rc_project_mapping_target_project FOREIGN KEY (target_project_space_id) REFERENCES rc_project_space(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='项目空间映射';

CREATE TABLE IF NOT EXISTS rc_datasource_mapping (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  project_mapping_id BIGINT DEFAULT NULL,
  datasource_type VARCHAR(64) NOT NULL,
  source_pattern VARCHAR(1024) NOT NULL COMMENT '测试数据源匹配文本',
  target_value VARCHAR(1024) NOT NULL COMMENT '生产数据源替换文本',
  connectivity_status VARCHAR(32) NOT NULL DEFAULT 'unknown',
  description VARCHAR(512) DEFAULT NULL,
  sort_order INT NOT NULL DEFAULT 0,
  is_enabled TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_rc_datasource_mapping_project (project_mapping_id),
  INDEX idx_rc_datasource_mapping_enabled (is_enabled),
  CONSTRAINT fk_rc_datasource_mapping_project FOREIGN KEY (project_mapping_id) REFERENCES rc_project_mapping(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='数据源映射规则';

CREATE TABLE IF NOT EXISTS rc_datasource_resource (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  env_id BIGINT NOT NULL,
  project_space_id BIGINT DEFAULT NULL,
  project_space_code VARCHAR(128) DEFAULT NULL COMMENT '数栈项目空间 ID',
  project_space_name VARCHAR(128) DEFAULT NULL,
  datasource_id VARCHAR(128) NOT NULL COMMENT '数栈数据源中心 ID',
  datasource_name VARCHAR(256) NOT NULL,
  datasource_type VARCHAR(64) NOT NULL,
  schema_name VARCHAR(128) DEFAULT NULL,
  datasource_key VARCHAR(512) NOT NULL COMMENT '前端下拉与发布替换使用的展示值',
  connectivity_status VARCHAR(32) NOT NULL DEFAULT 'unknown',
  source_module VARCHAR(32) NOT NULL DEFAULT 'stream' COMMENT 'stream/offline',
  is_meta TINYINT(1) NOT NULL DEFAULT 0,
  is_enabled TINYINT(1) NOT NULL DEFAULT 1,
  last_synced_at DATETIME DEFAULT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_rc_datasource_resource_source (env_id, project_space_code, datasource_id, source_module),
  INDEX idx_rc_datasource_resource_env (env_id, is_enabled),
  INDEX idx_rc_datasource_resource_project (project_space_id),
  INDEX idx_rc_datasource_resource_name (datasource_name),
  CONSTRAINT fk_rc_datasource_resource_env FOREIGN KEY (env_id) REFERENCES rc_environment(id),
  CONSTRAINT fk_rc_datasource_resource_project FOREIGN KEY (project_space_id) REFERENCES rc_project_space(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='数栈数据源资源缓存';

CREATE TABLE IF NOT EXISTS rc_git_repo (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  project_space_id BIGINT DEFAULT NULL,
  repo_url VARCHAR(512) NOT NULL,
  default_branch VARCHAR(128) NOT NULL DEFAULT 'main',
  current_branch VARCHAR(128) NOT NULL DEFAULT 'main',
  latest_commit_id VARCHAR(128) DEFAULT NULL,
  latest_commit_time DATETIME DEFAULT NULL,
  latest_commit_author VARCHAR(128) DEFAULT NULL,
  latest_commit_message VARCHAR(512) DEFAULT NULL,
  is_current TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_rc_git_repo_project (project_space_id),
  INDEX idx_rc_git_repo_current (is_current),
  CONSTRAINT fk_rc_git_repo_project FOREIGN KEY (project_space_id) REFERENCES rc_project_space(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Git 仓库信息';

CREATE TABLE IF NOT EXISTS rc_git_commit (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  repo_id BIGINT DEFAULT NULL,
  commit_id VARCHAR(128) NOT NULL,
  committed_at DATETIME NOT NULL,
  author VARCHAR(128) NOT NULL,
  commit_message VARCHAR(512) NOT NULL,
  changed_files INT NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_rc_git_commit_id (commit_id),
  INDEX idx_rc_git_commit_repo_time (repo_id, committed_at),
  CONSTRAINT fk_rc_git_commit_repo FOREIGN KEY (repo_id) REFERENCES rc_git_repo(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Git 提交历史';

CREATE TABLE IF NOT EXISTS rc_release_batch (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  batch_code VARCHAR(128) NOT NULL UNIQUE,
  batch_name VARCHAR(128) NOT NULL,
  source_env_id BIGINT DEFAULT NULL,
  source_env_name VARCHAR(128) NOT NULL,
  target_env_id BIGINT DEFAULT NULL,
  target_env_name VARCHAR(128) NOT NULL,
  source_project_name VARCHAR(128) DEFAULT NULL,
  target_project_name VARCHAR(128) DEFAULT NULL,
  task_count INT NOT NULL DEFAULT 0,
  success_count INT NOT NULL DEFAULT 0,
  failed_count INT NOT NULL DEFAULT 0,
  release_status VARCHAR(32) NOT NULL DEFAULT '进行中',
  git_commit_id VARCHAR(128) DEFAULT NULL,
  publisher VARCHAR(128) NOT NULL,
  started_at DATETIME NOT NULL,
  finished_at DATETIME DEFAULT NULL,
  failure_reason VARCHAR(1024) DEFAULT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_rc_release_batch_status (release_status),
  INDEX idx_rc_release_batch_started (started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='发布批次';

CREATE TABLE IF NOT EXISTS rc_release_task (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  batch_id BIGINT DEFAULT NULL,
  source_task_id VARCHAR(128) NOT NULL,
  target_task_id VARCHAR(128) DEFAULT NULL,
  task_name VARCHAR(256) NOT NULL,
  task_type VARCHAR(32) NOT NULL DEFAULT '实时',
  source_submit_time DATETIME DEFAULT NULL,
  submitter VARCHAR(128) NOT NULL,
  release_status VARCHAR(32) NOT NULL DEFAULT '未发布',
  sql_repo_path VARCHAR(512) DEFAULT NULL,
  failure_reason VARCHAR(1024) DEFAULT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_rc_release_task_batch (batch_id),
  INDEX idx_rc_release_task_status (release_status),
  INDEX idx_rc_release_task_submit_time (source_submit_time),
  CONSTRAINT fk_rc_release_task_batch FOREIGN KEY (batch_id) REFERENCES rc_release_batch(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='发布任务明细';

CREATE TABLE IF NOT EXISTS rc_user (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  username VARCHAR(128) NOT NULL UNIQUE,
  display_name VARCHAR(128) DEFAULT NULL,
  role_name VARCHAR(128) NOT NULL,
  credential_key VARCHAR(128) DEFAULT NULL COMMENT '凭据标识，不保存明文密码',
  is_enabled TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_rc_user_enabled (is_enabled)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Web 用户元数据';

CREATE TABLE IF NOT EXISTS rc_auth_session (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  session_id_hash CHAR(64) NOT NULL UNIQUE COMMENT 'Web 会话 ID 的 SHA-256 摘要',
  username VARCHAR(128) NOT NULL COMMENT '生产数栈登录账号',
  prod_env_id BIGINT NOT NULL COMMENT '生产环境 ID',
  cookie_ciphertext TEXT NOT NULL COMMENT '加密后的生产环境 Cookie JSON',
  cookie_expires_at DATETIME NOT NULL COMMENT 'Cookie 或本系统会话过期时间',
  last_validated_at DATETIME DEFAULT NULL COMMENT '最近一次本系统会话校验时间',
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_rc_auth_session_user (username),
  INDEX idx_rc_auth_session_active_expires (is_active, cookie_expires_at),
  CONSTRAINT fk_rc_auth_session_prod_env FOREIGN KEY (prod_env_id) REFERENCES rc_environment(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Web 登录会话与生产 Cookie 密文';

CREATE TABLE IF NOT EXISTS rc_operation_log (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  operator VARCHAR(128) NOT NULL,
  operation_type VARCHAR(64) NOT NULL,
  object_type VARCHAR(64) DEFAULT NULL,
  object_id VARCHAR(128) DEFAULT NULL,
  operation_content VARCHAR(1024) NOT NULL,
  operation_result VARCHAR(32) NOT NULL,
  ip_address VARCHAR(64) DEFAULT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_rc_operation_log_operator (operator),
  INDEX idx_rc_operation_log_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='操作审计日志';

CREATE TABLE IF NOT EXISTS rc_app_setting (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  setting_key VARCHAR(128) NOT NULL UNIQUE,
  setting_value VARCHAR(1024) NOT NULL,
  description VARCHAR(512) DEFAULT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='系统配置';

-- Seed 示例：
-- INSERT INTO rc_environment(env_code, env_name, env_type, base_url, credential_key)
-- VALUES ('test_env', '测试环境', 'test', 'http://test.example.com', 'dtstack_test');
