# AgentDeck 官方 Agent 接入能力调研：Claude Code / Codex

**调研日期：2026-10-31**  
**用途：AgentDeck 架构决策输入**  
**原则：官方能力优先，非必要不重复造轮子**

## 1. 结论摘要

Claude Code 和 Codex 都具备三类官方能力：

1. Remote：用户从手机/浏览器远程操作本机 Agent。
2. Local SDK / App Server：第三方应用程序化启动、继续、观察和控制本地 Agent。
3. Hosted Agent API：使用厂商托管的 Agent Runtime。

但三者不是同一种接口，不能混为一个“Remote API”。

对 AgentDeck 的建议：
- 第一层复用各厂商 Remote，不开发第二套手机 Agent 聊天/终端/审批 UI。
- 第二层建立轻量 Runtime Registry，保存 Runtime、Machine、外部 Session 的关联和厂商 Remote 入口。
- 第三层只对官方公开 SDK / App Server / API 做 Connector。
- Codex 适合作为第一个深度控制 Connector 候选。
- Claude Code 适合作为 Remote + MCP 轻量集成，后续用 Claude Agent SDK 深度接入。
- 不要为了统一而自建 Agent Loop、通用 Runtime、Remote 隧道或私有协议适配器。

## 2. 官方能力对比

| 产品 | Remote 手机控制 | 本地程序化控制 | 云端托管 Agent | 主要官方接入 |
|---|---|---|---|---|
| Antigravity | 有官方 Remote | 有 Python SDK（需区分具体产品/接口） | 有 Gemini API Antigravity Agent | Remote、SDK、MCP |
| OpenCode | 有 Web/Mobile Remote 生态 | 有公开 HTTP Server API / SDK | 主要由用户自托管 | HTTP API、SDK |
| Claude Code | 有官方 Remote Control | Claude Agent SDK（Python/TS） | Managed Agents | Remote、Agent SDK、Managed Agents、MCP |
| Codex | ChatGPT Mobile Remote 到 Windows/macOS Host | Codex SDK（TS/Python Beta）、App Server | Agents API | Remote Connections、SDK、App Server、Agents API |

说明：
- Remote 是厂商给人的控制入口；不自动代表第三方 AgentDeck 可以调用 Remote 的内部协议。
- SDK / App Server 是程序化控制本地 Agent 的接口。
- Hosted API 是厂商托管 Agent 的另一条运行路线。
- 各能力须按实际账号、地区、版本、组织策略验证。

## 3. Claude Code 官方能力

### 3.1 Remote Control

官方文档：
https://code.claude.com/docs/en/remote-control

Remote Control 可以把运行在本机的 Claude Code Session 暴露给 claude.ai/code 或 Claude iOS/Android App：
- 本机进程和文件系统继续在本机。
- 本机 MCP Server、工具和项目配置继续可用。
- 手机/浏览器可以继续对话、查看进度、发送附件。
- 支持 Subagents / workflows 状态同步。
- 可以启用手机推送，通知任务完成或需要决策。
- 可从 CLI、VS Code 或已有 Session 启动。

常用入口：
```bash
claude remote-control
```
或者在已有会话中：
```text
/remote-control
```

限制和注意：
- 官方页面标记为 Research Preview。
- Remote 需要 claude.ai 账号登录，不支持仅用 Anthropic API Key。
- 当前 requirements 页面列出的可用订阅为 Pro、Max、Team、Enterprise；Team / Enterprise 需要管理员开启。文档顶部另有更宽泛的 all plans 描述，因此部署时应以账号实际资格检查为准。
- 不适用于 Bedrock、Google Cloud Agent Platform、Microsoft Foundry 等特定 API Endpoint。
- 本机 Claude Code 进程必须运行；终端/IDE 进程结束，Remote Session 也会结束。
- 普通交互进程每进程一个 Remote Session；server mode 支持多并发 Session。
- 网络长时间不可用会超时。
- 部分命令仍需本机 CLI 操作。

设计含义：
AgentDeck 应保存 Runtime / Machine / External Session 关联和 Remote 入口，但不要尝试调用 Remote 的私有接口。Remote URL 如含一次性凭证，不能长期保存或写日志。

### 3.2 Claude Agent SDK

官方文档：
https://code.claude.com/docs/en/agent-sdk/overview

官方 SDK 提供 Python 和 TypeScript 两种语言，复用 Claude Code 的：
- Agent Loop 和上下文管理；
- 文件读写、命令执行等内置工具；
- Sessions、Resume、Fork；
- Hooks、Subagents；
- MCP；
- 权限模式和运行时审批回调；
- Streaming / structured output / observability。

