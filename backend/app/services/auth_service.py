from datetime import datetime, timezone, timedelta
from secrets import token_urlsafe

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.user import User
from app.schemas.auth import RegisterRequest, LoginRequest, ForgotPasswordRequest, ResetPasswordRequest
from app.utils.security import (
    verify_password,
    get_password_hash,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.services.audit_service import create_audit_log
from app.services.email_service import send_reset_email
from app.config import settings


def register_user(db: Session, data: RegisterRequest, ip_address: str = None):
    existing = db.query(User).filter(User.email == data.email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    from app.models.company import Company

    company_name = (getattr(data, "company_name", None) or "").strip() or f"{data.full_name}'s Company"
    company = Company(name=company_name)
    db.add(company)
    db.commit()
    db.refresh(company)

    user = User(
        email=data.email,
        password_hash=get_password_hash(data.password),
        full_name=data.full_name,
        phone=data.phone,
        company_id=company.id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    company.owner_id = user.id
    db.commit()

    # Assign role: SuperAdmin (full administrator access) on signup
    from app.models.role import Role, RolePermission
    from app.models.user import UserRole
    from app.models.permission import Permission

    superadmin_role = db.query(Role).filter(Role.name == "SuperAdmin").first()
    if not superadmin_role:
        superadmin_role = Role(
            name="SuperAdmin",
            description="Full system access with all permissions",
            is_system=True,
        )
        db.add(superadmin_role)
        db.commit()
        db.refresh(superadmin_role)

    # Ensure all permissions are granted to SuperAdmin role
    all_perms = db.query(Permission).all()
    existing_perm_ids = {
        rp.permission_id
        for rp in db.query(RolePermission).filter(RolePermission.role_id == superadmin_role.id).all()
    }
    for perm in all_perms:
        if perm.id not in existing_perm_ids:
            db.add(RolePermission(role_id=superadmin_role.id, permission_id=perm.id))

    ur = UserRole(user_id=user.id, role_id=superadmin_role.id)
    db.add(ur)
    db.commit()

    create_audit_log(
        db, user.id, "register", "auth", str(user.id),
        {"email": user.email, "company_id": company.id, "company_name": company.name}, ip_address=ip_address,
    )

    return build_token_response(db, user)


def login_user(db: Session, data: LoginRequest, ip_address: str = None):
    user = db.query(User).filter(User.email == data.email).first()
    if not user or not verify_password(data.password, user.password_hash):
        create_audit_log(
            db, user.id if user else 0, "login", "auth", None,
            {"email": data.email, "ip": ip_address}, "failure", ip_address=ip_address,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated",
        )

    create_audit_log(
        db, user.id, "login", "auth", str(user.id),
        {"email": user.email}, ip_address=ip_address,
    )

    return build_token_response(db, user)


def refresh_token(db: Session, token: str):
    payload = decode_token(token)
    if payload is None or payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    user = db.query(User).filter(User.id == int(user_id)).first()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    return build_token_response(db, user)


def forgot_password(db: Session, data: ForgotPasswordRequest):
    user = db.query(User).filter(User.email == data.email).first()
    if not user:
        # Don't reveal if user exists
        return

    token = token_urlsafe(32)
    user.reset_token = token
    user.reset_token_expires = datetime.now(timezone.utc) + timedelta(minutes=30)
    db.commit()

    send_reset_email(user.email, token)


def reset_password(db: Session, data: ResetPasswordRequest):
    user = db.query(User).filter(User.reset_token == data.token).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )
    if not user.reset_token_expires or user.reset_token_expires < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reset token has expired",
        )

    user.password_hash = get_password_hash(data.new_password)
    user.reset_token = None
    user.reset_token_expires = None
    db.commit()

    create_audit_log(db, user.id, "reset_password", "auth", str(user.id))


def build_token_response(db: Session, user: User):
    from app.schemas.user import UserResponse, RoleInUser
    from app.schemas.permission import PermissionResponse
    from app.models.user import UserRole
    from app.models.role import Role, RolePermission
    from app.models.permission import Permission

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
        comp = db.query(Company).filter(Company.id == user.company_id).first()
        if comp:
            company_name = comp.name

    user_data = UserResponse(
        id=user.id, email=user.email, full_name=user.full_name,
        phone=user.phone, avatar_url=user.avatar_url, bio=user.bio,
        company_id=user.company_id, company_name=company_name,
        auth_provider=user.auth_provider, is_active=user.is_active,
        mfa_enabled=user.mfa_enabled, roles=roles,
        created_at=user.created_at, updated_at=user.updated_at,
    )

    access_token = create_access_token({"sub": str(user.id)})
    refresh_token_str = create_refresh_token({"sub": str(user.id)})

    return {
        "access_token": access_token,
        "refresh_token": refresh_token_str,
        "token_type": "bearer",
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "user": user_data,
    }
