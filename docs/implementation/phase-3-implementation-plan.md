# AgentDeck Phase 3 文件级实施计划（Remote Registry 与能力证据管理 — 审阅修订版）

> **编制时间**：2026-10-31  
> **当前分支**：`feat/project-task-foundation`（已核验非 `main` 分支，工作区未提交修改完整保留）  
> **阶段基线**：`AgentDeck_Architecture_and_Roadmap_v1.3.md` Phase 3 + Phase 1/2 验收遗留质量改进项 + 审阅四项修正

---

## 一、Git 分支与工作区前置核验

1. **当前分支**：`feat/project-task-foundation`（跟踪 `AgentDeck/feat/project-task-foundation`），未触碰 `main` 分支。
2. **工作区状态**：Phase 1 / Phase 2 的全部重构文件及验收报告 `docs/implementation/phase-1-and-2-clean-slate-report.md` 均完好保留，不覆盖任何已有未提交成果。
3. **阶段红线确认**：
   - **不实施** Phase 4 MCP Server；
   - **不实施** Phase 6/7 Runtime SDK 控制；
   - **不重建** Agent Chat、Terminal 或 Agent Loop；
   - **不提前实现** P10 受控指令交互区（不展示指令发送输入框、发送按钮或模拟控制功能）；
   - **不读取或修改** CCSwitch 及任何外部 Agent 私有配置/数据库/逆向协议；
   - **不把** 官方 Remote URL 当作程序化控制 API，也**不凭空**将具备 Remote 功能的 Runtime 标记为 `L2` 或 `L3`。

---

## 二、审阅四项必改事项落实

1. **修正 1：统一 L0 / L1 / L2 / L3 能力分级定义（对齐 v1.3）**
   - `L0`：**官方 Remote 入口**（`remote_entry`）
   - `L1`：**Runtime 注册、状态与能力信息管理**（`runtime_registry_and_status` / `mcp_collab`）
   - `L2`：**程序化控制**（`programmatic_control`；SDK、公开 HTTP API 或本地 App Server Stdio 只是实现 `L2` 的技术途径，SDK 不等于 `L3`）
   - `L3`：**审批与恢复能力**（`approval_and_resume`）
   - `capability_policy.py` 按具体能力项分别校验 `support_status`（`supported / unsupported / unverified`）、验证证据（`evidence`）、版本（`runtime_version`, `connector_version`）和验证时间（`verified_at`）。
2. **修正 2：Phase 3 不提前实现 P10 受控指令交互区**
   - P10 仅展示 Runtime 控制能力矩阵、验证证据状态（未验证项显式标注 `unverified`）及官方 Remote 跳转入口；
   - **完全移除/不实现** 任何指令发送输入框、发送按钮或模拟控制 UI；待后续阶段完成真实 L2 Connector 验证后再决定是否开放。
3. **修正 3：安全回滚方案（严禁覆盖已有未提交工作区修改）**
   - 严禁使用 `git checkout -- <dir>`、`git reset --hard`、`git clean -fd` 等可能覆盖 Phase 1/2 未提交修改的命令；
   - 回滚前必须先检查 `git status` 与 `git diff`，仅针对 Phase 3 新增文件执行精确删除或对修改的代码块通过 `edit_file` 精确还原。
4. **修正 4：Decision 并发落定与 Session 恢复的数据库级事务原子性**
   - Decision 落定采用 SQLite 同事务内的**原子条件更新（CAS）**：`UPDATE decisions SET status = ..., ... WHERE id = :id AND status = 'pending'`，通过 `result.rowcount == 1` 判断是否赢得并发竞争；
   - 人工提交（`user_manual`）与超时结算（`timeout_auto_recommended` / `expire`）并发到达时，仅 `rowcount == 1` 的事务成功写入状态与 `decision.resolved` 审计事件；后到达事务若匹配相同 `idempotency_key` 则幂等重放，否则抛出 `VersionConflictError`，绝不产生重复审计事件；
   - 同一事务内检查剩余未决 Decision（`SELECT count(*) FROM decisions WHERE session_id = :sid AND status = 'pending'`），且**仅当关联 `Session.platform_status == 'waiting_interaction'` 且剩余 `pending == 0` 时**，使用原子条件更新 `WHERE id = :sid AND platform_status = 'waiting_interaction'` 恢复为 `active`，绝不覆盖 `paused`、`completed`、`archived` 等其他原因产生的状态。

---

## 三、数据库与 API 变化说明

### 3.1 数据库变化
- **复用现有 21 张规范表**，无需新增表或破坏性迁移。
- **领域层强化约束**：
  - `runtime_instances.control_level` 与 `capability_manifest_json`：按 `L0`（官方 Remote 入口）、`L1`（Runtime 注册、状态与能力信息管理）、`L2`（程序化控制）、`L3`（审批与恢复能力）分级校验验证证据。
  - `sessions.connection_status` 与 `external_session_refs.link_status`：独立演进连接状态，断线（`disconnected` / `stale`）绝不把 `Session.platform_status`、`Task.platform_status` 或 `Execution` 置为失败。
  - `validate_safe_remote_url`：强化协议校验（仅允许 `http`/`https`，拦截 `javascript:`、`file:`、`ws:`、`ssh:`、`vscode:`）、异常 URL（空主机、非法端口、控制字符/空白字符）及 userinfo / query / fragment 敏感凭证拦截，并提供脱敏展示摘要。

