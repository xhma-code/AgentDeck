# -*- coding: utf-8 -*-
"""
任务依赖有向无环图 (DAG) 与同 Project 边界校验纯领域服务。
"""

from __future__ import annotations

from collections import deque
from typing import Iterable

from backend.v2.domain.exceptions import CrossProjectReferenceError, DependencyCycleError


def validate_task_dependency_addition(
    *,
    task_id: str,
    task_project_id: str,
    depends_on_task_id: str,
    depends_on_project_id: str,
    existing_edges: Iterable[tuple[str, str]],
) -> None:
    """
    校验新增任务依赖 (task_id 依赖于 depends_on_task_id)：
    1. 严禁跨 Project 建立依赖。
    2. 严禁任务依赖自身。
    3. 加入新边后不得形成有向环（若 depends_on_task_id 已经直接或间接依赖 task_id，则成环）。

    参数 existing_edges: 现有 (dependent_task_id, prerequisite_task_id) 二元组序列。
    """
    if task_project_id != depends_on_project_id:
        raise CrossProjectReferenceError(
            problem="禁止建立跨项目的任务依赖关系",
            cause=f"任务 {task_id} (项目 {task_project_id}) 试图依赖另一项目 ({depends_on_project_id}) 的任务 {depends_on_task_id}",
            fix="请仅在同一 Project 内部的 Task 之间建立前置依赖。",
            context={
                "task_id": task_id,
                "task_project_id": task_project_id,
                "depends_on_task_id": depends_on_task_id,
                "depends_on_project_id": depends_on_project_id,
            },
        )

    if task_id == depends_on_task_id:
        raise DependencyCycleError(
            problem="任务不能将自身设为前置依赖",
            cause=f"task_id 与 depends_on_task_id 均为 {task_id}",
            fix="请选择同项目下的其他前置任务。",
            context={"task_id": task_id},
        )

    # 构建现有依赖映射：node -> 它所依赖的前置节点集合
    prereq_map: dict[str, set[str]] = {}
    for dep_task, pre_task in existing_edges:
        prereq_map.setdefault(dep_task, set()).add(pre_task)

    # 检测从 depends_on_task_id 出发顺着前置链条能否到达 task_id；若能到达则新增边必成环
    visited: set[str] = set()
    queue: deque[str] = deque([depends_on_task_id])
    while queue:
        curr = queue.popleft()
        if curr == task_id:
            raise DependencyCycleError(
                problem="新增任务依赖会导致循环依赖死锁（违反 DAG 无环约束）",
                cause=f"前置任务 {depends_on_task_id} 的上游依赖链已包含任务 {task_id}",
                fix="请检查任务依赖链条，移除反向依赖后再试。",
                context={"task_id": task_id, "depends_on_task_id": depends_on_task_id},
            )
        if curr in visited:
            continue
        visited.add(curr)
        for nxt in prereq_map.get(curr, ()):
            if nxt not in visited:
                queue.append(nxt)


def find_downstream_dependent_task_ids(
    source_task_id: str,
    existing_edges: Iterable[tuple[str, str]],
) -> list[str]:
    """
    给定发生修订的上游任务 source_task_id，按拓扑广度优先搜索返回所有直接或间接依赖它的下游任务 ID 列表。
    existing_edges 元素格式为 (dependent_task_id, prerequisite_task_id)。
    """
    reverse_adj: dict[str, list[str]] = {}
    for dep_task, pre_task in existing_edges:
        reverse_adj.setdefault(pre_task, []).append(dep_task)

    impacted: list[str] = []
    visited: set[str] = {source_task_id}
    queue: deque[str] = deque(reverse_adj.get(source_task_id, []))

    while queue:
        curr = queue.popleft()
        if curr in visited:
            continue
        visited.add(curr)
        impacted.append(curr)
        for nxt in reverse_adj.get(curr, ()):
            if nxt not in visited:
                queue.append(nxt)

    return impacted

