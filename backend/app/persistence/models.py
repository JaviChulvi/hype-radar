from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from .base import Base


class MarketModel(Base):
    __tablename__ = "markets"
    __table_args__ = (UniqueConstraint("network", "dex", "coin", name="uq_market_identity"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    network: Mapped[str] = mapped_column(String(32), nullable=False)
    dex: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    coin: Mapped[str] = mapped_column(String(128), nullable=False)
    base_unit: Mapped[str | None] = mapped_column(String(32))
    quote_unit: Mapped[str | None] = mapped_column(String(32))
    timezone: Mapped[str | None] = mapped_column(String(64))
    metadata_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MarketSampleModel(Base):
    __tablename__ = "market_samples"
    __table_args__ = (
        UniqueConstraint("market_id", "source", "channel", "source_at", name="uq_market_sample_source"),
        Index("ix_market_samples_market_received", "market_id", "received_at"),
        CheckConstraint("schema_version > 0", name="positive_schema_version"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    market_id: Mapped[UUID] = mapped_column(ForeignKey("markets.id", ondelete="CASCADE"), nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    channel: Mapped[str] = mapped_column(String(64), nullable=False)
    source_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    mark_price: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    oracle_price: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    bid_price: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    ask_price: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    mid_price: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    open_interest: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    funding_rate: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    bid_depth: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    ask_depth: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    gap: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    out_of_order: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class RegimeObservationModel(Base):
    __tablename__ = "regime_observations"
    __table_args__ = (Index("ix_regime_observations_market_observed", "market_id", "observed_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    market_id: Mapped[UUID] = mapped_column(ForeignKey("markets.id", ondelete="CASCADE"), nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    confirmation: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class AlertRuleModel(Base):
    __tablename__ = "alert_rules"
    __table_args__ = (Index("ix_alert_rules_owner_status", "owner_id", "status"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    quality_policy: Mapped[str] = mapped_column(String(32), nullable=False)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_version_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey(
            "alert_rule_versions.id",
            name="fk_alert_rules_active_version_id_alert_rule_versions",
            use_alter=True,
            ondelete="SET NULL",
        ),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    versions: Mapped[list["AlertRuleVersionModel"]] = relationship(
        back_populates="rule",
        cascade="all, delete-orphan",
        foreign_keys="AlertRuleVersionModel.rule_id",
    )


class AlertRuleVersionModel(Base):
    __tablename__ = "alert_rule_versions"
    __table_args__ = (UniqueConstraint("rule_id", "version", name="uq_alert_rule_version"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    rule_id: Mapped[UUID] = mapped_column(ForeignKey("alert_rules.id", ondelete="CASCADE"), nullable=False)
    market_id: Mapped[UUID] = mapped_column(ForeignKey("markets.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    rule: Mapped[AlertRuleModel] = relationship(back_populates="versions", foreign_keys=[rule_id])


class RuleRuntimeModel(Base):
    __tablename__ = "rule_runtime"

    rule_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("alert_rule_versions.id", ondelete="CASCADE"), primary_key=True
    )
    condition_state: Mapped[str] = mapped_column(String(32), nullable=False)
    true_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trigger_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    episode_triggered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    blocked_event_recorded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AlertEventModel(Base):
    __tablename__ = "alert_events"
    __table_args__ = (
        Index("ix_alert_events_rule_evaluated", "rule_version_id", "evaluated_at"),
        Index("ix_alert_events_market_status", "market_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    rule_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("alert_rule_versions.id", ondelete="RESTRICT"), nullable=False
    )
    market_id: Mapped[UUID] = mapped_column(ForeignKey("markets.id"), nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    condition_state: Mapped[str] = mapped_column(String(32), nullable=False)
    quality_status: Mapped[str] = mapped_column(String(32), nullable=False)
    transition: Mapped[str] = mapped_column(String(64), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AlertEvidenceModel(Base):
    __tablename__ = "alert_evidence"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[UUID] = mapped_column(
        ForeignKey("alert_events.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class NotificationOutboxModel(Base):
    __tablename__ = "notification_outbox"
    __table_args__ = (
        UniqueConstraint("event_id", "channel", "recipient", name="uq_notification_target"),
        Index("ix_notification_outbox_status_available", "status", "available_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    event_id: Mapped[UUID] = mapped_column(ForeignKey("alert_events.id", ondelete="CASCADE"), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    recipient: Mapped[str] = mapped_column(String(256), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DeliveryModel(Base):
    __tablename__ = "deliveries"
    __table_args__ = (UniqueConstraint("event_id", "channel", "recipient", name="uq_delivery_target"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    event_id: Mapped[UUID] = mapped_column(ForeignKey("alert_events.id", ondelete="CASCADE"), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    recipient: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider_response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    provider_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
