# AgentDeck 架构与分阶段开发方案

**版本：v1.3**  
**日期：2026-10-31**  
**状态：建议基线，待用户验收后交给 Antigravity 执行**  
**适用仓库：`xhma-code/AgentDeck`**

---

## 0. 本次更新的核心决策

用户已确认 AgentDeck 的产品定位和设计原则：

1. **AgentDeck 是跨项目、跨 Agent 的工作管理与统一控制平台。**
2. **Agent 自身能力很强，AgentDeck 不替代 Agent，不重造 Agent 的核心能力。**
3. **官方功能优先；非必要绝不重复造轮子。**
4. **先轻量接入，再逐步深度集成。**
5. **用户现有的 Agent 使用方式和本机环境应尽量保持不变。**

本次 v1.3 重点补充 Claude Code 的真实使用环境：用户在 Windows 电脑上运行 Claude Code，并通过 CCSwitch 将其模型服务切换为国内模型服务商。CCSwitch 只是用户现有的本地配置工具，**不是 AgentDeck 的集成目标**。

### 0.1 最终产品定义

> AgentDeck 是跨项目、跨 Agent 的工作管理与统一控制平台。它负责组织项目、任务、版本、决策、执行记录和交付物，并为不同 Agent 提供统一的工作上下文和协作入口。Agent 的推理、模型调用、工具执行、代码修改、终端、权限和远程交互，优先由 Agent 自身及其官方生态完成。AgentDeck 只在存在稳定、公开、受支持的接口时增加程序化控制能力。

### 0.2 本次新增的明确边界

- 用户可以继续通过 CCSwitch 管理 Claude Code 的模型服务配置。
- AgentDeck **不集成 CCSwitch**，不开发 CCSwitch Connector，不复制其模型切换功能。
- AgentDeck 不读取、修改或同步 CCSwitch 的配置文件、数据库、密钥或本地状态。
- AgentDeck 不要求用户改变 Claude Code 当前的登录方式、模型供应商、CLI 使用习惯或本地配置。
- Claude Code 仍然是 Agent Runtime；底层模型服务商只是 Runtime 的模型提供方。
- AgentDeck 管理的是 Claude Code Runtime / Session / Project / Task 关系，而不是管理 Claude Code 的模型供应商。
- Claude Agent SDK 是否能在用户当前的国内兼容 API 配置下工作，必须单独做真实技术验证；不能因为 CLI 可用就推断 SDK 一定可用。
- Claude Remote Control 与 Claude Agent SDK 是不同能力：Remote 是厂商提供给人的远程交互入口；SDK 是本地程序化运行 Agent 的开发接口。不能混为一谈。

---

# 第一部分：产品边界与架构原则

## 1. AgentDeck 与 Agent Runtime 的职责

### 1.1 AgentDeck 负责

- Project、Task、Step、Dependency。
- Task / Step Revision 和历史版本。
- Session 与 Project 的关联，Task 关联可选。
- Execution、Attempt、状态和运行历史。
- Decision、人工检查点和审计。
- Artifact 登记、版本、来源和追溯。
- 跨 Agent 项目上下文连续性。
- Agent / Runtime / Machine 登记、健康状态和能力清单。
- 从统一工作台进入对应 Agent 的官方 Remote。
- 通过 MCP 让 Agent 主动读取任务、汇报进度、请求决策和登记产物。
- 在具备稳定官方接口时，通过 Connector 启动、查询、继续、取消 Agent 执行。
- 将不同 Agent 的状态映射到统一 Project / Task / Execution 领域模型。

### 1.2 Agent Runtime 自己负责

- 模型推理、Agent Loop、规划和工具调用。
- Runtime 内部上下文、会话细节和子 Agent。
- IDE、终端、文件编辑和工具执行体验。
- Runtime 原生权限确认、沙箱和执行安全策略。
- 厂商提供的 Remote 连接、移动端交互、推送通知等。
- Runtime 自身支持的 Session 生命周期和恢复。
- 模型服务商选择、模型名称、API 地址、认证和模型切换配置。

### 1.3 明确不做

