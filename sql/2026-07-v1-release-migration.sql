-- 数栈发布项目 V1 实际发布功能迁移
-- 目标：把“草稿为中心”切换为“待发布任务池 + 真实发布批次”

CREATE TABLE IF NOT EXISTS rc_pending_release_task (
  id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
  source_project_space_id BIGINT NOT NULL COMMENT '测试项目空间主键 ID（rc_project_space.id）',
  repo_id BIGINT NULL COMMENT 'Git 仓库绑定 ID（rc_git_repo.id）',
  module_type VARCHAR(32) NOT NULL COMMENT '模块类型：offline/stream',
  git_path VARCHAR(1024) NOT NULL COMMENT 'Git 文件路径，作为待发布唯一识别的一部分',
  task_name VARCHAR(255) NOT NULL COMMENT '任务名称，当前主要来自 SQL 文件名',
  source_task_id VARCHAR(128) NULL COMMENT '测试侧任务 ID，可先为占位值',
  task_type_label VARCHAR(64) NULL COMMENT '任务类型标签，如 SQL',
  head_commit VARCHAR(64) NULL COMMENT '当前分支最新提交',
  last_success_commit VARCHAR(64) NULL COMMENT '该项目空间最近一次成功发布提交',
  submitted_at DATETIME NULL COMMENT 'Git 最新提交时间',
  submitter VARCHAR(128) NULL COMMENT 'Git 最新提交人',
  pending_status VARCHAR(32) NOT NULL DEFAULT 'pending' COMMENT 'pending/blocked/publishing/failed/success/unsupported',
  validation_status VARCHAR(32) NOT NULL DEFAULT 'ready' COMMENT 'ready/blocked/warning',
  validation_message VARCHAR(500) NULL COMMENT '校验说明或阻断原因',
  last_batch_id BIGINT NULL COMMENT '最近一次关联的发布批次',
  last_failure_reason VARCHAR(500) NULL COMMENT '最近失败原因',
  target_task_id VARCHAR(128) NULL COMMENT '生产任务 ID',
  first_detected_at DATETIME NOT NULL COMMENT '首次识别时间',
  last_detected_at DATETIME NOT NULL COMMENT '最近识别时间',
  published_at DATETIME NULL COMMENT '最近成功发布时间',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (id),
  KEY idx_pending_task_lookup (source_project_space_id, module_type, git_path(255), pending_status),
  KEY idx_pending_task_batch (last_batch_id, pending_status),
  KEY idx_pending_task_detected (source_project_space_id, last_detected_at),
  KEY idx_pending_task_source_name (source_task_id, task_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='待发布任务池';

ALTER TABLE rc_release_task
  ADD COLUMN pending_task_id BIGINT NULL COMMENT '关联待发布任务池 ID' AFTER batch_id,
  ADD COLUMN operation_type VARCHAR(32) NULL COMMENT '发布动作：create/update/unsupported' AFTER target_task_id;

ALTER TABLE rc_release_step_log
  ADD COLUMN pending_task_id BIGINT NULL COMMENT '关联待发布任务池 ID' AFTER batch_id,
  ADD COLUMN task_name VARCHAR(255) NULL COMMENT '步骤所属任务名' AFTER step_name,
  ADD COLUMN target_task_id VARCHAR(128) NULL COMMENT '步骤关联的生产任务 ID' AFTER task_name;

CREATE INDEX idx_release_task_batch_pending
  ON rc_release_task(batch_id, pending_task_id);

CREATE INDEX idx_task_publish_binding_source_name
  ON rc_task_publish_binding(source_project_space_id, module_type, task_name);

CREATE INDEX idx_release_step_log_batch_pending_step
  ON rc_release_step_log(batch_id, pending_task_id, step_key);
