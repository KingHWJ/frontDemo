# 生产环境凭据与 Cookie 复用实现计划

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让账号凭据页可以真实测试生产登录并保存生产 Cookie，后续发布时优先复用该 Cookie，缺失或过期时自动用已保存的生产账号密码重新登录。

**Architecture:** 保持现有登录页的测试环境链路不变，只在 `auth.py` 上补一层“生产环境凭据登录/取 Cookie”通用 helper。账号凭据页只维护生产环境账号密码，生产 Cookie 继续落在 `rc_auth_session`，状态展示沿用 `sys_user_credential` 的时间字段。

**Tech Stack:** FastAPI、requests、cryptography、gmssl、ddddocr、MySQL

---

### Task 1: 补测试与明确边界

**Files:**
- Create: `tests/test_production_credential_auth.py`
- Modify: `docs/发布操作逻辑文档.md`

- [ ] **Step 1: 写失败测试**
  - 覆盖“凭据页测试生产登录会刷新 Cookie 状态”
  - 覆盖“发布前取生产 Cookie 时会先复用，缺失/过期时自动重登”

- [ ] **Step 2: 运行测试确认失败**

- [ ] **Step 3: 补文档边界**
  - 说明当前自动重登基于保存的生产账号密码
  - 说明测试环境登录页逻辑保持不变

- [ ] **Step 4: 提交**

### Task 2: 补生产凭据与 Cookie helper

**Files:**
- Modify: `app/auth.py`
- Modify: `app/repositories.py`

- [ ] **Step 1: 实现凭据读取 helper**
  - 从 `sys_user_credential` 读取当前平台用户保存的生产账号密码
  - 解密返回明文密码供自动登录使用

- [ ] **Step 2: 实现生产自动登录 helper**
  - 使用生产环境 `base_url`
  - 自动拉验证码、OCR、SM2 加密、提交登录
  - 登录成功后继续写 `rc_auth_session`

- [ ] **Step 3: 实现生产 Cookie 复用 helper**
  - 优先读取最近有效的生产 Cookie
  - 缺失或过期则自动重新登录
  - 成功后刷新 `sys_user_credential.dtstack_cookie_expire_time`

- [ ] **Step 4: 跑测试确认通过**

- [ ] **Step 5: 提交**

### Task 3: 接入账号凭据页与发布入口

**Files:**
- Modify: `app/main.py`
- Modify: `app/templates/credentials.html`（如按钮文案需要微调）

- [ ] **Step 1: 把 `/credentials/test-dtstack` 接成真实生产登录**
  - 使用当前平台用户保存的生产账号密码
  - 返回真实成功/失败消息

- [ ] **Step 2: 把发布入口切到生产 Cookie helper**
  - 不再直接复用当前浏览器登录会话里的 Cookie
  - 改成按当前平台用户读取/刷新生产 Cookie

- [ ] **Step 3: 验证凭据页状态与发布入口行为**

- [ ] **Step 4: 提交**

### Task 4: 验证与收尾

**Files:**
- Modify: `docs/数栈测试任务发布至生产环境功能细节文档.md`

- [ ] **Step 1: 运行验证**
  - `python -m unittest discover -s tests`
  - `python -m compileall app tests`
  - `git diff --check`

- [ ] **Step 2: 补技术文档**
  - 记录生产 Cookie 的来源、落库位置、自动重登策略

- [ ] **Step 3: 提交**
