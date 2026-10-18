from datetime import datetime
from typing import Generic, TypeVar
from uuid import UUID

import re

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


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
    legal_name: str | None = None
    tagline: str | None = None
    description: str | None = None
    industry: str | None = None
    company_size: str | None = None
    founded_year: int | None = None
    email: str | None = None
    phone: str | None = None
    country: str | None = None
    currency: str = "INR"
    address: dict = {}
    registration: dict = {}
    social_links: dict = {}
    contacts: list = []
    logo_asset_id: UUID | None = None
    logo_dark_asset_id: UUID | None = None
    logo_url: str | None = None
    logo_dark_url: str | None = None

    @model_validator(mode="after")
    def _urls(self):
        from app.core.signing import media_url

        for f in ("address", "registration", "social_links"):
            if getattr(self, f) is None:
                setattr(self, f, {})
        if self.contacts is None:
            self.contacts = []
        self.currency = self.currency or "INR"
        if self.logo_asset_id:
            self.logo_url = media_url(self.logo_asset_id, self.id)
        if self.logo_dark_asset_id:
            self.logo_dark_url = media_url(self.logo_dark_asset_id, self.id)
        return self


ADDRESS_KEYS = ("line1", "line2", "city", "state", "postal_code")
REGISTRATION_KEYS = ("gstin", "cin", "pan", "udyam", "other")
SOCIAL_KEYS = ("linkedin", "facebook", "instagram", "x", "youtube", "tiktok", "threads", "pinterest", "github", "other")
COMPANY_SIZES = ("1", "2-10", "11-50", "51-200", "201-500", "501-1000", "1001-5000", "5000+")
GSTIN_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][A-Z\d]Z[A-Z\d]$")


def _clean_map(v: dict | None, keys: tuple[str, ...], n: int = 300) -> dict:
    out = {}
    for k in keys:
        x = str((v or {}).get(k) or "").strip()
        if x:
            out[k] = x[:n]
    return out


class Contact(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    role: str | None = Field(None, max_length=120)
    email: EmailStr | None = None
    phone: str | None = Field(None, max_length=40)


class OrganizationUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=200)
    timezone: str | None = Field(None, max_length=64)
    website: str | None = Field(None, max_length=300)
    legal_name: str | None = Field(None, max_length=200)
    tagline: str | None = Field(None, max_length=200)
    description: str | None = Field(None, max_length=5000)
    industry: str | None = Field(None, max_length=120)
    company_size: str | None = None
    founded_year: int | None = Field(None, ge=1800, le=2100)
    email: EmailStr | None = None
    phone: str | None = Field(None, max_length=40)
    country: str | None = Field(None, max_length=80)
    currency: str | None = Field(None, pattern="^[A-Z]{3}$")
    address: dict | None = None
    registration: dict | None = None
    social_links: dict | None = None
    contacts: list[Contact] | None = Field(None, max_length=20)

    @field_validator("company_size")
    @classmethod
    def _size(cls, v):
        if v and v not in COMPANY_SIZES:
            raise ValueError("Pick a company size from the list.")
        return v or None

    @field_validator("phone")
    @classmethod
    def _phone(cls, v):
        if v and not re.fullmatch(r"[+\d][\d \-()]{5,39}", v.strip()):
            raise ValueError("Enter a phone number with digits, spaces, + or -.")
        return v.strip() if v else None

    @field_validator("address")
    @classmethod
    def _address(cls, v):
        return None if v is None else _clean_map(v, ADDRESS_KEYS)

    @field_validator("registration")
    @classmethod
    def _registration(cls, v):
        if v is None:
            return None
        out = _clean_map(v, REGISTRATION_KEYS, 60)
        for k in ("gstin", "cin", "pan"):
            if k in out:
                out[k] = out[k].upper().replace(" ", "")
        if out.get("gstin") and not GSTIN_RE.match(out["gstin"]):
            raise ValueError("GSTIN should be 15 characters, like 29ABCDE1234F1Z5.")
        if out.get("pan") and not re.fullmatch(r"[A-Z]{5}\d{4}[A-Z]", out["pan"]):
            raise ValueError("PAN should be 10 characters, like ABCDE1234F.")
        return out

    @field_validator("social_links")
    @classmethod
    def _social(cls, v):
        if v is None:
            return None
        out = _clean_map(v, SOCIAL_KEYS)
        for k, url in out.items():
            if not re.match(r"^https?://", url):
                out[k] = "https://" + url.lstrip("/")
        return out


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
