# AgentDeck Phase 5：Web Push 与桌面通知中心实施计划（第七轮架构定稿版）

> **文档状态**：第六轮修订（修复 4 项 P1 + 1 项迁移预检 + 2 项 P2 落实），仍处于计划编制阶段，严禁编写业务代码、ORM 模型、Alembic 迁移脚本、前端代码，严禁修改数据库、执行迁移。  
> **基线对齐**：AgentDeck v1.3.0、Clean-Slate 21 张规范表、Phase 1–4 已验证基座。  
> **本轮核查依据（只读）**：`backend/migrations/env.py`、`backend/migrations/versions/20261031_0001_phase1_core_foundation.py`、`backend/db/migration.py`、`backend/db/uow.py`、`backend/db/engine.py`、`backend/db/models/system.py`。

---

## 零、第七轮修订说明（在第六轮基础上，仅小范围补充）

| 项 | 修订 |
| :--- | :--- |
| 1 | 2.3 修正 VAPID 全局异常判定矛盾：不再以“任一订阅 401/403”熔断，改为独立订阅数+成功率或签名自检失败 |
| 2 | 新增 2.6：Dispatcher 派生写入与 processed 同事务；Delivery Worker 完整状态机、重试与 Fencing |
| 3 | 新增 2.7：SSRF 完整约束与设备身份/订阅归属规范（5.4 实施） |
| 4 | 第三章补齐 5 张新表完整数据字典；新增聚合部分唯一索引 `uq_notifications_open_agg` |
| 5 | 测试 8 明确为 env.py 外部连接分支覆盖，不替代生产路径；测试扩至 37 项 |

### 第八轮修订（仅 4 项）
1. 404 统计范围明确为同一 delivery 的连续 HTTP 尝试（2.3、2.6、3.4）；2. 项目删除统一为 CASCADE，新增 `purged` 变更类型（3.2、3.5）；3. `link_url` CHECK 加强（3.2）；4. 测试 33、36 并新增 38、39 纳入 5.2 验收，均要求真实 SQLite 文件。

### 第六轮修订说明（保留）

| 编号 | 审阅问题 | 修订位置 | 结论摘要 |
| :--- | :--- | :--- | :--- |
| P1-1 | 事务原子性与“通知异常不回滚业务”表述自相矛盾 | 1.2、2.0、2.1 | 拆分为“提交前原子一致”与“提交后彻底解耦”两个阶段，明确 Outbox 写失败则整个业务事务失败 |
| P1-2 | Alembic 自动开关 SQLite 外键的说法不成立 | 2.4 | 删除该说法；以“迁移顺序规避 + 事务外连接配置 + 备份回滚”替代；列出必须保留的约束与索引 |
| P1-3 | 401/403 被误判为订阅失效 | 2.3 | 按状态码分级；区分全局 VAPID 异常与单订阅异常；引入按指纹熔断 |
| P1-4 | 换绑失败时“保留旧记录”≠保留旧订阅 | 2.2 | 增加持久化恢复记录、旧公钥重订阅、可恢复登记、启动对账 |
| 迁移预检 | 外键目标与孤儿数据检查 | 2.4.2 | 增加 4 项预检，异常即中止并报告，不静默修改 |
| P2-1 | 设备外键数据检查 | 2.4.2 | 并入预检 |
| P2-2 | `BEGIN IMMEDIATE` 必须是事务首条语句 | 2.5 | 明确连接/事务模式、BUSY 处理、多真实连接测试 |

---

## 一、阶段目标与架构总览

### 1.1 业务目标与产品决策（用户已确认）
- **单用户全局通知模型**：所有认证设备共享同一通知中心；各设备独立 Web Push 订阅与投递状态；任一设备已读，全端同步；当前不引入多用户/角色/多租户隔离。

### 1.2 七大架构铁律
1. **事务边界两阶段原则（修正 P1-1）**
   - **阶段一：业务事务提交前（原子一致）**：核心业务实体、`events`、`notification_outbox` 必须在**同一个数据库事务**内提交。三者要么一起成功，要么一起失败。**Outbox 写入失败 ⇒ 整个业务事务失败回滚**，系统不允许出现“业务成功但通知事件丢失”。这是 Transactional Outbox 的固有特性，不是缺陷。
   - **阶段二：业务事务提交后（彻底解耦）**：Outbox 一旦提交，后续的通知聚合、`notifications` 写入、Push 外呼、SSE 派生处理发生任何失败，**均不得回滚、修改或阻塞已提交的核心业务事务**；失败仅通过 Outbox 重试、`failed` 终态、`last_error` 记录与告警恢复。
   - 因此 Outbox 写入路径必须保持极简（单表单行插入、固定白名单事件类型、无聚合/无查询逻辑），以将“Outbox 自身故障拖垮业务写入”的概率降到最低。
2. **多端独立订阅，共享全局已读**。
3. **通知合并不删除业务事实**：底层 `events` 100% 保留。
4. **通知不等于免密审批**：Push 仅含安全摘要与深链；拍板必须经 `POST /api/v1/decisions/{id}/resolve`，沿用 CAS 与幂等。
5. **离线可恢复与 At-least-once**：投递持久化；Push `tag = notif-{notification_id}` 覆盖重复弹窗。
6. **全链路严格鉴权，废除 Localhost 免密特权**（通知/订阅/拍板接口必须持合法设备 Token；现有 `verify_http_client` 对回环放行的行为需在 5.4 中针对这些接口专项收紧）。
7. **全库表总数 21 → 26**：新增 5 张表；新增表与新增字段 100% 中文注释登记。

---

## 二、核心机制设计

```
 业务事务（单一 SqlAlchemyUnitOfWork 事务，提交前原子一致）
   业务实体 + events + notification_outbox  ── 任一失败 → 全部回滚
 ──────────────────────── 事务提交点 ────────────────────────
 提交后（与业务彻底解耦，失败只影响 Outbox 重试）
   NotificationDispatcher：认领 outbox → 聚合/分流 → notifications / notification_events
                           → notification_deliveries → notification_changes → outbox=processed
   NotificationDeliveryWorker：认领 delivery → Web Push 外呼 → 状态回写
   SSE：notification_changes 持久流（RingBuffer 仅缓存）
```

### 2.0 事务边界落地约定（对应 P1-1）
- 现有 `SqlAlchemyUnitOfWork.commit()` 先 `flush` 业务实体，再 `add_all` 并 `flush` 待写事件，最后 `commit`。Outbox 需要 `events.seq_id`，因此**在事件 flush 之后、`session.commit()` 之前**，由同一 Session 为白名单事件类型追加 `notification_outbox` 行。该改动属于 Phase 5.3 范围，5.2 不触碰 `uow.py`。
- 白名单事件（初版）：`decision.created`、`task.blocked`/任务终态类、`execution.failed`、系统严重异常类；其余事件不入 Outbox，避免无谓写放大。
- 提交前失败（含 Outbox 唯一键冲突、磁盘错误）：沿用 UoW 现有回滚与错误日志汇聚到 `logs/error.log`，业务请求返回失败，调用方可按原幂等机制重试。
- 提交后失败：由 Dispatcher 捕获，按 2.1 状态机处理，**绝不向业务调用链回传异常**。

