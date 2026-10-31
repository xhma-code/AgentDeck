# -*- coding: utf-8 -*-
"""
任务规划与追加式修订应用服务（PlanningService）。

核心领域不变量实现：
1. Task 支持同项目父子树（跨项目父子关联严格拦截）。
2. TaskDependency 仅限同项目内轻量前置依赖，强制通过有向无环图 (DAG) 检测。
3. 修改 Task 或 Step 只生成递增的 TaskRevision / StepRevision，绝不覆写历史快照。
4. 修订后自动将受影响的下游 Step 与下游依赖 Task 标记 `needs_revalidation = 1`，绝不自动重跑。
5. 严格分离 `platform_status` 与 `agent_reported_status`：Agent 自报完成绝不自动触发平台验收完成。
"""

from __future__ import annotations

import json
import uuid
from typing import Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.db.base import utc_now_iso
from backend.db.models.planning import (
    StepModel,
    StepRevisionModel,
    TaskDependencyModel,
    TaskModel,
    TaskRevisionModel,
)
from backend.db.models.project import ProjectModel
from backend.db.uow import SqlAlchemyUnitOfWork
from backend.domain.enums import (
    ActorType,
    AgentReportedStatus,
    DependencyKind,
    StepPlatformStatus,
    TaskPlatformStatus,
    TaskPriority,
)
from backend.domain.exceptions import (
    CrossProjectReferenceError,
    EntityNotFoundError,
    InvariantViolationError,
    VersionConflictError,
)
from backend.domain.services.dependency_graph import (
    find_downstream_dependent_task_ids,
    validate_task_dependency_addition,
)
from backend.domain.services.revision_policy import (
    RevalidationImpact,
    should_step_enter_revalidation,
    should_task_enter_revalidation,
)


