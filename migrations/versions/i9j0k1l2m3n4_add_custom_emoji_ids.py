"""add Telegram Premium custom emoji ids to dynamic entities

Revision ID: i9j0k1l2m3n4
Revises: h3i4j5k6l7m8
Create Date: 2026-10-03

يضيف عمود ``custom_emoji_id`` للأقسام الرئيسية والفرعية وخدمات الأرقام
والسيرفرات، حتى يستطيع الأدمن وضع **إيموجي تيليجرام المميز** على أزرار هذه
العناصر بدل الإيموجي النصي العادي.

العمود اختياري (nullable) ولا يغيّر أي سلوك قائم: إن كان فارغاً يبقى العمل
على ``emoji`` النصي كما كان تماماً.
"""

from alembic import op
import sqlalchemy as sa


revision = "i9j0k1l2m3n4"
down_revision = "h3i4j5k6l7m8"
branch_labels = None
depends_on = None


TARGETS = (
    ("categories", "إيموجي مميز لزر القسم الرئيسي"),
    ("sub_categories", "إيموجي مميز لزر القسم الفرعي"),
    ("number_services", "إيموجي مميز لزر خدمة الأرقام"),
    ("number_servers", "إيموجي مميز لزر سيرفر الأرقام"),
    ("store_servers", "إيموجي مميز لزر سيرفر المتجر"),
)


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return False
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    for table, _comment in TARGETS:
        if not _has_column(table, "custom_emoji_id"):
            op.add_column(
                table,
                sa.Column("custom_emoji_id", sa.String(length=64), nullable=True),
            )


def downgrade() -> None:
    for table, _comment in TARGETS:
        if _has_column(table, "custom_emoji_id"):
            op.drop_column(table, "custom_emoji_id")