- 不自建通用 Agent Loop / Harness。
- 不自建第二套 Claude Code / Codex / Antigravity 聊天、终端、IDE、工具审批 UI。
- 不开发 CCSwitch 替代品或 CCSwitch Connector。
- 不把模型供应商配置抽象成 AgentDeck 的核心配置中心。
- 不要求所有 Agent 使用同一个模型供应商或认证方式。
- 不读取厂商私有数据库，不抓包，不逆向私有 RPC。
- 不把 Remote 能力误认为第三方程序化控制 API。
- 不承诺所有 Runtime 都支持暂停、恢复、取消、审批回调。
- 第一版不做自动 Agent 路由、自动多 Agent 调度或复杂工作流引擎。
- 不为了统一而统一；统一的是管理领域模型和用户体验，不是底层 Runtime 实现。

---

## 2. 四个维度必须分开

AgentDeck 设计中必须区分以下概念：

| 维度 | 例子 | 归属 | AgentDeck 的处理方式 |
|---|---|---|---|
| Agent Runtime | Claude Code、Codex、Antigravity、OpenCode | Agent 产品 | 登记、关联、按官方接口集成 |
| Remote | Claude Remote Control、ChatGPT Codex Remote、Antigravity Remote | Agent 厂商 | 优先复用官方入口，不重造 UI |
| Runtime Control API | Claude Agent SDK、Codex SDK/App Server、OpenCode HTTP API | Agent 厂商 | 验证后实现独立 Connector |
| Model Provider | Anthropic、国内模型厂商、兼容 API 网关 | 模型服务商/用户环境 | 不纳入核心控制层；保留可选的非敏感运行环境说明 |

特别说明：一个 Claude Code Runtime 可以使用不同模型服务提供商。模型供应商变化不应改变 AgentDeck 的 Project、Task、Session、Execution 数据结构，也不应要求 AgentDeck 重新接入一种新的 Agent。

### 2.1 用户本机配置的处理原则

例如用户通过 CCSwitch 把 Claude Code 从 Anthropic 官方服务切换到国内兼容 API：

- Claude Code 的 Agent 能力没有因此自动变成另一种 Agent。
- AgentDeck 仍将其登记为 `claude_code` Runtime。
- 模型供应商配置仍由用户现有工具和 Claude Code 本机环境管理。
- AgentDeck 不需要知道 CCSwitch 的内部实现。
- 若用户主动希望在 AgentDeck 里备注当前模型环境，可手动填写非敏感信息；这只是说明性元数据，不参与连接和认证。
- 不采集 API Key、Token、账号凭证、完整环境变量或包含秘密的配置文件。

---

## 3. Remote、MCP、REST API、Runtime SDK 的职责

| 技术 | 正确职责 | AgentDeck 如何使用 |
|---|---|---|
| 厂商 Remote | 人通过手机/浏览器远程操作 Agent | 提供官方入口或说明，不复制交互界面 |
| MCP Server | Agent → AgentDeck 协作 | Agent 读取项目/任务、汇报进度、请求决策、登记产物 |
| REST API | 前端/管理端 → AgentDeck | 平台自身数据和业务 API |
| Runtime API / SDK / CLI | AgentDeck → Agent Runtime | 仅使用公开、受支持的接口，按能力实现 Connector |
| Hosted Agent API | AgentDeck → 厂商托管 Runtime | 后期可选路线，不混同本地 Runtime |
| Worker Supervisor | AgentDeck 管理本机/服务器子进程 | 只有在确实需要托管 Runtime 时才引入 |

### 3.1 Remote 不等于第三方控制 API

手机能够远程操作 Agent，不代表 AgentDeck 可以通过同一接口：
- 枚举 Session；
- 创建 Session；
- 发送 Prompt；
- 读取事件流；
- 控制权限审批；
- 取消或恢复任务；
- 获取完整、可信的执行状态。

上述能力必须逐项找到官方公开接口并通过真实测试验证。

### 3.2 MCP 是 Agent → AgentDeck

第一批 MCP 工具建议：
1. `project_context_get`
2. `task_get`
3. `task_report_progress`
4. `step_update`
5. `checkpoint_request`
6. `decision_get`
7. `artifact_register`
8. `task_revision_get`

MCP 不负责启动 Claude Code / Codex / Antigravity 的完整 Session 生命周期，也不等价于 Remote Control API。

---

# 第二部分：Runtime 接入策略

## 4. 统一接入分为三条通道

### A. Remote Link Connector（轻量入口）