class PlanningService:
    """Task、Step、Dependency 与 Revision 编排服务。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create_task(
        self,
        *,
        project_id: str,
        title: str,
        goal: str = "",
        acceptance_criteria: Optional[Sequence[str]] = None,
        parent_task_id: Optional[str] = None,
        priority: TaskPriority = TaskPriority.MEDIUM,
        sort_order: int = 0,
        actor_type: ActorType = ActorType.USER,
    ) -> TaskModel:
        """创建任务并同步写入初始第 1 版 TaskRevision 与创建事件。"""
        clean_title = (title or "").strip()
        if not clean_title:
            raise InvariantViolationError(
                problem="任务标题不能为空",
                cause="传入了空白 title",
                fix="请填写明确的任务标题。",
            )

        criteria_list = [str(item).strip() for item in (acceptance_criteria or ()) if str(item).strip()]
        criteria_json = json.dumps(criteria_list, ensure_ascii=False)

        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            proj = uow.session.get(ProjectModel, project_id)
            if proj is None:
                raise EntityNotFoundError(
                    problem="无法在不存在的项目下创建任务",
                    cause=f"未找到项目 ID: {project_id}",
                    fix="请先创建或选择有效项目。",
                    context={"project_id": project_id},
                )

            if parent_task_id is not None:
                parent = uow.session.get(TaskModel, parent_task_id)
                if parent is None:
                    raise EntityNotFoundError(
                        problem="父任务不存在",
                        cause=f"未找到父任务 ID: {parent_task_id}",
                        fix="请指定有效的同项目父任务。",
                        context={"parent_task_id": parent_task_id},
                    )
                if parent.project_id != project_id:
                    raise CrossProjectReferenceError(
                        problem="禁止跨项目挂载父子任务",
                        cause=f"父任务 {parent_task_id} 属于项目 {parent.project_id}，与当前项目 {project_id} 不一致",
                        fix="子任务必须与父任务归属同一个 Project。",
                        context={
                            "project_id": project_id,
                            "parent_task_id": parent_task_id,
                            "parent_project_id": parent.project_id,
                        },
                    )

            now = utc_now_iso()
            task_id = str(uuid.uuid4())
            task = TaskModel(
                id=task_id,
                project_id=project_id,
                parent_task_id=parent_task_id,
                title=clean_title[:240],
                goal=(goal or "").strip(),
                acceptance_criteria_json=criteria_json,
                platform_status=TaskPlatformStatus.DRAFT.value,
                agent_reported_status=AgentReportedStatus.UNKNOWN.value,
                priority=priority.value,
                sort_order=sort_order,
                active_revision_no=1,
                needs_revalidation=0,
                revalidation_reason="",
                version=1,
                created_at=now,
                updated_at=now,
            )
            rev1 = TaskRevisionModel(
                id=str(uuid.uuid4()),
                project_id=project_id,
                task_id=task_id,
                revision_no=1,
                title_snapshot=task.title,
                goal_snapshot=task.goal,
                acceptance_criteria_json=criteria_json,
                change_reason="初始创建任务基线",
                changed_by=actor_type.value,
                created_at=now,
            )
            uow.session.add(task)
            uow.session.flush()
            uow.session.add(rev1)
            uow.record_event(
                project_id=project_id,
                entity_type="task",
                entity_id=task_id,
                event_type="task.created",
                actor_type=actor_type,
                payload={
                    "title": task.title,
                    "parent_task_id": parent_task_id,
                    "revision_no": 1,
                    "priority": task.priority,
                },
            )
            uow.commit()
            return task

    def get_task(self, project_id: str, task_id: str) -> TaskModel:
        """按项目与 ID 获取任务详情。"""
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            task = uow.session.get(TaskModel, task_id)
            if task is None:
                raise EntityNotFoundError(
                    problem="目标任务不存在",
                    cause=f"未找到任务 ID: {task_id}",
                    fix="请核对任务 ID。",
                )
            if task.project_id != project_id:
                raise CrossProjectReferenceError(
                    problem="禁止跨项目访问任务",
                    cause=f"任务 {task_id} 不属于项目 {project_id}",
                    fix="请核对项目上下文。",
                )
            return task

    def get_step(self, project_id: str, step_id: str) -> StepModel:
        """按项目与 ID 获取步骤详情。"""
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            step = uow.session.get(StepModel, step_id)
            if step is None:
                raise EntityNotFoundError(
                    problem="目标步骤不存在",
                    cause=f"未找到步骤 ID: {step_id}",
                    fix="请核对步骤 ID。",
                )
            if step.project_id != project_id:
                raise CrossProjectReferenceError(
                    problem="禁止跨项目访问步骤",
                    cause=f"步骤 {step_id} 不属于项目 {project_id}",
                    fix="请核对项目上下文。",
                )
            return step

    def add_task_dependency(
        self,
        *,
        project_id: str,
        task_id: str,
        depends_on_task_id: str,
        dependency_kind: DependencyKind = DependencyKind.FINISH_TO_START,
    ) -> TaskDependencyModel:
        """在同项目内添加任务前置依赖，执行跨项目校验与全局 DAG 环路检测。"""
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            task = uow.session.get(TaskModel, task_id)
            prereq = uow.session.get(TaskModel, depends_on_task_id)
            if task is None or prereq is None:
                raise EntityNotFoundError(
                    problem="建立依赖的目标任务不存在",
                    cause=f"task={task_id} 或 depends_on={depends_on_task_id} 未找到",
                    fix="请确认前后置任务均已创建。",
                )

            if task.project_id != project_id:
                raise CrossProjectReferenceError(
                    problem="任务不属于请求指定的项目",
                    cause=f"任务 {task_id} 归属项目 {task.project_id}，而非 {project_id}",
                    fix="请在正确的项目上下文中操作。",
                )

            existing_rows = uow.session.execute(
                select(TaskDependencyModel.task_id, TaskDependencyModel.depends_on_task_id).where(
                    TaskDependencyModel.project_id == project_id
                )
            ).all()
            edges = [(str(r[0]), str(r[1])) for r in existing_rows]

            if (task_id, depends_on_task_id) in set(edges):
                raise InvariantViolationError(
                    problem="该任务前置依赖关系已存在",
                    cause=f"任务 {task_id} 已依赖 {depends_on_task_id}",
                    fix="无需重复添加相同依赖。",
                )

            validate_task_dependency_addition(
                task_id=task_id,
                task_project_id=task.project_id,
                depends_on_task_id=depends_on_task_id,
                depends_on_project_id=prereq.project_id,
                existing_edges=edges,
            )

            dep = TaskDependencyModel(
                id=str(uuid.uuid4()),
                project_id=project_id,
                task_id=task_id,
                depends_on_task_id=depends_on_task_id,
                dependency_kind=dependency_kind.value,
                created_at=utc_now_iso(),
            )
            uow.session.add(dep)
            uow.record_event(
                project_id=project_id,
                entity_type="task",
                entity_id=task_id,
                event_type="task.dependency_added",
                actor_type=ActorType.USER,
                payload={
                    "dependency_id": dep.id,
                    "depends_on_task_id": depends_on_task_id,
                    "dependency_kind": dep.dependency_kind,
                },
            )
            uow.commit()
            return dep

    def list_project_dependencies(self, project_id: str) -> list[TaskDependencyModel]:
        """获取项目下全部任务依赖边。"""
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            stmt = (
                select(TaskDependencyModel)
                .where(TaskDependencyModel.project_id == project_id)
                .order_by(TaskDependencyModel.created_at.asc())
            )
            return list(uow.session.execute(stmt).scalars().all())

    def create_step(
        self,
        *,
        project_id: str,
        task_id: str,
        title: str,
        instruction: str = "",
        seq_order: Optional[int] = None,
        actor_type: ActorType = ActorType.USER,
    ) -> StepModel:
        """在指定任务下追加步骤，并同步创建第 1 版 StepRevision。"""
        clean_title = (title or "").strip()
        if not clean_title:
            raise InvariantViolationError(
                problem="步骤标题不能为空",
                cause="传入了空白步骤标题",
                fix="请提供明确的步骤标题。",
            )

        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            task = uow.session.get(TaskModel, task_id)
            if task is None:
                raise EntityNotFoundError(
                    problem="所属任务不存在",
                    cause=f"未找到任务 ID: {task_id}",
                    fix="请确认目标任务存在。",
                )
            if task.project_id != project_id:
                raise CrossProjectReferenceError(
                    problem="禁止跨项目创建任务步骤",
                    cause=f"任务 {task_id} 属于项目 {task.project_id}，与传入项目 {project_id} 不符",
                    fix="请使用任务所属的正确 project_id。",
                )

            if seq_order is None:
                max_seq = uow.session.execute(
                    select(func.max(StepModel.seq_order)).where(StepModel.task_id == task_id)
                ).scalar()
                next_seq = int(max_seq or 0) + 1
            else:
                if seq_order < 1:
                    raise InvariantViolationError(
                        problem="步骤顺序号必须大于等于 1",
                        cause=f"收到非法 seq_order={seq_order}",
                        fix="请传入正整数顺序号。",
                    )
                next_seq = seq_order

            now = utc_now_iso()
            step_id = str(uuid.uuid4())
            step = StepModel(
                id=step_id,
                project_id=project_id,
                task_id=task_id,
                seq_order=next_seq,
                title=clean_title[:240],
                instruction=(instruction or "").strip(),
                platform_status=StepPlatformStatus.PENDING.value,
                agent_reported_status=AgentReportedStatus.UNKNOWN.value,
                active_revision_no=1,
                needs_revalidation=0,
                revalidation_reason="",
                version=1,
                created_at=now,
                updated_at=now,
            )
            s_rev1 = StepRevisionModel(
                id=str(uuid.uuid4()),
                project_id=project_id,
                task_id=task_id,
                step_id=step_id,
                revision_no=1,
                title_snapshot=step.title,
                instruction_snapshot=step.instruction,
                change_reason="初始创建步骤基线",
                changed_by=actor_type.value,
                created_at=now,
            )
            uow.session.add(step)
            uow.session.flush()
            uow.session.add(s_rev1)
            uow.record_event(
                project_id=project_id,
                entity_type="step",
                entity_id=step_id,
                event_type="step.created",
                actor_type=actor_type,
                payload={
                    "task_id": task_id,
                    "seq_order": next_seq,
                    "title": step.title,
                    "revision_no": 1,
                },
            )
            uow.commit()
            return step

    def revise_task(
        self,
        *,
        project_id: str,
        task_id: str,
        title: Optional[str] = None,
        goal: Optional[str] = None,
        acceptance_criteria: Optional[Sequence[str]] = None,
        change_reason: str = "",
        expected_version: Optional[int] = None,
        actor_type: ActorType = ActorType.USER,
    ) -> tuple[TaskModel, TaskRevisionModel, RevalidationImpact]:
        """追加新版本 TaskRevision，保留历史快照，并将本任务步骤、子任务及下游依赖任务标为 needs_revalidation。"""
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            task = uow.session.get(TaskModel, task_id)
            if task is None:
                raise EntityNotFoundError(
                    problem="待修订任务不存在",
                    cause=f"未找到任务 ID: {task_id}",
                    fix="请确认任务 ID 正确。",
                )
            if task.project_id != project_id:
                raise CrossProjectReferenceError(
                    problem="禁止跨项目修订任务",
                    cause=f"任务 {task_id} 归属项目 {task.project_id}，而非 {project_id}",
                    fix="请在任务所属项目下提交修订。",
                )
            if expected_version is not None and task.version != expected_version:
                raise VersionConflictError(
                    problem="任务已被并发修改（乐观锁冲突）",
                    cause=f"期望版本 {expected_version}，当前版本 {task.version}",
                    fix="请先读取最新任务详情后再提交修订。",
                )

            new_title = task.title if title is None else title.strip()
            if not new_title:
                raise InvariantViolationError(
                    problem="修订后的任务标题不能为空",
                    cause="title 为空白字符串",
                    fix="请提供有效的任务标题。",
                )
            new_goal = task.goal if goal is None else goal.strip()
            if acceptance_criteria is not None:
                clean_list = [str(x).strip() for x in acceptance_criteria if str(x).strip()]
                new_criteria_json = json.dumps(clean_list, ensure_ascii=False)
            else:
                new_criteria_json = task.acceptance_criteria_json

            now = utc_now_iso()
            next_rev_no = task.active_revision_no + 1
            task.title = new_title[:240]
            task.goal = new_goal
            task.acceptance_criteria_json = new_criteria_json
            task.active_revision_no = next_rev_no
            task.version += 1
            task.updated_at = now

            new_rev = TaskRevisionModel(
                id=str(uuid.uuid4()),
                project_id=project_id,
                task_id=task_id,
                revision_no=next_rev_no,
                title_snapshot=task.title,
                goal_snapshot=task.goal,
                acceptance_criteria_json=task.acceptance_criteria_json,
                change_reason=(change_reason or "任务内容修订").strip(),
                changed_by=actor_type.value,
                created_at=now,
            )
            uow.session.add(new_rev)

            reason_msg = f"上游任务 '{task.title}' 升级至修订版 r{next_rev_no}，需人工复核有效性"
            impact = self._propagate_revalidation_from_task(
                uow=uow,
                project_id=project_id,
                source_task_id=task_id,
                include_own_steps=True,
                reason=reason_msg,
                now_iso=now,
                actor_type=actor_type,
            )

            uow.record_event(
                project_id=project_id,
                entity_type="task",
                entity_id=task_id,
                event_type="task.revision_created",
                actor_type=actor_type,
                payload={
                    "revision_id": new_rev.id,
                    "revision_no": next_rev_no,
                    "change_reason": new_rev.change_reason,
                    "impacted_step_ids": impact.impacted_step_ids,
                    "impacted_task_ids": impact.impacted_task_ids,
                },
            )
            uow.commit()
            return task, new_rev, impact

    def revise_step(
        self,
        *,
        project_id: str,
        step_id: str,
        title: Optional[str] = None,
        instruction: Optional[str] = None,
        change_reason: str = "",
        expected_version: Optional[int] = None,
        actor_type: ActorType = ActorType.USER,
    ) -> tuple[StepModel, StepRevisionModel, RevalidationImpact]:
        """追加新版本 StepRevision，保留历史快照，并将同任务后继 Step (seq_order > k) 及下游依赖 Task 标为 needs_revalidation。"""
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            step = uow.session.get(StepModel, step_id)
            if step is None:
                raise EntityNotFoundError(
                    problem="待修订步骤不存在",
                    cause=f"未找到步骤 ID: {step_id}",
                    fix="请确认步骤 ID 正确。",
                )
            if step.project_id != project_id:
                raise CrossProjectReferenceError(
                    problem="禁止跨项目修订任务步骤",
                    cause=f"步骤 {step_id} 归属项目 {step.project_id}，而非 {project_id}",
                    fix="请在步骤所属项目下提交修订。",
                )
            if expected_version is not None and step.version != expected_version:
                raise VersionConflictError(
                    problem="步骤已被并发修改（乐观锁冲突）",
                    cause=f"期望版本 {expected_version}，当前版本 {step.version}",
                    fix="请先读取最新步骤详情后再提交修订。",
                )

            new_title = step.title if title is None else title.strip()
            if not new_title:
                raise InvariantViolationError(
                    problem="修订后的步骤标题不能为空",
                    cause="title 为空白字符串",
                    fix="请提供有效的步骤标题。",
                )
            new_instr = step.instruction if instruction is None else instruction.strip()

            now = utc_now_iso()
            next_rev_no = step.active_revision_no + 1
            step.title = new_title[:240]
            step.instruction = new_instr
            step.active_revision_no = next_rev_no
            step.version += 1
            step.updated_at = now

            new_rev = StepRevisionModel(
                id=str(uuid.uuid4()),
                project_id=project_id,
                task_id=step.task_id,
                step_id=step.id,
                revision_no=next_rev_no,
                title_snapshot=step.title,
                instruction_snapshot=step.instruction,
                change_reason=(change_reason or "步骤内容修订").strip(),
                changed_by=actor_type.value,
                created_at=now,
            )
            uow.session.add(new_rev)

            reason_msg = f"前置步骤 #{step.seq_order} '{step.title}' 升级至 r{next_rev_no}，需人工复核下游"
            impacted_step_ids: list[str] = []

            later_steps = list(
                uow.session.execute(
                    select(StepModel)
                    .where(
                        StepModel.task_id == step.task_id,
                        StepModel.seq_order > step.seq_order,
                    )
                    .order_by(StepModel.seq_order.asc())
                )
                .scalars()
                .all()
            )
            for ds in later_steps:
                if should_step_enter_revalidation(ds.platform_status):
                    ds.needs_revalidation = 1
                    ds.platform_status = StepPlatformStatus.NEEDS_REVALIDATION.value
                    ds.revalidation_reason = reason_msg
                    ds.version += 1
                    ds.updated_at = now
                    impacted_step_ids.append(ds.id)
                    uow.record_event(
                        project_id=project_id,
                        entity_type="step",
                        entity_id=ds.id,
                        event_type="step.marked_needs_revalidation",
                        actor_type=actor_type,
                        payload={"source_step_id": step.id, "reason": reason_msg},
                    )

            downstream_task_impact = self._propagate_revalidation_from_task(
                uow=uow,
                project_id=project_id,
                source_task_id=step.task_id,
                include_own_steps=False,
                reason=reason_msg,
                now_iso=now,
                actor_type=actor_type,
            )

            full_impact = RevalidationImpact(
                impacted_step_ids=impacted_step_ids + downstream_task_impact.impacted_step_ids,
                impacted_task_ids=downstream_task_impact.impacted_task_ids,
                reason=reason_msg,
            )

            uow.record_event(
                project_id=project_id,
                entity_type="step",
                entity_id=step.id,
                event_type="step.revision_created",
                actor_type=actor_type,
                payload={
                    "task_id": step.task_id,
                    "revision_id": new_rev.id,
                    "revision_no": next_rev_no,
                    "change_reason": new_rev.change_reason,
                    "impacted_step_ids": full_impact.impacted_step_ids,
                    "impacted_task_ids": full_impact.impacted_task_ids,
                },
            )
            uow.commit()
            return step, new_rev, full_impact

    def update_task_status(
        self,
        *,
        project_id: str,
        task_id: str,
        platform_status: Optional[TaskPlatformStatus] = None,
        agent_reported_status: Optional[AgentReportedStatus] = None,
        clear_revalidation: bool = False,
        actor_type: ActorType = ActorType.USER,
    ) -> TaskModel:
        """分离更新任务的平台管理状态与 Agent 自报状态。"""
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            task = uow.session.get(TaskModel, task_id)
            if task is None:
                raise EntityNotFoundError(
                    problem="任务不存在",
                    cause=f"未找到任务 ID: {task_id}",
                    fix="请核对任务 ID。",
                )
            if task.project_id != project_id:
                raise CrossProjectReferenceError(
                    problem="禁止跨项目更新任务状态",
                    cause=f"任务 {task_id} 不属于项目 {project_id}",
                    fix="请核对项目上下文。",
                )

            if agent_reported_status is not None:
                task.agent_reported_status = agent_reported_status.value

            if platform_status is not None:
                if actor_type == ActorType.MCP_AGENT and platform_status == TaskPlatformStatus.COMPLETED:
                    raise InvariantViolationError(
                        problem="禁止外部 Agent 直接将任务平台验收状态标记为已完成",
                        cause="违反核心不变量 #7：Agent 自报完成不等于平台验收通过",
                        fix="Agent 请仅更新 agent_reported_status='completed'，由用户在平台确认验收。",
                    )
                task.platform_status = platform_status.value

            if clear_revalidation:
                task.needs_revalidation = 0
                task.revalidation_reason = ""
                if task.platform_status == TaskPlatformStatus.NEEDS_REVALIDATION.value and platform_status is None:
                    task.platform_status = TaskPlatformStatus.READY.value

            task.version += 1
            task.updated_at = utc_now_iso()

            uow.record_event(
                project_id=project_id,
                entity_type="task",
                entity_id=task.id,
                event_type="task.status_updated",
                actor_type=actor_type,
                payload={
                    "platform_status": task.platform_status,
                    "agent_reported_status": task.agent_reported_status,
                    "needs_revalidation": task.needs_revalidation,
                },
            )
            uow.commit()
            return task

    def update_step_status(
        self,
        *,
        project_id: str,
        step_id: str,
        platform_status: Optional[StepPlatformStatus] = None,
        agent_reported_status: Optional[AgentReportedStatus] = None,
        clear_revalidation: bool = False,
        actor_type: ActorType = ActorType.USER,
    ) -> StepModel:
        """分离更新步骤的平台管理状态与 Agent 自报状态。"""
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            step = uow.session.get(StepModel, step_id)
            if step is None:
                raise EntityNotFoundError(
                    problem="步骤不存在",
                    cause=f"未找到步骤 ID: {step_id}",
                    fix="请核对步骤 ID。",
                )
            if step.project_id != project_id:
                raise CrossProjectReferenceError(
                    problem="禁止跨项目更新步骤状态",
                    cause=f"步骤 {step_id} 不属于项目 {project_id}",
                    fix="请核对项目上下文。",
                )

            if agent_reported_status is not None:
                step.agent_reported_status = agent_reported_status.value

            if platform_status is not None:
                if actor_type == ActorType.MCP_AGENT and platform_status == StepPlatformStatus.COMPLETED:
                    raise InvariantViolationError(
                        problem="禁止外部 Agent 直接将步骤平台验收状态标记为已完成",
                        cause="违反核心不变量 #7：Agent 自报完成不等于平台验收通过",
                        fix="Agent 请仅更新 agent_reported_status='completed'，由用户在平台确认验收。",
                    )
                step.platform_status = platform_status.value

            if clear_revalidation:
                step.needs_revalidation = 0
                step.revalidation_reason = ""
                if step.platform_status == StepPlatformStatus.NEEDS_REVALIDATION.value and platform_status is None:
                    step.platform_status = StepPlatformStatus.READY.value

            step.version += 1
            step.updated_at = utc_now_iso()

            uow.record_event(
                project_id=project_id,
                entity_type="step",
                entity_id=step.id,
                event_type="step.status_updated",
                actor_type=actor_type,
                payload={
                    "platform_status": step.platform_status,
                    "agent_reported_status": step.agent_reported_status,
                    "needs_revalidation": step.needs_revalidation,
                },
            )
            uow.commit()
            return step

    def list_project_tasks(self, project_id: str) -> list[TaskModel]:
        """获取项目下全部任务（按 sort_order 与创建时间升序）。"""
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            stmt = (
                select(TaskModel)
                .where(TaskModel.project_id == project_id)
                .order_by(TaskModel.sort_order.asc(), TaskModel.created_at.asc())
            )
            return list(uow.session.execute(stmt).scalars().all())

    def list_task_revisions(self, project_id: str, task_id: str) -> list[TaskRevisionModel]:
        """按版本号升序返回任务的全部历史修订记录。"""
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            stmt = (
                select(TaskRevisionModel)
                .where(
                    TaskRevisionModel.project_id == project_id,
                    TaskRevisionModel.task_id == task_id,
                )
                .order_by(TaskRevisionModel.revision_no.asc())
            )
            return list(uow.session.execute(stmt).scalars().all())

    def list_step_revisions(self, project_id: str, step_id: str) -> list[StepRevisionModel]:
        """按版本号升序返回步骤的全部历史修订记录。"""
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            stmt = (
                select(StepRevisionModel)
                .where(
                    StepRevisionModel.project_id == project_id,
                    StepRevisionModel.step_id == step_id,
                )
                .order_by(StepRevisionModel.revision_no.asc())
            )
            return list(uow.session.execute(stmt).scalars().all())

    def list_task_steps(self, project_id: str, task_id: str) -> list[StepModel]:
        """按 seq_order 升序返回任务下全部步骤。"""
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            stmt = (
                select(StepModel)
                .where(StepModel.project_id == project_id, StepModel.task_id == task_id)
                .order_by(StepModel.seq_order.asc())
            )
            return list(uow.session.execute(stmt).scalars().all())

    def list_project_steps(self, project_id: str) -> list[StepModel]:
        """按 task_id 与 seq_order 返回项目下全部步骤。"""
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            stmt = (
                select(StepModel)
                .where(StepModel.project_id == project_id)
                .order_by(StepModel.task_id.asc(), StepModel.seq_order.asc())
            )
            return list(uow.session.execute(stmt).scalars().all())

    def _propagate_revalidation_from_task(
        self,
        *,
        uow: SqlAlchemyUnitOfWork,
        project_id: str,
        source_task_id: str,
        include_own_steps: bool,
        reason: str,
        now_iso: str,
        actor_type: ActorType,
    ) -> RevalidationImpact:
        """在当前事务中将受影响步骤、后代子任务及依赖图下游任务标记为 needs_revalidation。"""
        assert uow.session is not None
        impacted_steps: list[str] = []
        impacted_tasks: list[str] = []

        if include_own_steps:
            own_steps = list(
                uow.session.execute(
                    select(StepModel)
                    .where(StepModel.task_id == source_task_id)
                    .order_by(StepModel.seq_order.asc())
                )
                .scalars()
                .all()
            )
            for st in own_steps:
                if should_step_enter_revalidation(st.platform_status):
                    st.needs_revalidation = 1
                    st.platform_status = StepPlatformStatus.NEEDS_REVALIDATION.value
                    st.revalidation_reason = reason
                    st.version += 1
                    st.updated_at = now_iso
                    impacted_steps.append(st.id)
                    uow.record_event(
                        project_id=project_id,
                        entity_type="step",
                        entity_id=st.id,
                        event_type="step.marked_needs_revalidation",
                        actor_type=actor_type,
                        payload={"source_task_id": source_task_id, "reason": reason},
                    )

        dep_rows = uow.session.execute(
            select(TaskDependencyModel.task_id, TaskDependencyModel.depends_on_task_id).where(
                TaskDependencyModel.project_id == project_id
            )
        ).all()
        edges: list[tuple[str, str]] = [(str(r[0]), str(r[1])) for r in dep_rows]

        child_rows = uow.session.execute(
            select(TaskModel.id, TaskModel.parent_task_id).where(
                TaskModel.project_id == project_id,
                TaskModel.parent_task_id.is_not(None),
            )
        ).all()
        for child_id, parent_id in child_rows:
            if parent_id:
                edges.append((str(child_id), str(parent_id)))

        downstream_ids = find_downstream_dependent_task_ids(source_task_id, edges)
        if downstream_ids:
            downstream_tasks = list(
                uow.session.execute(select(TaskModel).where(TaskModel.id.in_(downstream_ids)))
                .scalars()
                .all()
            )
            for dt in downstream_tasks:
                if should_task_enter_revalidation(dt.platform_status):
                    dt.needs_revalidation = 1
                    dt.platform_status = TaskPlatformStatus.NEEDS_REVALIDATION.value
                    dt.revalidation_reason = reason
                    dt.version += 1
                    dt.updated_at = now_iso
                    impacted_tasks.append(dt.id)
                    uow.record_event(
                        project_id=project_id,
                        entity_type="task",
                        entity_id=dt.id,
                        event_type="task.marked_needs_revalidation",
                        actor_type=actor_type,
                        payload={"source_task_id": source_task_id, "reason": reason},
                    )

        return RevalidationImpact(
            impacted_step_ids=impacted_steps,
            impacted_task_ids=impacted_tasks,
            reason=reason,
        )

