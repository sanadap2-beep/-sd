"""add grizzly provider codes

Revision ID: f0a1b2c3d4e5
Revises: e9f0a1b2c3d4
Create Date: 2026-09-20 00:00:00.000000

مزود GrizzlySMS (متوافق مع sms-activate، شحن كريبتو، واتساب متاح openly
وبدون شروط أهلية). يضيف عمود grizzly_code لجدولي countries
و number_services.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f0a1b2c3d4e5"
down_revision: Union[str, None] = "e9f0a1b2c3d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ("countries", "number_services"):
        try:
            op.add_column(table, sa.Column("grizzly_code", sa.String(length=32), nullable=True))
        except Exception:
            # العمود موجود مسبقاً (create_all في بيئات جديدة)
            pass


def downgrade() -> None:
    for table in ("countries", "number_services"):
        try:
            op.drop_column(table, "grizzly_code")
        except Exception:
            pass
