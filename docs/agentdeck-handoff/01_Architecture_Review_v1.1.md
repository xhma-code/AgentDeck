# AgentDeck 最终架构复核与重构决策书 v1.1

-   日期：2026-10-31
-   范围：产品定位、Antigravity
    SDK/MCP、后端语言、Connector、领域模型、前端、DX、安全和实施路径
-   旧版兼容：不要求兼容旧数据库、API、前端或 Adapter
-   评审方式：参考 gstack `/autoplan` 的 CEO → Design → DX → Eng
    顺序进行方案级独立复核
-   状态：可进入架构冻结；真实 Runtime 集成尚未验证

------------------------------------------------------------------------

# 0. 最终结论

## 0.1 产品定位

**AgentDeck 是 Agent 协作管理平台，不是 Agent Runtime、Agent Harness
或工作流引擎。**

AgentDeck 负责： - Project、Task、Step 和任务版本； - Session
的项目归属和任务关联； - Execution、Attempt、状态、历史和结果； -
人工决策、权限审批和检查点； - Agent 进度、计划和交付物的结构化记录； -
任务修改后的下游影响分析； - 多个 Agent 之间的项目上下文连续性。

Agent Runtime 负责： - 模型推理和 Agent Loop； - 规划和任务拆解； -
工具选择与执行； - Runtime 内部上下文； - Runtime 自身的子 Agent； -
Runtime 内部权限策略。

**平台管理协作，不接管 Agent 的大脑。**

## 0.2 Antigravity 最终选择

  -----------------------------------------------------------------------------------------------
  能力                  定位                                                               优先级
  --------------------- -------------------------------------------- ----------------------------
  AgentDeck MCP Server  对外提供项目、任务、汇报、决策和交付物工具                             P0

  Antigravity IDE/CLI + 外部 Agent 使用 AgentDeck 的协作能力                                   P0
  AgentDeck MCP                                                      

  Antigravity SDK       可选的自托管 Agent Runtime Connector                                   P2

  Antigravity Managed   独立云端 Runtime Connector                                             P2
  Agent API                                                          

  Antigravity 私有      实验性插件，不进入核心                                         不纳入核心
  Language Server RPC                                                
  -----------------------------------------------------------------------------------------------

**对于当前产品定位，MCP 比 SDK 更适合作为 Antigravity 的首选集成方式。**

SDK 和 MCP 不是同一层： - SDK 是 Agent Runtime/Harness，负责 Agent
Loop、工具执行、上下文和安全策略； - MCP 是 Agent
与工具/上下文服务之间的协议； - Antigravity SDK 本身支持连接 MCP
Server。

关键限制：Antigravity 通过 MCP 调用 AgentDeck 工具，不代表 AgentDeck
能通过 MCP 控制 Antigravity IDE 的完整 Session 生命周期。MCP 不是
Session Control API。

## 0.3 后端语言最终选择

**后端采用 Python，前端采用 TypeScript。**

推荐： - Backend：Python 3.12+、FastAPI、Pydantic v2、SQLAlchemy
2.x、Alembic； - Frontend：TypeScript、React、Vite； - Database：SQLite
WAL； - Runtime：官方 SDK/API、JSON-RPC
stdio、HTTP/SSE、ACP，按厂商分别实现； - Worker：Python 独立子进程，受控
stdio JSONL IPC。

不选 Go 或 TypeScript 作为后端的理由： 1. Antigravity SDK 当前是 Python
SDK； 2. Python 的 AI Agent、MCP、ACP 和厂商 SDK 生态更直接； 3.
系统瓶颈主要是外部 Agent 执行、网络等待和进程管理，不是高吞吐 CPU 计算；
4. FastAPI async IO 足以满足单用户自托管场景； 5. 改用 Go/TypeScript
会增加跨语言 SDK 适配成本，没有解决核心风险。

不是说 Python 理论性能高于 Go/Node，而是当前工作负载不值得为理论吞吐牺牲
SDK 生态和开发效率。

------------------------------------------------------------------------

# 1. 证据与官方资料

