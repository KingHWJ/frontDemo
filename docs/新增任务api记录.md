# 前置任务版本与锁版本机制说明文档
## 一、核心概念定义
系统维护两条独立的版本轨道，服务于不同目的：

| 概念 | 存储位置 | 字段名 | 业务含义 |
| :--- | :--- | :--- | :--- |
| **业务版本** | 任务主表 + `ide.rdos_batch_task_version` | `version` | 记录任务**内容本身的修改次数**（数据快照编号） |
| **锁版本** | `ide.rdos_read_write_lock` | `version` | 记录**编辑锁的变更次数**（并发控制令牌，每次锁状态变动即+1） |


> **关键理解**：锁版本 ≠ 业务版本。锁是一个独立的“门禁系统”，它有自己的计数器，与内容版本无关。
>

---

## 二、数据库表结构及字段说明
### 1. 编辑锁表（`ide.rdos_read_write_lock`）
| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `id` | bigint | 主键 |
| `relation_id` | bigint | 关联的任务ID |
| `lock_name` | varchar | 锁名称，格式：`{任务ID}_{项目ID}_BATCH_TASK` |
| `version` | int | **锁版本号**（自增计数器，每次锁状态变更+1） |
| `project_id` | int | 项目ID |
| `tenant_id` | int | 租户ID |
| `create_user_id` | int | 创建人ID |
| `modify_user_id` | int | 最近修改人ID |
| `gmt_create` | datetime | 创建时间 |
| `gmt_modified` | datetime | 最近修改时间 |


**作用**：独立于业务数据的并发控制表，用于判断当前编辑会话是否有效。

### 2. 任务版本历史表（`ide.rdos_batch_task_version`）
| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `id` | bigint | 主键 |
| `task_id` | bigint | 关联的任务ID |
| `version` | int | **业务版本号**（每次保存+1） |
| `sql_text` | text | SQL内容（明文存储） |
| `task_params` | text | 任务参数 |
| `schedule_conf` | text | 调度配置 |
| `create_user_id` | int | 创建人ID |
| `gmt_create` | datetime | 创建时间 |
| `gmt_modified` | datetime | 修改时间 |


**作用**：存储每次保存时的任务内容快照，支持历史回溯。

### 3. 任务主表
包含任务最新状态，其中的 `version` 字段与历史表联动。

---

## 三、版本号取值与变化规则
### 1. 业务版本（`version`）
| 场景 | 取值 | 说明 |
| :--- | :--- | :--- |
| 新建任务（未保存） | `0` | 表示该任务从未保存过，历史表中无记录 |
| 首次保存成功 | `1` | 历史表插入第一条记录，主表同步更新为 `1` |
| 第N次保存成功 | `N` | 每次保存，历史表插入一条新记录，`version = 当前最大 + 1` |
| 非保存类操作（抢锁/解锁等） | **不变** | 仅锁状态变更，不影响业务版本 |


> **规则**：业务版本 **仅在 **`addOrUpdateTask`** 接口成功保存后递增**。
>

---

### 2. 锁版本（`lockVersion` / `readWriteLockVO.version`）
| 场景 | 取值变化 | 说明 |
| :--- | :--- | :--- |
| 新建任务 | `0` → `1` | 系统在锁表插入一条记录，初始版本号为 `1` |
| 用户点击“保存”（数据落盘） | `N` → `N+1` | 锁版本 +1（无论业务版本是否变化） |
| 用户A强占/抢锁 | `N` → `N+1` | 锁归属变更，令牌版本 +1 |
| 用户主动释放锁 | `N` → `N+1` | 锁释放，令牌版本 +1 |
| 其他涉及锁状态变更的操作 | `N` → `N+1` | 任何修改锁状态的行为都会使锁版本递增 |


> **规则**：锁版本 **只要锁的状态或归属发生任何变化，就立即 +1**，与业务数据是否修改无关。
>

---

