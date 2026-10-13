"""Create device, credential and telemetry tables.

Revision ID: 0001_initial
"""

import sqlalchemy as sa

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "devices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("device_id", sa.String(100), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("device_type", sa.String(60), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("firmware_version", sa.String(60), nullable=True),
        sa.Column("hardware_model", sa.String(100), nullable=True),
        sa.Column("location", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'active', 'disabled', 'revoked')",
            name="ck_devices_status",
        ),
    )
    op.create_index("ix_devices_device_id", "devices", ["device_id"], unique=True)
    op.create_table(
        "device_capabilities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("capability", sa.String(100), nullable=False),
        sa.UniqueConstraint("device_id", "capability", name="uq_device_capability"),
    )
    op.create_index("ix_device_capabilities_device_id", "device_capabilities", ["device_id"])
    op.create_table(
        "credentials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("selector", sa.String(32), nullable=False),
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("salt", sa.String(64), nullable=False),
        sa.Column("secret_hash", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_credentials_selector", "credentials", ["selector"], unique=True)
    op.create_index("ix_credentials_device_id", "credentials", ["device_id"])
    op.create_table(
        "message_receipts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("message_id", sa.String(36), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("device_id", "message_id", name="uq_message_device_id"),
    )
    op.create_index("ix_message_receipts_device_id", "message_receipts", ["device_id"])
    op.create_table(
        "telemetry",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("message_id", sa.String(36), nullable=False),
        sa.Column("metric", sa.String(40), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(12), nullable=False),
        sa.Column("device_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "device_id", "message_id", "metric", name="uq_telemetry_message_metric"
        ),
    )
    op.create_index("ix_telemetry_device_id", "telemetry", ["device_id"])
    op.create_index("ix_telemetry_message_id", "telemetry", ["message_id"])
    op.create_index("ix_telemetry_device_timestamp", "telemetry", ["device_timestamp"])
    op.create_index(
        "ix_telemetry_recent_by_device_metric",
        "telemetry",
        ["device_id", "metric", "device_timestamp"],
    )


def downgrade() -> None:
    op.drop_table("telemetry")
    op.drop_table("message_receipts")
    op.drop_table("credentials")
    op.drop_table("device_capabilities")
    op.drop_table("devices")
