# AgentDeck 前三阶段代码架构审查报告

**仓库：** xhma-code/AgentDeck\
**审查分支：** feat/project-task-foundation\
**范围：** Clean-Slate 后的关键领域模型、应用服务、API
鉴权、启动/数据库初始化、前端入口、README 与项目元数据。\
**方式：** GitHub 源码静态审阅；未修改仓库。

## 1. 总体结论

**核心架构方向基本正确，不建议推翻前三阶段，也不需要再次 Clean-Slate。**

代码已经体现出我们确认的核心模型：

-   Project → Task → Step：项目和工作管理主线。
-   TaskRevision / StepRevision：追加式修订历史。
-   Session / Execution /
    ExecutionAttempt：协同会话、业务执行单、单次执行尝试分层。
-   RuntimeProvider / RuntimeInstance / Machine：Agent
    产品、运行实例、宿主机器分层。
-   Decision / Artifact / Event：决策、交付物、事件独立建模。
-   CapabilityManifest + L0-L3：能力声明与真实验证证据分离。

SessionService 对"Agent 自报完成不等于平台验收完成""Remote
断线不等于执行失败"的处理是正确的。Capability Policy 也明确要求 L2/L3
必须具备实测证据，不能把 Remote 或安装 SDK 当作高阶控制能力。

这与"AgentDeck 管理工作，Agent Runtime 负责推理和实际执行"的定位一致。

## 2. 发现的问题

### P0：README 和项目描述仍然宣传旧产品