## 四、请求与返回参数取值说明（API契约）
### 1. GET 获取任务详情（响应）
```json
{
  "data": {
    "id": 481,
    "version": 1,                         // ← 当前业务版本（数据快照号）
    "sqlText": "LS0tbmFtZS...",          // ← Base64编码的SQL内容
    "...其他业务字段...": "...",
    "readWriteLockVO": {                  // ← 锁信息对象（嵌套）
      "version": 2,                       // ← 当前锁版本（下一次请求要用的锁令牌）
      "lockName": "481_39_BATCH_TASK",
      "getLock": true,
      "lastKeepLockUserName": "admin@dtstack.com"
    }
  }
}
```

**前端取值规则**：

+ 从 `data.version` 读取当前业务版本 → 用于下次保存时传入请求体的 `version` 字段。
+ 从 `data.readWriteLockVO.version` 读取当前锁版本 → 用于下次保存/抢锁/解锁时传入请求体的 `lockVersion` 字段。

---

### 2. POST/PUT 保存任务（请求体）
```json
{
  "id": 481,
  "version": 1,                         // ← 必须取自上次 GET 响应的 data.version
  "lockVersion": 2,                     // ← 必须取自上次 GET 响应的 readWriteLockVO.version（平铺，不嵌套）
  "sqlText": "LS0tbmFtZSBzcGF...",      // ← Base64编码的修改后SQL内容
  "preSave": true,                      // ← 前端UI控制字段，后端可忽略
  "...其他业务字段...": "..."
}
```

**请求体特殊约定**：

| 关键点 | 说明 |
| :--- | :--- |
| `lockVersion` 必须**平铺**在根节点 | 不能嵌套在 `readWriteLockVO` 对象内 |
| `version` 必须原样传回 | 不要自行计算 +1 |
| `sqlText` 必须 Base64 编码 | 服务端收到后解码为明文存入历史表 |
| `id` 必须正确 | 指明要更新哪个任务 |


---

### 3. POST/PUT 保存任务（响应）
```json
{
  "code": 1,
  "data": {
    "id": 481,
    "version": 2,                         // ← 新的业务版本（旧值 +1）
    "sqlText": "LS0tbmFtZS...",          // ← Base64编码的最新SQL内容
    "readWriteLockVO": {
      "version": 3,                       // ← 新的锁版本（旧值 +1），下一次请求要用
      "lockName": "481_39_BATCH_TASK",
      "getLock": true,
      "lastKeepLockUserName": "admin@dtstack.com"
    },
    "...其他字段...": "..."
  },
  "success": true
}
```

**前端更新规则**：

+ 用 `data.version` 更新本地缓存的业务版本。
+ 用 `data.readWriteLockVO.version` 更新本地缓存的锁版本（准备下一次请求使用）。

---

### 4. 抢锁/解锁接口（示例）
**请求体**：

```json
{
  "taskId": 481,
  "lockVersion": 2                      // ← 取自当前持有的 readWriteLockVO.version
}
```

**响应**：

```json
{
  "data": {
    "readWriteLockVO": {
      "version": 3,                     // ← 新的锁版本（旧值 +1），仅锁变化，业务版本不变
      "getLock": true
    }
  }
}
```

> **注意**：纯权限操作（抢锁/解锁）的响应中可能**不包含业务版本**，或者业务版本保持不变。
>

---

## 五、完整生命周期示例（任务 481）
假设场景：用户 A 新建任务 → 保存 → 用户 B 抢锁 → 用户 B 修改并保存。

