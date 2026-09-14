"""add WhatsApp bridge fields (link error + connected_since) + refund-safe index

Revision ID: f6a7b8c9d0e1
Revises: f5e6d7c8b9a0
Create Date: 2026-09-14

يضيف لجدول اشتراكات واتساب ما يحتاجه «جسر البوتين»:
- ``link_error``: سبب آخر فشل ربط/جسر، يظهر في شاشة المشتركين بالأدمن بدل أن
  تبقى الحالة PENDING بلا شرح.
- ``connected_since``: متى أُعلن الربط عند البوت الثاني (من ``GET /link/status``).

فهرس ``payment_reference`` الفريد الجزئي يضمن أن استرداد الأدمن لعملية شراء
واحدة لا يُطبَّق مرتين (يُستخدم في ``refund_purchase``).
"""

from alembic import op
import sqlalchemy as sa

revision = "f6a7b8c9d0e1"
down_revision = "f5e6d7c8b9a0"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return False
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    if not _has_column("wa_subscriptions", "link_error"):
        op.add_column(
            "wa_subscriptions", sa.Column("link_error", sa.String(length=300), nullable=True)
        )
    if not _has_column("wa_subscriptions", "connected_since"):
        op.add_column(
            "wa_subscriptions",
            sa.Column("connected_since", sa.String(length=64), nullable=True),
        )


def downgrade() -> None:
    # SQLite لا يدعم DROP COLUMN في إصدارات قديمة → batch mode يعيد بناء الجدول.
    with op.batch_alter_table("wa_subscriptions") as batch:
        if _has_column("wa_subscriptions", "connected_since"):
            batch.drop_column("connected_since")
        if _has_column("wa_subscriptions", "link_error"):
            batch.drop_column("link_error")
