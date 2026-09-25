import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.connection import DatabaseConnection
from app.schemas.connection import (
    DatabaseConnectionRequest, DatabaseConnectionUpdate,
    DatabaseResponse, DatabaseTestResult, ConnectionHealthResponse,
    ConnectionHealthItem, BatchHealthResponse,
    SchemaResponse, SyncResult,
)
from app.mcp.postgresql_connector import PostgreSQLConnector
from app.mcp.mysql_connector import MySQLConnector
from app.mcp.sqlserver_connector import SQLServerConnector
from app.mcp.mongodb_connector import MongoDBConnector
from app.mcp.oracle_connector import OracleConnector
from app.utils.error_messages import friendly_error


def get_connector(db_conn: DatabaseConnection):
    if db_conn.connection_type == "mysql" or db_conn.connection_type == "mariadb":
        return MySQLConnector(
            host=db_conn.host,
            port=db_conn.port,
            database=db_conn.database_name,
            user=db_conn.username,
            password=db_conn.password,
            schema=db_conn.database_name,
            ssl=db_conn.ssl,
        )
    elif db_conn.connection_type == "sqlserver":
        return SQLServerConnector(
            host=db_conn.host,
            port=db_conn.port,
            database=db_conn.database_name,
            user=db_conn.username,
            password=db_conn.password,
            schema=db_conn.schema_name,
            ssl=db_conn.ssl,
        )
    elif db_conn.connection_type == "mongodb":
        return MongoDBConnector(
            host=db_conn.host,
            port=db_conn.port,
            database=db_conn.database_name,
            user=db_conn.username,
            password=db_conn.password,
            ssl=db_conn.ssl,
        )
    elif db_conn.connection_type == "oracle":
        return OracleConnector(
            host=db_conn.host,
            port=db_conn.port,
            database=db_conn.database_name,
            user=db_conn.username,
            password=db_conn.password,
            schema=db_conn.schema_name or db_conn.username,
            ssl=db_conn.ssl,
        )
    return PostgreSQLConnector(
        host=db_conn.host,
        port=db_conn.port,
        database=db_conn.database_name,
        user=db_conn.username,
        password=db_conn.password,
        schema=db_conn.schema_name,
        ssl=db_conn.ssl,
    )


def list_databases(
    db: Session,
    page: int = 1,
    per_page: int = 20,
    search: Optional[str] = None,
    type_filter: Optional[str] = None,
    is_active: Optional[bool] = None,
) -> tuple[list[DatabaseResponse], int]:
    query = db.query(DatabaseConnection)
    if search:
        query = query.filter(
            DatabaseConnection.name.ilike(f"%{search}%")
            | DatabaseConnection.host.ilike(f"%{search}%")
            | DatabaseConnection.database_name.ilike(f"%{search}%")
        )
    if type_filter:
        query = query.filter(DatabaseConnection.connection_type == type_filter)
    if is_active is not None:
        query = query.filter(DatabaseConnection.is_active == is_active)

    total = query.count()
    offset = (page - 1) * per_page
    items = query.order_by(DatabaseConnection.created_at.desc()).offset(offset).limit(per_page).all()

    return [DatabaseResponse.model_validate(c) for c in items], total


def get_database(db: Session, db_id: int) -> Optional[DatabaseConnection]:
    return db.query(DatabaseConnection).filter(DatabaseConnection.id == db_id).first()


def create_database(db: Session, data: DatabaseConnectionRequest, user_id: int) -> DatabaseResponse:
    conn = DatabaseConnection(
        name=data.name,
        description=data.description,
        connection_type=data.connection_type,
        host=data.host,
        port=data.port,
        database_name=data.database_name,
        schema_name=data.schema_name,
        username=data.username,
        password=data.password,
        ssl=data.ssl,
        pool_size=data.pool_size,
        timeout_seconds=data.timeout_seconds,
        created_by=user_id,
    )
    db.add(conn)
    db.commit()
    db.refresh(conn)
    return DatabaseResponse.model_validate(conn)