适合：
- AgentDeck 主动启动 Claude Code Agent；
- 把 Agent 执行与 Project / Task / Execution 关联；
- 订阅执行事件；
- 在 AgentDeck 的产品流程中接入审批。

重要限制：
- SDK 是在应用所管理的进程中运行 Agent Loop，不是 Claude Code Remote 的开放 REST API。
- Anthropic 条款明确限制第三方产品未经批准提供 claude.ai 登录或订阅额度；集成应按 SDK Quickstart 使用允许的 API Key 认证，并审查商业条款。
- AgentDeck 不应将用户 Claude 订阅登录态当作可转售/代理的模型服务。

### 3.3 Managed Agents

Anthropic 提供 Managed Agents，属于托管 Runtime / API 产品，与本地 Claude Code Remote 和 Agent SDK 不同。
可用于未来云端长任务；当前不应作为 AgentDeck 第一版依赖。

## 4. Codex 官方能力

### 4.1 ChatGPT Mobile Remote Connections

官方文档：
https://learn.chatgpt.com/docs/remote-connections
https://learn.chatgpt.com/docs/mobile

官方支持 ChatGPT iOS / Android 手机端连接运行 ChatGPT 桌面应用的 Windows 或 macOS Host：
- 手机发起新任务、继续对话、发送后续指令；
- 回答问题、审批操作；
- 查看 Diff、测试结果、终端输出和截图；
- 在多台 Host 间切换；
- 使用 Host 本机文件、凭证、插件和工具；
- 可通过桌面端配置 SSH Host。

Windows 特别注意：
- Windows 桌面端可以作为 Host。
- Computer Use 在 Windows 上运行于前台；需要屏幕交互的工作应保持桌面 Session 可用且未锁定。
- Host 休眠、离线或关闭桌面应用时，Remote 不可用。
- 手机端和桌面端必须登录同一账号/工作区，功能可用性可能受 rollout 和管理员策略影响。
- 不要将 App Server 端口直接暴露到公网；远程主机连接应优先使用 SSH / VPN / Mesh VPN。

### 4.2 Codex App Server

官方文档：
https://learn.chatgpt.com/docs/app-server

App Server 是 Codex CLI / IDE 丰富客户端使用的官方双向 JSON-RPC 接口。
能力包括：
- Thread 创建、继续、恢复；
- Turn 启动、取消和状态事件；
- 对话历史；
- Agent 事件流；
- 工具执行和权限审批；
- 客户端响应 Agent 发起的人工输入请求。

Transport：
- stdio JSONL：默认方式，适合本机子进程集成。
- WebSocket：当前官方文档标记为实验性 / 不支持生产使用。
- Unix socket：支持本地场景。

安全：
- WebSocket 不应直接暴露在共享/公网网络。
- 非 loopback WebSocket listener 在 rollout 阶段可能默认无认证，远程使用必须显式配置认证和 TLS。
- 远程访问优先使用 SSH 隧道或受控 VPN。
- App Server 协议和版本应按实际 Codex 版本生成 schema 并进行契约测试。

### 4.3 Codex SDK

官方文档：
https://learn.chatgpt.com/docs/codex-sdk

官方提供：
- TypeScript：`@openai/codex-sdk`，Node.js 18+。
- Python：`openai-codex`，Python 3.10+，文档标注 Beta。
- SDK 可以创建、继续和恢复本地 Codex Threads。
- Python SDK 底层控制本地 Codex App Server。
- 可配置 Sandbox，例如 read-only、workspace-write、full-access。

对 AgentDeck 的意义：
现有 Python Backend 不需要为接入 Codex 改成 TypeScript。可以用 Python SDK 做第一阶段验证；如果需要更丰富的协议能力，再直接集成 App Server。

### 4.4 Codex Agents API

官方文档：
https://developers.openai.com/api/docs/guides/agents-api/overview

Agents API 提供 OpenAI 托管的 Codex Harness：
- OpenAI 管理 Session、Orchestration、Context Compaction 和 Recovery；
- 应用提供工具并选择执行环境；
- 支持沙箱、MCP、长时间运行任务和产物。

它是云端托管 Agent，不等于本地 Codex Remote 或 App Server。

## 5. AgentDeck 架构建议

### 5.1 不要做一个万能 Remote Adapter

不同产品的 Remote 是面向人的官方产品入口，不是共同协议。AgentDeck 不应：
- 模拟点击厂商 Remote；
- 抓包调用私有 WebSocket / RPC；
- 读取厂商内部数据库；
- 假设 Remote URL 就能程序化发送 Prompt；
- 以一个“统一 Remote API”包装不同产品。

