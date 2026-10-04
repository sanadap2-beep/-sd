"""plisio review state: REVIEW status + actual paid amount columns

Revision ID: m7n8o0p1q2r3
Revises: l6m7n8o0p1q2
Create Date: 2026-10-04

- auto_invoices.actual_amount / actual_currency (المبلغ الفعلي المستلم).
- حالة REVIEW في enum الحالات (PostgreSQL: ALTER TYPE، SQLite: لا شيء).
"""

from alembic import op
import sqlalchemy as sa

revision = "m7n8o0p1q2r3"
down_revision = "l6m7n8o0p1q2"
branch_labels = None
depends_on = None


def _pg_add_enum_value(type_name: str, value: str) -> None:
    """ALTER TYPE ... ADD VALUE لا يعمل داخل معاملة — اتصال autocommit منفصل."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    from sqlalchemy import create_engine

    eng = create_engine(str(bind.engine.url), isolation_level="AUTOCOMMIT")
    try:
        with eng.connect() as conn:
            conn.execute(
                sa.text(f"ALTER TYPE {type_name} ADD VALUE IF NOT EXISTS '{value}'")
            )
    finally:
        eng.dispose()


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "auto_invoices" in set(insp.get_table_names()):
        cols = {c["name"] for c in insp.get_columns("auto_invoices")}
        if "actual_amount" not in cols:
            with op.batch_alter_table("auto_invoices") as batch:
                batch.add_column(sa.Column("actual_amount", sa.String(64), nullable=True))
        if "actual_currency" not in cols:
            with op.batch_alter_table("auto_invoices") as batch:
                batch.add_column(sa.Column("actual_currency", sa.String(16), nullable=True))
    if bind.dialect.name == "postgresql":
        for value in ("review",):
            try:
                _pg_add_enum_value("autoinvoicestatus", value)
            except Exception:
                pass


def downgrade() -> None:
    try:
        with op.batch_alter_table("auto_invoices") as batch:
            batch.drop_column("actual_amount")
            batch.drop_column("actual_currency")
    except Exception:
        pass