职责：
- 登记 Runtime、Machine 和外部 Session。
- 保存不含一次性凭证的 Remote 入口配置。
- 从 AgentDeck 打开官方 Remote。
- 显示入口类型、连接状态和最近活动。
- 不控制 Remote 内部会话生命周期。

### B. Local Runtime Connector（本地程序化控制）

职责：
- 通过官方 SDK / App Server / HTTP API / CLI。
- 在接口真实支持时创建、继续、恢复 Session。
- 发送任务、读取事件、取消执行。
- 处理 Runtime 原生权限请求。
- 按能力声明和测试结果开放 UI 操作。

Connector 不能强迫不同 Runtime 实现同一套内部协议。统一的是外部领域模型和能力声明。

### C. Hosted Agent Connector（云端托管）

职责：
- 对接厂商正式提供的托管 Agent API。
- 创建和管理云端任务、Session、事件和产物。
- 管理云端执行环境和费用边界。
- 作为后期独立执行方式，不是第一版核心依赖。

## 5. 统一控制能力等级

| 等级 | 名称 | 能力 |
|---|---|---|
| L0 | Remote Link | 登记 Runtime / Machine / Session，打开官方 Remote |
| L1 | Registry & Status | 心跳、健康状态、最近活动、Agent 主动汇报 |
| L2 | Programmatic Control | 官方接口创建/继续 Session、发送任务、读取事件、取消 |
| L3 | Approval & Recovery | 官方接口支持的审批、检查点、恢复和状态协调 |

每个 Runtime 独立声明具体能力，不因为支持某一级就默认支持该级所有操作。

每项能力记录：
- `supported / unsupported / unverified`
- 证据来源：`official_docs / integration_test / inferred`
- Runtime 版本
- Connector 版本
- 验证日期
- 限制和适用认证方式

不能因为某产品有 Remote，就把 `prompt_send`、`session_list` 自动标记为 supported。

---

## 6. Claude Code：v1.3 重点修订

### 6.1 已知使用前提

用户当前在 Windows 电脑上使用 Claude Code，并通过 CCSwitch 切换到国内模型服务。该信息用于确定技术验证场景，不代表 AgentDeck 需要集成 CCSwitch。

### 6.2 接入策略

第一阶段：
- Claude Code 作为独立 Runtime 登记。
- 支持手动关联 Project / Task / 外部 Session。
- 优先使用官方 Remote Control 作为人的远程入口（前提是用户账号、地区、套餐和当前模型服务组合实际支持）。
- 使用 AgentDeck MCP Server 让 Claude Code 主动读取任务、汇报进度和登记交付物。
- 不读取或修改 CCSwitch 配置。

后续深度集成：
- 评估 Claude Agent SDK（Python / TypeScript）。
- SDK 验证必须在用户实际 Windows 环境中进行。
- 必须同时测试 Anthropic 官方 API 路径与用户实际采用的国内兼容 API 路径（如果用户授权且能提供非敏感的测试环境信息）。
- CLI 可用不等于 SDK 可用；CLI 与 SDK 的认证、配置加载、环境变量继承和运行方式可能不同。
- SDK 能否使用第三方兼容 API、是否能继承当前 CLI 的模型配置、工具/权限/MCP/Session 能否正常工作，都标记为 `unverified`，直到真实测试通过。
- 不把 Claude.ai 订阅登录态当成可供第三方产品代理或复用的凭证。
- 不绕过 Anthropic 服务条款或国内服务商的使用条款。

### 6.3 Claude SDK Spike 验证清单

**配置和认证**
1. SDK 是否支持用户当前 Claude Code 版本及 Windows 环境。
2. SDK 运行时是否读取当前进程环境变量。
3. `ANTHROPIC_BASE_URL`、模型变量和认证变量如何传入。
4. SDK 是否自动读取 Claude Code CLI 的 `settings.json` / 用户配置；若没有明确官方承诺，必须以实测为准。
5. CCSwitch 修改配置后，SDK 新进程是否能使用更新后的配置。
6. 是否需要为 SDK 单独构造受控环境变量；如果需要，不能复制密钥到 AgentDeck 数据库。