### 3.2 `/api/v1/*` API 新增与增强端点（含严格项目级权限过滤）

| 方法与路径 | 用途说明 |
| :--- | :--- |
| `PATCH /api/v1/machines/{machine_id}` | 更新宿主机器显示名、别名、连接通道类型与在线健康状态 |
| `GET /api/v1/runtimes/instances/{instance_id}` | 获取单个 Runtime 实例详情（含关联 Provider、Machine、能力证据清单） |
| `PATCH /api/v1/runtimes/instances/{instance_id}` | 更新 Runtime 实例版本、健康状态、非敏感模型环境说明，或提交能力验证证据（含 L0–L3 证据门禁校验） |
| `GET /api/v1/projects/{project_id}/remotes` | 获取指定项目的 Remote Registry 台账（严格项目隔离） |
| `GET /api/v1/remotes?project_id=...` | 按必填或已授权 `project_id` 过滤查询 Remote Registry 台账（支持 `X-Project-Token` 项目令牌作用域强制校验，防止跨项目数据泄露） |
| `PATCH /api/v1/projects/{project_id}/sessions/{session_id}/external-refs/{ref_id}` | 更新已登记的 `ExternalSessionRef`（原生 `external_session_id`、`remote_entry_kind`、脱敏 `remote_entry_url`、`link_status`） |
| `PATCH /api/v1/projects/{project_id}/sessions/{session_id}/connection` | 独立更新会话 `connection_status`（`connected/disconnected/unknown`）及可选同步更新关联 `ExternalSessionRef.link_status`，绝不影响任务与会话执行成败状态 |

---

## 四、文件级修改与新增清单

1. `backend/domain/enums.py`（修改）：更新 `ControlLevel` 注释，明确 `L0` 官方 Remote 入口、`L1` Runtime 注册/状态与能力信息管理、`L2` 程序化控制、`L3` 审批与恢复能力。
2. `backend/domain/types.py`（修改）：强化 `validate_safe_remote_url`（拦截控制字符、非法端口、非 http/https 协议、敏感参数）并新增 `mask_remote_url_for_display` 脱敏展示工具函数。
3. `backend/domain/services/capability_policy.py`（新增）：实现按具体能力验证证据与 `L0`–`L3` 控制等级对齐的校验器 `validate_runtime_control_and_capabilities`。
4. `backend/domain/services/decision_policy.py`（修改）：定义统一的 `DECISION_RETRY_POLICY`（`backoff_ms: [1000, 2000, 4000]`, `max_retries: 3`）。
5. `backend/application/decision_service.py`（修改）：将 `resolve_decision` 与 `expire_decision` 升级为 SQLite 原子条件更新（`WHERE id = :id AND status = 'pending'`），并在同事务内原子检查剩余 `pending` Decision 后仅将 `waiting_interaction` 状态的 Session 恢复为 `active`。
6. `backend/application/runtime_registry_service.py`（修改）：对齐 v1.3 能力名称（`remote_entry`、`runtime_registry_and_status`、`mcp_collab`、`programmatic_control`、`approval_and_resume`），接入 `capability_policy.py`，新增 `update_machine`、`get_runtime_instance`、`update_runtime_instance`。
7. `backend/application/session_service.py`（修改）：新增 `update_external_session_ref`、`update_session_connection_status`、`list_remote_registry`（强制支持项目级权限过滤）。
8. `backend/api/schemas.py` & `backend/api/router.py`（修改）：暴露 Phase 3 新增路由、项目级权限校验与 `/api/v1/health` 中的 `decision_retry_policy`。
9. `frontend/src/types/api.ts`、`frontend/src/api/client.ts`、`frontend/src/App.tsx`（修改）：
   - 将 Decision 重试退避统一为 **`1000ms / 2000ms / 4000ms`（1s / 2s / 4s）**；
   - P10 会话详情页：展示完整 `Project -> Task -> Runtime @ Machine -> ExternalSessionRef` 映射、官方 Remote 入口、在线编辑 ExternalSessionRef、独立 Connection Status 切换、只读能力与证据状态展示（**不显示任何受控指令输入框或发送按钮**）；
   - P14 Runtime & Remote 注册中心：新增按当前项目隔离的 Remote Registry 台账、Machine 状态更新与 Runtime 能力证据管理。
10. `tests/test_phase3_remote_registry.py`（新增）：覆盖 L0–L3 能力证据门禁、Remote URL 安全边界、Connection Status 断线不判败、跨项目权限隔离、以及 Decision 并发原子竞争与幂等回归测试。