Antigravity SDK 官方资料说明： - SDK 是 Python SDK； - 提供 Agent
Loop、工具执行、上下文管理、安全策略和子 Agent； - 支持自定义 Python
callable、MCP Server、Skills、Hooks； - 支持多轮会话持久化； - 依赖平台
wheel 内的编译 Runtime Binary； - 当前处于 Research Preview； -
TypeScript 和 Go 是路线图方向，不能当成当前稳定 SDK。

官方来源： - https://www.antigravity.google/docs/sdk/overview/ -
https://www.antigravity.google/product/antigravity-sdk -
https://antigravity.google/blog/introducing-google-antigravity-sdk -
https://github.com/google-antigravity/antigravity-sdk-python

FastAPI 官方文档说明 async IO 适合等待型网络工作；CPU
密集任务应使用进程并行或独立 Worker： -
https://fastapi.tiangolo.com/async/ -
https://fastapi.tiangolo.com/deployment/concepts/

------------------------------------------------------------------------

# 2. CEO Review：战略和范围

## 2.1 核心前提挑战

### 前提 A：平台需要接入很多 Agent

真正价值不是 Connector 数量，而是： - 同一 Project 内可以使用多个
Agent； - Task/Step 有明确版本； - 执行过程可追踪； -
关键节点有人类决策； - 产物可归档； - 上游修改后能识别下游结果是否过期。

第一版不应追求支持十几个 Agent。优先打通一个 MCP 协作链路和一个托管
Runtime。

### 前提 B：所有 Agent 都必须由 AgentDeck 启动

不成立。应区分三种模式：

**外部 Agent 协作模式** - 用户在 Antigravity、Claude Code、Codex
等客户端启动 Agent； - Agent 通过 MCP 使用 AgentDeck 工具； - AgentDeck
管理项目上下文、任务、决策、汇报和产物； - AgentDeck 不负责外部 Agent
的完整进程生命周期。

这是最匹配当前定位的模式。

**平台托管 Runtime 模式** - 用户在 AgentDeck 选择 Runtime； - AgentDeck
启动 Connector/Worker； - 平台管理 Session、Execution、事件和审批； -
厂商 SDK/API 管理 Agent 内部执行。

**模型 API 模式** - 平台直接调用模型 API； - 自己实现 Agent
Loop、工具执行、上下文和策略。

第一版不做第三种。否则 AgentDeck 会变成 Agent Harness。

### 前提 C：统一 Connector 就能统一所有 Agent

不成立。统一的是平台领域接口，不是底层协议。必须允许能力缺失，区分： -
外部 Agent Client； - 托管 Agent Runtime； - Model Provider； - MCP
Server； - ACP Client； - 私有/实验性 Adapter。

## 2.2 10 倍产品价值

核心应是跨 Agent 的工作连续性： 1. 一个 Project 使用多个 Agent； 2. 讨论
Session 可以不关联 Task； 3. Task 和 Step 有版本； 4. Execution
记录实际使用的版本； 5. Agent 的计划和进度可结构化汇报； 6.
用户在关键节点介入； 7. 下游步骤识别上游变更； 8. 新 Agent
可以根据平台保留的上下文、决策、产物和历史接手工作。

## 2.3 第一版明确不做

-   自动寻找最优 Agent；
-   自动多 Agent 分工和动态调度；
-   多 Agent 自主辩论；
-   通用 Agent Loop；
-   模型路由/评测平台；
-   多租户企业 RBAC；
-   分布式执行集群；
-   插件市场；
-   对所有 Agent 承诺强制暂停/恢复。

**CEO 结论：保留产品方向，缩小第一阶段 Runtime 范围，把 MCP
和协作数据模型放在前面。**

------------------------------------------------------------------------

# 3. Antigravity SDK vs MCP：最终判断

## 3.1 四个概念

  --------------------------------------------------------------------------------------
  概念                    职责                                   AgentDeck 是否实现
  ----------------------- -------------------------------------- -----------------------
  Agent Runtime/Harness   推理循环、工具执行、上下文和生命周期   不自建，通过 Connector
                                                                 使用外部 Runtime

  MCP Server              向 Agent 暴露工具、资源和提示          是，核心集成面

  MCP Client              让 Agent 调用外部 MCP 工具             由 Runtime
                                                                 提供，AgentDeck 配置

  Agent Connector         把 Runtime 能力映射到 AgentDeck        是
                          领域模型                               
  --------------------------------------------------------------------------------------