**Agent 能力**
1. 创建新 Session。
2. 继续/恢复 Session。
3. 发送 Prompt 并获取流式事件。
4. MCP Server 是否可正常加载。
5. Hooks / Subagents / 工具调用是否正常。
6. 权限请求是否能以受支持方式交给用户确认。
7. 取消、超时、进程退出后的状态是否可靠。
8. Artifact / 文件结果能否安全地登记回 AgentDeck。

**模型兼容性**
1. 基础文本对话。
2. 工具调用 / Tool Use。
3. 多轮上下文。
4. 长上下文。
5. 流式输出。
6. Subagents / 并行任务（如果 SDK 和模型服务支持）。
7. 结构化输出（如果使用）。
8. 错误码、限流、超时和重试。

每项测试记录 CLI / SDK 版本、Provider 类型、模型标识（非敏感）、测试时间、结果和限制。不能记录密钥。

### 6.4 Claude Code 最终能力声明规则

- Remote Control 可用：只代表用户可使用官方 Remote。
- MCP 可用：只代表 Agent 能与 AgentDeck 协作。
- SDK 能启动：只代表 SDK 在已测试环境可启动。
- SDK 能读取事件：只代表事件能力已验证。
- 国内兼容 API 测试通过：只对测试过的 Provider / Model / 版本组合有效。
- 不允许从一个国内模型服务商的成功结果推断所有兼容 API 都支持。

---

## 7. Codex 接入策略

### 7.1 Remote

优先复用 ChatGPT Mobile Remote Connections，让用户通过官方 ChatGPT 移动端连接 Windows / macOS Host。AgentDeck 提供项目与 Session 关联入口，不重造手机控制界面。

### 7.2 本地程序化控制

优先验证：
- 官方 Python Codex SDK；
- Codex App Server 的本地 stdio JSONL transport；
- Thread 创建、继续、恢复；
- Turn 启动、事件流、取消；
- 权限审批和人工输入。

App Server WebSocket 若官方文档标记为实验性，不作为第一版生产依赖。优先本地 stdio 子进程方式；跨机器访问使用受控 SSH / VPN，不直接暴露公网端口。

### 7.3 云端托管

Codex Agents API 作为 Hosted Agent 路线单独评估，不混同本地 Codex SDK / App Server。

---

## 8. OpenCode 与 Antigravity 接入策略

### 8.1 OpenCode

- 先复用用户现有 Remote / Mobile 使用方式。
- 公开 HTTP API / SDK 是深度控制候选。
- 先只读健康、Session 和事件，再验证 Prompt、Permission、Abort、Resume。
- 版本固定并建立契约测试。
- 不读写内部 SQLite，不使用未公开 RPC。

### 8.2 Antigravity

- 优先官方 Remote + MCP。
- 不把 Remote 推断为第三方程序化控制接口。
- 只有官方 SDK/API 的具体产品形态、Windows 支持、会话能力和安全边界经过验证后，才考虑 L2/L3。

---

# 第三部分：领域模型与数据边界

## 9. 领域模型

```text
Project
  ├── Task
  │    ├── Step
  │    ├── TaskRevision
  │    └── StepRevision
  ├── Session
  │    ├── RuntimeInstance
  │    └── ExternalSessionRef
  ├── Execution
  │    └── ExecutionAttempt
  ├── Decision
  ├── Artifact
  └── Event

RuntimeProvider
  └── RuntimeInstance
       ├── Machine
       ├── ConnectionProfile
       └── CapabilityManifest
```

注意：`RuntimeProvider` 在此表示 Agent Runtime 产品（例如 Claude Code、Codex），不要与模型服务商（例如 Anthropic 或国内模型 API）混淆。若未来确实需要描述模型服务环境，使用独立、可选、非敏感的 `ModelEndpointDescriptor`，不将其作为核心执行依赖。

## 10. 核心实体

### RuntimeProvider
- `id`
- `provider_key`：`claude_code` / `codex` / `antigravity` / `opencode`
- `display_name`
- `integration_type`：`remote_only` / `mcp_client` / `http_api` / `sdk` / `cli`
- `documentation_url`
- `enabled`

### Machine
- `id`
- `display_name`
- `os`
- `hostname_alias`
- `last_seen_at`
- `health_status`：`online` / `offline` / `unknown`
- `connection_kind`：`tailscale` / `lan` / `vendor_remote` / `local` / `other`

