"""Seed supported device types and constrain device records.

Revision ID: 0002_device_type_catalog
Revises: 0001_initial
"""

import sqlalchemy as sa

from alembic import op

revision = "0002_device_type_catalog"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

DEVICE_TYPES = (
    (
        "environment_sensor",
        "Publishes environmental measurements such as temperature and humidity.",
    ),
    (
        "touchscreen",
        "Reads environment, weather, waste collection, and dashboard data.",
    ),
)


def upgrade() -> None:
    supported_names = {name for name, _ in DEVICE_TYPES}
    existing_types = set(
        op.get_bind().execute(sa.text("SELECT DISTINCT device_type FROM devices")).scalars().all()
    )
    unsupported = sorted(existing_types - supported_names)
    if unsupported:
        raise RuntimeError(
            "Cannot apply device type catalog migration; devices use unsupported types: "
            + ", ".join(unsupported)
        )

    op.create_table(
        "device_types",
        sa.Column("name", sa.String(60), primary_key=True),
        sa.Column("description", sa.Text(), nullable=False),
    )
    op.bulk_insert(
        sa.table(
            "device_types",
            sa.column("name", sa.String(60)),
            sa.column("description", sa.Text()),
        ),
        [{"name": name, "description": description} for name, description in DEVICE_TYPES],
    )

    with op.batch_alter_table("devices") as batch_op:
        batch_op.create_foreign_key(
            "fk_devices_device_type_device_types",
            "device_types",
            ["device_type"],
            ["name"],
            ondelete="RESTRICT",
        )
        batch_op.create_index("ix_devices_device_type", ["device_type"])


def downgrade() -> None:
    with op.batch_alter_table("devices") as batch_op:
        batch_op.drop_index("ix_devices_device_type")
        batch_op.drop_constraint("fk_devices_device_type_device_types", type_="foreignkey")
    op.drop_table("device_types")
