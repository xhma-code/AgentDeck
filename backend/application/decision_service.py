# -*- coding: utf-8 -*-
"""
交互式决策点与审批应用服务（DecisionService）。

核心领域规则（Clean-Slate 授权规范 & v1.3）：
1. Decision 具备完整生命周期：pending / resolved / expired / cancelled。
2. 创建 Decision 时：
   - 强制校验高风险决策（risk_level in [high, critical] 或 decision_type == risk_gate）严禁使用 auto_submit_recommended；
   - 自动将关联的 Task、Step、Session、Execution 置为 `waiting_interaction` 状态，唤醒前端全局决策弹窗；
   - 同事务写入 `decision.created` 事件。
3. 回答 Decision（resolve_decision）时：
   - 支持幂等提交（Idempotency-Key）：若同一幂等键或相同答案重复提交，返回幂等成功结果而不重复变更；
   - 若已被其他答案处理（如人工抢先提交或超时自动提交），抛出 409 VersionConflictError 并返回当前状态；
   - 若超时自动提交（resolved_by == timeout_auto_recommended），再次严格校验 timeout_policy 与风险等级，并记录 `decision.auto_recommended_submitted` 审计事件；
   - 当关联对象下所有 pending Decision 全部结案后，自动将关联对象从 `waiting_interaction` 恢复至活跃执行态。
"""

from __future__ import annotations

import json
import uuid
from typing import Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.db.base import utc_now_iso
from backend.db.models.decision import DecisionModel
from backend.db.models.execution import ExecutionModel
from backend.db.models.planning import StepModel, TaskModel
from backend.db.models.project import ProjectModel
from backend.db.models.session import SessionModel
from backend.db.models.system import CommandIdempotencyModel
from backend.db.uow import SqlAlchemyUnitOfWork
from backend.domain.enums import (
    ActorType,
    DecisionResolvedBy,
    DecisionRiskLevel,
    DecisionStatus,
    DecisionTimeoutPolicy,
    DecisionType,
    ExecutionPlatformStatus,
    SessionPlatformStatus,
    StepPlatformStatus,
    TaskPlatformStatus,
)
from backend.domain.exceptions import (
    CrossProjectReferenceError,
    EntityNotFoundError,
    InvariantViolationError,
    VersionConflictError,
)
from backend.domain.services.decision_policy import (
    validate_decision_creation_policy,
    validate_decision_resolution_policy,
)
from backend.domain.types import DecisionOptionItem