### RuntimeInstance
- `id`
- `provider_id`
- `machine_id`
- `display_name`
- `runtime_version`
- `connection_profile_id`
- `control_level`
- `health_status`
- `last_seen_at`

### ExternalSessionRef
- `id`
- `project_id`
- `runtime_instance_id`
- `platform_session_id`
- `external_session_id`
- `remote_entry_kind`
- `remote_entry_url`（不得包含认证 Token / 一次性凭证）
- `last_activity_at`
- `link_status`
- 可选关联 Task；Session 必须属于 Project。

### CapabilityManifest
- `capability`
- `support_status`
- `evidence`
- `runtime_version`
- `connector_version`
- `verified_at`
- `limitations`

### ModelEndpointDescriptor（可选、非核心）
仅在用户主动需要记录运行环境说明时使用：
- `provider_label`：用户可填写的显示名称。
- `model_label`：用户可填写的模型名称。
- `endpoint_kind`：`official` / `compatible_api` / `local_gateway` / `unknown`。
- `configuration_owner`：固定为 `user_environment`。
- `verified_status`：`unverified` / `verified_for_runtime`。
- 不保存 Base URL、API Key、Token、账号信息或配置文件副本，除非未来有独立且明确的安全需求与用户授权。

---

## 11. 状态与领域不变量

### 11.1 状态分层

至少区分：
- `platform_status`：AgentDeck 对 Task / Execution 的管理状态；
- `agent_reported_status`：Agent 自己报告的状态；
- `runtime_status`：Runtime 返回的状态；
- `connection_status`：Remote / Server 连接状态。

Remote 断开不代表 Agent 执行失败；Agent 报告完成不代表平台验收完成。

### 11.2 不变量

1. Session 必须属于 Project，Task 关联可空。
2. Session、Execution、Attempt 是三个独立实体。
3. 同一 Session 可关联多个 Execution。
4. Execution 可有多个 Attempt。
5. Step 修改只生成新 Revision，保留历史。
6. 下游标记 `needs_revalidation`，不自动重跑。
7. Agent 报告完成不等于平台验收通过。
8. Event 与业务状态同事务持久化。
9. Decision 必须持久化，服务重启后仍可见。
10. 跨 Project 引用必须由服务端校验。
11. 不支持或未知的 Runtime 字段标记 `unknown / unverified`，不伪造数据。

---

# 第四部分：UI 与安全

## 12. 前端产品形态

原 UI 规格书中的 Project、Task Tree、Task Detail、Decision、Artifact、Session 等主页面继续保留。

新增/调整 Agent 控制中心，展示：
- Agent / Runtime；
- 所属 Machine；
- Runtime 类型和版本；
- 在线/离线/未知；
- L0–L3 控制等级；
- 已验证能力；
- 当前活跃 Session；
- 最近活动；
- 关联 Project / Task；
- 打开官方 Remote；
- 仅在真实支持时显示创建、继续、取消、审批等操作。

Session 详情显示 Runtime、Machine、外部 Session ID（默认折叠）、能力限制和 Remote 入口。

手机端优先管理 Project / Task / Decision；点击 Remote 后进入厂商官方界面，不在 AgentDeck 中复制完整 Agent Chat / Terminal。

## 13. 安全边界

1. 不读取 CCSwitch 配置文件、数据库或凭证。
2. 不把 API Key、OAuth Token、密码或完整环境变量保存到 AgentDeck。
3. Remote URL 若含一次性凭证，默认不保存、不写日志。
4. Runtime API 凭证若未来确需保存，必须独立加密、最小权限、支持撤销轮换。
5. MCP Token 绑定 Project 和最小权限。
6. 高风险控制动作记录审计。
7. 不支持由 AgentDeck 强制中断时，UI 必须明确说明。
8. 不读取 Runtime 私有数据库。
9. 不通过私有 RPC、逆向协议或 UI 自动化实现核心控制。
10. 对外监听端口必须有认证、来源限制和明确网络边界。
11. 不把敏感 Prompt、Token、Secret 放入 Event 或普通日志。
12. 国内模型服务的使用须遵守对应服务商的条款；AgentDeck 不负责规避地区、订阅或认证限制。

---

# 第五部分：技术栈与开发顺序

## 14. 技术栈

