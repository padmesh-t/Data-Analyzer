from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.schemas.connection import (
    DatabaseConnectionRequest, DatabaseConnectionUpdate,
    DatabaseResponse, DatabaseListResponse, DatabaseTestResult,
    SchemaResponse, SyncResult, ConnectionHealthResponse, BatchHealthResponse,
)
from app.schemas.common import MessageResponse
from app.services import connection_service
from app.services.audit_service import create_audit_log

router = APIRouter(prefix="/connections", tags=["Databases"])


@router.get("", response_model=DatabaseListResponse)
def list_databases(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    search: str = Query(None),
    type_filter: str = Query(None, alias="type"),
    is_active: bool = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    databases, total = connection_service.list_databases(
        db, page, per_page, search, type_filter, is_active,
    )
    return DatabaseListResponse(connections=databases, total=total)


@router.post("", response_model=DatabaseResponse, status_code=status.HTTP_201_CREATED)
def create_database(
    data: DatabaseConnectionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    result = connection_service.create_database(db, data, current_user.id)
    create_audit_log(db, current_user.id, "create", "database", result.id, {"name": data.name})
    return result


@router.get("/{database_id}", response_model=DatabaseResponse)
def get_database(
    database_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conn = connection_service.get_database(db, database_id)
    if not conn:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Database not found")
    return DatabaseResponse.model_validate(conn)


@router.put("/{database_id}", response_model=DatabaseResponse)
def update_database(
    database_id: int,
    data: DatabaseConnectionUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    result = connection_service.update_database(db, database_id, data)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Database not found")
    create_audit_log(db, current_user.id, "update", "database", database_id)
    return result


@router.delete("/{database_id}", response_model=MessageResponse)
def delete_database(
    database_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    deleted = connection_service.delete_database(db, database_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Database not found")
    create_audit_log(db, current_user.id, "delete", "database", database_id)
    return MessageResponse(message="Database removed successfully")


@router.post("/{database_id}/test", response_model=DatabaseTestResult)
def test_database_connection(
    database_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    result = connection_service.test_connection(db, database_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Database not found")
    create_audit_log(db, current_user.id, "test", "database", database_id, {"success": result.success})
    return result


@router.get("/health/batch", response_model=BatchHealthResponse)
def batch_connection_health(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return connection_service.check_all_health(db)


@router.get("/{database_id}/health", response_model=ConnectionHealthResponse)
def get_connection_health(
    database_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    result = connection_service.check_health(db, database_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Database not found")
    return result


@router.post("/{database_id}/sync", response_model=SyncResult)
def sync_database_schema(
    database_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    result = connection_service.sync_schema(db, database_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Database not found")
    create_audit_log(db, current_user.id, "sync", "database", database_id, {"status": result.status})
    return result


@router.get("/{database_id}/schema", response_model=SchemaResponse)
def get_database_schema(
    database_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    schema = connection_service.get_schema(db, database_id)
    if schema is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Database not found")
    return schema


@router.get("/{database_id}/tables")
def list_tables(
    database_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tables = connection_service.get_tables(db, database_id)
    if tables is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Database not found")
    return {"tables": tables}


@router.get("/{database_id}/tables/{table_name}")
def get_table_schema(
    database_id: int,
    table_name: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    table = connection_service.get_table_details(db, database_id, table_name)
    if table is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Database or table not found")
    return table
