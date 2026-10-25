# -*- coding: utf-8 -*-
"""
多 Agent 统一会话管理路由。
包含会话列表检索 (ETag 304)、新建会话、分页拉取结构化消息、更新元数据、发送指令与工具审批。
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from backend.core.logger import logger
from backend.routes.deps import (
    get_meta_db,
    get_orchestrator,
    verify_request_auth,
)
from backend.routes.schemas import (
    CreateSessionRequest,
    SendPromptRequest,
    SessionMetaUpdateRequest,
    SwitchSessionModelRequest,
    ToolApprovalDecisionRequest,
    MarkAllViewedRequest,
    SyncViewedRequest,
    check_etag_matches,
)

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])


@router.get("")
async def list_sessions(
    request: Request,
    agent_type: str = Query(default="all"),
    limit: int = Query(default=40, ge=1, le=200),
    if_none_match: Optional[str] = Header(default=None, alias="if-none-match"),
    _auth: bool = Depends(verify_request_auth),
) -> Response:
    """获取聚合后的 4 大 Agent 会话列表（带 ETag 304 协商缓存）。"""
    orchestrator = get_orchestrator(request)
    etag = orchestrator.get_sessions_etag(agent_filter=agent_type, limit=limit)
    if check_etag_matches(if_none_match, etag):
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-cache"})

    sessions = orchestrator.list_all_sessions(
        agent_filter=agent_type, limit_per_agent=limit
    )
    data = {
        "ok": True,
        "count": len(sessions),
        "pending_approvals": [
            pa.to_dict() for pa in orchestrator.pending_approvals.values()
        ],
        "sessions": sessions,
    }
    return JSONResponse(content=data, headers={"ETag": etag, "Cache-Control": "no-cache"})


@router.get("/search")
async def search_sessions(
    request: Request,
    q: str = Query(default="", min_length=1),
    limit: int = Query(default=50, ge=1, le=100),
    _auth: bool = Depends(verify_request_auth),
) -> dict[str, Any]:
    """跨 Agent 全量历史会话内容与元数据秒级穿透检索。"""
    orchestrator = get_orchestrator(request)
    matches = orchestrator.search_sessions(query=q, limit=limit)
    return {
        "ok": True,
        "query": q,
        "count": len(matches),
        "matches": matches,
    }


@router.post("")
async def create_new_session(
    req: CreateSessionRequest,
    request: Request,
    _auth: bool = Depends(verify_request_auth),
) -> dict[str, Any]:
    """在手机端新建一个 Agent 会话（委托 Orchestrator 深模块一站式初始化）。"""
    orchestrator = get_orchestrator(request)
    unified_id, meta = await orchestrator.create_session(
        agent_type=req.agent_type,
        project_path=req.project_path,
        title=req.title,
        initial_prompt=req.initial_prompt,
        model_name=req.model_name,
        execution_mode=req.execution_mode,
    )
    return {"ok": True, "unified_id": unified_id, "meta": meta}


@router.get("/{unified_id:path}/messages")
async def get_session_messages(
    unified_id: str,
    request: Request,
    limit: int = Query(default=30, ge=1, le=500),
    before_step: Optional[int] = Query(default=None),
    since_step: Optional[int] = Query(default=None),
    if_none_match: Optional[str] = Header(default=None, alias="if-none-match"),
    _auth: bool = Depends(verify_request_auth),
) -> Response:
    """获取单个会话的结构化对话流（带毫秒级 ETag 304 快速预检）。"""
    orchestrator = get_orchestrator(request)
    matched, quick_etag = orchestrator.check_session_messages_etag_matches(
        if_none_match=if_none_match,
        unified_id=unified_id,
        limit=limit,
        before_step=before_step,
        since_step=since_step,
    )
    if matched and quick_etag:
        return Response(status_code=304, headers={"ETag": quick_etag, "Cache-Control": "no-cache"})

    detail = orchestrator.get_session_detail(
        unified_id, limit=limit, before_step=before_step, since_step=since_step
    )
    msg_etag = orchestrator.get_session_messages_etag(
        detail,
        unified_id=unified_id,
        limit=limit,
        before_step=before_step,
        since_step=since_step,
    )

    if check_etag_matches(if_none_match, msg_etag):
        return Response(status_code=304, headers={"ETag": msg_etag, "Cache-Control": "no-cache"})

    return JSONResponse(
        content={"ok": True, **detail},
        headers={"ETag": msg_etag, "Cache-Control": "no-cache"},
    )


@router.get("/{unified_id:path}/modified-files")
async def get_session_modified_files(
    unified_id: str,
    request: Request,
    _auth: bool = Depends(verify_request_auth),
) -> dict[str, Any]:
    """获取指定会话实际修改的文件汇总列表及 Git 提交状态。"""
    orchestrator = get_orchestrator(request)
    return await asyncio.to_thread(orchestrator.get_session_modified_files, unified_id)



@router.post("/{unified_id:path}/model")
async def switch_session_model(
    unified_id: str,
    req: SwitchSessionModelRequest,
    request: Request,
    _auth: bool = Depends(verify_request_auth),
) -> dict[str, Any]:
    """切换指定会话选定的模型并持久化到本地元数据库。"""
    clean_model = req.model_name.strip()
    if not clean_model:
        raise HTTPException(status_code=400, detail="模型名称不能为空")

    meta_db = get_meta_db(request)
    orchestrator = get_orchestrator(request)

    if ":" not in unified_id:
        raise HTTPException(status_code=400, detail="会话 ID 格式非法")
    agent_type, raw_id = unified_id.split(":", 1)

    updated = meta_db.upsert_session_meta(
        unified_id=unified_id,
        agent_type=agent_type,
        raw_session_id=raw_id,
        model_name=clean_model,
    )
    orchestrator.bump_version()
    logger.info(f"已成功持久化会话模型切换: {unified_id} -> {clean_model}")
    return {"ok": True, "unified_id": unified_id, "model_name": clean_model, "meta": updated}


@router.patch("/{unified_id:path}/meta")
async def update_session_meta(
    unified_id: str,
    req: SessionMetaUpdateRequest,
    request: Request,
    _auth: bool = Depends(verify_request_auth),
) -> dict[str, Any]:
    """更新会话置顶状态、执行策略或自定义标题。"""
    if ":" not in unified_id:
        raise HTTPException(status_code=400, detail="无效的 unified_id 格式")
    meta_db = get_meta_db(request)
    orchestrator = get_orchestrator(request)

    agent_type, raw_id = unified_id.split(":", 1)
    normalized_mode = (
        orchestrator.normalize_execution_mode(agent_type, req.execution_mode)
        if req.execution_mode is not None
        else None
    )
    updated = meta_db.upsert_session_meta(
        unified_id=unified_id,
        agent_type=agent_type,
        raw_session_id=raw_id,
        title=req.title,
        is_pinned=req.is_pinned,
        execution_mode=normalized_mode,
        model_name=req.model_name,
    )
    orchestrator.bump_version()

    if req.title and req.title.strip():
        adapter = orchestrator.adapters.get(agent_type)
        if adapter and hasattr(adapter, "rename_session"):
            try:
                adapter.rename_session(raw_id, req.title.strip())
            except Exception as exc:
                logger.warning(f"向 Agent {agent_type} 回写原生会话名称失败: {exc}")
    return {"ok": True, "meta": updated}


@router.post("/mark-all-viewed")
async def mark_all_sessions_viewed_endpoint(
    request: Request,
    req: Optional[MarkAllViewedRequest] = None,
    _auth: bool = Depends(verify_request_auth),
) -> dict[str, Any]:
    """批量将所有会话或指定会话标记为已读，并通过 WebSocket 广播给所有终端。"""
    meta_db = get_meta_db(request)
    orchestrator = get_orchestrator(request)
    viewed_at = meta_db.mark_all_sessions_viewed(session_ids=req.session_ids if req else None)
    orchestrator.bump_version()
    await orchestrator.broadcast_event(
        event_type="ALL_SESSIONS_VIEWED",
        payload={
            "last_viewed_at": viewed_at,
            "session_ids": req.session_ids if req else None,
        },
    )
    return {"ok": True, "last_viewed_at": viewed_at}


@router.post("/sync-viewed")
async def sync_viewed_sessions_endpoint(
    req: SyncViewedRequest,
    request: Request,
    _auth: bool = Depends(verify_request_auth),
) -> dict[str, Any]:
    """客户端后台静默上报本地查看时间字典，服务端增量合并落库。"""
    meta_db = get_meta_db(request)
    count = meta_db.batch_update_session_viewed_times(req.viewed_sessions)
    return {"ok": True, "updated_count": count}


@router.post("/{unified_id:path}/viewed")
async def mark_session_viewed_endpoint(
    unified_id: str,
    request: Request,
    _auth: bool = Depends(verify_request_auth),
) -> dict[str, Any]:
    """标记会话为已查看，记录服务端查看时间戳并通过 WebSocket 广播给所有客户端（跨端实时消红）。"""
    if ":" not in unified_id:
        raise HTTPException(status_code=400, detail="无效的 unified_id 格式")
    meta_db = get_meta_db(request)
    orchestrator = get_orchestrator(request)

    viewed_at = meta_db.mark_session_viewed(unified_id)
    orchestrator.bump_version()

    await orchestrator.broadcast_event(
        event_type="SESSION_VIEWED",
        payload={
            "unified_id": unified_id,
            "last_viewed_at": viewed_at,
        },
    )
    return {"ok": True, "unified_id": unified_id, "last_viewed_at": viewed_at}


@router.delete("/{unified_id:path}")
async def delete_session(
    unified_id: str,
    request: Request,
    _auth: bool = Depends(verify_request_auth),
) -> dict[str, Any]:
    """彻底删除指定会话（终止运行、清理底层存储、登记墓碑并全端广播）。"""
    orchestrator = get_orchestrator(request)
    await orchestrator.delete_session(unified_id)
    return {"ok": True, "deleted": unified_id}


@router.post("/{unified_id:path}/send")
async def send_prompt(
    unified_id: str,
    req: SendPromptRequest,
    request: Request,
    _auth: bool = Depends(verify_request_auth),
) -> dict[str, Any]:
    """在手机端向指定会话发送新指令。"""
    if not req.prompt.strip():
        raise HTTPException(status_code=400, detail="指令内容不能为空")
    orchestrator = get_orchestrator(request)
    res = await orchestrator.send_prompt_to_session(
        unified_id=unified_id,
        prompt=req.prompt.strip(),
        project_path=req.project_path,
        execution_mode=req.execution_mode,
        model_name=req.model_name,
        auto_fork_ide=req.auto_fork_ide,
    )
    return res


@router.post("/{unified_id:path}/approve")
async def approve_tool_call(
    unified_id: str,
    req: ToolApprovalDecisionRequest,
    request: Request,
    _auth: bool = Depends(verify_request_auth),
) -> dict[str, Any]:
    """手机端对挂起的工具调用执行审批。"""
    orchestrator = get_orchestrator(request)
    return await orchestrator.resolve_tool_approval(
        unified_id=unified_id,
        decision=req.decision,
        feedback=req.feedback,
    )


@router.post("/{unified_id:path}/abort")
async def abort_session_execution(
    unified_id: str,
    request: Request,
    _auth: bool = Depends(verify_request_auth),
) -> dict[str, Any]:
    """手机端点击中断按钮：终止进程并释放锁。"""
    orchestrator = get_orchestrator(request)
    await orchestrator.abort_session(unified_id, reason="手机端手动触发紧急中断 (SIGINT)")
    return {"ok": True, "unified_id": unified_id, "status": "aborted"}


@router.get("/{unified_id:path}/steps/{step_index}/full")
async def get_full_step_output(
    unified_id: str,
    step_index: int,
    request: Request,
    _auth: bool = Depends(verify_request_auth),
) -> dict[str, Any]:
    """按需拉取被截断的单步完整工具输出。"""
    orchestrator = get_orchestrator(request)
    full_text = orchestrator.get_full_step_output(unified_id, step_index)
    return {
        "ok": True,
        "unified_id": unified_id,
        "step_index": step_index,
        "full_output": full_text,
    }