### 2.1 Outbox 完整可靠状态机

**状态**：`pending`（初始）→ `processing`（已认领，持租约）→ `processed`（成功终态）；失败：`retry`（待退避重试）→ 超限 `failed`（终态）。

**初始化规则**（与业务事务同写）：`status='pending'`、`attempt_count=0`、`max_attempts=5`、`next_retry_at = utc_now_iso()`、`locked_by/locked_at/lease_expires_at/failed_at = NULL`。

**每轮调度先做熔断清理**（保证不会永久卡在 processing/retry）：
```sql
-- processing 租约超时且已达上限 → failed
UPDATE notification_outbox
SET status='failed', locked_by=NULL, locked_at=NULL, lease_expires_at=NULL,
    failed_at=:now, updated_at=:now,
    last_error=coalesce(last_error,'lease expired and max attempts reached')
WHERE status='processing' AND lease_expires_at < :now AND attempt_count >= max_attempts;

-- retry 且已达上限 → failed
UPDATE notification_outbox
SET status='failed', locked_by=NULL, locked_at=NULL, lease_expires_at=NULL,
    failed_at=:now, updated_at=:now,
    last_error=coalesce(last_error,'max attempts reached')
WHERE status='retry' AND attempt_count >= max_attempts;
```

**原子认领（常规认领 + 租约超时接管，两路条件）**：
```sql
UPDATE notification_outbox
SET status='processing', locked_by=:disp, locked_at=:now,
    lease_expires_at=:now_plus_60s, attempt_count=attempt_count+1, updated_at=:now
WHERE id=:id AND attempt_count < max_attempts
  AND ( (status IN ('pending','retry') AND next_retry_at <= :now)
     OR (status='processing' AND lease_expires_at < :now) );
```
受影响行数为 1 才算认领成功。

**异常回写**（带归属校验 `locked_by=:disp AND status='processing'`）：
- `attempt_count < max_attempts`：`status='retry'`，`next_retry_at = now + 2*4^(attempt_count-1)` 秒，清锁，写 `last_error`；
- `attempt_count >= max_attempts`：`status='failed'`，写 `failed_at`、`last_error`，清锁，触发告警日志。

**成功回写（Fencing）**：`UPDATE ... SET status='processed', processed_at=:now, 清锁 WHERE id=:id AND locked_by=:disp AND status='processing'`；受影响行数为 0 ⇒ 租约已被接管 ⇒ **回滚本次派生事务**，丢弃所有派生写入。

### 2.2 VAPID 密钥轮换：方案 A（新旧密钥并存）与客户端换绑恢复（修正 P1-4）

**技术前提（不可绕开）**：同一 Service Worker Registration 同一时刻仅有一个 PushSubscription；已有订阅时以不同 `applicationServerKey` 调用 `subscribe()` 会抛 `InvalidStateError`。因此换绑必须经历“浏览器端先 `unsubscribe()`”这一不可逆步骤；`unsubscribe()` 成功后旧浏览器订阅即失效，服务端保留旧记录**并不代表**旧订阅仍可用。计划据此采用“**服务端并存 + 客户端可恢复事务**”。

**服务端多代密钥库** `data/vapid_key_store.json`：以公钥 SHA-256 指纹为键，记录 `public_key_b64`、`private_key_pem`、`status(active/retired)`、`created_at`、`retired_at`、`retain_until`。写入采用临时文件 + fsync + `os.replace` 原子替换；私钥不出日志与 API。旧密钥**默认不主动轮换**，仅在运维明确执行轮换时才产生新 active 密钥。

**投递寻钥**：按订阅 `vapid_key_fingerprint` 精确选择私钥签名；存量订阅在换绑前持续用对应旧私钥投递，无需强制迁移。

**旧私钥保留与淘汰条件（二者同时满足才物理删除）**：
1. 已超过 `retain_until`（退役后至少 90 天）；
2. `SELECT count(*) FROM push_subscriptions WHERE vapid_key_fingerprint=:fp AND is_active=1` 为 0。
若满足 1 但仍有活跃订阅：不删除，仅告警，并向对应设备下发 `resync_subscription`；**换绑期间（存在未完成的恢复记录）私钥一律不得淘汰**。
公钥可通过 `GET /api/v1/push/vapid-public-key?fingerprint=<fp>` 查询（active 与 retired 均返回**公钥**，从不返回私钥），供客户端恢复旧订阅使用。

**客户端换绑状态机（仅在用户主动操作或启动对账发现不一致时执行；状态持久化于 IndexedDB，而非仅 localStorage）**

恢复记录 `rebind_record = {state, old_fingerprint, old_public_key_b64, old_endpoint, new_fingerprint, new_endpoint?, attempt, updated_at}`。

| 步骤 | 动作 | 失败处理 |
| :--- | :--- | :--- |
| 0 | 读取 `oldSub.options.applicationServerKey`，计算 `old_fingerprint`，写入恢复记录 `state=started`；向服务端确认旧指纹对应私钥仍在库（`retired` 且未淘汰） | 服务端无该私钥 ⇒ 中止换绑，保持现状并提示 |
| 1 | `oldSub.unsubscribe()`，`state=old_unsubscribed` | 失败 ⇒ 保持旧订阅，结束 |
| 2 | 用新公钥 `subscribe()`，`state=new_subscribed`，记录 `new_endpoint` | **失败 ⇒ 进入恢复分支 R** |
| 3 | `POST /api/v1/push/subscribe`（携带 `replaced_endpoint`、幂等键、新指纹），带指数退避重试 | 失败 ⇒ 保持 `new_subscribed`，**由启动对账/定时重试继续重新登记**（订阅在浏览器侧已有效，只缺登记） |
| 4 | 服务端在**同一事务**内：校验 `replaced_endpoint` 属于同一 `device_id` → 写入新订阅（含有效指纹）→ 将旧记录置 `is_active=0`；返回确认 | 校验失败 ⇒ 仅登记新订阅，不动旧记录 |
| 5 | 客户端收到确认后 `state=done`，清除恢复记录 | — |

