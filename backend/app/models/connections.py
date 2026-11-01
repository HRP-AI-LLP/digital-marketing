"""Connections: outgoing webhooks (with their delivery log) and personal API tokens for automation tools
(Zapier, Make, n8n...).

All three tables are tenant-owned (organization_id + RLS, see TENANT_TABLES_CONNECTIONS).
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TenantMixin, TimestampMixin, utcnow

TENANT_TABLES_CONNECTIONS = ["webhooks", "webhook_deliveries", "api_tokens"]


class Webhook(IdMixin, TenantMixin, TimestampMixin, Base):
    """An address the studio calls (HTTP POST, JSON) when something happens.

    secret_enc:    Fernet-encrypted {"secret": "..."}; used to sign every delivery (X-HRP-Signature)
    events:        event names this webhook wants (see app.services.webhooks.EVENTS)
    last_status:   "ok" | "failed" | None (never called)
    failure_count: consecutive failed attempts; the webhook turns itself off at 20
    """

    __tablename__ = "webhooks"

    name: Mapped[str] = mapped_column(String(120))
    url: Mapped[str] = mapped_column(Text)
    events: Mapped[list] = mapped_column(JSON, default=list)
    secret_enc: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_status: Mapped[str | None] = mapped_column(String(20))
    last_status_code: Mapped[int | None] = mapped_column(Integer)
    last_delivery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    disabled_reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))


class WebhookDelivery(Base, IdMixin, TenantMixin):
    """One attempt to deliver one event to one webhook. Kept for 30 days.

    id is sent as X-HRP-Delivery; event_id is the body's "id" (the same for retries and redeliveries).
    payload is the exact JSON body that was sent (it never contains secrets).
    """

    __tablename__ = "webhook_deliveries"

    webhook_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("webhooks.id", ondelete="CASCADE"), index=True)
    event: Mapped[str] = mapped_column(String(40), index=True)
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status_code: Mapped[int | None] = mapped_column(Integer)
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    response_body: Mapped[str | None] = mapped_column(Text)
    redelivery: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ApiToken(IdMixin, TenantMixin, TimestampMixin, Base):
    """A personal access token: hrp_<prefix>_<secret>. Only a SHA-256 hash is stored.

    A request with the token acts as `user_id` in this organization, limited to `scopes`
    (and to whatever that person may still do: scopes ∩ their current permissions).
    """

    __tablename__ = "api_tokens"

    name: Mapped[str] = mapped_column(String(120))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    prefix: Mapped[str] = mapped_column(String(16), unique=True)
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_ip: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
