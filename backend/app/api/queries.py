from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.deps import require_permission, require_any_permission
from app.models.user import User
from app.schemas.query import (
    QueryRequest, SQLExecutionRequest, FollowUpRequest,
    QueryResponse, QueryListResponse,
    ExplainResponse, OptimizeResponse, VisualizeResponse,
)
from app.schemas.conversation import MessageResponse
from app.services import query_service
from app.services.audit_service import create_audit_log

router = APIRouter(prefix="/queries", tags=["Queries"])


@router.get("", response_model=QueryListResponse)
def list_queries(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    database_id: int = Query(None),
    status: str = Query(None, pattern=r"^(pending|executing|completed|failed|cancelled)$"),
    current_user: User = Depends(require_permission("query.read")),
    db: Session = Depends(get_db),
):
    queries, total, pages = query_service.list_queries(
        db, current_user, page, per_page, database_id, status,
    )
    return QueryListResponse(queries=queries, total=total, page=page, per_page=per_page, pages=pages)


@router.post("", response_model=QueryResponse)
def execute_query(
    data: QueryRequest,
    current_user: User = Depends(require_permission("query.execute")),
    db: Session = Depends(get_db),
):
    result = query_service.execute_natural_language_query(db, data, current_user.id)
    create_audit_log(
        db, current_user.id, "execute", "query",
        result.id, {"database_id": data.database_id, "status": result.status},
    )
    return result


@router.get("/suggestions", response_model=list[str])
def query_suggestions(
    q: str = Query("", min_length=1, max_length=200),
    current_user: User = Depends(require_permission("query.read")),
    db: Session = Depends(get_db),
):
    return query_service.get_suggestions(db, current_user, q)


@router.get("/{query_id}", response_model=QueryResponse)
def get_query(
    query_id: int,
    current_user: User = Depends(require_any_permission("query.read", "dashboard.read", "access.manage")),
    db: Session = Depends(get_db),
):
    return query_service.get_query(db, query_id, current_user)


@router.post("/sql", response_model=QueryResponse)
def execute_sql(
    data: SQLExecutionRequest,
    current_user: User = Depends(require_permission("query.execute")),
    db: Session = Depends(get_db),
):
    result = query_service.execute_raw_sql(db, data, current_user.id)
    create_audit_log(
        db, current_user.id, "execute_sql", "query",
        result.id, {"database_id": data.database_id, "status": result.status},
    )
    return result


@router.post("/{query_id}/cancel", response_model=MessageResponse)
def cancel_query(
    query_id: int,
    current_user: User = Depends(require_permission("query.execute")),
    db: Session = Depends(get_db),
):
    cancelled = query_service.cancel_query(db, query_id, current_user)
    if not cancelled:
        raise HTTPException(status_code=400, detail="Query cannot be cancelled in its current state")
    create_audit_log(db, current_user.id, "cancel", "query", query_id)
    return MessageResponse(message="Query cancelled")


@router.post("/{query_id}/follow-up", response_model=QueryResponse)
def query_follow_up(
    query_id: int,
    data: FollowUpRequest,
    current_user: User = Depends(require_permission("query.execute")),
    db: Session = Depends(get_db),
):
    result = query_service.follow_up_query(db, query_id, data, current_user.id)
    create_audit_log(
        db, current_user.id, "follow_up", "query",
        result.id, {"parent_query_id": query_id},
    )
    return result


@router.get("/{query_id}/explain", response_model=ExplainResponse)
def explain_query(
    query_id: int,
    current_user: User = Depends(require_permission("query.read")),
    db: Session = Depends(get_db),
):
    return query_service.explain_query(db, query_id, current_user)


@router.post("/{query_id}/optimize", response_model=OptimizeResponse)
def optimize_query(
    query_id: int,
    current_user: User = Depends(require_permission("query.read")),
    db: Session = Depends(get_db),
):
    return query_service.optimize_query(db, query_id, current_user)


@router.post("/{query_id}/visualize", response_model=VisualizeResponse)
def visualize_query(
    query_id: int,
    current_user: User = Depends(require_permission("query.read")),
    db: Session = Depends(get_db),
):
    return query_service.visualize_query(db, query_id, current_user)


@router.post("/{query_id}/favorite", response_model=MessageResponse)
def favorite_query(
    query_id: int,
    current_user: User = Depends(require_permission("query.read")),
    db: Session = Depends(get_db),
):
    query = query_service.get_query(db, query_id, current_user)
    if not query:
        raise HTTPException(status_code=404, detail="Query not found")
    create_audit_log(db, current_user.id, "favorite", "query", query_id)
    return MessageResponse(message="Query favorited")


@router.delete("/{query_id}", response_model=MessageResponse)
def delete_query(
    query_id: int,
    current_user: User = Depends(require_permission("query.delete")),
    db: Session = Depends(get_db),
):
    from app.models.query import Query as QueryModel
    q = db.query(QueryModel).filter(QueryModel.id == query_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="Query not found")
    if q.user_id != current_user.id and not _user_has_manage_permission(db, current_user.id):
        raise HTTPException(status_code=404, detail="Query not found")
    db.delete(q)
    db.commit()
    create_audit_log(db, current_user.id, "delete", "query", query_id)
    return MessageResponse(message="Query deleted")


@router.get("/{query_id}/export")
def export_query(
    query_id: int,
    current_user: User = Depends(require_permission("query.read")),
    db: Session = Depends(get_db),
):
    query = query_service.get_query(db, query_id, current_user)
    if not query:
        raise HTTPException(status_code=404, detail="Query not found")
    create_audit_log(db, current_user.id, "export", "query", query_id)
    return query
    return query

