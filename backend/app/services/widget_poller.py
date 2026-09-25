import asyncio
import hashlib
import json
import logging
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.services.connection_service import get_connector
from app.models.connection import DatabaseConnection

logger = logging.getLogger(__name__)

_get_db: Callable[[], Session] | None = None


def init_poller(get_db_callable: Callable[[], Session]) -> None:
    global _get_db
    _get_db = get_db_callable


def _compute_hash(results: dict[str, Any]) -> str:
    raw = json.dumps(results, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _execute_widget_query(database_id: int, sql: str) -> dict[str, Any] | None:
    if not _get_db:
        logger.error("widget_poller not initialized")
        return None
    db = _get_db()
    try:
        conn = (
            db.query(DatabaseConnection)
            .filter(DatabaseConnection.id == database_id)
            .first()
        )
        if not conn:
            logger.warning("Database %d not found for widget poll", database_id)
            return None
        connector = get_connector(conn)
        raw = connector.execute_query(sql)
        columns = raw.get("columns", [])
        rows = raw.get("rows", [])
        return {
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
        }
    except Exception as e:
        logger.error("Widget poll query failed: %s", e)
        return None
    finally:
        db.close()


class WidgetPollManager:
    """Manages per-widget polling tasks for real-time dashboard updates."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._hashes: dict[str, str] = {}
        self._broadcast_cb: Callable[[int, dict[str, Any]], Any] | None = None

    def set_broadcast_cb(
        self, cb: Callable[[int, dict[str, Any]], Any]
    ) -> None:
        self._broadcast_cb = cb

    def _task_key(self, dashboard_id: int, widget_id: int) -> str:
        return f"{dashboard_id}:{widget_id}"

    def start(
        self,
        dashboard_id: int,
        widget_id: int,
        interval: int,
        database_id: int,
        sql: str,
    ) -> None:
        key = self._task_key(dashboard_id, widget_id)
        if key in self._tasks:
            return

        async def _loop() -> None:
            logger.info(
                "Poll start: dashboard=%d widget=%d interval=%ds",
                dashboard_id,
                widget_id,
                interval,
            )
            try:
                while True:
                    await asyncio.sleep(interval)
                    results = await asyncio.to_thread(
                        _execute_widget_query, database_id, sql
                    )
                    if results is None:
                        continue
                    new_hash = _compute_hash(results)
                    if self._hashes.get(key) == new_hash:
                        continue
                    self._hashes[key] = new_hash
                    if self._broadcast_cb:
                        await self._broadcast_cb(
                            dashboard_id,
                            {
                                "type": "widget_update",
                                "widget_id": widget_id,
                                "results": results,
                            },
                        )
            except asyncio.CancelledError:
                logger.info(
                    "Poll cancelled: dashboard=%d widget=%d",
                    dashboard_id,
                    widget_id,
                )
            except Exception as e:
                logger.error(
                    "Poll error dashboard=%d widget=%d error=%s",
                    dashboard_id,
                    widget_id,
                    e,
                )

        task = asyncio.create_task(_loop())
        self._tasks[key] = task

    def stop(self, dashboard_id: int, widget_id: int) -> None:
        key = self._task_key(dashboard_id, widget_id)
        task = self._tasks.pop(key, None)
        if task and not task.done():
            task.cancel()
        self._hashes.pop(key, None)

    def stop_all(self, dashboard_id: int) -> None:
        to_remove = [
            k for k in self._tasks if k.startswith(f"{dashboard_id}:")
        ]
        for key in to_remove:
            task = self._tasks.pop(key, None)
            if task and not task.done():
                task.cancel()
            self._hashes.pop(key, None)

    def is_polling(self, dashboard_id: int, widget_id: int) -> bool:
        return self._task_key(dashboard_id, widget_id) in self._tasks


poll_manager = WidgetPollManager()