**恢复分支 R（新订阅失败）**：
1. 用 `old_public_key_b64`（必要时向服务端按指纹拉取旧公钥）调用 `subscribe()` 重新建立旧代订阅（endpoint 通常会变化）；
2. 成功 ⇒ 以 `old_fingerprint` 向服务端登记（带 `replaced_endpoint`，服务端用保留的旧私钥继续投递）⇒ 旧记录置失效、新登记生效；
3. 失败 ⇒ `state=recovery_failed`，界面明确提示“通知功能暂时不可用，请点击重试”；**不得**把服务端遗留的旧记录描述为可正常投递；该旧记录保持审计保留，待投递获得 404/410 时按 2.3 规则确认失效；
4. 只有“新订阅登记成功（步骤 4）”或“旧代订阅恢复登记成功”后，才允许停用旧订阅记录。

**启动对账（reconcile）**：每次应用启动/回到前台时，对比 `getSubscription()` 的 endpoint 与服务端 `GET /api/v1/push/subscriptions/me` 的活跃集合：浏览器有而服务端无 ⇒ 重新登记（幂等，`subscription_id=sha256(endpoint)`）；存在未完成恢复记录 ⇒ 继续执行对应步骤。

### 2.3 投递结果分级处理与历史指纹策略（修正 P1-3）

**历史指纹**：存量订阅统一为 `legacy_unverified`；首次投递以**当前 active 密钥**签名尝试。

| 网关响应 | 处理 |
| :--- | :--- |
| 201 | 成功；若指纹为 `legacy_unverified`，更新为本次所用密钥指纹 |
| 401 | 认证异常：记录、告警，delivery 进入 `retry`；**不得停用订阅** |
| 403 | VAPID 签名/密钥不匹配：记录、告警，delivery 进入 `retry`（超限 `failed`）；**不得停用订阅**，不批量停用 |
| 404 | 按 Push Service 语义视为订阅疑似失效：**按同一条 delivery 的连续 HTTP 投递尝试统计**（不跨通知）：第一次 404 记录 `last_http_status=404` 并 `retry`；该 delivery 的下一次独立 HTTP 请求仍为 404 ⇒ `gone` 并停用订阅；中间出现其他状态码则重置判断 |
| 410 | 订阅明确过期（RFC 8030）：停用 `is_active=0`，delivery 记 `gone`/终态 |
| 429 | 遵循 `Retry-After`，否则指数退避重试 |
| 5xx / 网络错误 / 超时 | 暂时性错误，指数退避重试，超限 `failed`；不影响订阅状态 |

**全局异常 vs 单订阅异常（第七轮修正，消除规则矛盾）**：对每个 `vapid_key_fingerprint` 维护 5 分钟滑动窗口，统计出现 401/403 的**不同订阅数**与该指纹下的成功投递数。判定规则互斥且唯一：

| 条件 | 判定 | 处理 |
| :--- | :--- | :--- |
| 窗口内同一指纹下 ≥3 个**独立订阅（不同 `device_id`）**出现 401/403，且同窗口内该指纹成功投递占比 <50% | **全局 VAPID 异常** | 暂停该指纹外呼（delivery 保持 `retry`，`next_retry_at` 顺延），写 error 日志并告警；**不改动任何订阅 `is_active`** |
| 独立的 VAPID 签名自检失败（用密钥库私钥对测试 JWT 签名/验签失败，或公私钥不匹配） | **全局 VAPID 异常** | 同上，并标记该指纹为 `degraded` 直到自检通过 |
| 仅单个订阅 401/403，同指纹其他订阅正常投递 | **单订阅异常** | 保留订阅，记录告警，delivery 按普通重试规则处理，向该设备下发 `resync_subscription` |

**明确禁止**把“任一已验证订阅出现 401/403”作为全局熔断条件（示例：同一指纹 10 台设备仅 1 台 401、9 台成功 ⇒ 只按单订阅处理）。无论全局还是单订阅异常，401/403 **都不得**触发订阅停用。
`legacy_unverified` 订阅持续 401/403 超过阈值：保持 `is_active=1`、指纹保持 `legacy_unverified`，由 UI 提示用户重新订阅；**不自动认定为当前密钥，也不自动停用**。

**新订阅写入规则**：`POST /api/v1/push/subscribe` 必须写入 64 位十六进制有效指纹，**禁止写入空字符串**，也禁止新建时写入 `legacy_unverified`（后者仅限迁移回填）。

### 2.4 SQLite Alembic 迁移方案（修正 P1-2，含预检）

#### 2.4.1 真实环境事实（只读核查结论）
- `env.py`：`render_as_batch=True`；在线迁移使用独立 `create_engine(NullPool)`，**在 `connect` 事件中**执行 `PRAGMA foreign_keys=ON`（位于任何事务之前），随后 `context.begin_transaction()` 包裹迁移。
- 基线迁移中 `push_subscriptions`：主键 `pk_push_subscriptions(subscription_id)`；索引 `ix_push_subscriptions_device_active(device_id,is_active)`；**无外键、无 CHECK、无其他唯一约束**；`is_active` 带 `server_default='1'`。`device_sessions.device_id` 在 ORM 与基线中为主键（迁移实施前仍须以预检 SQL 实证）。
- 当前**没有任何表引用 `push_subscriptions`**（新表 `notification_deliveries` 尚未创建）。
- `backend/db/migration.py` 的 `apply_database_migrations` 已在迁移前备份数据库文件（`.bak.<时间戳>`）。
- SQLite 事实：`PRAGMA foreign_keys` 在事务内修改**无效**；pysqlite 对 DDL 的事务行为不保证原子回滚；Alembic 对 SQLite 默认 `transactional_ddl=False`。**因此不得假设“迁移失败自动回滚”。**

#### 2.4.2 迁移前数据预检（任一失败 ⇒ 中止迁移并输出报告，绝不静默删除或修改历史数据）
1. `device_sessions.device_id` 是 PK 或具备 UNIQUE（`PRAGMA index_list/table_info` 校验）；
2. 孤儿订阅检查：
   ```sql
   SELECT ps.subscription_id, ps.device_id FROM push_subscriptions ps
   LEFT JOIN device_sessions ds ON ps.device_id = ds.device_id
   WHERE ds.device_id IS NULL;
   ```
3. 违反新唯一约束检查：`SELECT subscription_id, device_id, count(*) FROM push_subscriptions GROUP BY 1,2 HAVING count(*)>1;`（PK 保证为空，仍作为防御性断言）；
4. 当前无其他表通过外键引用 `push_subscriptions`：`PRAGMA foreign_key_list` 遍历全部表确认；若存在，中止并要求先设计专项方案；
5. `PRAGMA foreign_key_check` 迁移前基线为空（避免把既有问题误算到本次迁移）。
预检失败的处置：抛出带明细的错误，记录 `logs/error.log`，由人工决定清理方式（例如先让用户重新配对设备或手工核对孤儿订阅）。

