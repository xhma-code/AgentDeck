# -*- coding: utf-8 -*-
"""
Task / Step 追加式修订与下游 needs_revalidation 传播策略（v1.3 第 11.2 节）。
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.domain.enums import StepPlatformStatus, TaskPlatformStatus


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

