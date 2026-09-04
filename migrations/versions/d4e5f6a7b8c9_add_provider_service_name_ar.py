"""add provider service arabic name

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-09-04 12:00:00.000000

provider_services.name_ar: الاسم العربي المُعرَّب للخدمة، يُحفظ وقت السحب
من المزود (مثل Hyper Store) حتى تظهر كل الخدمات بالعربية في اللوحات
والبحث وعند نشر المنتج. الاسم الأصلي الإنجليزي يبقى في ``name``.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "provider_services",
        sa.Column("name_ar", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("provider_services", "name_ar")