| 步骤 | 操作 | 请求体携带 | 服务端操作 | 响应返回 |
| :--- | :--- | :--- | :--- | :--- |
| **1** | 新建任务 | 无（创建接口） | 插入锁表：`version=1`；任务主表：`version=0` | `{ version: 0, readWriteLockVO: { version: 1 } }` |
| **2** | 保存（首次） | `{ id:481, version:0, lockVersion:1 }` | 校验通过；历史表插入 `version=1`；锁表变 `2` | `{ version: 1, readWriteLockVO: { version: 2 } }` |
| **3** | 用户 B 抢锁 | `{ taskId:481, lockVersion:2 }` | 校验通过；锁表变 `3`；数据不变 | `{ readWriteLockVO: { version: 3 } }` |
| **4** | 用户 B 抢锁（再次） | `{ taskId:481, lockVersion:3 }` | 校验通过；锁表变 `4`；数据不变 | `{ readWriteLockVO: { version: 4 } }` |
| **5** | 用户 B 修改后保存 | `{ id:481, version:1, lockVersion:4 }` | 校验通过；历史表插入 `version=2`；锁表变 `5` | `{ version: 2, readWriteLockVO: { version: 5 } }` |


**版本号演变总览**：

| 时间线 | 业务版本（主表+历史表） | 锁版本（锁表） | 操作类型 |
| :--- | :--- | :--- | :--- |
| 新建后 | `0` | `1` | 初始化 |
| 首次保存后 | `1` | `2` | 保存（双轨同增） |
| 抢锁后 | `1`（不变） | `3` | 权限变更（仅锁增） |
| 再抢锁 | `1`（不变） | `4` | 权限变更（仅锁增） |
| 二次保存 | `2` | `5` | 保存（双轨同增） |


---

## 六、开发注意事项（给 Codex 的核心要点）
1. **锁表是独立的“门禁系统”**：不要将锁版本与业务版本绑定理解，它们是两条独立的轨道。
2. **锁版本递增的触发条件**：
    - 新建任务（初始化锁）
    - 抢锁/强制解锁
    - 释放锁
    - 保存任务（数据落盘）
    - 任何其他涉及锁状态变更的操作
3. **业务版本递增的唯一条件**：仅在 `addOrUpdateTask` 成功保存时 +1。
4. **前端传参原则**：
    - 从 GET 响应中读取 `version` 和 `readWriteLockVO.version` 并缓存。
    - 在下次任何需要锁的操作中，将缓存的锁版本平铺到请求体的 `lockVersion` 字段。
    - 永远不要在前端自行计算任何版本号的 +1。
5. **Base64 编解码**：
    - 响应中的 `sqlText` 是 Base64 编码，前端解码后展示/编辑。
    - 请求体中的 `sqlText` 必须 Base64 编码后提交，服务端解码后存储明文。
6. **并发冲突校验**：后端收到请求后，必须比对请求中的 `lockVersion` 与 DB 锁表的 `version`，若不相等，抛出 409 并发冲突异常。

---

## 七、数据一致性保证
| 场景 | 服务端校验逻辑 | 异常处理 |
| :--- | :--- | :--- |
| 用户 A 持 `lockVersion=2` 保存 | DB锁表当前 `version=2` ✅ | 通过，保存后锁表变 `3` |
| 用户 B 抢锁后持 `lockVersion=3` 保存 | DB锁表当前 `version=3` ✅ | 通过，保存后锁表变 `4` |
| 用户 A 继续用旧 `lockVersion=2` 保存 | DB锁表当前已是 `3`或更高 ❌ | 抛出异常：任务已被他人修改，请刷新重试 |


# 新增一个不存在的文件


