# -*- coding: utf-8 -*-
"""
AgentDeck v2 核心重构包（基于架构基线 v1.3）。

职责边界：
- config: 统一环境与含空格路径安全配置
- domain: 纯领域枚举、值对象、不变量规则与异常体系（零外部私有协议依赖）
- db: SQLAlchemy 2.x ORM 模型、SQLite WAL/外键连接引擎、UnitOfWork 同事务事件提交与在线热备
- security: 工作区路径白名单/符号链接防护、CORS 受控来源策略、MCP Project Token 鉴权守卫
- application: 项目、规划（Task/Step/Dependency/Revision）、会话与 Runtime 注册用例服务
- api: /api/v2 隔离路由接口
"""

from __future__ import annotations

__version__ = "2.2.0-phase1"

