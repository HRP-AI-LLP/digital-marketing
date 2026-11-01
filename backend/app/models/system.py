"""Platform-level runtime state (not tenant data): the worker's heartbeat and recorded downtime."""

from datetime import datetime

import uuid

from sqlalchemy import JSON, BigInteger, DateTime, String, Text, UniqueConstraint, Uuid
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


class BackupRun(IdMixin, Base):
    """One database backup attempt (platform level). kind: daily | manual. status: running | ok | failed."""

    __tablename__ = "backup_runs"

    kind: Mapped[str] = mapped_column(String(20), default="daily")
    status: Mapped[str] = mapped_column(String(20), default="running", index=True)
    method: Mapped[str | None] = mapped_column(String(20))  # podman | pg_dump
    file_name: Mapped[str | None] = mapped_column(String(200))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    media_file: Mapped[str | None] = mapped_column(String(200))
    media_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    copied_to: Mapped[str | None] = mapped_column(String(500))
    warning: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    triggered_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # file deleted by rotation


class UiTranslation(IdMixin, Base):
    """AI translation of one piece of interface text into one language (shared by everyone, never tenant data)."""

    __tablename__ = "ui_translations"
    __table_args__ = (UniqueConstraint("language", "source_hash", name="uq_ui_translations_lang_hash"),)

    language: Mapped[str] = mapped_column(String(80))
    source_hash: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