#### 2.4.3 外键开关处理策略（不依赖 Alembic 自动切换）
- **策略：靠迁移顺序规避，而非关闭外键。** 在同一迁移（或拆分为两个相邻 revision，推荐拆分：`A` 重建 push_subscriptions、`B` 创建 5 张新表）中，**先**重建 `push_subscriptions`，**后**创建引用它的新表。重建时没有任何子表引用它，`DROP TABLE` 的隐式删除不会触发外键违规，因此在 `foreign_keys=ON` 下可安全执行，无需在事务内切换 PRAGMA。
- 外键开关由 `env.py` 的 `connect` 事件在**事务外**统一设为 `ON`，迁移期间保持不变；迁移结束后断言 `PRAGMA foreign_keys` 仍为 `1`（等价于“恢复”）。
- **若实施期实测发现无法在 ON 下安全重建**：退回替代方案——由迁移入口（`apply_database_migrations`）在**事务外**用独立连接执行 `PRAGMA foreign_keys=OFF` → 运行迁移 → `PRAGMA foreign_keys=ON` → `foreign_key_check`；此方案需先通过测试再采用，**不直接上线**。
- `downgrade` 同理必须**先删除 5 张新表（含 `notification_deliveries`），再重建 `push_subscriptions` 还原**，避免子表阻塞 `DROP`。

#### 2.4.4 `batch_alter_table` 重建内容与必须保留的约束/索引清单
重建后 `push_subscriptions` 必须包含（实施时以显式声明/测试断言，不假设自动恢复）：

| 类别 | 内容 |
| :--- | :--- |
| 列 | 原 9 列全部保留（类型、NOT NULL、`is_active` 默认值 1）+ 新增 `vapid_key_fingerprint VARCHAR(64) NOT NULL DEFAULT 'legacy_unverified'` |
| 主键 | `pk_push_subscriptions(subscription_id)`（命名保持） |
| 唯一约束 | `uq_push_subscriptions_sub_device(subscription_id, device_id)`（SQLite 复合外键的父键必须是 PK/UNIQUE，因此虽与 PK 逻辑冗余仍须存在） |
| 外键 | `fk_push_subscriptions_device_id_device_sessions(device_id → device_sessions.device_id) ON DELETE CASCADE` |
| 索引 | `ix_push_subscriptions_device_active(device_id,is_active)`（重建后断言存在；缺失则显式 `create_index`） |
| CHECK | 原表无 CHECK，仅断言“无新增、无遗漏” |
| 表/列中文注释 | SQLite 不存储注释；重建后须调用项目既有 `sync_comments_to_connection` 重新同步 `table_column_comments`，并同步更新 ORM 模型 |

实施要点：使用 `batch_alter_table(..., recreate="always")`；所有新约束显式命名（`naming_convention` 对未命名约束的复制存在已知缺口）；迁移内先反射重建前的列/索引/约束快照，重建后逐项比对。

#### 2.4.5 回填、校验与失败恢复
- 回填：新增列使用静态 `server_default='legacy_unverified'`，**迁移脚本不读取 `data/vapid_keys.json` 或任何部署机文件**，保证可重复、可测试、可脱机；绝不将当前公钥指纹批量回填给历史订阅。
- 迁移后校验：`PRAGMA foreign_key_check` 结果为空、`PRAGMA integrity_check` 为 `ok`、`PRAGMA foreign_keys` 为 `1`、行数与迁移前一致、逐行比对原 9 列内容、索引/约束清单比对通过。任一失败 ⇒ 抛错并终止启动。
- **失败恢复**：因 SQLite DDL 不保证事务原子，恢复依赖三层——①预检阻止高风险迁移；②`batch` 采用“建临时表 → 复制 → 删旧表 → 重命名”，复制阶段失败时旧表仍完整；③迁移前文件备份 `.bak.<时间戳>`，校验失败时由迁移入口给出明确的“使用备份恢复”指引（不自动覆盖生产库）。该恢复路径必须在真实 SQLite 文件上测试（见 4.2）。

### 2.5 通知聚合并发保护与 `BEGIN IMMEDIATE` 落地（P2-2，面向 5.3）
- 聚合查询与写入在同一写事务内；候选条件含 `is_read=0`、窗口 10 秒、`aggregated_count<20`；`notification_events.event_seq_id UNIQUE` 防重复关联。
- **实施约束**：`BEGIN IMMEDIATE` 必须是该事务的首条有效语句；使用**全新连接**并关闭 pysqlite/SQLAlchemy 的隐式 autobegin（如 `isolation_level=None` + 显式 `BEGIN IMMEDIATE`，或经 `connect`/`begin` 事件配置），避免“事务已开启”错误；捕获 `SQLITE_BUSY/database is locked`，配合 `busy_timeout` 与带抖动重试。
- 唯一性兜底：除串行化外，为未读聚合窗口增加可验证的唯一性手段（实施时采用 3.2 的部分唯一索引 `uq_notifications_open_agg`），并以并发测试证明不产生重复通知或错误计数。
- 验收测试必须使用**多个真实数据库连接/线程**，而非单连接协程模拟。

### 2.6 派生写入事务边界与 Delivery Worker 状态机（第七轮补充）

#### 2.6.1 Dispatcher 派生写入的同事务要求
`notifications` 的创建/聚合更新、`notification_events`、`notification_deliveries`、`notification_changes` 的派生写入，以及 `notification_outbox` 置为 `processed`，**必须在同一个数据库事务（`BEGIN IMMEDIATE`）内完成**；Fencing 校验（`locked_by=:disp AND status='processing'` 影响行数=1）失败则**整体回滚**。**Web Push 网络外呼严禁放入该事务**，由 Delivery Worker 独立执行。由此保证：Dispatcher 在写入 `processed` 前崩溃，则全部派生写入一并回滚，重试不会产生重复通知；`notification_events.event_seq_id UNIQUE` 为最终防重兜底。

#### 2.6.2 `notification_deliveries` 创建规则
Dispatcher 在同一事务内为**当时所有 `is_active=1` 的订阅**各创建一条 delivery：`status='pending'`、`attempt_count=0`、`max_attempts=4`、`next_retry_at=utc_now_iso()`、锁字段为 NULL。此后新建的订阅不补发历史通知（用户通过通知中心查看）。

