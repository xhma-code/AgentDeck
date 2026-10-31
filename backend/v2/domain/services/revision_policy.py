# -*- coding: utf-8 -*-
"""
Task / Step 追加式修订与下游 needs_revalidation 传播策略（v1.3 第 11.2 节）。

核心不变量：
1. Task 与 Step 的内容变更只生成递增的 Revision 快照，历史 Revision 永不覆写。
2. 当某 Step (seq_order = k) 修订时，同任务内 seq_order > k 的下游 Step 及依赖所属 Task 的下游 Task
   自动标记 needs_revalidation = 1，绝不自动触发 Agent 重跑。
3. 当某 Task 修订时，其下非 skipped 的 Step、其子任务以及所有下游依赖 Task 自动标记 needs_revalidation = 1。
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.v2.domain.enums import StepPlatformStatus, TaskPlatformStatus


@dataclass(frozen=True)
class RevalidationImpact:
    """一次修订引发的下游待重验影响汇总。"""

    impacted_step_ids: list[str]
    impacted_task_ids: list[str]
    reason: str


def should_step_enter_revalidation(current_status: str) -> bool:
    """判断步骤是否应进入 needs_revalidation 状态（已跳过的步骤除外）。"""
    return current_status != StepPlatformStatus.SKIPPED.value


def should_task_enter_revalidation(current_status: str) -> bool:
    """判断下游任务是否应进入 needs_revalidation 状态（已取消的任务除外）。"""
    return current_status != TaskPlatformStatus.CANCELLED.value

