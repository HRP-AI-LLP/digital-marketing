"""Platform-level runtime state (not tenant data): the worker's heartbeat and recorded downtime."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, utcnow


class SystemState(Base):
    """Small key/value store. key 'worker': {last_seen, online, offline_since, clock_skew_seconds, wake_at}."""

    __tablename__ = "system_state"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class DowntimePeriod(IdMixin, Base):
    """A stretch when the app couldn't post: the laptop was off / the app stopped ('stopped'),
    the laptop slept or hibernated ('asleep'), or there was no internet ('offline')."""

    __tablename__ = "downtime_periods"

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    reason: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