## 3.2 为什么 MCP 更匹配

用户希望继续使用熟悉的 Agent，而不是被迫改用 AgentDeck 内置 Agent。

MCP 的优势： - 不接管 Agent Loop； -
Antigravity、Claude、OpenCode、Codex 等可使用同一套 AgentDeck 工具； -
项目管理工具与模型厂商解耦； - 不必为每个 Agent 重写业务工具； -
平台统一鉴权、审计和作用域。

MCP 的局限： - 不提供通用 Agent Session 管理； - 不管理 Runtime 进程； -
不保证 Agent 主动汇报； - 不保证强制暂停； - 不等于完整权限控制； -
不等于执行状态协议。

因此 MCP 是协作入口，不是 Runtime 生命周期的替代品。

## 3.3 SDK 何时值得使用

当用户明确需要 AgentDeck 托管 Antigravity Agent 执行时才启用 SDK：

``` text
用户创建 Execution
      ↓
AgentDeck 启动 Antigravity SDK Worker
      ↓
Worker 创建 SDK Agent
      ↓
Agent 执行
      ↓
Worker 转换 Runtime 事件
      ↓
Agent 通过 MCP/受控工具汇报
      ↓
平台 Decision 处理审批
      ↓
Execution 完成并登记 Artifact
```

这是一种可选托管模式，不是默认使用方式。

## 3.4 推荐结构

``` text
                 AgentDeck
       ┌─────────────────────────────┐
       │ Project / Task / Step       │
       │ Session / Execution         │
       │ Decision / Artifact         │
       │ MCP Server                  │
       └──────────────┬──────────────┘
                      │ MCP
       ┌──────────────┼──────────────────┐
       ▼              ▼                  ▼
 Antigravity IDE   Claude Code       Other Agent

可选托管执行：
AgentDeck Execution Service
          ↓
  Antigravity SDK Worker
          ↓
  Google Antigravity Runtime
```

------------------------------------------------------------------------

# 4. Backend Language Review

## 4.1 评估标准

优先级： 1. Agent SDK 接入； 2. 异步网络和事件流； 3. 子进程与 IPC； 4.
SQLite 生态； 5. 跨平台； 6. 可维护性； 7. 性能； 8. 前后端类型共享。

## 4.2 语言比较

  -----------------------------------------------------------------------
  维度              Python            TypeScript/Node   Go
  ----------------- ----------------- ----------------- -----------------
  Antigravity SDK   直接支持          当前未交付官方    当前未交付官方
  当前支持                            SDK               SDK

  AI SDK 生态       强                强                相对少

  异步网络          asyncio/AnyIO     原生事件循环      goroutine

  进程/stdio        成熟              成熟              成熟

  SQLite            成熟              成熟              成熟

  类型共享          OpenAPI 生成      最方便            代码生成

  当前综合适配      最优              次选              次选
  -----------------------------------------------------------------------

## 4.3 Python 工程约束

-   API 主进程只负责 API、鉴权、持久化和事件协调；
-   Agent Runtime 在独立 Worker；
-   CPU 密集任务不能阻塞事件循环；
-   网络调用采用 async client；
-   阻塞 SDK 放入独立进程，优先于线程池；
-   Worker 通过受控 IPC；
-   限制并发 Worker 数；
-   记录退出码、版本、日志和启动参数；
-   Windows/Linux/macOS 做真实测试。

## 4.4 最终技术栈

Backend： - Python 3.12+（经依赖验证后锁定 minor 版本） - FastAPI -
Pydantic v2 - SQLAlchemy 2.x - Alembic - SQLite WAL - httpx - asyncio /
AnyIO

Frontend： - TypeScript - React - Vite - TanStack Query -
Zustand（仅轻量 UI 状态）

不引入： -
PostgreSQL、Redis、Kafka、Celery、Kubernetes、微服务、第二套后端语言。

------------------------------------------------------------------------

# 5. Eng Review：核心架构修订

## 5.1 领域模型

``` text
Project
  ├── Session
  ├── Task
  │    ├── Step
  │    ├── TaskRevision
  │    └── StepRevision
  ├── Execution
  │    └── ExecutionAttempt
  ├── Decision
  ├── Artifact
  └── Event
```

