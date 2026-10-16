"""Database engine and tenant-aware sessions.

Every transaction sets two PostgreSQL settings that the row-level-security policies read:
  app.org_id      -> the current tenant (organization) id
  app.bypass_rls  -> 'on' only for platform-level operations (login lookup, tenant admin)
"""

from collections.abc import AsyncIterator
from uuid import UUID

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

_s = get_settings()
engine = (
    create_async_engine(_s.database_url, poolclass=NullPool)
    if _s.db_null_pool
    else create_async_engine(_s.database_url, pool_pre_ping=True)
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


@event.listens_for(Session, "after_begin")
def _apply_tenant_context(session: Session, transaction, connection) -> None:
    org_id = session.info.get("org_id")
    bypass = "on" if session.info.get("bypass_rls") else "off"
    connection.execute(
        text("SELECT set_config('app.org_id', :org, true), set_config('app.bypass_rls', :bypass, true)"),
        {"org": str(org_id) if org_id else "", "bypass": bypass},
    )


def set_tenant(session: AsyncSession, org_id: UUID | None) -> None:
    """Scope the session to one organization. Applies from the next transaction."""
    session.info["org_id"] = org_id
    session.info["bypass_rls"] = False


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


def platform_session() -> AsyncSession:
    """Session that bypasses tenant isolation. Use only for platform-level work."""
    session = SessionLocal()
    session.info["bypass_rls"] = True
    return session
