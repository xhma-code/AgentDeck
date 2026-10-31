# -*- coding: utf-8 -*-
"""
MCP 写操作事务级原子幂等服务（McpIdempotencyService）。

严格落实 Phase 4 审阅阻塞项 5：
1. 幂等记录（command_idempotency）、业务数据写入、events 审计事件与响应快照必须在**同一个数据库事务**中完成。
2. 利用 command_idempotency 主键唯一约束 + 同事务预占位 flush，拦截双线程并发重复创建（仅产生一份业务结果与一条审计事件）。
3. 相同 idempotency_key + 相同参数指纹重放（含网络中断后客户端重试）：直接返回首次成功响应快照（标记 is_idempotent_replay=True）。
4. 相同 idempotency_key + 不同参数指纹：拒绝并抛出 MCP_IDEMPOTENCY_CONFLICT。
5. 业务执行抛错：整个事务回滚，不残留任何成功或半成品的幂等快照。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from backend.db.base import utc_now_iso
from backend.db.models.system import CommandIdempotencyModel
from backend.db.uow import SqlAlchemyUnitOfWork
from backend.domain.exceptions import InvariantViolationError


def compute_payload_fingerprint(payload: dict[str, Any]) -> str:
    """计算排除 idempotency_key 与 _meta 后的规范化参数 SHA-256 指纹。"""
    filtered = {
        k: v
        for k, v in payload.items()
        if k not in ("idempotency_key", "_meta")
    }
    canonical = json.dumps(filtered, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_scoped_idempotency_key(project_id: str, tool_name: str, raw_key: str) -> str:
    """构造绑定项目与工具名的复合幂等主键（长度控制在 120 字符以内）。"""
    clean_key = (raw_key or "").strip()
    if not clean_key:
        raise InvariantViolationError(
            problem="MCP 写操作缺少必填的 idempotency_key",
            cause=f"工具 '{tool_name}' 要求提供非空 idempotency_key",
            fix="请为每次写操作生成唯一的幂等键（如 UUID），并在超时重试时保持不变。",
            context={"tool_name": tool_name, "mcp_error_code": "MCP_STATE_NOT_ALLOWED"},
        )
    prefix = f"mcp:{project_id[:18]}:{tool_name[:28]}:"
    if len(prefix) + len(clean_key) <= 120:
        return f"{prefix}{clean_key}"
    key_hash = hashlib.sha256(clean_key.encode("utf-8")).hexdigest()[:40]
    return f"{prefix}{key_hash}"


class McpIdempotencyService:
    """同事务原子幂等执行器。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def execute_idempotent(
        self,
        *,
        project_id: str,
        tool_name: str,
        idempotency_key: str,
        payload: dict[str, Any],
        operation: Callable[[SqlAlchemyUnitOfWork], tuple[str, str, dict[str, Any]]],
    ) -> tuple[dict[str, Any], bool]:
        """
        在单一 SQLite 事务中原子执行幂等检查、业务操作、事件记录与快照保存。
        operation(uow) 需返回 (target_entity_type, target_entity_id, response_data_dict)，且内部不得提前调用 uow.commit()。
        返回值: (response_data_dict, is_idempotent_replay)
        """
        scoped_key = build_scoped_idempotency_key(project_id, tool_name, idempotency_key)
        payload_hash = compute_payload_fingerprint(payload)

        try:
            with SqlAlchemyUnitOfWork(self._session_factory) as uow:
                assert uow.session is not None
                existing = uow.session.get(CommandIdempotencyModel, scoped_key)
                if existing is not None:
                    return self._resolve_existing_record(existing, payload_hash, tool_name, idempotency_key)

                now = utc_now_iso()
                record = CommandIdempotencyModel(
                    idempotency_key=scoped_key,
                    command_type=f"mcp.{tool_name}",
                    target_entity_type="",
                    target_entity_id="",
                    status="pending",
                    response_snapshot_json=json.dumps(
                        {"_payload_hash": payload_hash}, ensure_ascii=False
                    ),
                    created_at=now,
                    updated_at=now,
                )
                uow.session.add(record)
                # 立即 flush 获取 SQLite 写锁并触发主键唯一约束检查，阻断并发穿透
                uow.session.flush()

                entity_type, entity_id, response_data = operation(uow)

                snapshot_envelope = {
                    "_payload_hash": payload_hash,
                    "data": response_data,
                }
                record.target_entity_type = (entity_type or "")[:40]
                record.target_entity_id = (entity_id or "")[:64]
                record.status = "done"
                record.response_snapshot_json = json.dumps(
                    snapshot_envelope, ensure_ascii=False, separators=(",", ":")
                )
                record.updated_at = utc_now_iso()

                # 同事务原子提交：业务表 + events 表 + command_idempotency 表
                uow.commit()
                return response_data, False

        except IntegrityError:
            # 双线程同时到达且另一线程已赢得唯一键插入并提交，回查已提交记录
            with SqlAlchemyUnitOfWork(self._session_factory) as retry_uow:
                assert retry_uow.session is not None
                committed = retry_uow.session.get(CommandIdempotencyModel, scoped_key)
                if committed is not None:
                    return self._resolve_existing_record(
                        committed, payload_hash, tool_name, idempotency_key
                    )
            raise

    @staticmethod
    def _resolve_existing_record(
        record: CommandIdempotencyModel,
        payload_hash: str,
        tool_name: str,
        raw_key: str,
    ) -> tuple[dict[str, Any], bool]:
        """比对已有幂等记录的参数指纹，一致则重放首次结果，不一致则抛冲突异常。"""
        try:
            stored = json.loads(record.response_snapshot_json or "{}")
        except json.JSONDecodeError:
            stored = {}

        stored_hash = stored.get("_payload_hash", "")
        if stored_hash and stored_hash != payload_hash:
            raise InvariantViolationError(
                problem="幂等键冲突（同一 idempotency_key 被用于不同参数的请求）",
                cause=f"工具 '{tool_name}' 的幂等键 '{raw_key}' 已绑定其他请求参数指纹",
                fix="不同参数的写请求必须生成新的 idempotency_key；仅对同一请求的重试复用原幂等键。",
                context={
                    "tool_name": tool_name,
                    "idempotency_key": raw_key,
                    "mcp_error_code": "MCP_IDEMPOTENCY_CONFLICT",
                },
            )

        data = stored.get("data", {})
        if not isinstance(data, dict):
            data = {"result": data}
        return data, True

