import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.user import User
from app.models.dashboard import Dashboard, DashboardWidget
from app.utils.security import decode_token
from app.services.ws_manager import manager
from app.services.widget_poller import poll_manager, init_poller

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])

init_poller(SessionLocal)


async def _get_user_from_token(token: str | None) -> User | None:
    if not token:
        return None
    payload = decode_token(token)
    if payload is None or payload.get("type") != "access":
        return None
    user_id = payload.get("sub")
    if user_id is None:
        return None
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == int(user_id)).first()
        if user and user.is_active:
            return user
        return None
    finally:
        db.close()


def _get_dashboard_widgets(dashboard_id: int) -> list[DashboardWidget]:
    db = SessionLocal()
    try:
        dash = db.query(Dashboard).filter(Dashboard.id == dashboard_id).first()
        if not dash:
            return []
        return list(dash.widgets)
    finally:
        db.close()


@router.websocket("/ws/dashboards/{dashboard_id}")
async def dashboard_websocket(
    websocket: WebSocket,
    dashboard_id: int,
    token: str | None = Query(None),
):
    user = await _get_user_from_token(token)
    if not user:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    await manager.connect(dashboard_id, websocket)

    try:
        widgets = await _get_dashboard_widgets_async(dashboard_id)
        for w in widgets:
            cfg = w.config or {}
            interval = cfg.get("refresh_interval", 0)
            if interval > 0 and w.query_id and cfg.get("sql") and cfg.get("database_id"):
                poll_manager.start(
                    dashboard_id=dashboard_id,
                    widget_id=w.id,
                    interval=interval,
                    database_id=cfg["database_id"],
                    sql=cfg["sql"],
                )

        await websocket.send_json({
            "type": "connected",
            "dashboard_id": dashboard_id,
            "active_pollers": [
                w.id for w in widgets
                if (w.config or {}).get("refresh_interval", 0) > 0
            ],
        })

        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                await websocket.send_json({
                    "type": "error",
                    "message": "Invalid JSON",
                })
                continue

            msg_type = msg.get("type")

            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})

            elif msg_type == "refresh_all":
                for w in widgets:
                    cfg = w.config or {}
                    if w.query_id and cfg.get("sql") and cfg.get("database_id"):
                        poll_manager.stop(dashboard_id, w.id)
                        interval = cfg.get("refresh_interval", 10)
                        poll_manager.start(
                            dashboard_id=dashboard_id,
                            widget_id=w.id,
                            interval=interval,
                            database_id=cfg["database_id"],
                            sql=cfg["sql"],
                        )
                await websocket.send_json({
                    "type": "refresh_all",
                    "status": "started",
                })

            elif msg_type == "refresh_widget":
                widget_id = msg.get("widget_id")
                if widget_id:
                    poll_manager.stop(dashboard_id, widget_id)
                    w = next((x for x in widgets if x.id == widget_id), None)
                    if w:
                        cfg = w.config or {}
                        interval = cfg.get("refresh_interval", 10)
                        if cfg.get("sql") and cfg.get("database_id"):
                            poll_manager.start(
                                dashboard_id=dashboard_id,
                                widget_id=widget_id,
                                interval=interval,
                                database_id=cfg["database_id"],
                                sql=cfg["sql"],
                            )
                            await websocket.send_json({
                                "type": "refresh_widget",
                                "widget_id": widget_id,
                                "status": "started",
                            })

    except WebSocketDisconnect:
        pass
    finally:
        poll_manager.stop_all(dashboard_id)
        await manager.disconnect(dashboard_id, websocket)


async def _get_dashboard_widgets_async(dashboard_id: int) -> list[DashboardWidget]:
    from asyncio import to_thread
    return await to_thread(_get_dashboard_widgets, dashboard_id)
