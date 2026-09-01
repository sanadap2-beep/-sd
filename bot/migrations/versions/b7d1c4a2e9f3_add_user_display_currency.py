"""add user display currency and display exchange rates

Revision ID: b7d1c4a2e9f3
Revises: acf26de32101
Create Date: 2026-08-23

- عمود جديد users.display_currency (USD افتراضياً) لعرض الأسعار بعملة
  المستخدم مع بقاء الحسابات بالدولار.
- إعدادات أسعار صرف العرض اليومية (USD→EUR/EGP)؛ USD→SYP موجود مسبقاً.
"""

from alembic import op
import sqlalchemy as sa

revision = "b7d1c4a2e9f3"
down_revision = "acf26de32101"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column("display_currency", sa.String(8), nullable=False, server_default="USD")
        )

    settings_table = sa.Table(
        "settings",
        sa.MetaData(),
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.Text),
    )
    connection = op.get_bind()
    for key, value in (
        ("usd_to_eur_rate", "0.92"),
        ("usd_to_egp_rate", "48.5"),
    ):
        exists = connection.execute(
            sa.select(settings_table.c.key).where(settings_table.c.key == key)
        ).first()
        if not exists:
            connection.execute(settings_table.insert().values(key=key, value=value))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("display_currency")
