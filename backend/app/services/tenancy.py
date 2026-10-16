"""Organization (tenant) provisioning and permission sync."""

import logging
import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import platform_session
from app.core.permissions import ADMIN_ROLE, ALL_CODES, DEFAULT_ROLES, PERMISSIONS
from app.core.security import generate_password, hash_password
from app.models import Membership, MembershipRole, Organization, Permission, Role, RolePermission, User

log = logging.getLogger(__name__)


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:70] or "org"


async def unique_slug(session: AsyncSession, name: str) -> str:
    base = slugify(name)
    slug, n = base, 2
    while await session.scalar(select(Organization.id).where(Organization.slug == slug)):
        slug, n = f"{base}-{n}", n + 1
    return slug


async def sync_permissions(session: AsyncSession) -> None:
    """Upsert the permission catalog and give every system Administrator role all permissions.
    Must run in a platform (bypass) session."""
    existing = {p.code: p for p in (await session.scalars(select(Permission))).all()}
    for code, group, desc in PERMISSIONS:
        if code in existing:
            existing[code].group, existing[code].description = group, desc
        else:
            session.add(Permission(code=code, group=group, description=desc))
    await session.flush()

    admin_roles = (await session.scalars(select(Role).where(Role.is_system.is_(True)))).all()
    for role in admin_roles:
        have = {rp.permission_code for rp in role.permissions}
        for code in ALL_CODES:
            if code not in have:
                role.permissions.append(
                    RolePermission(organization_id=role.organization_id, permission_code=code)
                )


async def provision_organization(
    session: AsyncSession,
    *,
    name: str,
    admin_email: str,
    admin_name: str,
    timezone: str | None = None,
    website: str | None = None,
    admin_password: str | None = None,
) -> tuple[Organization, User, str | None]:
    """Create a tenant with default roles and its first administrator.

    Returns (organization, admin user, temporary password if a new user was created).
    Must run in a platform (bypass) session.
    """
    org = Organization(
        id=uuid.uuid4(),
        name=name,
        slug=await unique_slug(session, name),
        timezone=timezone or get_settings().default_timezone,
        website=website,
    )
    session.add(org)
    await session.flush()

    admin_role = Role(organization_id=org.id, name=ADMIN_ROLE, description="Full access", is_system=True)
    admin_role.permissions = [RolePermission(organization_id=org.id, permission_code=c) for c in ALL_CODES]
    session.add(admin_role)
    for role_name, (desc, codes) in DEFAULT_ROLES.items():
        role = Role(organization_id=org.id, name=role_name, description=desc)
        role.permissions = [RolePermission(organization_id=org.id, permission_code=c) for c in codes]
        session.add(role)
    await session.flush()

    email = admin_email.strip().lower()
    user = await session.scalar(select(User).where(User.email == email))
    temp_password = None
    if user is None:
        temp_password = admin_password or generate_password()
        user = User(
            email=email,
            full_name=admin_name,
            password_hash=hash_password(temp_password),
            must_change_password=True,
        )
        session.add(user)
        await session.flush()

    membership = Membership(organization_id=org.id, user_id=user.id)
    membership.roles = [MembershipRole(organization_id=org.id, role_id=admin_role.id)]
    session.add(membership)
    await session.flush()
    return org, user, temp_password


async def bootstrap() -> None:
    """Runs on startup: sync permissions; on an empty database create the platform admin
    and the first tenant (HRP AI LLP)."""
    s = get_settings()
    async with platform_session() as session:
        await sync_permissions(session)
        if not await session.scalar(select(User.id).limit(1)):
            password = s.platform_admin_password or generate_password()
            org, user, _ = await provision_organization(
                session,
                name=s.default_org_name,
                admin_email=s.platform_admin_email,
                admin_name=s.platform_admin_name,
                admin_password=password,
                website="https://hrpai.in",
            )
            user.is_platform_admin = True
            if org.slug != s.default_org_slug and not await session.scalar(
                select(Organization.id).where(Organization.slug == s.default_org_slug)
            ):
                org.slug = s.default_org_slug
            if not s.platform_admin_password:
                log.warning("Created platform admin %s with password: %s", user.email, password)
            log.info("Bootstrapped platform admin %s and organization %s", user.email, org.name)
        await session.commit()
