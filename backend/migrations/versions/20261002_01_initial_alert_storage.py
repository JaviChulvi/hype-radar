"""Create deterministic alert storage.

Revision ID: 20261002_01
Revises:
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261002_01"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "markets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("network", sa.String(length=32), nullable=False),
        sa.Column("dex", sa.String(length=128), nullable=False),
        sa.Column("coin", sa.String(length=128), nullable=False),
        sa.Column("base_unit", sa.String(length=32), nullable=True),
        sa.Column("quote_unit", sa.String(length=32), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=True),
        sa.Column("metadata_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_markets"),
        sa.UniqueConstraint("network", "dex", "coin", name="uq_market_identity"),
    )
    op.create_table(
        "alert_rules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("quality_policy", sa.String(length=32), nullable=False),
        sa.Column("cooldown_seconds", sa.Integer(), nullable=False),
        sa.Column("active_version_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_alert_rules"),
    )
    op.create_index("ix_alert_rules_owner_status", "alert_rules", ["owner_id", "status"])
    op.create_table(
        "market_samples",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("market_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("channel", sa.String(length=64), nullable=False),
        sa.Column("source_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mark_price", sa.Numeric(38, 18), nullable=True),
        sa.Column("oracle_price", sa.Numeric(38, 18), nullable=True),
        sa.Column("bid_price", sa.Numeric(38, 18), nullable=True),
        sa.Column("ask_price", sa.Numeric(38, 18), nullable=True),
        sa.Column("mid_price", sa.Numeric(38, 18), nullable=True),
        sa.Column("open_interest", sa.Numeric(38, 18), nullable=True),
        sa.Column("funding_rate", sa.Numeric(38, 18), nullable=True),
        sa.Column("bid_depth", sa.Numeric(38, 18), nullable=True),
        sa.Column("ask_depth", sa.Numeric(38, 18), nullable=True),
        sa.Column("gap", sa.Boolean(), nullable=False),
        sa.Column("out_of_order", sa.Boolean(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint("schema_version > 0", name="positive_schema_version"),
        sa.ForeignKeyConstraint(
            ["market_id"],
            ["markets.id"],
            name="fk_market_samples_market_id_markets",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_market_samples"),
        sa.UniqueConstraint("market_id", "source", "channel", "source_at", name="uq_market_sample_source"),
    )
    op.create_index("ix_market_samples_market_received", "market_samples", ["market_id", "received_at"])
    op.create_table(
        "regime_observations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("market_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("confirmation", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["market_id"],
            ["markets.id"],
            name="fk_regime_observations_market_id_markets",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_regime_observations"),
    )
    op.create_index(
        "ix_regime_observations_market_observed",
        "regime_observations",
        ["market_id", "observed_at"],
    )
    op.create_table(
        "alert_rule_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("rule_id", sa.Uuid(), nullable=False),
        sa.Column("market_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"], name="fk_alert_rule_versions_market_id_markets"),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["alert_rules.id"],
            name="fk_alert_rule_versions_rule_id_alert_rules",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_alert_rule_versions"),
        sa.UniqueConstraint("rule_id", "version", name="uq_alert_rule_version"),
    )
    op.create_table(
        "rule_runtime",
        sa.Column("rule_version_id", sa.Uuid(), nullable=False),
        sa.Column("condition_state", sa.String(length=32), nullable=False),
        sa.Column("true_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trigger_sequence", sa.Integer(), nullable=False),
        sa.Column("episode_triggered", sa.Boolean(), nullable=False),
        sa.Column("blocked_event_recorded", sa.Boolean(), nullable=False),
        sa.Column("last_processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["rule_version_id"],
            ["alert_rule_versions.id"],
            name="fk_rule_runtime_rule_version_id_alert_rule_versions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("rule_version_id", name="pk_rule_runtime"),
    )
    op.create_foreign_key(
        "fk_alert_rules_active_version_id_alert_rule_versions",
        "alert_rules",
        "alert_rule_versions",
        ["active_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_table(
        "alert_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("rule_version_id", sa.Uuid(), nullable=False),
        sa.Column("market_id", sa.Uuid(), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("condition_state", sa.String(length=32), nullable=False),
        sa.Column("quality_status", sa.String(length=32), nullable=False),
        sa.Column("transition", sa.String(length=64), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"], name="fk_alert_events_market_id_markets"),
        sa.ForeignKeyConstraint(
            ["rule_version_id"],
            ["alert_rule_versions.id"],
            name="fk_alert_events_rule_version_id_alert_rule_versions",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_alert_events"),
        sa.UniqueConstraint("fingerprint", name="uq_alert_events_fingerprint"),
    )
    op.create_index("ix_alert_events_rule_evaluated", "alert_events", ["rule_version_id", "evaluated_at"])
    op.create_index("ix_alert_events_market_status", "alert_events", ["market_id", "status"])
    op.create_table(
        "alert_evidence",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["alert_events.id"],
            name="fk_alert_evidence_event_id_alert_events",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_alert_evidence"),
        sa.UniqueConstraint("event_id", name="uq_alert_evidence_event_id"),
    )
    op.create_table(
        "notification_outbox",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("recipient", sa.String(length=256), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["alert_events.id"],
            name="fk_notification_outbox_event_id_alert_events",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_notification_outbox"),
        sa.UniqueConstraint("event_id", "channel", "recipient", name="uq_notification_target"),
    )
    op.create_index(
        "ix_notification_outbox_status_available",
        "notification_outbox",
        ["status", "available_at"],
    )
    op.create_table(
        "deliveries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("recipient", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("provider_response", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("provider_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["alert_events.id"],
            name="fk_deliveries_event_id_alert_events",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_deliveries"),
        sa.UniqueConstraint("event_id", "channel", "recipient", name="uq_delivery_target"),
    )


def downgrade() -> None:
    op.drop_table("deliveries")
    op.drop_index("ix_notification_outbox_status_available", table_name="notification_outbox")
    op.drop_table("notification_outbox")
    op.drop_table("alert_evidence")
    op.drop_index("ix_alert_events_market_status", table_name="alert_events")
    op.drop_index("ix_alert_events_rule_evaluated", table_name="alert_events")
    op.drop_table("alert_events")
    op.drop_table("rule_runtime")
    op.drop_constraint(
        "fk_alert_rules_active_version_id_alert_rule_versions",
        "alert_rules",
        type_="foreignkey",
    )
    op.drop_table("alert_rule_versions")
    op.drop_index("ix_regime_observations_market_observed", table_name="regime_observations")
    op.drop_table("regime_observations")
    op.drop_index("ix_market_samples_market_received", table_name="market_samples")
    op.drop_table("market_samples")
    op.drop_index("ix_alert_rules_owner_status", table_name="alert_rules")
    op.drop_table("alert_rules")
    op.drop_table("markets")