保留既有技术栈，不因接入策略改变而重写语言：
- Backend：Python 3.12+、FastAPI、Pydantic v2、SQLAlchemy 2.x、Alembic。
- Frontend：TypeScript、React、Vite。
- Database：SQLite WAL。
- 前后端：REST API。
- Agent → AgentDeck：MCP Server，排在核心管理模型之后。
- AgentDeck → Runtime：官方 HTTP API / SDK / CLI Connector，按需接入。
- Worker：仅在需要托管 Runtime 时引入。
- 实时更新：第一版优先 REST + SSE，不预先引入复杂消息基础设施。
- 不引入 PostgreSQL、Redis、Kafka、微服务和通用工作流引擎，除非真实负载和需求证明必要。

## 15. 分阶段开发计划

### Phase 0：仓库审查与架构差异报告
- 检查分支、HEAD、工作区、依赖、前后端、数据库、Runtime、MCP。
- 映射现有代码与 v1.3 目标设计。
- 输出 `docs/implementation/phase-0-repository-audit.md`。
- 只读审查，不安装依赖、不迁移、不改业务代码、不提交、不推送。
- 若当前在 main，停止并询问用户，不自行创建或切换分支。

**验收：** 真实目录图、模块映射、复用清单、风险、Phase 1 文件级计划。完成后停止等待验收。

### Phase 1：基础工程与核心领域模型
- Project / Task / Step / Dependency。
- TaskRevision / StepRevision。
- Session / Event。
- RuntimeProvider / Machine / RuntimeInstance 的最小登记模型。
- 状态字典、跨 Project 校验。
- Windows 本地运行和数据库迁移基线。

**验收：** 不启动任何 Agent Runtime，也能独立管理项目和任务；领域不变量测试通过。

### Phase 2：REST API 与项目管理 UI
- Project / Task / Step / Revision API。
- Session 登记和查询。
- Runtime / Machine / RuntimeInstance 登记。
- 工作总览、项目列表、任务树、任务详情、Session 页面。
- 移动端和桌面端适配。

**验收：** 用户能独立管理项目、任务和 Session；修改 Step 生成新 Revision。

### Phase 3：Remote Registry 与统一入口（L0/L1）
- 手动登记外部 Runtime 和 Session。
- 显示连接状态、最后活动时间和关联 Project / Task。
- 配置官方 Remote 入口。
- “打开 Remote”动作。
- 无稳定深链时使用明确配置入口，不猜 URL。

**验收：** 从 Project / Task 找到相关 Session 并打开官方 Remote；Remote 断开不误判 Execution 失败。

### Phase 4：MCP Server（Agent → AgentDeck）
- `project_context_get`、`task_get`、`task_report_progress`、`step_update`。
- `checkpoint_request`、`decision_get`、`artifact_register`、`task_revision_get`。
- Project 级 Token、最小权限、幂等、审计、版本冲突检测。
- 至少一个真实 Agent 客户端完成 E2E。

**验收：** Agent 能读取任务、汇报进度、请求 Decision、登记 Artifact；用户能在 AgentDeck 处理 Decision。

### Phase 5：Decision、Event、Execution / Attempt
- Decision 持久化。
- Event 增量查询、断线补拉和幂等。
- Execution / Attempt 记录外部 Agent 执行事实。
- 记录 Task / Step Revision、Runtime / Session 关联。
- 平台状态、Agent 状态、Runtime 状态、连接状态分离。

**验收：** 服务重启后待决策不丢失；历史执行不可覆盖；Remote 断开不自动标记失败。

### Phase 6：Runtime 官方能力 Spike（不先写生产 Connector）
建立独立验证报告和能力矩阵。至少覆盖：
- Claude Code + 用户当前 Windows / CCSwitch 国内模型配置环境；
- Codex Python SDK / App Server stdio；
- OpenCode HTTP API；
- Antigravity Remote / MCP 边界。

Claude Code 必须按第 6.3 节验证 SDK 配置继承、认证、国内兼容 API、Session、工具、MCP、审批、事件和取消。不得把 CCSwitch 集成到 AgentDeck。

**交付物：**
- `docs/architecture/runtime-capability-matrix.md`
- `docs/architecture/claude-code-sdk-spike.md`
- `docs/architecture/codex-sdk-spike.md`
- `docs/architecture/opencode-api-spike.md`

