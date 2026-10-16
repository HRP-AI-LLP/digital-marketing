from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog


def record(
    session: AsyncSession,
    *,
    action: str,
    organization_id: UUID | None,
    actor=None,
    entity_type: str | None = None,
    entity_id: str | UUID | None = None,
    summary: str | None = None,
    details: dict | None = None,
    ip: str | None = None,
) -> None:
    """Add an audit entry to the current transaction (committed with the change it describes)."""
    session.add(
        AuditLog(
            organization_id=organization_id,
            actor_user_id=getattr(actor, "id", None),
            actor_email=getattr(actor, "email", None),
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id else None,
            summary=summary,
            details=details,
            ip_address=ip,
        )
    )
