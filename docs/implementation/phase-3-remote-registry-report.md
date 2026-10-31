# AgentDeck Phase 3（Remote Registry、能力分级校正与质量清单闭环）验收报告

> **阶段定位**：Phase 3 — 官方 Remote 入口与远程会话登记管理（Remote Registry）及 Phase 2 遗留质量清单闭环  
> **实施基准**：[phase-3-implementation-plan.md](file:///d:/Development%20Files/PythonProjects/stu/AgentDeck/docs/implementation/phase-3-implementation-plan.md)（已落实审阅提出的 4 项必改要求）  
> **验收日期**：2026-10-31  
> **当前分支**：`feat/project-task-foundation`（未修改 `main` 分支）

---

## 一、四项审阅必改要求与质量清单闭环情况

| 编号 | 审阅要求 / 质量项 | 落地实现与验证结论 |
|---|---|---|
| **必改 1（阻塞项）** | **修正 L0–L3 定义（SDK != L3）** | 已在 [enums.py](file:///d:/Development%20Files/PythonProjects/stu/AgentDeck/backend/domain/enums.py) 与 [capability_policy.py](file:///d:/Development%20Files/PythonProjects/stu/AgentDeck/backend/domain/services/capability_policy.py) 统一为：<br>• `L0`：官方 Remote 入口（`remote_entry`）<br>• `L1`：Runtime 注册、状态与能力信息管理（`runtime_registry_and_status` / `mcp_collab`）<br>• `L2`：程序化控制（`programmatic_control`，SDK/HTTP API/Stdio 是技术途径，不等于 L3）<br>• `L3`：审批与恢复能力（`approval_and_resume`）<br>每项能力独立校验 `status (supported/unsupported/unverified)`、`evidence`、`runtime_version`、`connector_version`、`verified_at`。 |
| **必改 2** | **Phase 3 不提前实现 P10 受控指令交互区** | [App.tsx](file:///d:/Development%20Files/PythonProjects/stu/AgentDeck/frontend/src/App.tsx) 中 P10 仅展示只读 Capability Manifest（未经验证显式标识 `unverified`）与官方 Remote 跳转入口，**不包含任何指令输入框、发送按钮或模拟控制按钮**。 |
| **必改 3** | **修正回滚方案，禁止覆盖已有工作区修改** | 已更新 [phase-3-implementation-plan.md](file:///d:/Development%20Files/PythonProjects/stu/AgentDeck/docs/implementation/phase-3-implementation-plan.md)，严禁使用 `git checkout -- <dir>` / `git reset --hard` / `git clean -fd`，全程基于未提交修改增量演进。 |
| **必改 4** | **Decision 并发处理的数据库级事务原子性** | [decision_service.py](file:///d:/Development%20Files/PythonProjects/stu/AgentDeck/backend/application/decision_service.py) 采用 SQLite 同事务原子条件更新 `UPDATE decisions SET ... WHERE id = :id AND status = 'pending'`（校验 `rowcount == 1`），并在同事务内统计剩余 `pending == 0` 后仅对 `WHERE platform_status = 'waiting_interaction'` 执行原子恢复，绝不产生重复审计事件，也绝不覆盖 `paused` / `completed` 等状态。 |
| **质量项 1** | **Decision 重试间隔统一恢复为 1s / 2s / 4s** | 前后端统一收敛为 `backoff_ms: [1000, 2000, 4000]`、`max_retries: 3`（见 [decision_policy.py](file:///d:/Development%20Files/PythonProjects/stu/AgentDeck/backend/domain/services/decision_policy.py) 与 [api.ts](file:///d:/Development%20Files/PythonProjects/stu/AgentDeck/frontend/src/types/api.ts)）。 |
| **质量项 2** | **跨项目隔离与 Remote URL 安全脱敏** | `GET /api/v1/remotes` 强制要求 `project_id` 并通过 `enforce_project_scope` 拦截跨项目 Token / Scope 访问；`validate_safe_remote_url` 拦截非 `http/https`、控制字符、userinfo、query/fragment 敏感参数，并输出 `masked_remote_url`。 |
| **质量项 3** | **真实浏览器 E2E 交互验证** | 使用本机 Microsoft Edge Chromium Headless CDP 完成真实 DOM、键盘防误触、移动端 `>=48px` 触控目标、跨页面弹窗唤醒与 P10 状态切换端到端测试。 |

---

## 二、核心代码变更清单

### 1. 后端领域与服务层
- [backend/domain/enums.py](file:///d:/Development%20Files/PythonProjects/stu/AgentDeck/backend/domain/enums.py)：更新 `ControlLevel` 规范注释，补充 `EvidenceSource.LOCAL_TEST` 与 `SPIKE_VERIFIED`。
- [backend/domain/types.py](file:///d:/Development%20Files/PythonProjects/stu/AgentDeck/backend/domain/types.py)：强化 `validate_safe_remote_url` 校验（控制字符、非法端口、裸主机名、敏感参数拦截），新增 `mask_remote_url_for_display`。
- [backend/domain/services/capability_policy.py](file:///d:/Development%20Files/PythonProjects/stu/AgentDeck/backend/domain/services/capability_policy.py)（新增）：实现 L0–L3 控制等级与逐项能力证据门禁 `validate_runtime_control_and_capabilities`。
- [backend/domain/services/decision_policy.py](file:///d:/Development%20Files/PythonProjects/stu/AgentDeck/backend/domain/services/decision_policy.py)：新增 `DECISION_RETRY_POLICY`（`[1000, 2000, 4000]` ms，3 次重试）。
- [backend/application/decision_service.py](file:///d:/Development%20Files/PythonProjects/stu/AgentDeck/backend/application/decision_service.py)：`resolve_decision` 与 `expire_decision` 升级为 SQLite 事务内原子条件更新（CAS），同事务检查剩余未决项并保护非 `waiting_interaction` 的会话状态。
- [backend/application/runtime_registry_service.py](file:///d:/Development%20Files/PythonProjects/stu/AgentDeck/backend/application/runtime_registry_service.py)：更新 4 大官方 Provider 的能力清单结构，新增 `update_machine`、`get_runtime_instance`、`update_runtime_instance` 并接入 `capability_policy` 门禁。
- [backend/application/session_service.py](file:///d:/Development%20Files/PythonProjects/stu/AgentDeck/backend/application/session_service.py)：新增 `update_external_session_ref`、`update_session_connection_status`（断线不判败）、`list_remote_registry`（强制 `project_id` 隔离）。
- [backend/api/deps.py](file:///d:/Development%20Files/PythonProjects/stu/AgentDeck/backend/api/deps.py)、[backend/api/schemas.py](file:///d:/Development%20Files/PythonProjects/stu/AgentDeck/backend/api/schemas.py)、[backend/api/router.py](file:///d:/Development%20Files/PythonProjects/stu/AgentDeck/backend/api/router.py)：新增 `enforce_project_scope` 权限守卫及 Phase 3 全部 REST 路由。

### 2. 前端工程（`frontend/`）
- [frontend/src/types/api.ts](file:///d:/Development%20Files/PythonProjects/stu/AgentDeck/frontend/src/types/api.ts) & [frontend/src/api/client.ts](file:///d:/Development%20Files/PythonProjects/stu/AgentDeck/frontend/src/api/client.ts)：重试退避间隔恢复为 `[1000, 2000, 4000]`（1s / 2s / 4s），补齐 Machine / RuntimeInstance / ExternalSessionRef / RemoteRegistry / DecisionExpire 客户端方法。
- [frontend/src/App.tsx](file:///d:/Development%20Files/PythonProjects/stu/AgentDeck/frontend/src/App.tsx)：
  - **P10 会话详情页**：展示 `Project -> Task -> Session -> RuntimeInstance -> Machine` 全链路身份映射、独立 `Connection Status` 状态切换、官方 Remote 入口登记/编辑/跳转（显示脱敏 URL）、只读 Capability Manifest（清晰标注 `supported / unsupported / unverified` 与证据），**无任何受控指令发送框**。
  - **P14 Remote Registry 与系统设置页**：展示当前项目隔离下的官方 Remote 入口台账、Machine 在线状态切换、Runtime 能力等级定义说明。

---

## 三、验证与测试结果

### 1. 数据库诊断体检（`python run.py --doctor`）
- 21 张规范表、SQLite WAL + FK + `busy_timeout=15000`、4 个官方 `RuntimeProvider` 与默认本机 `Machine` 全部就绪。

### 2. 前端生产构建（`npm run build`）
- `tsc -b && vite build` 零错误通过，产出 `frontend/dist/index.html` 与静态资产包。

### 3. 后端单元、API、并发竞争、跨项目隔离与真实浏览器 E2E 测试（`pytest`）
- 测试文件：
  - [tests/test_phase1_clean_slate_domain.py](file:///d:/Development%20Files/PythonProjects/stu/AgentDeck/tests/test_phase1_clean_slate_domain.py)
  - [tests/test_phase2_rest_api.py](file:///d:/Development%20Files/PythonProjects/stu/AgentDeck/tests/test_phase2_rest_api.py)
  - [tests/test_phase3_remote_registry.py](file:///d:/Development%20Files/PythonProjects/stu/AgentDeck/tests/test_phase3_remote_registry.py)
- 关键验证覆盖：
  1. **L0–L3 能力分级门禁**：验证 `L2` 依赖 `programmatic_control` 证据、`L3` 依赖 `approval_and_resume` 证据，仅凭 SDK 字样声明 `L3` 被严格拦截（422）。
  2. **Remote URL 白名单与跨项目权限隔离**：拦截 `javascript:`、userinfo、query token、控制字符；验证无 `project_id` 或跨项目 `X-Project-Scope` / `X-MCP-Project-Token` 访问 `/api/v1/remotes` 均被拦截（422）。
  3. **Connection Status 独立演进**：切换 `connection_status=disconnected` 时，`Session.platform_status` 保持 `active`、`Task.platform_status` 保持 `in_progress`。
  4. **Decision 事务级 CAS 并发安全**：双线程 `threading.Barrier(2)` 并发执行人工提交与超时自动提交，严格保证 `["conflict", "won"]` 且 `events` 表仅生成 1 条结案事件；多未决 Decision 下仅当最后一个结案且 Session 处于 `waiting_interaction` 时才恢复 `active`，绝不覆盖 `paused` 状态。
  5. **真实 Chromium（Headless Edge CDP）浏览器 E2E**：验证跨页面（`#remotes`）自动唤醒全局弹窗、聚焦 `<input>` 按数字键 `'2'` 不误触弹窗选项、失焦按 `'2'` 切换选项并自动暂停倒计时、移动端（`375x812`）触控高度 `>= 48px`、以及 P10 官方 Remote 入口与无受控指令框边界。

