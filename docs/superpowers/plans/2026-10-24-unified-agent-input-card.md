# 一体化 Agent 输入卡片重构实施方案

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将底部的输入框、模型选择、审批权限、发送按钮和新对话统一合并为一个类似现代桌面端 Agent（Cursor/Antigravity）的复合卡片大框。

**Architecture:** 
- 结构上将原先分散的两行布局重构为单一的 `#agent-input-card` 容器，利用上下分层（上方输入区 + 下方内嵌工具栏）。
- 工具栏左侧集成沉浸式微边框胶囊（模型切换与审批模式），右侧集成自适应「+ 新对话」和「发送/中断」主按钮。
- 逻辑上补齐并优化输入自适应撑高（最高 180px 随后滚动）、Enter 发送 / Shift+Enter 换行、`/` 快捷指令浮层与模型选择器向上弹出定位。

**Tech Stack:** HTML5, Tailwind CSS, 原生 ES Modules (JavaScript)

---

## Global Constraints
- 中文交互与注释规范。
- 严禁触碰 Git 暂存区。
- 严禁调用自动化浏览器子代理进行前端测试。
- 复用现有项目 token 与组件样式，保证极简现代风格，与纯黑/深灰暗黑模式自然融合。
- 保证移动端（小屏）自适应，杜绝元素换行溢出。

---

## 实施任务列表

### Task 1: 样式优化与高度限制解除
**Files:**
- Modify: `frontend/styles.css:773-784`

- [x] **Step 1**: 修改 `frontend/styles.css`，解除 `#prompt-input` 固定的 `height: 2.5rem` 硬性限制，改由自适应 `min-height: 2.5rem; height: auto;` 驱动，确保多行输入自撑高。
- [x] **Step 2**: 检查是否有针对输入卡片 focus-within 的必要辅助样式。

---

### Task 2: 重构 HTML 结构（一体化复合大框）
**Files:**
- Modify: `frontend/index.html:289-356`

- [x] **Step 1**: 在 `frontend/index.html` 的 `footer` 区域构建 `#agent-input-card` 复合大框结构。
- [x] **Step 2**: 将多行 `textarea#prompt-input` 与自定义占位符放置在卡片上半部分。
- [x] **Step 3**: 将模型选择器、审批模式下拉框内嵌在卡片底部工具条左侧。
- [x] **Step 4**: 将新对话按钮（带响应式折叠文字）和发送/中断按钮内嵌在底部工具条右侧。
- [x] **Step 5**: 确保 `#slash-popover` 位于大框上方，不会被大框截断或层级压制。

---

### Task 3: 脚本与交互完善
**Files:**
- Modify: `frontend/js/actions-chat.js`
- Modify: `frontend/js/actions.js`
- Modify: `frontend/js/render-views.js`
- Modify: `frontend/app.js`

- [x] **Step 1**: 在 `frontend/js/actions-chat.js` 中完善 `handlePromptInput()`，支持 textarea 随输入内容自动自适应调整高度（40px ~ 180px），并在清空或发送后平滑复位高度。
- [x] **Step 2**: 检查斜杠指令 `/` 弹出浮层与 `model-picker-popover` 向上弹出的定位计算，确保在大框新位置下依然精准对齐。
- [x] **Step 3**: 在 `frontend/js/actions.js` 中将 `handlePromptInput` 导出并挂载到 `window.appActions`。
- [x] **Step 4**: 验证 `app.js` 中的 Enter 发送 / Shift+Enter 换行以及移动端换行行为。

---

### Task 4: 验证与验收
- [x] **Step 1**: 运行现有自动化测试套件确保无代码回归。
- [x] **Step 2**: 检查语法与静态错误。
- [x] **Step 3**: 总结修改成果。