约束： - Session 必须属于 Project； - Session 可不关联 Task； - Task
属于 Project； - Step 属于 Task； - Execution 属于 Project，可选关联
Session/Task/Step； - Attempt 属于 Execution； - Decision 属于
Project，并关联触发它的 Execution/Session； - Artifact 属于
Project，可选关联 Task/Step/Execution； - 跨实体引用必须验证 Project
一致性。

## 5.2 Session / Execution / Attempt 分离

-   Session：Agent 持续对话上下文身份；
-   Execution：平台要求 Agent 完成的一次工作请求；
-   Attempt：一次实际启动或恢复 Runtime 的执行尝试。

一个 Session 可承载多个 Execution。 一个 Execution 可有多个 Attempt。
一个 Attempt 不等于一个 Agent Turn。

## 5.3 Revision 规则简化

上一版将 Task Revision、Step Revision、Execution Snapshot
都做成完整快照，容易重复。

建议： 1. Task 有独立 revision； 2. Step 有独立 revision； 3. Execution
创建时记录 Task revision 和 Step revision； 4. 保存必要的上游 revision
引用集合； 5. 只在确实需要复现时保存执行输入快照； 6. 不复制整个 Project
内容。

Execution 必须能回答： - 执行时 Task/Step 是哪个版本？ -
使用了哪些上游版本？ - 执行输入是什么？ - 产物是什么？

## 5.4 Agent 状态与平台状态分离

Agent 说 Step completed，不等于平台接受完成。

应记录： - `agent_reported_status` - `platform_status`

平台状态示例：

``` text
pending
ready
in_progress
blocked
needs_review
completed
failed
needs_revalidation
cancelled
```

完成状态由平台规则、Artifact 和人工验收决定。

## 5.5 事件存储：不要过度 Event Sourcing

第一版： - 业务表保存当前状态； - events 表追加执行/审计事件； -
关键业务状态与事件插入同一事务； - SSE/WebSocket 通知前端； - 断线后按
last_event_id 拉增量； - 重复事件幂等处理。

第一版不强制 Outbox + 多层
Dispatcher。只要提交后通知且支持增量拉取恢复，单用户系统足够。出现多进程广播可靠性问题再升级。

## 5.6 Worker：简单但隔离

``` text
FastAPI Main Process
  ├── API / Application Services
  ├── SQLite
  ├── Connector Registry
  └── Worker Supervisor
         ├── Worker A
         ├── Worker B
         └── Worker C
```

第一版 IPC 统一 JSONL over stdio： - Main 启动 Worker； - stdin
发送命令； - stdout 发送结构化消息； - stderr 仅诊断日志； - 消息带
request_id、execution_id、schema_version； - stdout 不允许普通 print； -
Worker 崩溃由 Supervisor 处理； - 工作目录和环境变量显式设置。

不要同时实现 Named Pipe、Unix Socket、HTTP IPC 三套传输。

## 5.7 SQLite 约束

-   WAL；
-   外键约束开启；
-   短事务；
-   避免长时间持锁；
-   高频事件必要时批量写入；
-   备份使用 SQLite Backup API；
-   不复制主库/WAL/SHM 文件冒充一致性备份；
-   Alembic 管理迁移；
-   不把 SQLite 当成分布式高并发数据库。

## 5.8 Connector 接口

统一平台接口，不统一底层协议。

``` python
class AgentConnector(Protocol):
    async def initialize(self) -> None: ...
    async def health_check(self) -> HealthStatus: ...
    async def capabilities(self) -> CapabilityManifest: ...
    async def create_session(self, request) -> SessionInfo: ...
    async def resume_session(self, request) -> SessionInfo: ...
    async def start_execution(self, request) -> ExecutionHandle: ...
    async def stream_events(self, handle) -> AsyncIterator[AgentEvent]: ...
    async def cancel_execution(self, handle) -> CancelResult: ...
    async def get_execution_status(self, handle) -> ExecutionStatus: ...
    async def shutdown(self) -> None: ...
```

外部 Agent MCP 模式不一定支持 create/resume/cancel。能力缺失必须明确返回
unsupported。

Capability 不只是一组 bool，应包含： - capability； -
support_status：supported / unsupported / unverified； -
evidence：official_docs / integration_test / inferred； -
runtime_version； - connector_version； - verified_at； - limitations。

