"""number intent-first: idempotency key + UNKNOWN status for timeouts

Revision ID: l6m7n8o0p1q2
Revises: k6l7m8n9o0p1
Create Date: 2026-10-04

- number_orders.idempotency_key فريد nullable (نية الشراء قبل الخصم).
- حالة UNKNOWN في enum الحالات (PostgreSQL: ALTER TYPE، SQLite: لا شيء).
"""

from alembic import op
import sqlalchemy as sa

revision = "l6m7n8o0p1q2"
down_revision = "k6l7m8n9o0p1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "number_orders" in set(insp.get_table_names()):
        cols = {c["name"] for c in insp.get_columns("number_orders")}
        if "idempotency_key" not in cols:
            with op.batch_alter_table("number_orders") as batch:
                batch.add_column(
                    sa.Column("idempotency_key", sa.String(64), nullable=True)
                )
                batch.create_unique_constraint(
                    "uq_number_orders_idempotency", ["idempotency_key"]
                )
    # PostgreSQL يخزن SAEnum كنوع أصلي — أضف القيمة الجديدة صراحة.
    if bind.dialect.name == "postgresql":
        try:
            op.execute("ALTER TYPE orderstatus ADD VALUE IF NOT EXISTS 'unknown'")
        except Exception:
            pass


def downgrade() -> None:
    try:
        with op.batch_alter_table("number_orders") as batch:
            batch.drop_constraint("uq_number_orders_idempotency", type_="unique")
            batch.drop_column("idempotency_key")
    except Exception:
        pass
