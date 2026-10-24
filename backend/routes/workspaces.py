# -*- coding: utf-8 -*-
"""
常用工作区管理、文档阅读器与 Git 状态接口。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.core.workspace import WorkspaceDocumentReader
from backend.core.workspace_git import (
    get_git_workspace_diff,
    get_git_workspace_status,
)
from backend.core.workspace_workflows import discover_quick_workflows
from backend.routes.deps import get_meta_db, verify_request_auth
from backend.routes.schemas import AddWorkspaceRequest, HideWorkspaceRequest

router = APIRouter(tags=["workspaces"])


@router.get("/api/v1/workspaces")
async def list_workspaces(
    request: Request,
    _auth: bool = Depends(verify_request_auth),
) -> dict[str, Any]:
    db = get_meta_db(request)
    return {"ok": True, "workspaces": db.list_workspaces(include_hidden=False)}


@router.post("/api/v1/workspaces")
async def add_workspace(
    req: AddWorkspaceRequest,
    request: Request,
    _auth: bool = Depends(verify_request_auth),
) -> dict[str, Any]:
    db = get_meta_db(request)
    ws = db.add_workspace(req.workspace_name, req.workspace_path, req.default_agent)
    return {"ok": True, "workspace": ws}


@router.post("/api/v1/workspaces/hide")
async def hide_workspace(
    req: HideWorkspaceRequest,
    request: Request,
    _auth: bool = Depends(verify_request_auth),
) -> dict[str, Any]:
    db = get_meta_db(request)
    res = db.hide_workspace(req.workspace_path)
    return {"ok": True, **res}




@router.get("/api/v1/workflows")
async def list_quick_workflows(
    project_path: str = Query(default=""),
    _auth: bool = Depends(verify_request_auth),
) -> dict[str, Any]:
    """返回 `/` 斜杠快捷指令与项目工作流补全列表。"""
    items = await asyncio.to_thread(discover_quick_workflows, project_path)
    return {"ok": True, "workflows": items}


@router.get("/api/v1/files/read")
async def read_local_document(
    path: str = Query(..., description="本地文件绝对路径或 file:// URI"),
    _auth: bool = Depends(verify_request_auth),
) -> dict[str, Any]:
    """供手机端全屏阅读器安全读取本地 Markdown 文档或源码文件。"""
    try:
        return await asyncio.to_thread(WorkspaceDocumentReader.read_document, path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))


@router.get("/api/v1/git/status")
async def read_git_status(
    project_path: str = Query(..., description="项目工作区路径"),
    _auth: bool = Depends(verify_request_auth),
) -> dict[str, Any]:
    """供手机端轻量查看当前项目分支与未提交文件变更列表（纯只读）。"""
    return await asyncio.to_thread(get_git_workspace_status, project_path)


@router.get("/api/v1/git/diff")
async def read_git_diff(
    project_path: str = Query(..., description="项目工作区路径"),
    file_path: str = Query(default="", description="可选：指定单文件相对路径"),
    _auth: bool = Depends(verify_request_auth),
) -> dict[str, Any]:
    """供手机端轻量查看单文件或全局未提交 Unified Diff（纯只读）。"""
    res = await asyncio.to_thread(get_git_workspace_diff, project_path, file_path)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error") or "获取 Diff 失败")
    return res