------------------------------------------------------------------------

# 6. Runtime 优先级

## P0：AgentDeck MCP Server

第一批工具： 1. `project_context_get` 2. `task_get` 3.
`task_report_progress` 4. `step_update` 5. `checkpoint_request` 6.
`decision_get` 7. `artifact_register` 8. `task_revision_get`

## P0：Antigravity 真实 MCP 协作验收

端到端流程： 1. 创建 Project； 2. 创建 Task； 3. Antigravity 通过 MCP
读取任务； 4. Agent 执行； 5. 汇报进度； 6. 请求 checkpoint； 7.
用户批准； 8. Agent 继续； 9. 登记 Artifact； 10. AgentDeck
展示状态和历史。

## P1：一个托管 Runtime

Claude 或 OpenCode 二选一，按用户实际使用优先级决定。完成真实
Session、Streaming、Permission、Cancel、Resume 能力验证。

## P1：第二个 Runtime

证明 Connector 抽象真正可扩展，而不是只为第一个 Agent 定制。

## P2：Codex App Server

JSON-RPC
stdio，固定版本，注意官方实验性限制，契约测试通过后再纳入稳定能力矩阵。

## P2：Antigravity SDK Runtime

只有托管 Antigravity Agent 需求明确后再实施。

## P2：ACP v1

只支持具备 ACP 能力的 Runtime，不强制所有厂商使用。

## P3：Antigravity Managed Agent

独立云端执行能力，先解决文件同步、数据传输、费用、权限和数据驻留。

------------------------------------------------------------------------

# 7. Design Review：前端体验

## 7.1 页面结构

``` text
Project
├── Overview
├── Tasks
├── Sessions
├── Decisions
├── Artifacts
└── Settings
```

主界面以 Task Tree 为中心，展示： - 标题； - 状态； - 当前
Agent/负责人； - 是否待决策； - 是否需重新验证； - 最近更新时间。

不要把底层工具调用和完整 Event Stream 放主界面。

## 7.2 Task Tree 与 Chat

任务树是工作管理入口，Chat 是 Agent 协作入口。

Project 内允许自由讨论和探索，不强制每个 Session 关联 Task。

## 7.3 必须定义的 UI 状态

-   首次加载；
-   空状态；
-   错误状态；
-   无权限；
-   离线；
-   数据过期；
-   实时连接断开；
-   部分加载；
-   待人工决策；
-   Execution 状态未知；
-   Agent Runtime 不可用。

## 7.4 移动端

移动端重点： - 查看任务状态； - 处理 Decision； - 查看 Agent 汇报； -
查看 Artifact； - 发送简短指令。

复杂任务编辑和 Runtime 配置以桌面端为主。

## 7.5 方案评分（评审估分）

  维度         v1.0   目标 主要问题
  ---------- ------ ------ ------------------------------------
  信息层级        6      9 主任务与会话视图需更清楚
  核心流程        6      9 外部 Agent 与托管 Runtime 未区分
  状态反馈        6      9 缺少未知/断连/部分失败状态
  错误恢复        5      9 恢复和重试 UX 不清楚
  移动体验        5      8 PWA 交互策略不足
  可访问性        4      8 键盘、焦点、对比度和触控目标未定义
  一致性          6      9 状态命名需统一

以上为方案评分，不是现有 UI 实测结果。

------------------------------------------------------------------------

# 8. DX Review：开发者体验

AgentDeck 本身是开发者工具，DX 是必审范围。

## 8.1 首次运行目标

新开发者从克隆到看到可用界面目标不超过 15 分钟。

必须提供： - Python/Node 版本； - 一键安装脚本； - `.env.example`； -
SQLite 自动初始化； - Demo Project； - Mock Connector； - E2E smoke
test； - Windows PowerShell 指引； - Linux/macOS 指引。

## 8.2 Mock Connector 先于真实 Runtime

不能要求开发者必须有 Google/Anthropic/OpenAI API Key 才能启动项目。

Mock Connector 用于 UI、API、状态机、事件、Decision 和故障测试。

真实 Runtime 测试独立运行。

## 8.3 Connector 开发体验

