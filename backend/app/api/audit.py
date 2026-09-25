from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.schemas.audit import AuditLogListResponse, AuditStats
from app.services import audit_service

router = APIRouter(tags=["Audit"])


@router.get("/audit/logs", response_model=AuditLogListResponse)
def list_audit_logs(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    user_id: int = Query(None),
    action: str = Query(None),
    resource_type: str = Query(None),
    status: str = Query(None),
    start_date: str = Query(None),
    end_date: str = Query(None),
    sort_by: str = Query("created_at"),
    sort_order: str = Query("desc"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    logs, total = audit_service.get_audit_logs(
        db, page, per_page, user_id, action,
        resource_type, status, start_date, end_date,
        sort_by, sort_order,
    )
    return AuditLogListResponse(logs=logs, total=total, page=page, per_page=per_page)


@router.get("/audit/stats", response_model=AuditStats)
def get_audit_stats(
    start_date: str = Query(None),
    end_date: str = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return audit_service.get_audit_stats(db)