文件： -
[README.md](https://github.com/xhma-code/AgentDeck/blob/feat/project-task-foundation/README.md) -
[pyproject.toml](https://github.com/xhma-code/AgentDeck/blob/feat/project-task-foundation/pyproject.toml)

README 仍把 AgentDeck 定义为"基于 Tailscale 私有局域网的多 Agent
移动端统一中控台"，并宣传 Antigravity 内部会话镜像、IDE 关闭后 CLI
接力、Claude JSONL 解析、OpenCode SQLite
读取、动态权限模式切换、手机端工具审批拦截等旧能力。

这与 Clean-Slate 后的产品职责冲突，也会误导后续开发者。pyproject.toml 的
description 仍是旧版定位。

**建议：** 全面改写 README
和项目描述，删除私有协议/内部数据库/会话镜像/CLI 接力等旧能力；明确
AgentDeck 管理项目、任务、决策、执行记录、交付物和 Runtime
Registry，实际推理与工具执行由 Agent Runtime 完成。同步启动 banner、CLI
description 和 API 文档。

### P1：Execution 创建时被直接标记为 RUNNING

文件：[execution_service.py](https://github.com/xhma-code/AgentDeck/blob/feat/project-task-foundation/backend/application/execution_service.py)

`create_execution()` 创建记录时直接设置 Execution 为 running、Agent
状态为 running、Runtime 为 online、连接为 connected，并创建 running
状态的 Attempt。

但创建执行单不等于已经把任务发送给 Runtime，更不等于 Runtime
确认开始执行。这会产生虚假的运行状态。

**建议：** 1. 新建 Execution 使用 queued / pending_dispatch。 2. 新建
Attempt 使用 created / queued。 3. 只有真实 Dispatch
获得明确确认后才进入 running。 4. Runtime 状态、Agent
自报状态、连接状态独立维护。 5. 为 Attempt
定义明确的生命周期状态转换；当前 AttemptStatus 缺少 queued / created。

### P1：Alembic 与 create_all 的职责不清

文件： -
[backend/app.py](https://github.com/xhma-code/AgentDeck/blob/feat/project-task-foundation/backend/app.py) -
[run.py](https://github.com/xhma-code/AgentDeck/blob/feat/project-task-foundation/run.py)

`create_app()` 默认 `auto_init_schema=True`，启动时执行
`Base.metadata.create_all()`。但项目同时使用 Alembic；常规 `run.py`
启动路径没有执行 `alembic upgrade head`。

`create_all()`
只能创建缺失表，不能替代已有数据库的列、索引和数据迁移。可能出现应用能启动但
schema 未升级、Alembic 版本记录与实际结构脱节等问题。

**建议：** Alembic 作为唯一正式 Schema 版本来源；启动前执行迁移或检查
Alembic head，不一致则拒绝启动；`create_all()` 仅用于隔离测试；Doctor
检查当前 revision 与目标 head，并在迁移前保留备份。

### P1：项目作用域 Header 不能单独作为权限边界

文件：[backend/api/deps.py](https://github.com/xhma-code/AgentDeck/blob/feat/project-task-foundation/backend/api/deps.py)

`enforce_project_scope()` 对 `X-Project-Scope`
的处理只是比较客户端自己提供的值与目标 project_id。客户端可以自行构造该
Header，因此它只能防止误切项目，不能证明客户端有权访问项目。

MCP Token 检查目前也没有在该函数中体现 scopes 与过期时间校验；当
`project_service` 不存在时会跳过检查。

这不必然意味着当前单用户 Web 管理端存在直接越权，因为 Web
端另有设备级认证；但不能把此函数当成真正的项目授权机制。

**建议：** - 区分设备级 Web 管理权限与 MCP 项目级 Token 权限。 - MCP
请求由独立 Token Guard 认证，可信地得到 project_id 和 scopes。 - 校验
revoked、expires_at、scopes。 - `X-Project-Scope`
仅作附加一致性校验，不是授权凭证。 - 缺少权限依赖时 fail closed。

### P1：Decision 的多实体关联缺少完整一致性校验

文件：[decision_service.py](https://github.com/xhma-code/AgentDeck/blob/feat/project-task-foundation/backend/application/decision_service.py)

当前创建 Decision 时，分别检查 task、step、session、execution
是否存在并属于同一个 Project。但同属一个 Project
不代表对象之间的关系正确。例如 task_id 指向任务 A，而 step_id
指向同项目任务 B 的步骤，独立 project 校验仍可能通过。

**建议：** - 同时传 task_id 和 step_id 时校验
`step.task_id == task_id`。 - Session 已绑定 Task 时，校验 Decision 的
task_id 是否一致。 - 关联 Execution 时校验其 task、step、session 与
Decision 的引用关系。 - 将跨实体一致性规则收敛为统一领域校验函数。

### P2：App.tsx 仍是过大的单体组件

文件： -
[frontend/src/App.tsx](https://github.com/xhma-code/AgentDeck/blob/feat/project-task-foundation/frontend/src/App.tsx) -
[frontend/src/api/client.ts](https://github.com/xhma-code/AgentDeck/blob/feat/project-task-foundation/frontend/src/api/client.ts)

React + TypeScript 已经落地，但 App.tsx
集中了多页面状态、数据加载、表单事件、导航和大量 JSX。

**建议：** 不要再建 frontend-v2，也不要大规模重写。渐进拆分为
Overview、Projects、Sessions、Decisions、Runtimes 页面和对应
feature/hooks；全局 Decision Modal 留在应用壳层即可。此项不必阻塞 Phase
4。

### P2：版本和技术栈元数据不一致

审查到的版本： - pyproject.toml：2.1.0 - backend/app.py：1.3.0 -
run.py：1.3.0 - frontend/package.json：1.3.0 - package.json 实际 React
依赖为 18.3.1，而此前 Phase 1/2 报告写的是 React 19。

**建议：** 统一版本号来源；明确 React 实际版本，若目标为 React 19
则真正升级并测试，否则修正文档；同步 Python 最低版本、README
和启动信息。

## 3. 应保留的架构

  领域                                   判断
  -------------------------------------- ----------------------
  Project-first、Task/Step/Revision      保留
  Session 与外部 Agent 会话引用分离      保留
  Execution 与 Attempt 分离              保留，修正初始状态
  RuntimeProvider / Instance / Machine   保留
  CapabilityManifest 与 L0-L3 证据规则   强烈保留
  Decision / Artifact / Event            保留
  项目级 MCP Token                       保留，完善 Guard
  SQLite WAL                             当前单机阶段暂时保留

AgentDeck 的 Decision 是平台管理的协作决策与检查点，不应扩展成 Agent
原生工具审批 UI。Agent 原生 Chat、Terminal、IDE、工具调用和权限 UI 仍由
Agent 自己负责。

## 4. Phase 4 前建议修正

优先完成： 1. 修正 Execution / Attempt 初始状态和生命周期语义。 2. 明确
Alembic 是唯一正式 Schema 版本来源。 3. 完善 MCP Token Guard
的项目归属、过期和 scopes 校验。 4. 补 Decision 多实体关联一致性校验。
5. 改写 README 和项目描述，统一产品定位。

前端拆分和版本号清理可并行推进，不必为此暂停协议设计。

## 5. 最终架构判断

**不建议推翻前三阶段。** 当前领域模型已经从旧版 Agent
会话镜像/手机控制台转向"项目与任务管理 + 多 Agent 协同控制"，方向正确。

后续最需要防止的是 Phase 4/6 接入 MCP 和 Runtime SDK 后，又逐渐自行维护
Agent 执行流、内部状态、权限系统和聊天交互。

应坚持：

> AgentDeck 管理工作，不接管
> Agent；统一项目、任务、协作记录和能力入口，不统一重造各 Agent
> 的内部执行机制。

## 6. 审查边界

本次通过 GitHub Connector 静态读取了上述关键源码文件，未在本地完整 clone
并运行 pytest/npm build，未逐文件审查全部 API、迁移和测试，也未验证真实
MCP 客户端端到端兼容性。未修改任何代码或 GitHub 分支。

因此这是架构与关键路径静态审查，不是完整安全审计或最终代码验收。后续仍需对
MCP Guard、路径防逃逸、幂等事务、Alembic migrations
和测试覆盖做专项审查。