新增 Connector 只需要实现： - 官方 SDK/API 封装； - Capability
Manifest； - 事件映射； - 权限/取消适配； - 契约测试。

不能让每个 Connector 重写 Session、Execution、Decision 和数据库逻辑。

## 8.4 版本矩阵

  ----------------------------------------------------------------------------------
  Connector     Runtime    Session    Streaming   Permission   Cancel     验证日期
                Version    Resume                                         
  ------------- ---------- ---------- ----------- ------------ ---------- ----------
  Antigravity   实测       N/A/未知   实测        实测         N/A/未知   实测填写
  MCP Client                                                              

  Claude        实测       实测       实测        实测         实测       实测填写

  OpenCode      实测       实测       实测        实测         实测       实测填写

  Codex         实测       实测       实测        实测         实测       实测填写
  ----------------------------------------------------------------------------------

文档声称的能力不能代替实测能力。

## 8.5 DX 方案评分

  维度               v1.0   目标
  ---------------- ------ ------
  安装体验              5      9
  首次运行时间          5      9
  Mock/测试体验         4      9
  Connector 扩展        6      9
  文档可执行性          7      9
  错误诊断              5      9
  跨平台一致性          5      8
  版本升级              4      8

------------------------------------------------------------------------

# 9. 安全与失败模式

## 9.1 高风险控制

### MCP Token 越权

-   token 绑定 Project/Session/Execution；
-   短期有效、最小权限、可撤销；
-   每次调用审计；
-   不能只信任参数里的 project_id。

### Project 外文件访问

-   工作目录 allowlist；
-   路径规范化；
-   symlink 检查；
-   Windows UNC/盘符处理；
-   禁止任意绝对路径；
-   MCP 业务工具与文件工具分离。

### Agent 虚假完成

-   agent_reported_status 与 platform_status 分离；
-   检查 Artifact 和验收规则；
-   关键步骤需要人工 Review；
-   不凭自然语言自动完成业务任务。

### Worker 崩溃

-   Attempt 状态持久化；
-   记录退出码；
-   心跳/健康检查；
-   重启后核实 Runtime；
-   不确定时标记 unknown。

### 审批丢失

-   Decision 先落库；
-   启动时恢复 pending；
-   Runtime 回调恢复单独验证；
-   无法恢复时要求重新发起/恢复，不伪装审批仍有效。

### SDK/协议升级

-   固定版本；
-   Connector 契约测试；
-   Runtime smoke test；
-   Preview SDK 禁止自动升级。

## 9.2 失败模式表

  失败模式          影响               检测             恢复
  ----------------- ------------------ ---------------- -----------------------
  MCP Client 断连   Agent 无法汇报     超时             重连、幂等重试
  Agent 未汇报      状态滞后           长时间无事件     标记 stale、人工核实
  Runtime 崩溃      Execution 中断     Worker exit      新 Attempt 或 unknown
  不支持 cancel     用户误以为停止     Capability       显示 unsupported
  服务重启          审批 UI 丢失       pending 查询     数据库恢复
  事件重复          状态重复变化       event_id 约束    幂等丢弃
  事件乱序          UI 不一致          sequence 检查    拉增量事件
  SQLite 锁冲突     写入失败           DB 错误          短事务、有限重试
  路径越界          数据泄露           路径校验         拒绝并审计
  协议不兼容        Connector 不可用   handshake/test   禁止启动并提示

------------------------------------------------------------------------

# 10. REST API 与 MCP 边界

REST API 服务于平台 UI 和管理： - Project CRUD； - Task/Step CRUD； -
Revision； - Session； - Execution； - Decision； - Artifact； - Runtime
配置； - Event 查询。

MCP 只暴露 Agent 真正需要的协作工具，不把整个 REST API 全部映射成
Tools。

每个 MCP Tool 必须： - 有 JSON Schema； - 有 Project 作用域； -
有执行上下文； - 有幂等键； - 有结构化结果； - 有错误码； -
有审计记录； - 有超时和权限策略。

不允许任意 SQL、任意 Shell 或任意文件路径工具。

------------------------------------------------------------------------

# 11. 数据库表建议

``` text
projects
agent_runtime_definitions
agent_runtime_instances
model_providers
model_profiles

sessions

tasks
task_revisions
steps
step_revisions
task_dependencies

executions
execution_attempts

decisions
artifacts
events
audit_logs
settings
schema_migrations
```