#### 2.6.3 状态流转
| 场景 | 行为 |
| :--- | :--- |
| `pending`/`retry` 且 `next_retry_at<=now` | 原子认领：`status='processing'`、`locked_by`、`lease_expires_at=now+30s`、`attempt_count+1`（条件含 `attempt_count<max_attempts`） |
| `processing` 且租约过期、`attempt_count<max_attempts` | 其他 Worker 通过第二路条件接管（同样认领 SQL，`status='processing' AND lease_expires_at<now`） |
| `processing` 且租约过期、`attempt_count>=max_attempts` | 清理 SQL 置 `failed`，清锁，写 `failed_at/last_error`，保证不卡死 |
| 网关 201 | `delivered`，写 `delivered_at`，清锁，更新 `push_subscriptions.last_pushed_at`；`legacy_unverified` 订阅同时修正指纹（见 2.3） |
| 网关 410 | `gone`，写 `failed_at`，同一事务内将对应订阅 `is_active=0` |
| 网关 404 | **统计范围=同一条 delivery 的连续 HTTP 尝试，不跨通知**。回写前读取该 delivery 当前 `last_http_status`：若为 404（即上一次尝试也是 404）⇒ 视同 410：delivery 置 `gone`、订阅 `is_active=0`；否则写 `last_http_status=404` 并 `retry`。任何非 404 响应都会覆盖 `last_http_status`，自然重置连续计数。订阅停用与 delivery 回写在同一事务内，且仅在 Fencing 回写成功（影响行数=1）时提交 |
| 401 / 403 | `retry`（受 2.3 全局/单订阅规则约束），**不停用订阅**；达到上限 `failed` |
| 429 | `retry`，`next_retry_at` 取 `Retry-After` 与退避的较大值 |
| 5xx / 超时 / 网络错误 | `retry`，退避 `2*4^(attempt_count-1)` 秒（2s/8s/32s） |
| 达到 `max_attempts` 仍失败 | `failed`，清锁，记 `failed_at`、`last_error`，写 error 日志 |
| Worker 崩溃 | 租约过期后由接管路径或清理路径处理 |
| 旧 Worker 恢复 | 所有结果回写必须带 `WHERE id=:id AND locked_by=:worker AND status='processing'`；影响行数为 0 则丢弃结果，且订阅停用等副作用同样不得执行（与 delivery 回写同一事务，仅在回写成功时提交） |

外呼超时 10 秒，小于 30 秒租约；SQLite 写冲突按 2.5 的 BUSY 规则重试。

### 2.7 Web Push 安全规范与设备身份边界（面向 Phase 5.4，5.2 不实现）

#### 2.7.1 Push Endpoint SSRF 约束（订阅登记时与每次外呼前均校验）
1. 仅允许 `https`，仅允许端口 `443`（显式其他端口一律拒绝）；
2. 主机名严格匹配网关白名单：`*.push.apple.com`、`fcm.googleapis.com`、`*.fcm.googleapis.com`、`updates.push.services.mozilla.com`、`*.push.services.mozilla.com`、`*.notify.windows.com`（按完整标签后缀匹配，防 `evil-push.apple.com.attacker.com`、userinfo `@` 等绕过）；
3. 解析并检查该主机**全部 A 与 AAAA 记录**，任一不是全局单播公网地址即整体拒绝：拦截回环、RFC1918 私网、链路本地（含 `169.254.169.254` 云元数据）、CGNAT `100.64.0.0/10`、多播、保留、IPv6 ULA/链路本地、IPv4 映射 IPv6；
4. 外呼套接字直接连接**已校验通过的 IP**，禁止二次 DNS 解析（防 DNS Rebinding）；TLS SNI 与 HTTP `Host` 保留原始合法域名并校验证书；
5. 禁止跟随 HTTP 重定向，任何 3xx 视为失败并记录，防止经重定向绕过域名与 IP 校验；
6. 以上校验失败的订阅不入库（登记时）或本次 delivery 记 `failed` 并告警（外呼时），不得发起网络请求。

#### 2.7.2 设备身份与订阅归属
1. 5.4 新增依赖（如 `require_device_session`）从 `Authorization`/`X-AgentDeck-Token`/Cookie 的设备 Token 经 SHA-256 查 `device_sessions`（`is_revoked=0`）得到**可信 `device_id`**；`/api/v1/push/*`、`/api/v1/notifications/*` 与通知 SSE **必须使用该依赖**，不再套用对回环地址免鉴权的 `verify_http_client`；本机桌面端同样需配对获得设备 Token（`agentdeck_token` Cookie 已受支持）；
2. 请求体中的 `device_id` 一律忽略/拒绝，不得信任客户端自报；
3. `subscribe`：`subscription_id` 已存在且属于**其他设备** ⇒ 返回 409 拒绝，**不得像现有实现那样覆盖 `device_id`**；属于本设备则幂等更新；
4. `unsubscribe`、换绑 `replaced_endpoint`、更新、删除、测试推送：均先校验目标订阅 `device_id` 等于当前可信设备，否则 403；`push/test` 只能发给当前设备；
5. 订阅新建必须写入有效 `vapid_key_fingerprint`（见 2.3），且 endpoint 通过 2.7.1 校验。


---

## 三、最终数据模型与数据字典（全库 21 → 26 张；第七轮补齐，5.2 的 ORM 与 Alembic 必须以本章为唯一依据）

**通用约定**
- 时间字段：`VARCHAR(40)`，一律由 `backend.db.base.utc_now_iso()` 产生的 UTC ISO 8601 字符串（与现有 21 张表一致，固定格式使字典序等于时间序，禁止混入本地时区或不同精度格式）；所有 `*_at` 比较均用字符串比较，ORM 层统一用该函数赋值。
- JSON 字段：`TEXT`，序列化为 UTF-8、`ensure_ascii=False`、紧凑分隔符 `(",", ":")`；`payload_json` 必须经现有 `_sanitize_event_payload` 脱敏，严禁写入 Token、密钥、完整 endpoint。
- 主键 UUID 用 `VARCHAR(36)`；状态/枚举字段用 `VARCHAR + CHECK`，不引入数据库枚举类型。
- 所有约束、索引显式命名（`pk_/fk_/uq_/ck_/ix_` 前缀 + 表名），保证 batch 重建与测试可断言。
- 中文注释：表与全部字段在 ORM `comment` 与 `table_column_comments` 同步登记，覆盖率 100%。

### 3.1 `notification_outbox`（第 22 张，业务事件到通知的持久化 Outbox）
| 字段 | 类型 | 空 | 默认 | 说明 |
| :--- | :--- | :---: | :--- | :--- |
| `id` | VARCHAR(36) | N | — | PK |
| `event_seq_id` | INTEGER | N | — | UNIQUE；FK→`events.seq_id` ON DELETE CASCADE |
| `event_type` | VARCHAR(80) | N | — | 冗余自 events，便于调度分流 |
| `payload_json` | TEXT | N | `'{}'` | 脱敏载荷 |
| `status` | VARCHAR(20) | N | `'pending'` | `pending/processing/retry/processed/failed` |
| `locked_by` | VARCHAR(64) | Y | NULL | 认领的 Dispatcher 标识 |
| `locked_at` | VARCHAR(40) | Y | NULL | 认领时间 |
| `lease_expires_at` | VARCHAR(40) | Y | NULL | 租约到期（认领时 +60s） |
| `attempt_count` | INTEGER | N | 0 | 已尝试次数 |
| `max_attempts` | INTEGER | N | 5 | 上限 |
| `next_retry_at` | VARCHAR(40) | N | **无默认**，写入方必须赋 `utc_now_iso()` | 下次可认领时间 |
| `last_error` | TEXT | Y | NULL | 最近错误（脱敏、截断 2000 字符） |
| `created_at` / `updated_at` | VARCHAR(40) | N | — | 创建/更新时间 |
| `processed_at` / `failed_at` | VARCHAR(40) | Y | NULL | 成功/失败终态时间 |

