from typing import Optional

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.models.user import User, UserRole
from app.models.role import Role
from app.schemas.user import UserResponse, RoleInUser, UpdateProfileRequest
from app.services.audit_service import create_audit_log


def get_user_by_id(db: Session, user_id: int) -> Optional[User]:
    return db.query(User).filter(User.id == user_id).first()


def get_user_response(db: Session, user: User) -> UserResponse:
    from app.models.role import RolePermission
    from app.models.permission import Permission
    from app.schemas.permission import PermissionResponse

    user_roles = (
        db.query(Role)
        .join(UserRole, Role.id == UserRole.role_id)
        .filter(UserRole.user_id == user.id)
        .all()
    )
    roles = []
    for r in user_roles:
        perms = (
            db.query(Permission)
            .join(RolePermission, Permission.id == RolePermission.permission_id)
            .filter(RolePermission.role_id == r.id)
            .all()
        )
        roles.append(
            RoleInUser(
                id=r.id, name=r.name, description=r.description,
                is_system=r.is_system,
                permissions=[
                    PermissionResponse(
                        id=p.id, name=p.name, resource=p.resource,
                        action=p.action, description=p.description, created_at=p.created_at,
                    )
                    for p in perms
                ],
                created_at=r.created_at, updated_at=r.updated_at,
            )
        )
    company_name = None
    if user.company_id:
        from app.models.company import Company
        c = db.query(Company).filter(Company.id == user.company_id).first()
        if c:
            company_name = c.name

    return UserResponse(
        id=user.id, email=user.email, full_name=user.full_name,
        phone=user.phone, avatar_url=user.avatar_url, bio=user.bio,
        company_id=user.company_id, company_name=company_name,
        auth_provider=user.auth_provider, is_active=user.is_active,
        mfa_enabled=user.mfa_enabled, roles=roles,
        created_at=user.created_at, updated_at=user.updated_at,
    )


def list_users(
    db: Session,
    page: int = 1,
    per_page: int = 20,
    search: Optional[str] = None,
    is_active: Optional[bool] = None,
    role_id: Optional[int] = None,
    sort_by: str = "created_at",
    sort_order: str = "desc",
    company_id: Optional[int] = None,
):
    query = db.query(User)

    if company_id is not None:
        query = query.filter(User.company_id == company_id)

    if search:
        query = query.filter(
            User.full_name.ilike(f"%{search}%") | User.email.ilike(f"%{search}%")
        )
    if is_active is not None:
        query = query.filter(User.is_active == is_active)

    if role_id:
        query = query.join(UserRole).filter(UserRole.role_id == role_id)

    total = query.count()

    sort_column = getattr(User, sort_by, User.created_at)
    if sort_order == "desc":
        query = query.order_by(desc(sort_column))
    else:
        query = query.order_by(sort_column)

    users = query.offset((page - 1) * per_page).limit(per_page).all()

    return [get_user_response(db, u) for u in users], total


def update_profile(db: Session, user_id: int, data: UpdateProfileRequest, current_user_id: int) -> UserResponse:
    user = get_user_by_id(db, user_id)
    if not user:
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if data.full_name is not None:
        user.full_name = data.full_name
    if data.phone is not None:
        user.phone = data.phone
    if data.avatar_url is not None:
        user.avatar_url = data.avatar_url if data.avatar_url else None
    if data.bio is not None:
        user.bio = data.bio

    db.commit()
    db.refresh(user)

    create_audit_log(
        db, current_user_id, "user.update", "user", str(user_id),
        {"fields": [k for k, v in data.model_dump().items() if v is not None]},
    )

    return get_user_response(db, user)


def delete_user(db: Session, user_id: int, current_user_id: int):
    from fastapi import HTTPException, status
    from app.api.deps import is_user_superadmin

    if user_id == current_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot delete your own account",
        )
    user = get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if is_user_superadmin(db, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot delete the company SuperAdmin",
        )

    user.is_active = False
    db.commit()

    create_audit_log(db, current_user_id, "user.delete", "user", str(user_id))


def create_user(db: Session, data, current_user_id: int) -> UserResponse:
    from fastapi import HTTPException, status
    from app.utils.security import get_password_hash

    existing = db.query(User).filter(User.email == data.email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User with this email already exists",
        )

    creator = db.query(User).filter(User.id == current_user_id).first()
    creator_company_id = creator.company_id if creator else None

    user = User(
        email=data.email,
        password_hash=get_password_hash(data.password),
        full_name=data.full_name,
        phone=data.phone,
        company_id=creator_company_id,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # Assign roles (from role_id, role, role_ids, or role names)
    assigned_role_ids = set(data.role_ids or [])
    if data.role_id:
        assigned_role_ids.add(data.role_id)

    roles_list = list(data.roles or [])
    if data.role:
        roles_list.append(data.role)

    superadmin_role = db.query(Role).filter(Role.name == "SuperAdmin").first()
    admin_role = db.query(Role).filter(Role.name == "Admin").first()

    for r_name in roles_list:
        role_obj = db.query(Role).filter(Role.name == r_name).first()
        if role_obj:
            assigned_role_ids.add(role_obj.id)

    # If SuperAdmin was requested for a new user, prevent duplicate SuperAdmin and assign Admin
    if superadmin_role and superadmin_role.id in assigned_role_ids:
        assigned_role_ids.remove(superadmin_role.id)
        if admin_role:
            assigned_role_ids.add(admin_role.id)

    # If no roles specified, default to Analyst
    if not assigned_role_ids:
        default_role = db.query(Role).filter(Role.name == "Analyst").first() or db.query(Role).filter(Role.name == "Viewer").first()
        if default_role:
            assigned_role_ids.add(default_role.id)

    for rid in assigned_role_ids:
        db.add(UserRole(user_id=user.id, role_id=rid, assigned_by=current_user_id))

    db.commit()

    create_audit_log(
        db, current_user_id, "user.create", "user", str(user.id),
        {"email": user.email, "roles": list(assigned_role_ids)},
    )

    return get_user_response(db, user)
