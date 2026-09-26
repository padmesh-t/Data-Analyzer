from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime

from app.schemas.permission import PermissionResponse


class RoleInUser(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    is_system: bool
    permissions: List[PermissionResponse] = []
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class UserResponse(BaseModel):
    id: int
    email: str
    full_name: str
    phone: Optional[str] = None
    avatar_url: Optional[str] = None
    bio: Optional[str] = None
    company_id: Optional[int] = None
    company_name: Optional[str] = None
    auth_provider: str
    is_active: bool
    mfa_enabled: bool
    roles: List[RoleInUser] = []
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class UpdateProfileRequest(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    avatar_url: Optional[str] = None
    bio: Optional[str] = None


class CreateUserRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    phone: Optional[str] = None
    role: Optional[str] = None
    role_id: Optional[int] = None
    roles: Optional[List[str]] = None
    role_ids: Optional[List[int]] = None


class UserListResponse(BaseModel):
    users: List[UserResponse]
    total: int
    page: int
    per_page: int
    pages: int