CHECK：`ck_notif_outbox_status`（状态白名单）；`ck_notif_outbox_attempts`（`attempt_count>=0 AND max_attempts>=1`）；`ck_notif_outbox_lock`（`status='processing'` ⇔ `locked_by` 与 `lease_expires_at` 均非空）；`ck_notif_outbox_terminal`（`processed` ⇔ `processed_at` 非空；`failed` ⇔ `failed_at` 非空）。
索引：`ix_notif_outbox_claim(status, next_retry_at)`、`ix_notif_outbox_lease(status, lease_expires_at)`。

### 3.2 `notifications`（第 23 张，通知主台账，单用户全局已读）
| 字段 | 类型 | 空 | 默认 | 说明 |
| :--- | :--- | :---: | :--- | :--- |
| `id` | VARCHAR(36) | N | — | PK |
| `project_id` | VARCHAR(36) | Y | NULL | FK→`projects.id` ON DELETE CASCADE（`project_id` 为 NULL 的全局系统通知不受影响） |
| `category` | VARCHAR(32) | N | — | `decision_required/task_blocked/task_completed/execution_failed/system_alert` |
| `priority` | VARCHAR(20) | N | `'normal'` | `urgent/high/normal/low` |
| `title` | VARCHAR(240) | N | — | 标题 |
| `body` | TEXT | N | `''` | 摘要（不含敏感信息） |
| `link_url` | VARCHAR(500) | N | `'/'` | 仅允许站内相对路径 |
| `target_entity_type` / `target_entity_id` | VARCHAR(40)/VARCHAR(64) | Y | NULL | 跳转目标实体 |
| `aggregation_key` | VARCHAR(120) | Y | NULL | NULL=不聚合（urgent 与决策类必须为 NULL） |
| `aggregated_count` | INTEGER | N | 1 | 已聚合事件数，1–20 |
| `window_started_at` | VARCHAR(40) | N | — | 聚合窗口起点 |
| `is_window_open` | INTEGER | N | 1 | 聚合窗口是否仍可合并（0/1） |
| `is_read` | INTEGER | N | 0 | 全局已读（0/1） |
| `read_at` | VARCHAR(40) | Y | NULL | 已读时间 |
| `created_at` / `updated_at` | VARCHAR(40) | N | — | 创建/更新时间 |

