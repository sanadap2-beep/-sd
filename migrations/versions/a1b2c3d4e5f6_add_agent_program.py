"""add agent program (codes + profiles)

Revision ID: a1b2c3d4e5f6
Revises: e7f3a9c2b1d4
Create Date: 2026-09-03 12:00:00.000000

برنامج الوكلاء:
- agent_codes: أكواد وكالة يصدرها الأدمن (تُستعمل مرة واحدة).
- agent_profiles: الوكيل الفعّال/المسحوب مع نسبة الخصم الحالية وسبب
  السحب وأسطح الفحص الأسبوعي.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "e7f3a9c2b1d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_codes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("percent", sa.Numeric(18, 4), server_default="10", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="unused", nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("redeemed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_agent_codes_code"), "agent_codes", ["code"], unique=True)
    op.create_index(op.f("ix_agent_codes_created_by"), "agent_codes", ["created_by"], unique=False)
    op.create_index(op.f("ix_agent_codes_status"), "agent_codes", ["status"], unique=False)
    op.create_index(op.f("ix_agent_codes_user_id"), "agent_codes", ["user_id"], unique=False)

    op.create_table(
        "agent_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("code_id", sa.Integer(), nullable=True),
        sa.Column("granted_by", sa.Integer(), nullable=True),
        sa.Column("percent", sa.Numeric(18, 4), server_default="10", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("activated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("revoke_reason", sa.String(length=255), nullable=True),
        sa.Column("last_checked_week", sa.String(length=8), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["code_id"], ["agent_codes.id"]),
        sa.ForeignKeyConstraint(["granted_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_agent_profiles_user_id"), "agent_profiles", ["user_id"], unique=True)
    op.create_index(op.f("ix_agent_profiles_status"), "agent_profiles", ["status"], unique=False)
    op.create_index(op.f("ix_agent_profiles_granted_by"), "agent_profiles", ["granted_by"], unique=False)
    op.create_index(op.f("ix_agent_profiles_last_checked_week"), "agent_profiles", ["last_checked_week"], unique=False)


def downgrade() -> None:
    op.drop_table("agent_profiles")
    op.drop_table("agent_codes")