```shell
curl 'http://192.168.35.119/api/rdos/batch/batchTask/addOrUpdateTask' \
  -H 'Accept: */*' \
  -H 'Accept-Language: zh-CN,zh;q=0.9,en;q=0.8' \
  -H 'Cache-Control: no-cache' \
  -H 'Connection: keep-alive' \
  -H 'Content-Type: text/plain;charset=UTF-8' \
  -b 'dt_expire_cycle=0; dt_user_id=1; dt_username=admin%40dtstack.com; dt_can_redirect=true; dt_token=eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ0ZW5hbnRfaWQiOiIxIiwidXNlcl9pZCI6IjEiLCJ1c2VyX25hbWUiOiJhZG1pbkBkdHN0YWNrLmNvbSIsImV4cCI6MTc4MzIzMzk0OCwiaWF0IjoxNzgyOTc0NzQ4fQ.rKE2JepamoxeJu47zUZK05D0pd0RSZzLtKecwzKOnXk; sysLoginType=%7B%22sysId%22%3A1%2C%22sysName%22%3A%22UIC%E8%B4%A6%E5%8F%B7%E7%99%BB%E5%BD%95%22%2C%22sysType%22%3A0%7D; dt_tenant_id=1; dt_tenant_name=DT_demo; dt_is_tenant_admin=true; dt_is_tenant_creator=true; dt_cookie_time=2026-07-05+14%3A45%3A48; DT_SESSION_ID=e5e692a5-17af-43fe-a35d-846866e29073; track_rdos=true' \
  -H 'If-None-Match: 6.2.52-batch1762156828737' \
  -H 'Origin: http://192.168.35.119' \
  -H 'Pragma: no-cache' \
  -H 'Referer: http://192.168.35.119/batch/' \
  -H 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36' \
  -H 'X-Project-ID: 39' \
  --data-raw '{"name":"spark_job_1","taskType":0,"useOther":3,"nodePid":1979,"computeType":1,"componentVersion":"3.2","lockVersion":0,"version":0,"taskGroup":0}' \
  --insecure
```

```json
{
    "code": 1,
    "message": null,
    "data": {
        "isSupport": true,
        "isSubTask": 0,
        "type": "file",
        "version": 0,
        "parentId": 1979,
        "chargeFunctionType": 6,
        "isChangeComponent": false,
        "catalogueType": "TaskDevelop",
        "operateModel": 1,
        "taskType": 0,
        "name": "spark_job_1",
        "scheduleStatus": 1,
        "createUser": "admin@dtstack.com",
        "readWriteLockVO": {
            "result": 0,
            "gmtModified": 1782974876444,
            "getLock": true,
            "isDeleted": 0,
            "lastKeepLockUserName": "admin@dtstack.com",
            "modifyUserId": 1,
            "relationId": 477,
            "id": 465,
            "type": "BATCH_TASK",
            "lockName": "477_39_BATCH_TASK",
            "version": 1
        },
        "id": 477,
        "submitStatus": 0
    },
    "space": 101,
    "version": "36aa8eca5c1170a21aa46a5f7778053055339758",
    "success": true
}
```

在web页面，创建完成后，会自动调用通过任务id获取任务具体信息的方法，这些信息有很多是直接复用，传递给修改任务的接口的

```shell
curl 'http://192.168.35.119/api/rdos/batch/batchTask/getTaskById' \
  -H 'Accept: */*' \
  -H 'Accept-Language: zh-CN,zh;q=0.9,en;q=0.8' \
  -H 'Cache-Control: no-cache' \
  -H 'Connection: keep-alive' \
  -H 'Content-Type: text/plain;charset=UTF-8' \
  -b 'dt_expire_cycle=0; dt_user_id=1; dt_username=admin%40dtstack.com; dt_can_redirect=true; dt_token=eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ0ZW5hbnRfaWQiOiIxIiwidXNlcl9pZCI6IjEiLCJ1c2VyX25hbWUiOiJhZG1pbkBkdHN0YWNrLmNvbSIsImV4cCI6MTc4MzIzMzk0OCwiaWF0IjoxNzgyOTc0NzQ4fQ.rKE2JepamoxeJu47zUZK05D0pd0RSZzLtKecwzKOnXk; sysLoginType=%7B%22sysId%22%3A1%2C%22sysName%22%3A%22UIC%E8%B4%A6%E5%8F%B7%E7%99%BB%E5%BD%95%22%2C%22sysType%22%3A0%7D; dt_tenant_id=1; dt_tenant_name=DT_demo; dt_is_tenant_admin=true; dt_is_tenant_creator=true; dt_cookie_time=2026-07-05+14%3A45%3A48; DT_SESSION_ID=e5e692a5-17af-43fe-a35d-846866e29073; track_rdos=true' \
  -H 'If-None-Match: 6.2.52-batch1762156828737' \
  -H 'Origin: http://192.168.35.119' \
  -H 'Pragma: no-cache' \
  -H 'Referer: http://192.168.35.119/batch/' \
  -H 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36' \
  -H 'X-Project-ID: 39' \
  --data-raw '{"id":477}' \
  --insecure
```

