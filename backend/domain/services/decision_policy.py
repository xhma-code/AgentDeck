# -*- coding: utf-8 -*-
"""
交互式 Decision 领域规则与超时安全策略。

核心铁律（第 四 节）：
1. 高风险决策 (risk_level in ('high', 'critical') 或 decision_type == 'risk_gate')
   严禁启用 `auto_submit_recommended` 超时自动提交，必须强制由人工显式确认 (`manual_only`)。
2. 45 秒超时自动推荐提交 (`resolved_by == 'timeout_auto_recommended'`) 仅在：
   - `timeout_policy == 'auto_submit_recommended'`
   - `risk_level in ('low', 'normal')`
   - 存在明确有效的 `recommended_option_id`
   时才允许执行，否则拒绝自动通过并留痕。
"""

from __future__ import annotations

from typing import Optional, Sequence

from backend.domain.enums import (
    DecisionResolvedBy,
    DecisionRiskLevel,
    DecisionTimeoutPolicy,
    DecisionType,
)
from backend.domain.exceptions import InvariantViolationError
from backend.domain.types import DecisionOptionItem

HIGH_RISK_LEVELS = {DecisionRiskLevel.HIGH.value, DecisionRiskLevel.CRITICAL.value}


def validate_decision_creation_policy(
    *,
    decision_type: DecisionType,
    risk_level: DecisionRiskLevel,
    timeout_policy: DecisionTimeoutPolicy,
    timeout_sec: int,
    options: Sequence[DecisionOptionItem],
    recommended_option_id: Optional[str],
) -> Optional[str]:
    """
    校验 Decision 创建时的风险等级、超时策略与推荐项一致性，返回规范化后的 recommended_option_id。
    """
    if timeout_sec < 0 or timeout_sec > 86400:
        raise InvariantViolationError(
            problem="决策倒计时秒数超出合法范围",
            cause=f"收到 timeout_sec={timeout_sec}",
            fix="请将 timeout_sec 设置在 0 至 86400 秒之间（常用交互推荐为 45 秒）。",
        )

    is_high_risk = (
        risk_level.value in HIGH_RISK_LEVELS or decision_type == DecisionType.RISK_GATE
    )
    if is_high_risk and timeout_policy == DecisionTimeoutPolicy.AUTO_SUBMIT_RECOMMENDED:
        raise InvariantViolationError(
            problem="高风险决策或风险门禁严禁启用超时自动推荐提交",
            cause=f"decision_type={decision_type.value}, risk_level={risk_level.value} 不允许设置 timeout_policy='auto_submit_recommended'",
            fix="高风险决策必须将 timeout_policy 设为 'manual_only' 或 'expire_on_timeout'，由用户显式拍板。",
        )

    option_ids = {opt.option_id for opt in options}
    resolved_rec = (recommended_option_id or "").strip() or None
    if resolved_rec is None:
        for opt in options:
            if opt.is_recommended:
                resolved_rec = opt.option_id
                break

    if resolved_rec is not None and option_ids and resolved_rec not in option_ids:
        raise InvariantViolationError(
            problem="推荐选项 ID 不在候选选项列表中",
            cause=f"recommended_option_id='{resolved_rec}' 未出现在 options 列表 {sorted(option_ids)} 中",
            fix="请确保推荐选项 ID 与候选选项的 option_id 完全一致。",
        )

    if timeout_policy == DecisionTimeoutPolicy.AUTO_SUBMIT_RECOMMENDED and not resolved_rec:
        raise InvariantViolationError(
            problem="启用超时自动推荐提交时必须指定明确的推荐选项",
            cause="timeout_policy='auto_submit_recommended' 但未提供 recommended_option_id",
            fix="请为至少一个候选选项标记 is_recommended=True，或改为 manual_only 策略。",
        )

    return resolved_rec


def validate_decision_resolution_policy(
    *,
    decision_type: str,
    risk_level: str,
    timeout_policy: str,
    recommended_option_id: Optional[str],
    chosen_option_id: str,
    resolved_by: DecisionResolvedBy,
    valid_option_ids: set[str],
) -> None:
    """校验拍板提交（人工或 45s 超时自动提交）是否符合安全策略。"""
    if valid_option_ids and chosen_option_id not in valid_option_ids:
        raise InvariantViolationError(
            problem="提交的决策选项不在有效候选列表中",
            cause=f"chosen_option_id='{chosen_option_id}' 不属于 {sorted(valid_option_ids)}",
            fix="请选择候选列表中的合法选项 ID。",
        )

    if resolved_by == DecisionResolvedBy.TIMEOUT_AUTO_RECOMMENDED:
        if risk_level in HIGH_RISK_LEVELS or decision_type == DecisionType.RISK_GATE.value:
            raise InvariantViolationError(
                problem="拦截高风险决策的超时自动提交",
                cause=f"高风险决策 (risk_level={risk_level}, type={decision_type}) 严禁自动通过",
                fix="该决策必须由用户手动确认提交。",
            )
        if timeout_policy != DecisionTimeoutPolicy.AUTO_SUBMIT_RECOMMENDED.value:
            raise InvariantViolationError(
                problem="当前决策不允许超时自动推荐提交",
                cause=f"决策的 timeout_policy 为 '{timeout_policy}'，非 'auto_submit_recommended'",
                fix="请由人工手动选择并提交该决策。",
            )
        if not recommended_option_id or chosen_option_id != recommended_option_id:
            raise InvariantViolationError(
                problem="超时自动提交只能采纳预设的推荐选项",
                cause=f"自动提交选项 '{chosen_option_id}' 与预设推荐项 '{recommended_option_id}' 不一致",
                fix="超时自动提交必须严格传入 recommended_option_id。",
            )

