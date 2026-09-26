from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User, UserRole
from app.models.role import Role, RolePermission
from app.models.permission import Permission
from app.utils.security import decode_token

security = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = credentials.credentials
    payload = decode_token(token)
    if payload is None or payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    user = db.query(User).filter(User.id == int(user_id)).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is deactivated",
        )

    return user


def _role_ids(db: Session, user_id: int) -> list[int]:
    rows = db.query(UserRole.role_id).filter(UserRole.user_id == user_id).all()
    return [r[0] for r in rows]


def user_has_permission(db: Session, user: User, permission: str) -> bool:
    """Return True if the user (via any of their roles) holds the permission."""
    return user_has_permission_by_id(db, user.id, permission)


def user_has_permission_by_id(db: Session, user_id: int, permission: str) -> bool:
    """Return True if the user id (via any of their roles) holds the permission."""
    role_ids = _role_ids(db, user_id)
    if not role_ids:
        return False
    return (
        db.query(Permission.id)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .filter(
            RolePermission.role_id.in_(role_ids),
            Permission.name == permission,
        )
        .first()
        is not None
    )


def require_permission(permission: str):
    """Dependency factory: 403 unless the current user holds the permission."""

    def _dep(
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> User:
        if not user_has_permission(db, current_user, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return current_user

    return _dep


def require_any_permission(*permissions: str):
    """Dependency factory: 403 unless the current user holds at least one of the permissions."""

    def _dep(
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> User:
        if not any(user_has_permission(db, current_user, perm) for perm in permissions):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return current_user

    return _dep



def is_user_superadmin(db: Session, user: User) -> bool:
    """Return True if the user holds the SuperAdmin role or is the owner of their company."""
    if not user:
        return False
    if user.company_id:
        from app.models.company import Company
        company = db.query(Company).filter(Company.id == user.company_id).first()
        if company and company.owner_id == user.id:
            return True
    role_ids = _role_ids(db, user.id)
    if not role_ids:
        return False
    return (
        db.query(Role.id)
        .filter(Role.id.in_(role_ids), Role.name == "SuperAdmin")
        .first()
        is not None
    )


def require_superadmin():
    """Dependency factory: 403 unless the current user is a SuperAdmin / Company Owner."""

    def _dep(
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> User:
        if not is_user_superadmin(db, current_user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="SuperAdmin privileges required",
            )
        return current_user

    return _dep