```shell
{
    "code": 1,
    "message": null,
    "data": {
        "pythonVersion": 0,
        "createUserId": 1,
        "dtuicTenantId": 0,
        "keyForAutoSave": "88a482997ad3866f868622619b589d1f",
        "modifyUserId": 1,
        "taskGroup": 0,
        "modifyUser": {
            "phoneNumber": "18825166170",
            "id": 1,
            "userName": "admin@dtstack.com",
            "dtuicUserId": 1,
            "email": "admin@dtstack.com",
            "status": 1
        },
        "ownerUser": {
            "phoneNumber": "18825166170",
            "id": 1,
            "userName": "admin@dtstack.com",
            "dtuicUserId": 1,
            "email": "admin@dtstack.com",
            "status": 1
        },
        "taskPeriodId": 2,
        "createModel": 0,
        "id": 477,
        "cron": "0 0 0 * * ?",
        "componentVersion": "3.2",
        "forceUpdate": false,
        "currentProject": false,
        "priority": 1,
        "version": 0,
        "nodePid": 1979,
        "taskDesc": "",
        "syncModel": 0,
        "multiEngineType": 1,
        "refResourceList": [],
        "name": "spark_job_1",
        "projectName": "jnby_indep",
        "projectId": 39,
        "gmtModified": 1782974876000,
        "sqlText": "-- name spark_job_1\n-- type Spark SQL\n-- author admin@dtstack.com\n-- create time 2026-07-02 14:47:56\n-- desc \n",
        "computeType": 1,
        "taskPeriodType": "天任务",
        "mainClass": "",
        "exeArgs": "",
        "tagIds": [],
        "engineType": 1,
        "learningType": 0,
        "taskParams": "## Driver程序使用的CPU核数,默认为1\n# spark.driver.cores=1\n\n## Driver程序使用内存大小,默认1g\n# spark.driver.memory=1g\n\n## 对Spark每个action结果集大小的限制，最少是1M，若设为0则不限制大小。\n## 若Job结果超过限制则会异常退出，若结果集限制过大也可能造成OOM问题，默认1g\n# spark.driver.maxResultSize=1g\n\n## 启动的executor的数量，默认为1\n# spark.executor.instances=1\n\n## 每个executor使用的CPU核数，默认为1\n# spark.executor.cores=1\n\n## 每个executor内存大小,默认1g\n# spark.executor.memory=1g\n\n## 任务优先级, 值越小，优先级越高，范围:1-1000\n\n\n## spark 日志级别可选ALL, DEBUG, ERROR, FATAL, INFO, OFF, TRACE, WARN\n# logLevel = INFO\n\n## spark中所有网络交互的最大超时时间\n# spark.network.timeout=120s\n\n## executor的OffHeap内存，和spark.executor.memory配置使用\n# spark.yarn.executor.memoryOverhead=\n\n## 设置spark sql shuffle分区数，默认200\n# spark.sql.shuffle.partitions=200\n\n## 开启spark推测行为，默认false\n# spark.speculation=false",
        "eMPYT": "",
        "taskType": 0,
        "isDeleted": 0,
        "engineSchema": 1,
        "taskVariables": [],
        "scheduleStatus": 1,
        "yarnResourceId": 3,
        "flowId": 0,
        "submitStatus": 0,
        "resourceList": [],
        "nodePName": "test111",
        "scheduleConf": "{\"selfReliance\":0, \"min\":0,\"hour\":0,\"periodType\":\"2\",\"beginDate\":\"2001-01-01\",\"endDate\":\"2121-01-01\",\"isFailRetry\":true,\"maxRetryNum\":\"3\"}",
        "gmtCreate": 1782974876000,
        "userId": 1,
        "operateModel": 0,
        "useOther": 3,
        "ownerUserId": 1,
        "tenantId": 1,
        "createUser": {
            "phoneNumber": "18825166170",
            "id": 1,
            "userName": "admin@dtstack.com",
            "dtuicUserId": 1,
            "email": "admin@dtstack.com",
            "status": 1
        },
        "readWriteLockVO": {
            "gmtModified": 1782974876000,
            "getLock": true,
            "lastKeepLockUserName": "admin@dtstack.com",
            "modifyUserId": 1,
            "relationId": 477,
            "gmtCreate": 1782974876000,
            "type": "BATCH_TASK",
            "version": 1,
            "result": 0,
            "isDeleted": 0,
            "id": 465,
            "lockName": "477_39_BATCH_TASK",
            "projectId": 39
        }
    },
    "space": 60,
    "version": "36aa8eca5c1170a21aa46a5f7778053055339758",
    "success": true
}
```

