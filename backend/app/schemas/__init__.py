from datetime import datetime
from typing import Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---- Organizations ------------------------------------------------------------------
class OrganizationOut(ORM):
    id: UUID
    name: str
    slug: str
    timezone: str
    website: str | None
    status: str
    plan: str
    created_at: datetime


class OrganizationUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=200)
    timezone: str | None = Field(None, max_length=64)
    website: str | None = Field(None, max_length=300)


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    timezone: str = "Asia/Kolkata"
    website: str | None = None
    admin_email: EmailStr
    admin_name: str = Field(min_length=1, max_length=200)


class OrganizationAdminUpdate(OrganizationUpdate):
    status: str | None = Field(None, pattern="^(active|suspended)$")
    plan: str | None = Field(None, max_length=40)


class OrganizationCreated(BaseModel):
    organization: OrganizationOut
    admin_email: str
    temporary_password: str | None


# ---- Auth ---------------------------------------------------------------------------
class LoginIn(BaseModel):
    email: EmailStr
    password: str
    organization_id: UUID | None = None
    device_name: str | None = Field(None, max_length=120)


class RefreshIn(BaseModel):
    refresh_token: str


class SwitchOrgIn(BaseModel):
    organization_id: UUID
    refresh_token: str


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=128)


class MembershipSummary(BaseModel):
    organization_id: UUID
    organization_name: str
    organization_slug: str
    roles: list[str]


class UserOut(ORM):
    id: UUID
    email: str
    full_name: str
    is_platform_admin: bool
    must_change_password: bool


class SessionOut(BaseModel):
    user: UserOut
    organization: OrganizationOut | None
    permissions: list[str]
    memberships: list[MembershipSummary]


class TokenOut(SessionOut):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


# ---- Users (members of the current organization) ---------------------------------
class RoleRef(ORM):
    id: UUID
    name: str


class MemberOut(BaseModel):
    user_id: UUID
    email: str
    full_name: str
    is_active: bool
    roles: list[RoleRef]
    last_login_at: datetime | None
    joined_at: datetime


class MemberCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)
    role_ids: list[UUID] = Field(min_length=1)


class MemberCreated(BaseModel):
    member: MemberOut
    temporary_password: str | None


class MemberUpdate(BaseModel):
    full_name: str | None = Field(None, min_length=1, max_length=200)
    role_ids: list[UUID] | None = Field(None, min_length=1)
    is_active: bool | None = None


class PasswordReset(BaseModel):
    temporary_password: str


# ---- Roles & permissions -------------------------------------------------------------
class PermissionOut(ORM):
    code: str
    group: str
    description: str


class RoleOut(BaseModel):
    id: UUID
    name: str
    description: str | None
    is_system: bool
    permissions: list[str]
    member_count: int


class RoleIn(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    description: str | None = Field(None, max_length=300)
    permissions: list[str] = []


class RoleUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=100)
    description: str | None = Field(None, max_length=300)
    permissions: list[str] | None = None


# ---- Audit ------------------------------------------------------------------------
class AuditOut(ORM):
    id: UUID
    actor_email: str | None
    action: str
    entity_type: str | None
    entity_id: str | None
    summary: str | None
    ip_address: str | None
    created_at: datetime


T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