不要为了"未来扩展"提前建立几十张表。每张表必须对应清晰业务概念。

-   Runtime Definition：类型/配置模板；
-   Runtime Instance：实际安装或连接的 Runtime；
-   Model Provider 与 Runtime 分离；
-   外部 MCP Client 的 Session 不一定由 AgentDeck 创建，但可以登记；
-   外部 Session ID 不能当成平台内部主键。

------------------------------------------------------------------------

# 12. 实施阶段

## Phase 0：架构冻结

产品定位、领域模型、核心流程、安全边界、REST/MCP 边界、ADR。

## Phase 1：基础工程

Python/FastAPI、React/TypeScript、SQLite、Alembic、日志、Mock
Connector、CI。 验收：Windows 本地一键启动，Mock 数据可用。

## Phase 2：Project/Task/Step

Project、Task Tree、Step、Revision、Dependency、Session 项目归属。
验收：领域约束和状态机测试通过。

## Phase 3：MCP Server

工具 Schema、Token Scope、Project 隔离、幂等、审计、Antigravity
真实接入。 验收：外部 Agent 协作链路通过。

## Phase 4：Execution/Event/Decision

Execution、Attempt、Event、Decision、重启恢复、SSE。
验收：断连、重复事件、重启和审批恢复通过。

## Phase 5：第一个托管 Runtime

Claude 或 OpenCode 二选一，按真实使用优先级。
验收：Session、Streaming、Permission、Cancel、Resume 能力矩阵通过。

## Phase 6：前端工作台

Task Tree、Chat Workspace、Execution Detail、Decision Center、Artifact
Center、Runtime Settings、移动端关键操作。

## Phase 7：第二个 Runtime + Codex

验证 Connector 抽象。

## Phase 8：影响分析

Revision 变化、下游影响、needs_revalidation、Agent 判断和人工确认。

## Phase 9：安全与发布

文件隔离、Secret、审计、备份、跨平台、性能和安全测试。

------------------------------------------------------------------------

# 13. 必须通过的验收测试

## 产品主流程

1.  创建 Project；
2.  创建 Task；
3.  创建不关联 Task 的探索 Session；
4.  Agent 通过 MCP 读取 Task；
5.  Agent 汇报 Step 进度；
6.  Agent 请求 Decision；
7.  用户批准；
8.  Agent 继续；
9.  登记 Artifact；
10. 用户验收；
11. 修改上游 Step；
12. 下游标记 needs_revalidation；
13. Agent 判断哪些需要重做；
14. 保留旧版本和旧 Execution。

## Connector

每个 Connector 测试： - initialize； - health check； - capabilities； -
session create/resume（如支持）； - execution start； - streaming； -
status query； - permission； - cancel； - shutdown； - process
crash； - restart recovery。

## 安全

-   MCP Token 越权；
-   Project ID 伪造；
-   请求重放；
-   幂等键重复；
-   路径穿越；
-   symlink escape；
-   Windows UNC；
-   WebSocket 未授权；
-   CORS；
-   Secret 日志泄漏；
-   Agent 访问平台数据库；
-   Agent 访问其他 Project。

------------------------------------------------------------------------

# 14. ADR 决策记录

  ----------------------------------------------------------------------------
  ADR                     决策                    理由
  ----------------------- ----------------------- ----------------------------
  001                     不自建 Agent Harness    避免偏离定位

  002                     AgentDeck MCP Server    跨 Agent 暴露协作能力
                          为核心集成面            

  003                     Antigravity SDK 为可选  SDK 构建自定义 Agent，不是
                          Runtime                 IDE Session API

  004                     Python 后端             SDK 生态和集成效率优先

  005                     TypeScript 前端         复杂交互需要类型约束

  006                     SQLite                  单用户自托管、低维护

  007                     模块化单体              不需要微服务复杂度

  008                     外部 Agent 与托管       并非所有 Agent 都由平台启动
                          Runtime 双模式          

  009                     Session 与 Execution    会话与工作请求生命周期不同
                          分离                    

  010                     Agent                   防止未经验证状态污染业务
                          状态与平台状态分离      

  011                     事件持久化但不强制      可追溯且复杂度可控
                          Event Sourcing          

  012                     Worker 独立进程         隔离崩溃和权限边界

  013                     Task Tree 优先，DAG     层级与依赖不同
                          可选                    

  014                     Revision 追加式         历史可追溯

  015                     官方协议优先            降低维护风险
  ----------------------------------------------------------------------------