# 修改当前已存在的文件
```shell
curl 'http://192.168.35.119/api/rdos/batch/batchTask/addOrUpdateTaskEncryption' \
  -H 'Accept: */*' \
  -H 'Accept-Language: zh-CN,zh;q=0.9,en;q=0.8' \
  -H 'Cache-Control: no-cache' \
  -H 'Connection: keep-alive' \
  -H 'Content-Type: text/plain;charset=UTF-8' \
  -b 'dt_expire_cycle=0; dt_user_id=1; dt_username=admin%40dtstack.com; dt_can_redirect=true; dt_token=eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ0ZW5hbnRfaWQiOiIxIiwidXNlcl9pZCI6IjEiLCJ1c2VyX25hbWUiOiJhZG1pbkBkdHN0YWNrLmNvbSIsImV4cCI6MTc4MzIzMzk0OCwiaWF0IjoxNzgyOTc0NzQ4fQ.rKE2JepamoxeJu47zUZK05D0pd0RSZzLtKecwzKOnXk; sysLoginType=%7B%22sysId%22%3A1%2C%22sysName%22%3A%22UIC%E8%B4%A6%E5%8F%B7%E7%99%BB%E5%BD%95%22%2C%22sysType%22%3A0%7D; dt_tenant_id=1; dt_tenant_name=DT_demo; dt_is_tenant_admin=true; dt_is_tenant_creator=true; dt_cookie_time=2026-07-05+14%3A45%3A48; track_rdos=true' \
  -H 'If-None-Match: 6.2.52-batch1762156828737' \
  -H 'Origin: http://192.168.35.119' \
  -H 'Pragma: no-cache' \
  -H 'Referer: http://192.168.35.119/batch/' \
  -H 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36' \
  -H 'X-Project-ID: 39' \
  --data-raw '{"pythonVersion":0,"createUserId":1,"dtuicTenantId":0,"keyForAutoSave":"b23641c4bea46a04efdf818552ec75ef","modifyUserId":1,"taskGroup":0,"modifyUser":{"phoneNumber":"18825166170","id":1,"userName":"admin@dtstack.com","dtuicUserId":1,"email":"admin@dtstack.com","status":1},"ownerUser":{"phoneNumber":"18825166170","id":1,"userName":"admin@dtstack.com","dtuicUserId":1,"email":"admin@dtstack.com","status":1},"taskPeriodId":2,"createModel":0,"id":481,"cron":"0 0 0 * * ?","alreadyKnow":0,"operateType":2,"componentVersion":"3.2","forceUpdate":false,"currentProject":false,"priority":1,"autoLogoutTime":1782978872000,"version":27,"nodePid":1979,"taskDesc":"","syncModel":0,"multiEngineType":1,"refResourceList":[],"name":"spark_job_3","projectName":"jnby_indep","projectId":39,"gmtModified":1782978872000,"sqlText":"c2VsZWN0ICogZnJvbSAyMjI=","computeType":1,"taskPeriodType":"天任务","mainClass":"","exeArgs":"","tagIds":[],"engineType":1,"learningType":0,"taskVOS":null,"taskParams":"## Driver程序使用的CPU核数,默认为1\n# spark.driver.cores=1\n\n## Driver程序使用内存大小,默认1g\n# spark.driver.memory=1g\n\n## 对Spark每个action结果集大小的限制，最少是1M，若设为0则不限制大小。\n## 若Job结果超过限制则会异常退出，若结果集限制过大也可能造成OOM问题，默认1g\n# spark.driver.maxResultSize=1g\n\n## 启动的executor的数量，默认为1\n# spark.executor.instances=1\n\n## 每个executor使用的CPU核数，默认为1\n# spark.executor.cores=1\n\n## 每个executor内存大小,默认1g\n# spark.executor.memory=1g\n\n## 任务优先级, 值越小，优先级越高，范围:1-1000\n\n\n## spark 日志级别可选ALL, DEBUG, ERROR, FATAL, INFO, OFF, TRACE, WARN\n# logLevel = INFO\n\n## spark中所有网络交互的最大超时时间\n# spark.network.timeout=120s\n\n## executor的OffHeap内存，和spark.executor.memory配置使用\n# spark.yarn.executor.memoryOverhead=\n\n## 设置spark sql shuffle分区数，默认200\n# spark.sql.shuffle.partitions=200\n\n## 开启spark推测行为，默认false\n# spark.speculation=false","eMPYT":"","taskType":0,"isDeleted":0,"engineSchema":1,"taskVariables":[],"scheduleStatus":1,"yarnResourceId":3,"flowId":0,"submitStatus":0,"resourceList":[],"nodePName":"test111","scheduleConf":"{\"selfReliance\":0, \"min\":0,\"hour\":0,\"periodType\":\"2\",\"beginDate\":\"2001-01-01\",\"endDate\":\"2121-01-01\",\"isFailRetry\":true,\"maxRetryNum\":\"3\"}","gmtCreate":1782976492000,"userId":1,"dependOnSettings":0,"operateModel":0,"useOther":3,"ownerUserId":1,"tenantId":1,"createUser":{"phoneNumber":"18825166170","id":1,"userName":"admin@dtstack.com","dtuicUserId":1,"email":"admin@dtstack.com","status":1},"readWriteLockVO":{"gmtModified":1782978872000,"getLock":true,"lastKeepLockUserName":"admin@dtstack.com","modifyUserId":1,"relationId":481,"gmtCreate":1782976492000,"type":"BATCH_TASK","version":31,"result":0,"isDeleted":0,"id":469,"lockName":"481_39_BATCH_TASK","projectId":39},"queryLimit":1000,"reload":false,"loading":false,"sideBenchKey":"params1","merged":false,"notSynced":true,"preSave":true,"dependencyTasks":[{"pythonVersion":0,"createUserId":1,"gmtModified":1782892493000,"sqlText":"","computeType":1,"mainClass":"","dtuicTenantId":1,"exeArgs":"","modifyUserId":1,"engineType":1,"learningType":0,"taskParams":"","taskType":-1,"isDeleted":0,"tenantName":"DT_demo","appType":1,"createModel":0,"scheduleStatus":1,"id":467,"isPublishToProduce":0,"flowId":0,"submitStatus":1,"projectAlias":"jnby_v1","scheduleConf":"{\"isFailRetry\":true,\"beginDate\":\"2001-01-01\",\"min\":0,\"periodType\":\"2\",\"hour\":0,\"selfReliance\":0,\"endDate\":\"2121-01-01\",\"maxRetryNum\":\"3\"}","forceUpdate":false,"currentProject":false,"gmtCreate":1782892477000,"nodePid":1933,"taskDesc":"","upDownRelyType":0,"operateModel":0,"versionId":727,"periodType":2,"syncModel":0,"projectScheduleStatus":0,"name":"jnby_start","ownerUserId":1,"tenantId":1,"projectName":"jnby","projectId":43,"taskId":467}],"lockVersion":31}' \
  --insecure
```

```json

```

