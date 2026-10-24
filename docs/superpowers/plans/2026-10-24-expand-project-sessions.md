# 项目会话分批展开与收起 (See less) 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 AgentDeck 左侧栏项目会话列表增加步进展开（每次 +8 个）与一键收回（回到 8 个）交互功能，解决大量会话被截断无法查看且不被一次性撑爆的问题。

**Architecture:** 前端纯客户端状态与响应式渲染驱动。在全局状态 `state` 维护每个项目的当前展开上限；在 `actions-session.js` 提供递增和重置操作；在 `render-sidebar.js` 动态切片并渲染操作按钮（包含剩余计数与收起按钮）。

**Tech Stack:** 原生 JavaScript ES Modules, Tailwind CSS, HTML template literals.

**Spec:** 用户需求对话（每次展开 8 个，一键收回初始 8 个，保持 UI 紧凑暗色风格）。

## Global Constraints

- 中文交互与注释。
- 禁止修改暂存区代码，禁止主动将代码提交到暂存区 (`git add` / `git commit` 不执行)。
- 禁止调用自动化浏览器子代理进行前端测试。
- 遵从现有暗色风格与 UI 设计规范（`text-[11px]`, `hover:text-sky-400`, 避免布局抖动）。

---

### Task 1: 状态与动作定义 (State & Actions)

**Files:**
- Modify: `frontend/js/state.js`
- Modify: `frontend/js/actions-session.js`
- Modify: `frontend/js/actions.js`

- [x] **Step 1: 在 `frontend/js/state.js` 中增加 `projectSessionLimits` 字段**
- [x] **Step 2: 在 `frontend/js/actions-session.js` 中实现 `expandProjectSessions` 与 `resetProjectSessions`**
- [x] **Step 3: 在 `frontend/js/actions.js` 中导出并挂载到 `window.appActions`**

---

### Task 2: 侧边栏项目会话动态截取与按钮渲染 (Sidebar Rendering)

**Files:**
- Modify: `frontend/js/render-sidebar.js`

- [x] **Step 1: 替换写死的 `.slice(0, 8)` 为动态 limit 读取**
- [x] **Step 2: 在列表末尾渲染「展开更多」与「收起 (See less)」操作栏**
- [x] **Step 3: 处理边界情况（未超出 8 个时不显示按钮、刚好展开完时只显示收起按钮）**

---

### Task 3: 语法校验与全量验证 (Verification)

**Files:**
- Test: Node.js 语法分析或现有 Python 测试套件

- [x] **Step 1: 运行 JS 静态语法检查（node --check）确保前端 ES 模块无语法错误**
- [x] **Step 2: 运行现有 pytest 测试集验证无后端或回归影响**