CHECK：`ck_notifications_category`、`ck_notifications_priority`、`ck_notifications_count`（`aggregated_count BETWEEN 1 AND 20`）、`ck_notifications_flags`（`is_read`、`is_window_open` ∈ {0,1}）、`ck_notifications_read`（`is_read=1` ⇔ `read_at` 非空）、`ck_notifications_link`：`link_url LIKE '/%' AND link_url NOT LIKE '//%' AND instr(link_url, char(92)) = 0 AND instr(link_url, char(0)) = 0 AND instr(link_url, char(9)) = 0 AND instr(link_url, char(10)) = 0 AND instr(link_url, char(13)) = 0`（拒绝 `//host` 协议相对地址、反斜杠、NUL/TAB/CR/LF；其余控制字符与完整 URL 解析由业务层校验）。业务层要求：写入通知前与前端执行跳转前均须调用同一站内路径校验（只接受以单个 `/` 开头、不含 `\` 与控制字符、解析后 host 为空的路径）。CHECK 仅是基础防线，不替代业务层校验。
索引：`ix_notifications_cursor(created_at DESC, id DESC)`、`ix_notifications_project_cursor(project_id, created_at DESC, id DESC)`、`ix_notifications_unread(is_read, created_at DESC)`。
**聚合唯一性约束（落实审阅问题 5）**：部分唯一索引 `uq_notifications_open_agg ON (coalesce(project_id,''), aggregation_key) WHERE is_window_open=1 AND aggregation_key IS NOT NULL`——同一聚合键同一时刻至多一个开放窗口，数据库层面拒绝重复创建。窗口关闭条件（置 `is_window_open=0`）：超过 10 秒、达到 20 条、被标记已读；关闭旧窗口与创建新窗口在同一 `BEGIN IMMEDIATE` 事务内完成。

### 3.3 `notification_events`（第 24 张，通知与原始事件关联）
| 字段 | 类型 | 空 | 说明 |
| :--- | :--- | :---: | :--- |
| `id` | VARCHAR(36) | N | PK |
| `notification_id` | VARCHAR(36) | N | FK→`notifications.id` ON DELETE CASCADE |
| `event_seq_id` | INTEGER | N | UNIQUE；FK→`events.seq_id` ON DELETE CASCADE |
| `created_at` | VARCHAR(40) | N | 关联时间 |

索引：`ix_notif_events_notification(notification_id)`。

### 3.4 `notification_deliveries`（第 25 张，按订阅的外呼状态机）
| 字段 | 类型 | 空 | 默认 | 说明 |
| :--- | :--- | :---: | :--- | :--- |
| `id` | VARCHAR(36) | N | — | PK |
| `notification_id` | VARCHAR(36) | N | — | FK→`notifications.id` ON DELETE CASCADE |
| `subscription_id` | VARCHAR(64) | N | — | 复合 FK 成员 |
| `device_id` | VARCHAR(36) | N | — | 复合 FK 成员 |
| `status` | VARCHAR(20) | N | `'pending'` | `pending/processing/retry/delivered/failed/gone` |
| `locked_by` | VARCHAR(64) | Y | NULL | 认领 Worker 标识 |
| `locked_at` | VARCHAR(40) | Y | NULL | 认领时间 |
| `lease_expires_at` | VARCHAR(40) | Y | NULL | 租约到期（认领时 +30s） |
| `attempt_count` | INTEGER | N | 0 | 已尝试次数 |
| `max_attempts` | INTEGER | N | 4 | 1 次首发 + 3 次重试（对应退避 2s/8s/32s） |
| `next_retry_at` | VARCHAR(40) | N | **无默认**，创建时必须赋 `utc_now_iso()` | 下次可认领时间 |
| `last_http_status` | INTEGER | Y | NULL | 本条 delivery 最近一次尝试的网关响应码（仅用于同一 delivery 内连续 404 判断，每次尝试都覆盖写入） |
| `last_error` | TEXT | Y | NULL | 最近错误（脱敏截断） |
| `delivered_at` | VARCHAR(40) | Y | NULL | 成功时间 |
| `failed_at` | VARCHAR(40) | Y | NULL | `failed`/`gone` 终态时间 |
| `created_at` / `updated_at` | VARCHAR(40) | N | — | 创建/更新时间 |

约束：`uq_notif_deliveries_notif_sub UNIQUE(notification_id, subscription_id)`；`fk_notif_deliveries_sub_device FOREIGN KEY(subscription_id, device_id) REFERENCES push_subscriptions(subscription_id, device_id) ON DELETE CASCADE`。
CHECK：`ck_notif_deliveries_status`、`ck_notif_deliveries_attempts`（`attempt_count>=0 AND max_attempts>=1`）、`ck_notif_deliveries_lock`（`processing` ⇔ `locked_by` 与 `lease_expires_at` 非空）、`ck_notif_deliveries_terminal`（`delivered` ⇔ `delivered_at` 非空；`failed/gone` ⇔ `failed_at` 非空）。
索引：`ix_notif_deliveries_claim(status, next_retry_at)`、`ix_notif_deliveries_lease(status, lease_expires_at)`、`ix_notif_deliveries_subscription(subscription_id, created_at DESC)`。

### 3.5 `notification_changes`（第 26 张，持久化通知变更流）
| 字段 | 类型 | 空 | 默认 | 说明 |
| :--- | :--- | :---: | :--- | :--- |
| `change_seq` | INTEGER | N | — | PK AUTOINCREMENT，SSE `id`/`Last-Event-ID` 游标 |
| `project_id` | VARCHAR(36) | Y | NULL | FK→`projects.id` ON DELETE CASCADE（NULL=全局） |
| `change_type` | VARCHAR(32) | N | — | `created/updated/read/read_all/purged` |
| `notification_id` | VARCHAR(36) | Y | NULL | **有意不设外键**：通知被清理后变更流仍保留以便补流；`read_all` 时为 NULL |
| `payload_json` | TEXT | N | `'{}'` | 变更载荷与未读数快照 |
| `created_at` | VARCHAR(40) | N | — | 变更时间 |

CHECK：`ck_notif_changes_type`（含 `purged`）。
**项目物理删除策略（第八轮明确）**：项目被删除时，其通知（`notifications` CASCADE，并级联 `notification_events`、`notification_deliveries`）与其变更流（`notification_changes` CASCADE）**一并清除**，不把项目通知降级为全局通知（跳转目标实体已不存在）。两张表的 `project_id` 外键行为因此统一为 CASCADE。已连接客户端无法从已被级联删除的记录得知变化，因此项目删除流程（5.5 范围，若存在物理删除路径）须在同一事务内追加一条 `project_id=NULL`、`change_type='purged'`、`payload_json` 含被删 `project_id` 的变更记录，客户端收到后重新同步通知中心；离线客户端经 `resync_required` 或重连后的全量拉取恢复。索引：`ix_notif_changes_project_seq(project_id, change_seq)`。
保留策略：至少保留最近 7 天且不少于 10000 条；清理后游标小于最小保留 `change_seq` 的客户端收到 `resync_required`。`change_seq` 在 `BEGIN IMMEDIATE` 单写者临界区内写入，保证与提交顺序一致。

### 3.6 现有表变更
仅 `push_subscriptions`（见 2.4.4）：新增 `vapid_key_fingerprint`、`uq_push_subscriptions_sub_device`、`fk_push_subscriptions_device_id_device_sessions`。`--doctor` 校验表数量为 26。

---

## 四、测试规划（扩充至 39 个专项用例）

### 4.1 事务边界（新增）
1. Outbox 写入失败（注入异常）⇒ 业务实体、`events`、`notification_outbox` **整体回滚**，三表均无残留；
2. Outbox 成功提交后，Dispatcher 派生处理抛异常 ⇒ 业务实体与 `events` 不变，outbox 进入 `retry`，核心业务可继续读写；
3. Pending 初始赋值：`next_retry_at` 非空且 ≤ now，`attempt_count=0`；
4. Retry 超限转 `failed`，清锁，保留 `failed_at/last_error`；
5. Processing 租约超时且超限转 `failed`；
6. Fencing：被接管后旧 Dispatcher 回写影响 0 行并整体回滚。

### 4.2 SQLite 迁移（新增/强化，均在真实 SQLite 文件上执行）
7. `foreign_keys=ON` 下完成重建迁移，迁移后 `PRAGMA foreign_keys=1`；
8. **env.py 传入外部连接分支**（`config.attributes["connection"]`，该分支不会执行 `PRAGMA foreign_keys=ON`）：在 `foreign_keys=OFF` 的连接上运行同一迁移，验证迁移结果与用例 7 一致、迁移结果不依赖外键开关状态。**该用例仅用于覆盖外部连接分支，不替代生产路径验证**；生产路径以用例 7 为准，即通过 `apply_database_migrations()`（`env.py` 在线分支，`connect` 事件事务外设 `foreign_keys=ON`）在真实 SQLite 文件上执行；
9. 迁移失败注入（复制阶段/校验阶段）⇒ 旧表数据完好、迁移入口给出备份恢复指引，备份文件可还原且数据一致；
10. 迁移前后数据完整性：存量订阅 9 列逐行一致，`vapid_key_fingerprint='legacy_unverified'`；
11. 约束/索引清单比对：PK、`uq_push_subscriptions_sub_device`、FK(CASCADE)、`ix_push_subscriptions_device_active` 均存在且命名正确；
12. 预检测试：存在孤儿订阅（无对应 `device_sessions`）⇒ 迁移中止并输出明细，数据未被修改；
13. `PRAGMA foreign_key_check` 与 `integrity_check` 通过；`downgrade` 先删新表再还原且数据保留；
14. 复合外键：`notification_deliveries` 插入错位的 `(subscription_id, device_id)` 被拒绝；删除订阅级联删除投递。

### 4.3 VAPID（新增/强化）
15. 新旧密钥并存：不同指纹订阅分别用对应私钥签名投递成功；
16. 历史 `legacy_unverified` 订阅 201 ⇒ 指纹被修正；
17. 历史订阅收到 **401/403 ⇒ 订阅保持 `is_active=1`**，delivery 重试/告警，不批量停用；
18. 全局 VAPID 异常（同指纹多订阅 401/403）⇒ 暂停该指纹外呼并告警，不改任何 `is_active`；
19. **410 ⇒ 停用**；404 在同一 delivery 内连续 2 次才停用（见用例 31）；
20. 不同 `applicationServerKey` 下 `subscribe()` 返回 `InvalidStateError` 被安全捕获（浏览器环境测试/可替身模拟）；
21. 换绑：新订阅失败 ⇒ 用旧公钥重新订阅并重新登记，旧记录仅在成功后停用；
22. 换绑：新订阅成功但服务端登记失败 ⇒ 启动对账幂等重新登记成功；
23. 恢复彻底失败 ⇒ 界面提示通知不可用，服务端旧记录不被标注为可用。

### 4.4 聚合、SSE、安全、P13
24. 多真实连接并发写同一 `aggregation_key` ⇒ 仅 1 条通知且 `aggregated_count` 准确；已读通知强制新开；
25. 重启后 SSE 凭 `notification_changes` 补流；过旧游标下发 `resync_required`；
26. SSRF：私网/回环/元数据 IP 拦截；Socket 目标绑定防 DNS Rebinding；禁止重定向；
27. 通知/订阅接口不再因回环来源免鉴权；
28. P13：低风险推荐项 45 秒自动提交；
29. P13：`risk_gate`/high/critical 永不自动放行，触碰即暂停倒计时。

### 4.5 第七轮新增
30. VAPID 熔断规则：同指纹 10 订阅仅 1 个 401 ⇒ 仅单订阅处理、不暂停指纹；≥3 个独立订阅 401/403 且成功率低 ⇒ 全局暂停；签名自检失败 ⇒ 全局暂停；以上均不改 `is_active`；
31. Delivery 状态机：pending 认领、processing 租约超时接管、超限转 `failed`、201→`delivered`、410→`gone`+订阅停用、同一 delivery 内连续两次 404 才 `gone`+停用（中间夹杂其他状态码则重置；不跨通知统计）、429 `Retry-After`、Fencing 阻止旧 Worker 覆盖及其副作用；`next_retry_at` 初始非空；
32. Dispatcher 同事务：在写 `processed` 前注入崩溃 ⇒ `notifications/notification_events/notification_deliveries/notification_changes` 全部回滚，重试后无重复通知；Fencing 失败整体回滚；外呼不在事务内（锁持有时间断言）；
33. `uq_notifications_open_agg`：并发创建同聚合键第二个开放窗口被数据库拒绝；窗口超时/满 20/已读后允许新窗口；
34. 订阅归属：伪造请求体 `device_id`、跨设备覆盖已存在订阅、跨设备 unsubscribe/`replaced_endpoint`/test 均被拒绝（409/403）；回环来源无 Token 访问 `/push/*` 与 `/notifications/*` 返回 401；
35. SSRF 逐项：非 https、非 443、白名单绕过构造域名、A/AAAA 任一为私网、元数据地址、IPv4 映射 IPv6、重定向、DNS Rebinding（二次解析返回内网）、SNI/Host 保持；
36. 数据字典一致性：ORM 元数据、Alembic 迁移结果、`table_column_comments` 三者对 5 张新表的列、类型、NULL、默认值、CHECK、索引、外键一致，注释覆盖率 100%；
37. `notification_changes` 保留与游标：清理后过旧游标得 `resync_required`；`change_seq` 与提交顺序一致；

### 4.6 第八轮新增（属于 5.2 验收）
38. `ck_notifications_link`：拒绝 `https://x`、`//evil.example.com/p`、含 `\`、NUL/TAB/CR/LF 的值；接受 `/tasks/1?decision_id=2`；真实 SQLite 文件上验证；
39. 项目删除级联：删除项目后其 `notifications`、`notification_events`、`notification_deliveries`、`notification_changes` 全部清除，`project_id` 为 NULL 的全局通知保留；`purged` 为合法 `change_type`。


---

## 五、WBS

- **5.1** 现有设施核查（已完成）。
- **5.2** 数据模型与迁移（待批准）：5 张新表 ORM；`push_subscriptions` 扩充；Alembic（预检 + batch 重建 + 新表，推荐拆为 revision A/B）；`table_column_comments` 注册；**验收测试：7–14、33、36、38、39，全部必须在真实 SQLite 文件上执行**（部分唯一索引、表达式索引、CHECK、复合外键、batch 重建均不得仅靠 ORM 元数据检查）。**不触碰 `uow.py`、Dispatcher、Worker、前端。**
- **5.3** Outbox 写入接入 UoW、Dispatcher、Delivery Worker（测试 1–6、24）。
- **5.4** Web Push 外呼、多代密钥库、状态码分级、SSRF、鉴权收紧（测试 15–19、26–27）。
- **5.5** 通知中心 REST + `notification_changes` SSE（测试 25）。
- **5.6** 前端通知抽屉、换绑状态机与对账、P13（测试 20–23、28–29）。
- **5.7** 全量回归、`npm run build`、`python run.py --doctor`（26 张表）、验收报告。

---

## 六、技术限制与残余风险
0. **遗留到 5.4 的统一项**：①VAPID 密钥状态统一定义（`active/retired` 与全局异常使用的 `degraded` 须合并为明确的状态机）；②`device_sessions` 当前无过期字段，设备鉴权仅检查 `is_revoked=0`，5.4 须明确是否引入过期时间及校验方式。
1. **SQLite DDL 无可靠事务回滚**：迁移失败恢复依赖预检 + 临时表复制 + 迁移前文件备份，而非数据库级原子回滚；需在真实文件上验证。
2. **换绑存在不可避免的短暂空窗**：浏览器 `unsubscribe()` 到新订阅登记完成之间，设备无法收到推送；计划通过恢复状态机、对账与应用内通知中心兜底，但不能消除空窗。
3. **Outbox 同事务写入引入新的业务写入失败面**：Outbox 故障会令业务写失败（这是原子性的代价）；以极简白名单写入与测试降低风险。
4. **404 语义因 Push Service 而异**：采用“连续 2 次确认”折中，仍可能保留少量已失效订阅，仅造成多余重试，不会漏停用关键设备。
5. **iOS 仅限“添加到主屏幕”的 PWA 才能 Web Push**（平台限制），以应用内抽屉与 SSE 兜底。
6. **SQLite 单写者吞吐上限**：对单用户中枢足够；`BEGIN IMMEDIATE` 的 BUSY 处理与多连接并发测试为必须项。
7. **`push_subscriptions` 现有 `subscribe()` 实现**（直接信任客户端 `device_id`、无 SSRF 校验）需在 5.4 重写；5.2 迁移不修复该行为，仅提供结构约束。

---

## 七、实施边界与等待审批
批准后 Phase 5.2 仅限：ORM 模型、Alembic 迁移（含预检与校验）、`push_subscriptions` 扩充、`table_column_comments` 中文注释、模型/外键/唯一约束/级联/迁移测试。不得提前实施 Dispatcher、Delivery Worker、`uow.py` Outbox 接入、前端；不修改 `main`；不擅自 commit/push/PR/merge。
