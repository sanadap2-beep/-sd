"""add premium custom emoji ids to catalog (categories/sub/products)

Revision ID: k6l7m8n9o0p1
Revises: j5k6l7m8n9o0
Create Date: 2026-10-04

- عمود custom_emoji_id (nullable) على categories و sub_categories و products.
- يخزن الرقم التعريفي لإيموجي تيليجرام المميز الذي يرسله الأدمن كرسالة.
"""

from alembic import op
import sqlalchemy as sa

revision = "k6l7m8n9o0p1"
down_revision = "j5k6l7m8n9"
branch_labels = None
depends_on = None


def _column_names(table: str) -> set:
    try:
        return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}
    except Exception:
        return set()


def upgrade() -> None:
    for table in ("categories", "sub_categories", "products"):
        try:
            if table in set(sa.inspect(op.get_bind()).get_table_names()):
                if "custom_emoji_id" not in _column_names(table):
                    with op.batch_alter_table(table) as batch:
                        batch.add_column(
                            sa.Column("custom_emoji_id", sa.String(64), nullable=True)
                        )
        except Exception:
            pass


def downgrade() -> None:
    for table in ("categories", "sub_categories", "products"):
        try:
            with op.batch_alter_table(table) as batch:
                batch.drop_column("custom_emoji_id")
        except Exception:
            pass
