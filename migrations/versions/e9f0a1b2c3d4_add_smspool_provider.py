"""add smspool provider codes

Revision ID: e9f0a1b2c3d4
Revises: a3b4c5d6e7f8
Create Date: 2026-09-20 00:00:00.000000

مزود SMSPool عالي الجودة (non-VoIP، هولندا) لحل مشكلة رفض واتساب
لأرقام HeroSMS/5sim الرخيصة. يضيف عمود smspool_code لجدولي
countries و number_services.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e9f0a1b2c3d4"
down_revision: Union[str, None] = "a3b4c5d6e7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table, length in (("countries", 32), ("number_services", 32)):
        try:
            op.add_column(table, sa.Column("smspool_code", sa.String(length=length), nullable=True))
        except Exception:
            # العمود موجود مسبقاً (create_all في بيئات جديدة)
            pass


def downgrade() -> None:
    for table in ("countries", "number_services"):
        try:
            op.drop_column(table, "smspool_code")
        except Exception:
            pass
