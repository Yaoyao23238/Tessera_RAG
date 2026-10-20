"""WebSocket：任务进度 / 模型下载 / 实验进度三频道。

消息格式（共享知识 #3）：
- {"type":"task_progress","task_id","document_id","payload":{"status","progress","message"}}
- {"type":"model_download","payload":{"model","downloaded_mb","total_mb"}}
- {"type":"experiment_progress","experiment_id","payload":{"current","total"}}

本地单用户工具，所有事件广播到全部连接，前端按 type/document_id 过滤。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


class ConnectionManager:
    """WebSocket 连接管理器（广播语义）。"""

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.add(websocket)
        self._loop = asyncio.get_running_loop()
        logger.debug("WS 客户端接入，当前 %d 连接", len(self._connections))

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.discard(websocket)

    async def broadcast(self, message: dict[str, Any]) -> None:
        """向全部连接广播；断开的连接自动清理。"""
        dead: list[WebSocket] = []
        for ws in list(self._connections):
            try:
                await ws.send_json(message)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    def broadcast_threadsafe(self, message: dict[str, Any]) -> None:
        """从工作线程安全广播（模型下载进度等）。"""
        if self._loop is not None and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self.broadcast(message), self._loop)


manager = ConnectionManager()
ws_router = APIRouter()


@ws_router.websocket("/ws/tasks")
async def ws_tasks(websocket: WebSocket) -> None:
    """任务/下载/实验进度统一频道。"""
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong", "payload": {}})
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:  # noqa: BLE001
        manager.disconnect(websocket)


# ---------------------------------------------------------------------------
# 推送辅助（workers / api 层调用）
# ---------------------------------------------------------------------------


async def push_task_progress(
    task_id: str,
    document_id: str | None,
    status: str,
    progress: int,
    message: str = "",
) -> None:
    await manager.broadcast(
        {
            "type": "task_progress",
            "task_id": task_id,
            "document_id": document_id,
            "payload": {"status": status, "progress": progress, "message": message},
        }
    )


def push_model_download(model: str, downloaded_mb: float, total_mb: float) -> None:
    """模型下载线程内调用（线程安全）。"""
    manager.broadcast_threadsafe(
        {
            "type": "model_download",
            "payload": {
                "model": model,
                "downloaded_mb": round(downloaded_mb, 1),
                "total_mb": round(total_mb, 1),
            },
        }
    )


def push_experiment_progress(experiment_id: str, current: int, total: int) -> None:
    """实验进度（可能在工作线程中调用，线程安全）。"""
    manager.broadcast_threadsafe(
        {
            "type": "experiment_progress",
            "experiment_id": experiment_id,
            "payload": {"current": current, "total": total},
        }
    )