**验收：** 每个能力有官方依据或真实测试证据；未知项保持 `unverified`；没有通过的能力不进入生产 UI。

### Phase 7：第一个深度控制 Connector
候选优先级：
1. Codex Python SDK / App Server stdio；
2. OpenCode HTTP API；
3. Claude Agent SDK（取决于用户实际国内模型配置和 SDK 验证结果）。

不要仅凭技术热度决定，按 Phase 6 的真实验证结果选出第一个最稳定、最有价值的 Runtime。

先只读，再写操作：
- Health Check；
- Session 查询；
- 状态/事件读取；
- 创建 Session；
- 发送任务；
- Cancel / Abort；
- Permission / Resume（仅在官方支持且测试通过时）。

**验收：** 至少一条真实任务从 AgentDeck 发起并完成；能力矩阵、契约测试和审计完整。

### Phase 8：第二 Runtime Connector 与能力扩展
- 复用统一 Connector Contract Tests。
- 允许 Runtime 特定能力存在差异。
- 不将厂商特定字段泄漏到核心领域层。
- Antigravity 若没有公开稳定控制接口，继续保持 Remote + MCP，不强行深度接入。

### Phase 9：Hosted Agent / Worker（可选）
仅当产品确实需要 AgentDeck 托管 Agent 时再引入：
- Hosted Agent API；
- Worker Supervisor；
- 进程隔离、超时、崩溃恢复；
- 工作目录、环境变量白名单；
- Secret 注入和日志脱敏；
- 防止重试造成重复副作用。

如果 Runtime 已经是稳定常驻服务，优先连接其官方 API，不额外包 Worker。

### Phase 10：Artifact、Revision 影响分析与完整工作台
- Artifact 元数据、校验值、来源和版本。
- 路径 allowlist / symlink 检查。
- Revision 下游影响分析。
- 完整 Agent 控制中心、Decision 收件箱、活动记录、Runtime 设置。
- L0/L1/L2/L3 UI 只显示已验证能力。

### Phase 11：安全加固、发布与运维
- MCP Token 最小权限。
- Runtime 凭证安全（如存在）。
- Remote URL 脱敏。
- CORS / Origin / SSE / WebSocket 安全。
- Project 文件访问隔离。
- SQLite Backup API 和恢复演练。
- Windows 安装升级、Linux/macOS 核心兼容。
- 诊断日志、版本兼容矩阵和故障排查。

---

# 第六部分：验收标准与旧方案修订

## 16. 产品完成标准

1. 用户可以管理 Project、Task、Step 和版本。
2. 一个 Project 可以关联多个不同 Runtime 的 Session。
3. 用户可以从 AgentDeck 找到相关 Agent 并打开官方 Remote。
4. Agent 可以通过 MCP 读取任务、汇报进度、请求决策和登记交付物。
5. 用户能统一查看待决策、执行历史和 Artifact。
6. 对具备公开 API 的 Runtime，逐步实现真实的启动、查询、继续和取消控制。
7. 每个 Runtime 能力都有 supported / unsupported / unverified 证据状态。
8. Task / Step Revision、Execution / Attempt、Decision 和 Artifact 可追溯。
9. 上游修改只标记下游复核，不自动重跑。
10. 模型供应商变化不影响 AgentDeck 核心项目和任务数据。
11. 用户现有的 Agent 使用习惯不需要为了 AgentDeck 而改变。
12. 不把 AgentDeck 变成另一个 Agent IDE、模型切换器或远程终端。

## 17. 相对 v1.2 的主要变更

| v1.2 | v1.3 |
|---|---|
| Runtime / Provider 命名容易与模型服务商混淆 | 明确 Runtime Provider 指 Agent 产品；模型服务商单独处理 |
| Claude Code 以 Remote + MCP 为主，SDK 后续评估 | 增加用户真实 Windows + CCSwitch 国内模型环境下的 SDK Spike |
| 没有明确描述本地模型配置工具边界 | 明确不集成、不读取、不修改 CCSwitch |
| 容易从 CLI 可用推断 SDK 可用 | 明确 CLI / SDK 配置和认证必须独立验证 |
| 深度 Connector 候选主要按产品类型预排 | 先做 Spike，再按真实兼容性和价值选择第一个 Connector |
| Model Provider 关系不够明确 | 模型服务商不是 AgentDeck 核心控制对象，不影响 Runtime 领域模型 |

