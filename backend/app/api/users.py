from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.deps import get_current_user
from app.models.user import User, UserRole
from app.schemas.user import UserResponse, UpdateProfileRequest, UserListResponse
from app.schemas.common import MessageResponse
from app.services import user_service, role_service
from app.services.audit_service import create_audit_log

router = APIRouter(tags=["Users"])


@router.get("/users/me", response_model=UserResponse)
def get_current_user_profile(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return user_service.get_user_response(db, current_user)


@router.put("/users/me", response_model=UserResponse)
def update_current_user_profile(
    data: UpdateProfileRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return user_service.update_profile(db, current_user.id, data, current_user.id)


@router.get("/users", response_model=UserListResponse)
def list_users(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    search: str = Query(None),
    is_active: bool = Query(None),
    role_id: int = Query(None),
    sort_by: str = Query("created_at"),
    sort_order: str = Query("desc"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    users, total = user_service.list_users(
        db, page, per_page, search, is_active, role_id, sort_by, sort_order,
    )
    pages = max(1, (total + per_page - 1) // per_page)
    return UserListResponse(users=users, total=total, page=page, per_page=per_page, pages=pages)


@router.get("/users/{user_id}", response_model=UserResponse)
def get_user(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user = user_service.get_user_by_id(db, user_id)
    if not user:
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user_service.get_user_response(db, user)


@router.put("/users/{user_id}", response_model=UserResponse)
def update_user(
    user_id: int,
    data: UpdateProfileRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return user_service.update_profile(db, user_id, data, current_user.id)


@router.delete("/users/{user_id}", response_model=MessageResponse)
def delete_user(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user_service.delete_user(db, user_id, current_user.id)
    return MessageResponse(message="User deleted")


@router.post("/users/{user_id}/activate", response_model=MessageResponse)
def activate_user(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user = user_service.get_user_by_id(db, user_id)
    if not user:
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    user.is_active = True
    db.commit()
    create_audit_log(db, current_user.id, "user.activate", "user", str(user_id))
    return MessageResponse(message="User activated")


@router.post("/users/{user_id}/deactivate", response_model=MessageResponse)
def deactivate_user(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user = user_service.get_user_by_id(db, user_id)
    if not user:
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    user.is_active = False
    db.commit()
    create_audit_log(db, current_user.id, "user.deactivate", "user", str(user_id))
    return MessageResponse(message="User deactivated")


@router.get("/users/{user_id}/roles")
def get_user_roles(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.models.role import Role
    user_roles = (
        db.query(Role)
        .join(UserRole, Role.id == UserRole.role_id)
        .filter(UserRole.user_id == user_id)
        .all()
    )
    roles = [role_service.get_role_response(db, r) for r in user_roles]
    return {"roles": roles, "total": len(roles)}


@router.post("/users/{user_id}/roles", response_model=MessageResponse)
def assign_role_to_user(
    user_id: int,
    data: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    role_id = data.get("role_id")
    if not role_id:
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="role_id is required")

    existing = db.query(UserRole).filter(
        UserRole.user_id == user_id, UserRole.role_id == role_id
    ).first()
    if existing:
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Role already assigned")

    ur = UserRole(user_id=user_id, role_id=role_id, assigned_by=current_user.id)
    db.add(ur)
    db.commit()
    create_audit_log(db, current_user.id, "user.assign_role", "user", str(user_id),
                     {"role_id": role_id})
    return MessageResponse(message="Role assigned")


@router.delete("/users/{user_id}/roles/{role_id}", response_model=MessageResponse)
def remove_role_from_user(
    user_id: int,
    role_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ur = db.query(UserRole).filter(
        UserRole.user_id == user_id, UserRole.role_id == role_id
    ).first()
    if not ur:
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role assignment not found")

    db.delete(ur)
    db.commit()
    create_audit_log(db, current_user.id, "user.remove_role", "user", str(user_id),
                     {"role_id": role_id})
    return MessageResponse(message="Role removed")