### 5.2 将官方集成分成三类

**A. Remote Link Connector（轻量）**
- Provider / Machine / External Session 登记；
- Remote 入口配置；
- 打开厂商官方 Remote；
- 不控制会话内部生命周期。

**B. Local Runtime Connector（深度）**
- 通过官方 SDK / App Server / HTTP API；
- 创建、继续、恢复 Session；
- 发送任务；
- 读取事件；
- 取消执行；
- 处理权限审批（仅在接口真实支持时）。

**C. Hosted Agent Connector（云端）**
- 通过官方 Hosted Agent API；
- 创建和管理云端任务；
- 读取事件和产物；
- 管理云端执行环境和费用。

### 5.3 推荐接入优先级

1. 所有 Runtime 先支持 Remote Link / Registry。
2. Agent → AgentDeck 的 MCP 轻量协作：读取任务、汇报进度、请求决策、登记 Artifact。
3. Codex：优先验证 Python SDK 和 App Server，作为第一个深度控制 Connector 候选。
4. Claude Code：先 Remote + MCP；随后评估 Agent SDK 的运行时、审批、认证、条款和部署边界。
5. OpenCode：使用公开 HTTP API，作为另一个深度控制候选。
6. Antigravity：Remote + MCP 优先；只有公开 SDK/API 的具体能力和产品形态验证通过后再深度接入。
7. Hosted Agents（Codex Agents API / Claude Managed Agents）作为后期独立执行环境，不混同本地 Runtime。

### 5.4 统一能力清单

每个 Provider 通过 Capability Manifest 声明能力：
- `remote_link`
- `machine_registry`
- `session_list`
- `session_create`
- `session_resume`
- `prompt_send`
- `event_stream`
- `turn_cancel`
- `permission_request`
- `permission_respond`
- `artifact_access`
- `hosted_runtime`

每项能力记录：
- `supported / unsupported / unverified`
- `official_docs / integration_test / inferred`
- Runtime 版本
- Connector 版本
- 验证日期
- 限制说明

不能因为某产品有 Remote，就自动把 `prompt_send` 或 `session_list` 标为 supported。

## 6. 对原 AgentDeck 技术方案的修订

建议保留：
- Python + FastAPI Backend；
- React + TypeScript Frontend；
- SQLite；
- Project / Task / Step / Revision / Decision / Artifact；
- Session / Execution / Attempt 领域模型；
- MCP Server（Agent 主动协作）；
- Remote Registry。

建议延后或按需实现：
- 通用 Worker Supervisor；
- 自建 Agent Runtime；
- 自建手机 Agent Chat / Terminal / Permission UI；
- 全量 Runtime Connector；
- Hosted Agent 云端编排。

推荐开发路径：
1. Phase 0 仓库审查；
2. Phase 1 各厂商 Remote / SDK / API Spike；
3. 核心 Project / Task / Session / Registry；
4. Remote Link UI；
5. MCP 轻量协作；
6. Codex 深度控制 Spike；
7. Claude / OpenCode Connector；
8. Hosted Agent / Worker 按需扩展。

## 7. 官方资料

### Anthropic
- Remote Control: https://code.claude.com/docs/en/remote-control
- Agent SDK: https://code.claude.com/docs/en/agent-sdk/overview
- Permission control: https://code.claude.com/docs/en/agent-sdk/permissions
- Claude API SDKs: https://platform.claude.com/docs/en/cli-sdks-libraries/overview

### OpenAI
- Remote Connections: https://learn.chatgpt.com/docs/remote-connections
- Codex on Mobile: https://learn.chatgpt.com/docs/mobile
- Codex SDK: https://learn.chatgpt.com/docs/codex-sdk
- Codex App Server: https://learn.chatgpt.com/docs/app-server
- Agents API: https://developers.openai.com/api/docs/guides/agents-api/overview
- Codex as a platform: https://developers.openai.com/blog/codex-as-a-platform

## 8. 最终建议

AgentDeck 不应该成为另一个 Claude Code、Codex 或 Antigravity。

它应该成为：
- 统一管理不同 Agent 的工作；
- 复用各 Agent 官方 Remote；
- 使用各 Agent 官方 SDK / App Server / API 做可选深度控制；
- 用自己的领域模型统一 Project / Task / Revision / Decision / Artifact；
- 用 MCP 让不同 Agent 主动协作；
- 对所有能力标注真实支持状态，不制造虚假统一能力。

**原则：官方产品能做的，AgentDeck 不重做；官方接口能控制的，AgentDeck 不自建 Runtime；只有跨产品的管理能力和厂商没有提供的必要能力，才由 AgentDeck 自己实现。**