def update_database(db: Session, db_id: int, data: DatabaseConnectionUpdate) -> Optional[DatabaseResponse]:
    conn = get_database(db, db_id)
    if not conn:
        return None

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(conn, key, value)
    db.commit()
    db.refresh(conn)
    return DatabaseResponse.model_validate(conn)


def delete_database(db: Session, db_id: int) -> bool:
    conn = get_database(db, db_id)
    if not conn:
        return False
    db.delete(conn)
    db.commit()
    return True


def test_connection(db: Session, db_id: int) -> Optional[DatabaseTestResult]:
    conn = get_database(db, db_id)
    if not conn:
        return None
    connector = get_connector(conn)
    return connector.test_connection()


def check_health(db: Session, db_id: int) -> Optional[ConnectionHealthResponse]:
    conn = get_database(db, db_id)
    if not conn:
        return None

    now = datetime.now(timezone.utc)
    cache_seconds = 5

    if conn.health_checked_at and (now - conn.health_checked_at).total_seconds() < cache_seconds:
        return ConnectionHealthResponse(
            healthy=bool(conn.health_status) if conn.health_status is not None else False,
            latency_ms=conn.health_latency_ms,
            checked_at=conn.health_checked_at,
        )

    start = time.time()
    try:
        connector = get_connector(conn)
        result = connector.test_connection()
        healthy = result.success
        latency_ms = result.latency_ms
    except Exception:
        healthy = False
        latency_ms = None

    checked_at = now
    conn.health_status = healthy
    conn.health_latency_ms = latency_ms
    conn.health_checked_at = checked_at
    db.commit()

    return ConnectionHealthResponse(
        healthy=healthy,
        latency_ms=latency_ms,
        checked_at=checked_at,
    )


def check_all_health(db: Session) -> BatchHealthResponse:
    connections = db.query(DatabaseConnection).all()
    items = []
    for conn in connections:
        result = check_health(db, conn.id)
        items.append(ConnectionHealthItem(
            id=conn.id,
            name=conn.name,
            healthy=result.healthy if result else False,
            latency_ms=result.latency_ms if result else None,
            checked_at=result.checked_at if result else datetime.utcnow(),
        ))
    return BatchHealthResponse(connections=items)


def get_schema(db: Session, db_id: int) -> Optional[SchemaResponse]:
    conn = get_database(db, db_id)
    if not conn:
        return None

    if conn.schema_cache:
        return SchemaResponse(
            database_id=db_id,
            **{k: conn.schema_cache[k] for k in ("schema_name", "tables", "views")},
            last_synced_at=conn.schema_cache_updated_at or conn.last_sync_at,
        )

    connector = get_connector(conn)
    try:
        schema = connector.get_schema()
        schema.database_id = db_id
        schema.last_synced_at = conn.schema_cache_updated_at or conn.last_sync_at
        return schema
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to get schema: {friendly_error(str(e))}")
    finally:
        connector.close()


def sync_schema(db: Session, db_id: int) -> Optional[SyncResult]:
    conn = get_database(db, db_id)
    if not conn:
        return None
    connector = get_connector(conn)
    try:
        result = connector.sync_schema(db_id)

        if result.status == "completed":
            schema = connector.get_schema()
            conn.schema_cache = schema.model_dump()
            conn.schema_cache_updated_at = datetime.now(timezone.utc)
            conn.last_sync_at = result.synced_at
            db.commit()

        return result
    finally:
        connector.close()


def get_tables(db: Session, db_id: int) -> Optional[list[dict]]:
    conn = get_database(db, db_id)
    if not conn:
        return None
    connector = get_connector(conn)
    try:
        return connector.get_tables_list()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to list tables: {friendly_error(str(e))}")
    finally:
        connector.close()


def get_table_details(db: Session, db_id: int, table_name: str):
    conn = get_database(db, db_id)
    if not conn:
        return None
    connector = get_connector(conn)
    try:
        return connector.get_table_details(table_name)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to get table details: {friendly_error(str(e))}")
    finally:
        connector.close()
