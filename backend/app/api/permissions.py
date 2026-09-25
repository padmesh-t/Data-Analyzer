from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.models.permission import Permission
from app.schemas.permission import PermissionListResponse, PermissionResponse

router = APIRouter(tags=["Permissions"])


@router.get("/permissions", response_model=PermissionListResponse)
def list_permissions(
    page: int = Query(1, ge=1),
    per_page: int = Query(100, ge=1, le=200),
    resource: str = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(Permission)
    if resource:
        query = query.filter(Permission.resource == resource)
    total = query.count()
    permissions = query.offset((page - 1) * per_page).limit(per_page).all()

    perms = [
        PermissionResponse(
            id=p.id, name=p.name, resource=p.resource,
            action=p.action, description=p.description, created_at=p.created_at,
        )
        for p in permissions
    ]
    return PermissionListResponse(permissions=perms, total=total)


@router.get("/permissions/{permission_id}", response_model=PermissionResponse)
def get_permission(
    permission_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    perm = db.query(Permission).filter(Permission.id == permission_id).first()
    if not perm:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Permission not found")

    return PermissionResponse(
        id=perm.id, name=perm.name, resource=perm.resource,
        action=perm.action, description=perm.description, created_at=perm.created_at,
    )