## 18. 给 Antigravity 的执行约束

```text
你负责 AgentDeck 的仓库审查和后续开发。

当前架构基线为：
《AgentDeck 架构与分阶段开发方案 v1.3》。

产品定位：
AgentDeck 是跨项目、跨 Agent 的工作管理与统一控制平台。
Agent 自己负责推理、模型调用、工具执行和代码修改。
AgentDeck 负责项目、任务、版本、决策、执行记录、交付物和统一管理。

不可违反的原则：
1. 官方功能优先，非必要不重复造轮子。
2. 不自建 Agent Loop / Harness。
3. 不重造厂商 Remote、Agent Chat、Terminal 或 IDE。
4. Remote、MCP、REST API、Runtime SDK/API 职责严格区分。
5. MCP 是 Agent -> AgentDeck 协作接口，不是 AgentDeck 控制 Runtime 的协议。
6. 只使用公开、受支持的官方 API / SDK / CLI；禁止抓包、私有 RPC、内部数据库。
7. 不为统一而统一；Runtime 能力按 supported / unsupported / unverified 声明。
8. AgentDeck 不集成 CCSwitch，不读取或修改 CCSwitch 配置、数据库或密钥。
9. 用户可继续通过 CCSwitch 管理 Claude Code 模型配置；Claude Code 仍登记为 Claude Code Runtime。
10. Claude Agent SDK 对用户当前国内兼容 API 配置的兼容性必须实测；CLI 可用不代表 SDK 可用。
11. 不把 Claude.ai 订阅登录态作为第三方应用可代理复用的凭证。
12. 模型服务商变化不得破坏 Project / Task / Session 核心数据模型。
13. Session 必须属于 Project，Task 关联可空。
14. Session / Execution / Attempt 是独立实体。
15. Step 修改只生成新 Revision，下游标记 needs_revalidation，不自动重跑。
16. Agent 报告完成不等于平台验收。
17. 不保存 Remote 一次性 URL、Token、API Key 或密码。

Git 安全：
- 只在用户明确指定的功能分支开发，绝不直接修改或提交到 main。
- 首先执行 git status --short --branch、git branch --show-current、git log -1 --oneline。
- 如果当前在 main，停止并询问用户，不自行创建或切换分支。
- 不擅自合并 PR、推送 main、删除分支或重写历史。
- 不删除或静默迁移用户数据。
- 不把文档目标误认为仓库现状。

执行顺序：
- 当前只执行 Phase 0：仓库审查与架构差异报告。
- 不安装依赖，不修改业务代码，不迁移数据库，不提交，不推送。
- 输出 docs/implementation/phase-0-repository-audit.md。
- 报告中必须列出 v1.3 与真实仓库的差异、可复用代码、需要修改/新增/弃用的模块，以及 Claude Code SDK 的验证计划。
- 完成后停止，等待用户验收，不开始 Phase 1。
```

---

# 第七部分：官方参考资料与验证边界

以下为技术调研入口，实际接入前必须复核当前版本：

- Claude Code Remote Control：<https://code.claude.com/docs/en/remote-control>
- Claude Agent SDK：<https://code.claude.com/docs/en/agent-sdk/overview>
- Claude Code Settings：<https://code.claude.com/docs/en/settings>
- Codex Remote Connections：<https://learn.chatgpt.com/docs/remote-connections>
- Codex App Server：<https://learn.chatgpt.com/docs/app-server>
- Codex SDK：<https://learn.chatgpt.com/docs/codex-sdk>
- Codex Agents API：<https://developers.openai.com/api/docs/guides/agents-api/overview>
- OpenCode Server：<https://docs.opencode.ai/docs/server/>
- CCSwitch 文档（仅作为用户本机环境背景，不属于 AgentDeck 集成依赖）：<https://ccswitch.io/en/docs>

重要说明：
- 本文件是架构和实施建议，不代表 GitHub 仓库已被修改。
- 不代表任何 SDK / API 已在用户环境完成验证。
- 用户实际账号、地区、Runtime 版本、模型服务商和组织策略可能影响可用性。
- Phase 0 完成并经用户验收后，才允许进入后续阶段。
