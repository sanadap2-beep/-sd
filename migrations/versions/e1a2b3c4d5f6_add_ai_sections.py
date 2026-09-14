"""add AI sections (main AI area: programming/chat with per-message billing)

Revision ID: e1a2b3c4d5f6
Revises: e8f9a0b1c2d3
Create Date: 2026-09-13

يضيف القسم الرئيسي للذكاء الاصطناعي:
- ai_sections: الأقسام (برمجة/دردشة/مستقبلية) بموديل NanoGPT وتكلفة الرسالة.
- ai_sessions: جلسات المستخدمين لكل قسم.
- ai_messages: رسائل الجلسات (user/assistant) مع التكلفة.
- قيمة جديدة ai_usage في transactions (قاعدة SQLite لا تحتاج تعديل الجدول،
  لكن PostgreSQL تضيف العمود/القيمة عبر enum الافتراضي في create_all).
"""

from alembic import op
import sqlalchemy as sa

revision = "e1a2b3c4d5f6"
down_revision = "e8f9a0b1c2d3"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(18, 4)


def upgrade() -> None:
    # القيمة الجديدة ai_usage في transactions: SQLite يخزن نصاً فلا يلزم
    # شيء، أما PostgreSQL فيحتاج ALTER TYPE لإضافة قيمة للـ enum.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE transactiontype ADD VALUE IF NOT EXISTS 'ai_usage'")

    op.create_table(
        "ai_sections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("name_ar", sa.String(length=128), nullable=False),
        sa.Column("name_en", sa.String(length=128), nullable=True),
        sa.Column("description_ar", sa.Text(), nullable=True),
        sa.Column("description_en", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="chat"),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("cost_per_message_usd", MONEY, nullable=False, server_default="0.0100"),
        sa.Column("profit_multiplier", sa.Float(), nullable=False, server_default="3.0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_sections_key", "ai_sections", ["key"], unique=True)

    op.create_table(
        "ai_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "section_id",
            sa.Integer(),
            sa.ForeignKey("ai_sections.id"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=128), nullable=True),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_sessions_user_id", "ai_sessions", ["user_id"])
    op.create_index("ix_ai_sessions_section_id", "ai_sessions", ["section_id"])
    op.create_index("ix_ai_sessions_updated_at", "ai_sessions", ["updated_at"])

    op.create_table(
        "ai_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "session_id",
            sa.Integer(),
            sa.ForeignKey("ai_sessions.id"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("cost_usd", MONEY, nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_messages_session_id", "ai_messages", ["session_id"])


def downgrade() -> None:
    op.drop_table("ai_messages")
    op.drop_index("ix_ai_sessions_updated_at", table_name="ai_sessions")
    op.drop_index("ix_ai_sessions_section_id", table_name="ai_sessions")
    op.drop_index("ix_ai_sessions_user_id", table_name="ai_sessions")
    op.drop_table("ai_sessions")
    op.drop_index("ix_ai_sections_key", table_name="ai_sections")
    op.drop_table("ai_sections")
