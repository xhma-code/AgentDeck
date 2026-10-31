# -*- coding: utf-8 -*-
"""
MCP Project-scoped Token 安全签发与严格作用域鉴权守卫。

安全铁律（v1.3 第 13.5 节 & Gate 0 第 6 条）：
1. 每个 MCP Token 必须强制绑定单一 `project_id`，绝不存在全局通配 MCP Token。
2. 数据库仅存储 SHA-256 哈希，不保存明文 Token，事件与日志中严禁出现完整 Token。
3. 严禁复用 Web 管理端的 `127.0.0.1` 本机免 Token 放行规则；任何 MCP 调用必须显式携带有效 Bearer Token。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid
from dataclasses import dataclass
from typing import Optional, Sequence

from sqlalchemy import select

from backend.db.base import utc_now_iso
from backend.db.models.project import McpProjectTokenModel, ProjectModel
from backend.db.uow import SqlAlchemyUnitOfWork
from backend.domain.enums import ActorType
from backend.domain.exceptions import EntityNotFoundError, SecurityBoundaryError

MCP_TOKEN_PREFIX_TAG = "agmcp_"


def hash_mcp_token(raw_token: str) -> str:
    """计算 MCP 访问令牌的 SHA-256 十六进制摘要。"""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class IssuedMcpToken:
    """新签发的 MCP 项目令牌（raw_token 仅在签发当次返回，服务端不落盘）。"""

    token_id: str
    project_id: str
    token_name: str
    token_prefix: str
    raw_token: str
    scopes: list[str]
    expires_at: Optional[str]
    created_at: str


@dataclass(frozen=True)
class VerifiedMcpPrincipal:
    """通过鉴权的 MCP 调用方安全主体上下文。"""

    token_id: str
    project_id: str
    token_name: str
    scopes: frozenset[str]


def issue_mcp_project_token(
    uow: SqlAlchemyUnitOfWork,
    *,
    project_id: str,
    token_name: str,
    scopes: Sequence[str],
    expires_at: Optional[str] = None,
) -> IssuedMcpToken:
    """为指定单一 Project 签发最小权限 MCP 访问令牌（与审计事件同事务持久化）。"""
    assert uow.session is not None
    clean_pid = (project_id or "").strip()
    if not clean_pid:
        raise SecurityBoundaryError(
            problem="禁止签发未绑定项目的全局 MCP Token",
            cause="project_id 为空",
            fix="必须指定唯一归属的 Project ID 后再签发 MCP Token。",
        )

    project = uow.session.get(ProjectModel, clean_pid)
    if project is None:
        raise EntityNotFoundError(
            problem="无法为不存在的项目签发 MCP Token",
            cause=f"未找到项目 ID: {clean_pid}",
            fix="请先确认目标项目已创建。",
            context={"project_id": clean_pid},
        )

    clean_scopes = sorted({s.strip() for s in scopes if s and s.strip()})
    if not clean_scopes:
        raise SecurityBoundaryError(
            problem="MCP Token 必须声明至少一项明确权限范围 (scopes)",
            cause="传入的 scopes 列表为空",
            fix="请按最小权限原则授予明确权限（例如 ['task:read', 'progress:write']）。",
            context={"project_id": clean_pid},
        )

    raw_secret = f"{MCP_TOKEN_PREFIX_TAG}{secrets.token_urlsafe(32)}"
    token_prefix = raw_secret[:12]
    token_hash = hash_mcp_token(raw_secret)
    token_id = str(uuid.uuid4())
    now = utc_now_iso()
    clean_name = (token_name or "default-mcp-client").strip()[:120]

    record = McpProjectTokenModel(
        id=token_id,
        project_id=clean_pid,
        token_name=clean_name,
        token_prefix=token_prefix,
        token_hash=token_hash,
        scopes_json=json.dumps(clean_scopes, ensure_ascii=False),
        is_revoked=0,
        expires_at=expires_at,
        last_used_at=None,
        created_at=now,
    )
    uow.session.add(record)
    uow.record_event(
        project_id=clean_pid,
        entity_type="security",
        entity_id=token_id,
        event_type="security.mcp_token_issued",
        actor_type=ActorType.USER,
        payload={
            "token_id": token_id,
            "token_name": clean_name,
            "token_prefix": token_prefix,
            "scopes": clean_scopes,
            "expires_at": expires_at,
        },
    )
    uow.commit()

    return IssuedMcpToken(
        token_id=token_id,
        project_id=clean_pid,
        token_name=clean_name,
        token_prefix=token_prefix,
        raw_token=raw_secret,
        scopes=clean_scopes,
        expires_at=expires_at,
        created_at=now,
    )


def revoke_mcp_project_token(
    uow: SqlAlchemyUnitOfWork,
    *,
    project_id: str,
    token_id: str,
) -> None:
    """吊销指定项目下的 MCP Token（校验项目归属一致性）。"""
    assert uow.session is not None
    record = uow.session.get(McpProjectTokenModel, token_id)
    if record is None or record.project_id != project_id:
        raise EntityNotFoundError(
            problem="未找到待吊销的项目 MCP Token",
            cause=f"Token {token_id} 不存在或不属于项目 {project_id}",
            fix="请核对项目 ID 与令牌记录 ID。",
            context={"project_id": project_id, "token_id": token_id},
        )
    record.is_revoked = 1
    uow.record_event(
        project_id=project_id,
        entity_type="security",
        entity_id=token_id,
        event_type="security.mcp_token_revoked",
        actor_type=ActorType.USER,
        payload={"token_id": token_id, "token_prefix": record.token_prefix},
    )
    uow.commit()


def verify_mcp_project_token(
    uow: SqlAlchemyUnitOfWork,
    *,
    authorization_header: Optional[str],
    target_project_id: str,
    required_scope: Optional[str] = None,
) -> VerifiedMcpPrincipal:
    """
    严格校验 MCP 请求的 Bearer Token：
    - 即使请求来自 127.0.0.1 也绝不免密放行；
    - 校验哈希匹配、未吊销、未过期、且绑定 `project_id == target_project_id`；
    - 若指定 required_scope，校验该 scope 在已授予集合中。
    """
    assert uow.session is not None
    header_val = (authorization_header or "").strip()
    if not header_val.lower().startswith("bearer "):
        raise SecurityBoundaryError(
            problem="MCP 访问被拒绝（缺少 Bearer Token）",
            cause="请求未携带标准 Authorization: Bearer <project_token> 头（MCP 端点不启用本机免密）",
            fix="请在宿主 Agent 的 MCP 配置中填入对应 Project 的专属 Bearer Token。",
            context={"target_project_id": target_project_id},
        )

    raw_token = header_val[7:].strip()
    if not raw_token.startswith(MCP_TOKEN_PREFIX_TAG):
        raise SecurityBoundaryError(
            problem="MCP 访问被拒绝（令牌格式非法）",
            cause="提供的令牌不是合法的 Project-scoped MCP Token",
            fix="请使用由 AgentDeck 为该项目签发的 agmcp_ 前缀令牌（不得混用手机设备配对 Token）。",
            context={"target_project_id": target_project_id},
        )

    candidate_hash = hash_mcp_token(raw_token)
    stmt = select(McpProjectTokenModel).where(McpProjectTokenModel.token_hash == candidate_hash)
    record = uow.session.execute(stmt).scalar_one_or_none()

    if record is None or not hmac.compare_digest(record.token_hash, candidate_hash):
        raise SecurityBoundaryError(
            problem="MCP 访问被拒绝（令牌无效或不存在）",
            cause="未匹配到任何已签发的有效 MCP 项目令牌",
            fix="请重新生成并更新对应 Project 的 MCP 配置令牌。",
            context={"target_project_id": target_project_id},
        )

    if record.is_revoked != 0:
        raise SecurityBoundaryError(
            problem="MCP 访问被拒绝（令牌已被吊销）",
            cause=f"令牌 {record.token_prefix}*** 已处于吊销状态",
            fix="请使用新签发的有效项目 MCP Token。",
            context={"token_id": record.id, "target_project_id": target_project_id},
        )

    now_iso = utc_now_iso()
    if record.expires_at and record.expires_at <= now_iso:
        raise SecurityBoundaryError(
            problem="MCP 访问被拒绝（令牌已过期）",
            cause=f"令牌 {record.token_prefix}*** 已于 {record.expires_at} 过期",
            fix="请为该项目轮换签发新的 MCP Token。",
            context={"token_id": record.id, "target_project_id": target_project_id},
        )

    if record.project_id != target_project_id:
        raise SecurityBoundaryError(
            problem="拦截跨项目越权 MCP 访问",
            cause=f"令牌绑定项目 ({record.project_id}) 与目标访问项目 ({target_project_id}) 不一致",
            fix="每个 MCP Token 仅限访问其绑定的单一 Project，请为目标项目配置对应的专属令牌。",
            context={
                "token_id": record.id,
                "bound_project_id": record.project_id,
                "target_project_id": target_project_id,
            },
        )

    scopes_set = frozenset(json.loads(record.scopes_json or "[]"))
    if required_scope and required_scope not in scopes_set:
        raise SecurityBoundaryError(
            problem="MCP 访问被拒绝（超出令牌最小权限范围）",
            cause=f"当前操作需要权限 '{required_scope}'，但令牌仅拥有 {sorted(scopes_set)}",
            fix="请使用具备对应操作权限范围的项目 MCP Token。",
            context={"token_id": record.id, "required_scope": required_scope},
        )

    record.last_used_at = now_iso
    uow.commit()

    return VerifiedMcpPrincipal(
        token_id=record.id,
        project_id=record.project_id,
        token_name=record.token_name,
        scopes=scopes_set,
    )


def rotate_mcp_project_token(
    uow: SqlAlchemyUnitOfWork,
    *,
    project_id: str,
    token_id: str,
    expires_at: Optional[str] = None,
) -> IssuedMcpToken:
    """
    在同一数据库事务内原子轮换指定项目的 MCP Token：
    将旧 Token 标记为 is_revoked = 1，并继承其 token_name 与 scopes 签发新 Token。
    """
    assert uow.session is not None
    old_record = uow.session.get(McpProjectTokenModel, token_id)
    if old_record is None or old_record.project_id != project_id:
        raise EntityNotFoundError(
            problem="未找到待轮换的项目 MCP Token",
            cause=f"Token {token_id} 不存在或不属于项目 {project_id}",
            fix="请核对项目 ID 与待轮换令牌 ID。",
            context={"project_id": project_id, "token_id": token_id},
        )

    old_record.is_revoked = 1
    inherited_scopes = json.loads(old_record.scopes_json or "[]")
    eff_expires = expires_at if expires_at is not None else old_record.expires_at

    raw_secret = f"{MCP_TOKEN_PREFIX_TAG}{secrets.token_urlsafe(32)}"
    token_prefix = raw_secret[:12]
    token_hash = hash_mcp_token(raw_secret)
    new_token_id = str(uuid.uuid4())
    now = utc_now_iso()

    new_record = McpProjectTokenModel(
        id=new_token_id,
        project_id=project_id,
        token_name=old_record.token_name,
        token_prefix=token_prefix,
        token_hash=token_hash,
        scopes_json=json.dumps(inherited_scopes, ensure_ascii=False),
        is_revoked=0,
        expires_at=eff_expires,
        last_used_at=None,
        created_at=now,
    )
    uow.session.add(new_record)
    uow.record_event(
        project_id=project_id,
        entity_type="security",
        entity_id=new_token_id,
        event_type="security.mcp_token_rotated",
        actor_type=ActorType.USER,
        payload={
            "revoked_token_id": old_record.id,
            "revoked_token_prefix": old_record.token_prefix,
            "new_token_id": new_token_id,
            "new_token_prefix": token_prefix,
            "scopes": inherited_scopes,
        },
    )
    uow.commit()

    return IssuedMcpToken(
        token_id=new_token_id,
        project_id=project_id,
        token_name=old_record.token_name,
        token_prefix=token_prefix,
        raw_token=raw_secret,
        scopes=inherited_scopes,
        expires_at=eff_expires,
        created_at=now,
    )


def authenticate_mcp_token(
    uow: SqlAlchemyUnitOfWork,
    *,
    raw_token_or_header: Optional[str],
    claimed_project_id: Optional[str] = None,
    required_scope: Optional[str] = None,
) -> VerifiedMcpPrincipal:
    """
    从 MCP 请求携带的令牌解析唯一绑定的 Project 主体并校验最小权限：
    - 绝不信任本机 127.0.0.1 免密；
    - 服务端从 Token 反查 project_id，若调用方同时传入 claimed_project_id 且不一致，抛出跨项目拒绝错误；
    - 校验 required_scope 是否在已授权集合中。
    """
    assert uow.session is not None
    val = (raw_token_or_header or "").strip()
    if val.lower().startswith("bearer "):
        raw_token = val[7:].strip()
    else:
        raw_token = val

    if not raw_token:
        raise SecurityBoundaryError(
            problem="MCP 访问被拒绝（缺少项目令牌）",
            cause="未提供 Authorization: Bearer <token>、X-MCP-Project-Token 或 AGENTDECK_MCP_TOKEN",
            fix="请配置有效的 Project-scoped MCP Token（MCP 接口禁用本机免密）。",
            context={"mcp_error_code": "MCP_UNAUTHORIZED"},
        )

    if not raw_token.startswith(MCP_TOKEN_PREFIX_TAG):
        raise SecurityBoundaryError(
            problem="MCP 访问被拒绝（令牌格式非法）",
            cause="提供的令牌不是合法的 agmcp_ 前缀项目令牌",
            fix="请使用由 AgentDeck 签发的项目专属 MCP Token。",
            context={"mcp_error_code": "MCP_UNAUTHORIZED"},
        )

    candidate_hash = hash_mcp_token(raw_token)
    stmt = select(McpProjectTokenModel).where(McpProjectTokenModel.token_hash == candidate_hash)
    record = uow.session.execute(stmt).scalar_one_or_none()

    if record is None or not hmac.compare_digest(record.token_hash, candidate_hash):
        raise SecurityBoundaryError(
            problem="MCP 访问被拒绝（令牌无效或不存在）",
            cause="未匹配到任何已签发的有效 MCP 项目令牌",
            fix="请重新签发并配置对应 Project 的 MCP Token。",
            context={"mcp_error_code": "MCP_UNAUTHORIZED"},
        )

    if record.is_revoked != 0:
        raise SecurityBoundaryError(
            problem="MCP 访问被拒绝（令牌已被吊销）",
            cause=f"令牌 {record.token_prefix}*** 已处于吊销状态",
            fix="请使用轮换后的新 MCP Token。",
            context={"token_id": record.id, "mcp_error_code": "MCP_UNAUTHORIZED"},
        )

    now_iso = utc_now_iso()
    if record.expires_at and record.expires_at <= now_iso:
        raise SecurityBoundaryError(
            problem="MCP 访问被拒绝（令牌已过期）",
            cause=f"令牌 {record.token_prefix}*** 已于 {record.expires_at} 过期",
            fix="请为该项目轮换签发新的 MCP Token。",
            context={"token_id": record.id, "mcp_error_code": "MCP_UNAUTHORIZED"},
        )

    clean_claimed = (claimed_project_id or "").strip()
    if clean_claimed and clean_claimed != record.project_id:
        uow.record_event(
            project_id=record.project_id,
            entity_type="security",
            entity_id=record.id,
            event_type="security.mcp_cross_project_blocked",
            actor_type=ActorType.MCP_AGENT,
            actor_id=record.token_prefix,
            payload={
                "token_id": record.id,
                "bound_project_id": record.project_id,
                "claimed_project_id": clean_claimed,
            },
        )
        uow.commit()
        raise SecurityBoundaryError(
            problem="拦截跨项目越权 MCP 访问",
            cause=f"令牌绑定项目 ({record.project_id}) 与请求指定的项目 ({clean_claimed}) 不一致",
            fix="每个 MCP Token 仅限操作其绑定的单一 Project，严禁跨项目读写。",
            context={
                "token_id": record.id,
                "bound_project_id": record.project_id,
                "target_project_id": clean_claimed,
                "mcp_error_code": "MCP_CROSS_PROJECT_DENIED",
            },
        )

    scopes_set = frozenset(json.loads(record.scopes_json or "[]"))
    if required_scope and required_scope not in scopes_set:
        raise SecurityBoundaryError(
            problem="MCP 访问被拒绝（超出令牌最小权限范围）",
            cause=f"当前工具需要权限 '{required_scope}'，但令牌仅拥有 {sorted(scopes_set)}",
            fix=f"请为该项目签发包含 '{required_scope}' 权限范围的 MCP Token。",
            context={
                "token_id": record.id,
                "required_scope": required_scope,
                "granted_scopes": sorted(scopes_set),
                "mcp_error_code": "MCP_SCOPE_FORBIDDEN",
            },
        )

    record.last_used_at = now_iso
    uow.commit()

    return VerifiedMcpPrincipal(
        token_id=record.id,
        project_id=record.project_id,
        token_name=record.token_name,
        scopes=scopes_set,
    )