------------------------------------------------------------------------

# 15. gstack /autoplan 覆盖声明

本次参考公开的 gstack autoplan 结构，按 CEO、Design、DX、Eng
顺序进行了方案复核。

但当前对话环境没有 gstack 本地 Skill 运行时、阶段快照工具、原生子
Agent、Codex CLI 和审查日志能力。因此本次不是完整的原生 `/autoplan`
执行，不伪造其日志或双模型审查结果。

  -----------------------------------------------------------------------------------------------------------------
  阶段                    本次覆盖                                           状态
  ----------------------- -------------------------------------------------- --------------------------------------
  CEO                     产品前提、范围、差异化、未来风险                   方案级独立评审完成

  Design                  信息架构、流程、状态、移动端、可访问性             方案级评审完成；未做原型实测

  DX                      首次运行、Mock、Connector 开发、测试、升级         方案级评审完成

  Eng                     技术栈、数据模型、事件、Worker、协议、安全、测试   方案级评审完成；未做真实仓库代码审查

  Codex 外部审查          独立第二模型审查                                   未执行，环境不可用

  gstack 原生子 Agent     原生阶段审查                                       未执行

  gstack Review Logs      本机审查日志                                       未执行
  -----------------------------------------------------------------------------------------------------------------

gstack 官方 autoplan 说明 CEO → Design（有 UI）→ DX（开发者工具）→
Eng，Eng 最后审查前面阶段修改后的计划，并要求记录自动决策和最终审批门。

参考： -
https://github.com/garrytan/gstack/blob/main/autoplan/SKILL.md -
https://github.com/garrytan/gstack/blob/main/docs/skills.md

------------------------------------------------------------------------

# 16. 最终结论与开工门槛

**不建议直接按上一版 v1.0 全面开工。**

本次 v1.1 可以进入架构冻结和基础工程，但在大规模开发前必须完成：

1.  Antigravity IDE/CLI 连接 AgentDeck MCP Server 的真实 smoke test；
2.  确定第一款托管 Runtime（Claude 或 OpenCode）；
3.  Windows 下 Worker stdio JSONL 稳定性验证；
4.  SQLite 写入、事件和重启恢复测试；
5.  Task/Step Revision 与 Execution Snapshot 数据模型评审；
6.  Decision 重启恢复验证。

最终架构：

``` text
React + TypeScript PWA
          │
          ▼
FastAPI Application
          ├── Project / Task / Step
          ├── Session / Execution
          ├── Decision / Artifact
          ├── Revision / Impact Analysis
          ├── MCP Server
          ├── Event Store
          └── Connector Manager
                    │
                    ├── External Agent MCP Clients
                    │      ├── Antigravity
                    │      ├── Claude
                    │      └── Other Agents
                    │
                    └── Managed Runtime Workers
                           ├── Claude SDK
                           ├── OpenCode API
                           ├── Codex App Server
                           └── Optional Antigravity SDK

SQLite WAL
```

最终决策：

  -----------------------------------------------------------------------
  项目                                结论
  ----------------------------------- -----------------------------------
  产品定位                            Agent 协作管理平台

  自建 Agent Loop                     否

  Antigravity 首选                    AgentDeck MCP Server

  Antigravity SDK                     可选托管 Runtime

  后端                                Python

  前端                                TypeScript

  数据库                              SQLite

  架构                                模块化单体

  Runtime 执行                        独立 Worker

  通信                                统一领域接口，不统一底层协议

  任务组织                            Task Tree + 可选依赖

  版本                                Revision 追加式

  事件                                持久化事件，不强制 Event Sourcing

  旧版兼容                            不要求

  第一条验收链路                      Antigravity → AgentDeck MCP →
                                      Task/Decision/Artifact
  -----------------------------------------------------------------------

**一句话：AgentDeck 应成为各种 Agent
的协作控制台和项目工作记忆，而不是再造一个 Agent。**
