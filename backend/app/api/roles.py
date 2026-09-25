from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.models.role import Role, RolePermission
from app.models.permission import Permission
from app.schemas.role import RoleResponse, RoleListResponse, CreateRoleRequest, UpdateRoleRequest
from app.schemas.common import MessageResponse
from app.schemas.permission import PermissionListResponse, PermissionResponse
from app.services import role_service
from app.services.audit_service import create_audit_log

router = APIRouter(tags=["Roles"])


@router.get("/roles", response_model=RoleListResponse)
def list_roles(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    roles, total = role_service.list_roles(db, page, per_page)
    return RoleListResponse(roles=roles, total=total)


@router.post("/roles", response_model=RoleResponse, status_code=201)
def create_role(
    data: CreateRoleRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return role_service.create_role(db, data, current_user.id)


@router.get("/roles/{role_id}", response_model=RoleResponse)
def get_role(
    role_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
    return role_service.get_role_response(db, role)


@router.put("/roles/{role_id}", response_model=RoleResponse)
def update_role(
    role_id: int,
    data: UpdateRoleRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return role_service.update_role(db, role_id, data, current_user.id)


@router.delete("/roles/{role_id}", response_model=MessageResponse)
def delete_role(
    role_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    role_service.delete_role(db, role_id, current_user.id)
    return MessageResponse(message="Role deleted")


@router.post("/roles/{role_id}/permissions", response_model=MessageResponse)
def add_permission_to_role(
    role_id: int,
    data: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    permission_id = data.get("permission_id")
    if not permission_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="permission_id is required")

    existing = db.query(RolePermission).filter(
        RolePermission.role_id == role_id,
        RolePermission.permission_id == permission_id,
    ).first()
    if not existing:
        db.add(RolePermission(role_id=role_id, permission_id=permission_id))
        db.commit()

    create_audit_log(db, current_user.id, "role.add_permission", "role", str(role_id),
                     {"permission_id": permission_id})
    return MessageResponse(message="Permission added")


@router.delete("/roles/{role_id}/permissions/{permission_id}", response_model=MessageResponse)
def remove_permission_from_role(
    role_id: int,
    permission_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rp = db.query(RolePermission).filter(
        RolePermission.role_id == role_id,
        RolePermission.permission_id == permission_id,
    ).first()
    if not rp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Permission not found on role")

    db.delete(rp)
    db.commit()
    create_audit_log(db, current_user.id, "role.remove_permission", "role", str(role_id),
                     {"permission_id": permission_id})
    return MessageResponse(message="Permission removed")