class DecisionService:
    """管理全局交互式 Decision 创建、跨页唤醒查询与幂等裁决提交。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create_decision(
        self,
        *,
        project_id: str,
        title: str,
        context_summary: str = "",
        decision_type: DecisionType = DecisionType.INTERACTIVE_QUESTION,
        risk_level: DecisionRiskLevel = DecisionRiskLevel.NORMAL,
        options: Optional[Sequence[DecisionOptionItem | dict[str, object]]] = None,
        recommended_option_id: Optional[str] = None,
        allow_custom_input: bool = True,
        timeout_policy: DecisionTimeoutPolicy = DecisionTimeoutPolicy.AUTO_SUBMIT_RECOMMENDED,
        timeout_sec: int = 45,
        task_id: Optional[str] = None,
        step_id: Optional[str] = None,
        session_id: Optional[str] = None,
        execution_id: Optional[str] = None,
        actor_type: ActorType = ActorType.MCP_AGENT,
    ) -> DecisionModel:
        """创建待确认 Decision，联动关联实体进入 waiting_interaction 状态并写入事件。"""
        clean_title = (title or "").strip()
        if not clean_title:
            raise InvariantViolationError(
                problem="决策问题主标题不能为空",
                cause="传入了空白 title",
                fix="请提供清晰简明的决策问题标题。",
            )

        parsed_options: list[DecisionOptionItem] = []
        for raw_opt in options or ():
            if isinstance(raw_opt, DecisionOptionItem):
                parsed_options.append(raw_opt)
            else:
                parsed_options.append(DecisionOptionItem.model_validate(raw_opt))

        resolved_rec_id = validate_decision_creation_policy(
            decision_type=decision_type,
            risk_level=risk_level,
            timeout_policy=timeout_policy,
            timeout_sec=timeout_sec,
            options=parsed_options,
            recommended_option_id=recommended_option_id,
        )

        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            proj = uow.session.get(ProjectModel, project_id)
            if proj is None:
                raise EntityNotFoundError(
                    problem="创建决策关联的项目不存在",
                    cause=f"未找到 project_id: {project_id}",
                    fix="请确认项目已创建。",
                )

            now = utc_now_iso()

            if task_id is not None:
                task = uow.session.get(TaskModel, task_id)
                if task is None:
                    raise EntityNotFoundError(
                        problem="决策关联的任务不存在",
                        cause=f"未找到 task_id: {task_id}",
                        fix="请确认任务 ID 正确。",
                    )
                if task.project_id != project_id:
                    raise CrossProjectReferenceError(
                        problem="禁止跨项目创建任务决策",
                        cause=f"任务 {task_id} 不属于项目 {project_id}",
                        fix="请核对项目上下文。",
                    )
                task.platform_status = TaskPlatformStatus.WAITING_INTERACTION.value
                task.version += 1
                task.updated_at = now

            if step_id is not None:
                step = uow.session.get(StepModel, step_id)
                if step is None:
                    raise EntityNotFoundError(
                        problem="决策关联的步骤不存在",
                        cause=f"未找到 step_id: {step_id}",
                        fix="请确认步骤 ID 正确。",
                    )
                if step.project_id != project_id:
                    raise CrossProjectReferenceError(
                        problem="禁止跨项目创建步骤决策",
                        cause=f"步骤 {step_id} 不属于项目 {project_id}",
                        fix="请核对项目上下文。",
                    )
                step.platform_status = StepPlatformStatus.WAITING_INTERACTION.value
                step.version += 1
                step.updated_at = now

            if session_id is not None:
                sess = uow.session.get(SessionModel, session_id)
                if sess is None:
                    raise EntityNotFoundError(
                        problem="决策关联的协同会话不存在",
                        cause=f"未找到 session_id: {session_id}",
                        fix="请确认会话 ID 正确。",
                    )
                if sess.project_id != project_id:
                    raise CrossProjectReferenceError(
                        problem="禁止跨项目创建会话决策",
                        cause=f"会话 {session_id} 不属于项目 {project_id}",
                        fix="请核对项目上下文。",
                    )
                sess.platform_status = SessionPlatformStatus.WAITING_INTERACTION.value
                sess.status_seq += 1
                sess.last_activity_at = now
                sess.updated_at = now

            if execution_id is not None:
                execution = uow.session.get(ExecutionModel, execution_id)
                if execution is None:
                    raise EntityNotFoundError(
                        problem="决策关联的执行活动不存在",
                        cause=f"未找到 execution_id: {execution_id}",
                        fix="请确认执行 ID 正确。",
                    )
                if execution.project_id != project_id:
                    raise CrossProjectReferenceError(
                        problem="禁止跨项目绑定执行决策",
                        cause=f"执行 {execution_id} 不属于项目 {project_id}",
                        fix="请核对项目上下文。",
                    )
                execution.platform_status = ExecutionPlatformStatus.WAITING_INTERACTION.value
                execution.updated_at = now

            decision_id = str(uuid.uuid4())
            options_json = json.dumps([o.model_dump() for o in parsed_options], ensure_ascii=False)

            decision = DecisionModel(
                id=decision_id,
                project_id=project_id,
                task_id=task_id,
                step_id=step_id,
                session_id=session_id,
                execution_id=execution_id,
                decision_type=decision_type.value,
                risk_level=risk_level.value,
                title=clean_title[:240],
                context_summary=(context_summary or "").strip(),
                options_json=options_json,
                recommended_option_id=resolved_rec_id,
                allow_custom_input=1 if allow_custom_input else 0,
                timeout_policy=timeout_policy.value,
                timeout_sec=timeout_sec,
                status=DecisionStatus.PENDING.value,
                chosen_option_id=None,
                custom_note="",
                resolution_payload_json="{}",
                resolved_by=None,
                resolution_idempotency_key=None,
                version=1,
                created_at=now,
                resolved_at=None,
                updated_at=now,
            )
            uow.session.add(decision)
            uow.record_event(
                project_id=project_id,
                entity_type="decision",
                entity_id=decision_id,
                event_type="decision.created",
                actor_type=actor_type,
                payload={
                    "title": decision.title,
                    "decision_type": decision.decision_type,
                    "risk_level": decision.risk_level,
                    "timeout_policy": decision.timeout_policy,
                    "timeout_sec": decision.timeout_sec,
                    "recommended_option_id": decision.recommended_option_id,
                    "task_id": task_id,
                    "step_id": step_id,
                    "session_id": session_id,
                },
            )
            uow.commit()
            return decision

    def resolve_decision(
        self,
        *,
        decision_id: str,
        chosen_option_id: Optional[str] = None,
        custom_note: str = "",
        resolved_by: DecisionResolvedBy = DecisionResolvedBy.USER_MANUAL,
        idempotency_key: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> tuple[DecisionModel, bool]:
        """
        提交决策答案。
        返回 `(DecisionModel, is_idempotent_replay)`：
        - 若首次成功处理，返回 `(decision, False)`；
        - 若携带相同 `idempotency_key` 或完全相同的答案重复提交，返回 `(decision, True)`；
        - 若决策已被不同答案或状态结案，抛出 409 `VersionConflictError`。
        """
        clean_idem = (idempotency_key or "").strip() or None
        clean_note = (custom_note or "").strip()

        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            decision = uow.session.get(DecisionModel, decision_id)
            if decision is None:
                raise EntityNotFoundError(
                    problem="目标决策记录不存在",
                    cause=f"未找到 decision_id: {decision_id}",
                    fix="请刷新待决策列表后重试。",
                )
            if project_id is not None and decision.project_id != project_id:
                raise CrossProjectReferenceError(
                    problem="禁止跨项目提交决策答案",
                    cause=f"决策 {decision_id} 不属于项目 {project_id}",
                    fix="请核对项目上下文。",
                )

            effective_option_id = (chosen_option_id or "").strip() or None
            if (
                resolved_by == DecisionResolvedBy.TIMEOUT_AUTO_RECOMMENDED
                and effective_option_id is None
            ):
                effective_option_id = decision.recommended_option_id

            # 1. 幂等重放判定：若已 resolved 且幂等键一致（或所选选项+文本完全一致），直接视作幂等成功
            if decision.status == DecisionStatus.RESOLVED.value:
                if clean_idem and decision.resolution_idempotency_key == clean_idem:
                    return decision, True
                if (
                    decision.chosen_option_id == effective_option_id
                    and (decision.custom_note or "") == clean_note
                ):
                    return decision, True

                raise VersionConflictError(
                    problem="该决策点已被处理，无法覆盖提交",
                    cause=f"当前状态为 '{decision.status}'，处理来源为 '{decision.resolved_by}'",
                    fix="当前决策已完成同步，前端弹窗可直接关闭并刷新状态。",
                    context={
                        "decision_id": decision.id,
                        "current_status": decision.status,
                        "resolved_by": decision.resolved_by or "",
                        "chosen_option_id": decision.chosen_option_id or "",
                    },
                )

            if decision.status != DecisionStatus.PENDING.value:
                raise VersionConflictError(
                    problem="该决策点已失效或取消，无法提交答案",
                    cause=f"当前决策状态为 '{decision.status}'（非 pending）",
                    fix="请刷新页面查看最新执行状态。",
                    context={"decision_id": decision.id, "current_status": decision.status},
                )

            options_list = [
                DecisionOptionItem.model_validate(item)
                for item in json.loads(decision.options_json or "[]")
            ]
            valid_option_ids = {opt.option_id for opt in options_list}

            if not effective_option_id and not (decision.allow_custom_input and clean_note):
                raise InvariantViolationError(
                    problem="必须选择一个决策选项或填写补充说明",
                    cause="chosen_option_id 与 custom_note 同时为空",
                    fix="请选择候选选项或填写有效决策意见。",
                )

            if effective_option_id or resolved_by == DecisionResolvedBy.TIMEOUT_AUTO_RECOMMENDED:
                validate_decision_resolution_policy(
                    decision_type=decision.decision_type,
                    risk_level=decision.risk_level,
                    timeout_policy=decision.timeout_policy,
                    recommended_option_id=decision.recommended_option_id,
                    chosen_option_id=effective_option_id or "",
                    resolved_by=resolved_by,
                    valid_option_ids=valid_option_ids,
                )

            now = utc_now_iso()
            resolution_payload = {
                "chosen_option_id": effective_option_id,
                "custom_note": clean_note,
                "resolved_by": resolved_by.value,
                "resolved_at": now,
            }
            decision.status = DecisionStatus.RESOLVED.value
            decision.chosen_option_id = effective_option_id
            decision.custom_note = clean_note
            decision.resolution_payload_json = json.dumps(resolution_payload, ensure_ascii=False)
            decision.resolved_by = resolved_by.value
            decision.resolution_idempotency_key = clean_idem
            decision.resolved_at = now
            decision.version += 1
            decision.updated_at = now

            if clean_idem:
                existing_cmd = uow.session.get(CommandIdempotencyModel, clean_idem)
                if existing_cmd is None:
                    uow.session.add(
                        CommandIdempotencyModel(
                            idempotency_key=clean_idem,
                            command_type="decision.resolve",
                            target_entity_type="decision",
                            target_entity_id=decision.id,
                            status="done",
                            response_snapshot_json=decision.resolution_payload_json,
                            created_at=now,
                            updated_at=now,
                        )
                    )

            # 2. 检查关联实体是否还有其他未处理的 pending 决策，若无则解除 waiting_interaction
            self._restore_waiting_entities_if_unblocked(uow, decision, now)

            actor = (
                ActorType.USER
                if resolved_by == DecisionResolvedBy.USER_MANUAL
                else ActorType.SYSTEM
            )
            evt_type = (
                "decision.auto_recommended_submitted"
                if resolved_by == DecisionResolvedBy.TIMEOUT_AUTO_RECOMMENDED
                else "decision.resolved"
            )
            uow.record_event(
                project_id=decision.project_id,
                entity_type="decision",
                entity_id=decision.id,
                event_type=evt_type,
                actor_type=actor,
                payload={
                    "chosen_option_id": decision.chosen_option_id,
                    "has_custom_note": bool(clean_note),
                    "resolved_by": decision.resolved_by,
                    "task_id": decision.task_id,
                    "step_id": decision.step_id,
                    "session_id": decision.session_id,
                },
            )
            uow.commit()
            return decision, False

    def get_decision(self, decision_id: str) -> DecisionModel:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            decision = uow.session.get(DecisionModel, decision_id)
            if decision is None:
                raise EntityNotFoundError(
                    problem="目标决策记录不存在",
                    cause=f"未找到 decision_id: {decision_id}",
                    fix="请确认决策 ID 正确。",
                )
            return decision

    def list_decisions(
        self,
        *,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        status: Optional[DecisionStatus | str] = None,
        limit: int = 100,
    ) -> list[DecisionModel]:
        """按项目、会话、任务或状态筛选决策列表（默认按创建时间倒序）。"""
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            stmt = select(DecisionModel)
            if project_id:
                stmt = stmt.where(DecisionModel.project_id == project_id)
            if session_id:
                stmt = stmt.where(DecisionModel.session_id == session_id)
            if task_id:
                stmt = stmt.where(DecisionModel.task_id == task_id)
            if status:
                st_val = status.value if isinstance(status, DecisionStatus) else str(status)
                stmt = stmt.where(DecisionModel.status == st_val)
            stmt = stmt.order_by(DecisionModel.created_at.desc()).limit(limit)
            return list(uow.session.execute(stmt).scalars().all())

    def _restore_waiting_entities_if_unblocked(
        self,
        uow: SqlAlchemyUnitOfWork,
        resolved_decision: DecisionModel,
        now_iso: str,
    ) -> None:
        """若关联的 Task/Step/Session/Execution 已无其他 pending Decision，将其从 waiting_interaction 恢复为活跃态。"""
        assert uow.session is not None
        uow.session.flush()

        if resolved_decision.step_id:
            rem_step = uow.session.execute(
                select(func.count(DecisionModel.id)).where(
                    DecisionModel.step_id == resolved_decision.step_id,
                    DecisionModel.status == DecisionStatus.PENDING.value,
                )
            ).scalar()
            if not rem_step:
                step = uow.session.get(StepModel, resolved_decision.step_id)
                if step and step.platform_status == StepPlatformStatus.WAITING_INTERACTION.value:
                    step.platform_status = StepPlatformStatus.IN_PROGRESS.value
                    step.version += 1
                    step.updated_at = now_iso

        if resolved_decision.task_id:
            rem_task = uow.session.execute(
                select(func.count(DecisionModel.id)).where(
                    DecisionModel.task_id == resolved_decision.task_id,
                    DecisionModel.status == DecisionStatus.PENDING.value,
                )
            ).scalar()
            if not rem_task:
                task = uow.session.get(TaskModel, resolved_decision.task_id)
                if task and task.platform_status == TaskPlatformStatus.WAITING_INTERACTION.value:
                    task.platform_status = TaskPlatformStatus.IN_PROGRESS.value
                    task.version += 1
                    task.updated_at = now_iso

        if resolved_decision.session_id:
            rem_sess = uow.session.execute(
                select(func.count(DecisionModel.id)).where(
                    DecisionModel.session_id == resolved_decision.session_id,
                    DecisionModel.status == DecisionStatus.PENDING.value,
                )
            ).scalar()
            if not rem_sess:
                sess = uow.session.get(SessionModel, resolved_decision.session_id)
                if sess and sess.platform_status == SessionPlatformStatus.WAITING_INTERACTION.value:
                    sess.platform_status = SessionPlatformStatus.ACTIVE.value
                    sess.status_seq += 1
                    sess.last_activity_at = now_iso
                    sess.updated_at = now_iso

        if resolved_decision.execution_id:
            rem_exec = uow.session.execute(
                select(func.count(DecisionModel.id)).where(
                    DecisionModel.execution_id == resolved_decision.execution_id,
                    DecisionModel.status == DecisionStatus.PENDING.value,
                )
            ).scalar()
            if not rem_exec:
                execution = uow.session.get(ExecutionModel, resolved_decision.execution_id)
                if execution and execution.platform_status == ExecutionPlatformStatus.WAITING_INTERACTION.value:
                    execution.platform_status = ExecutionPlatformStatus.RUNNING.value
                    execution.updated_at = now_iso
