import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages WebSocket connections grouped by dashboard_id."""

    def __init__(self) -> None:
        self._connections: dict[int, set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, dashboard_id: int, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.setdefault(dashboard_id, set()).add(websocket)
        logger.info(
            "WS connect: dashboard=%d, total=%d",
            dashboard_id,
            len(self._connections[dashboard_id]),
        )

    async def disconnect(self, dashboard_id: int, websocket: WebSocket) -> None:
        async with self._lock:
            conns = self._connections.get(dashboard_id)
            if conns:
                conns.discard(websocket)
                if not conns:
                    del self._connections[dashboard_id]
        logger.info(
            "WS disconnect: dashboard=%d, remaining=%d",
            dashboard_id,
            len(self._connections.get(dashboard_id, set())),
        )

    async def broadcast(self, dashboard_id: int, message: dict[str, Any]) -> None:
        async with self._lock:
            conns = list(self._connections.get(dashboard_id, set()))
        if not conns:
            return
        text = json.dumps(message)
        dead: list[WebSocket] = []
        for ws in conns:
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                conns = self._connections.get(dashboard_id)
                if conns:
                    for ws in dead:
                        conns.discard(ws)
                    if not conns:
                        del self._connections[dashboard_id]

    def has_connections(self, dashboard_id: int) -> bool:
        conns = self._connections.get(dashboard_id)
        return bool(conns)

    async def get_connection_count(self, dashboard_id: int) -> int:
        async with self._lock:
            return len(self._connections.get(dashboard_id, set()))


manager = ConnectionManager()
