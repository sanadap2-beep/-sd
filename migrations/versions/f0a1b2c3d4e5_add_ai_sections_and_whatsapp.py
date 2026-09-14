"""add AI sections (dynamic) and WhatsApp bridge tables

Revision ID: f0a1b2c3d4e5
Revises: e8f9a0b1c2d3
Create Date: 2026-09-14

يضيف:
- ai_sections: أقسام ذكاء اصطناعي يضيفها الأدمن (برمجة/دردشة/أي نوع مستقبلي)
  مع موديل NanoGPT وشرح يدوي وتسعير (تكلفة المزود × مضاعف ربح).
- ai_sessions / ai_messages: جلسات ورسائل المستخدمين محفوظة للرجوع إليها.
- whatsapp_links: ربط جلسات واتساب عبر الجسر (البوت الثاني) بكود اقتران.
- whatsapp_subscriptions: اشتراك يومي مدفوع لقسم واتساب.
"""

from alembic import op
import sqlalchemy as sa

revision = "f0a1b2c3d4e5"
down_revision = "e8f9a0b1c2d3"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(18, 4)


def upgrade() -> None:
    op.create_table(
        "ai_sections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=64), nullable=False),
        sa.Column("emoji", sa.String(length=8), nullable=False, server_default="🤖"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "mode",
            sa.Enum("CODE", "CHAT", "CUSTOM", name="aisectionmode"),
            nullable=False,
            server_default="CHAT",
        ),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="nanogpt"),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=True),
        sa.Column(
            "pricing_mode",
            sa.Enum("USAGE", "FIXED", name="aipricingmode"),
            nullable=False,
            server_default="USAGE",
        ),
        sa.Column("est_cost_per_message", MONEY, nullable=False, server_default="0.003"),
        sa.Column("fixed_price", MONEY, nullable=False, server_default="0.01"),
        sa.Column("profit_multiplier", MONEY, nullable=False, server_default="3"),
        sa.Column("max_context_messages", sa.Integer(), nullable=False, server_default="12"),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False, server_default="4000"),
        sa.Column("temperature", MONEY, nullable=False, server_default="0.7"),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "ai_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("section_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=80), nullable=False, server_default="جلسة جديدة"),
        sa.Column("messages_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("provider_cost", MONEY, nullable=False, server_default="0"),
        sa.Column("charged_total", MONEY, nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["section_id"], ["ai_sections.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_sessions_user_id", "ai_sessions", ["user_id"])
    op.create_index("ix_ai_sessions_section_id", "ai_sessions", ["section_id"])

    op.create_table(
        "ai_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column(
            "role",
            sa.Enum("USER", "ASSISTANT", "SYSTEM", name="aimessagerole"),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("provider_cost", MONEY, nullable=False, server_default="0"),
        sa.Column("charged_amount", MONEY, nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["ai_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_messages_session_id", "ai_messages", ["session_id"])

    op.create_table(
        "whatsapp_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("phone", sa.String(length=20), nullable=False),
        sa.Column("bridge_session_id", sa.String(length=128), nullable=True),
        sa.Column("pairing_code", sa.String(length=32), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING", "LINKED", "EXPIRED", "DISCONNECTED", name="walinkstatus"
            ),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("last_menu_json", sa.Text(), nullable=True),
        sa.Column("linked_at", sa.DateTime(), nullable=True),
        sa.Column("last_check_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_whatsapp_links_user_id", "whatsapp_links", ["user_id"])
    op.create_index("ix_whatsapp_links_status", "whatsapp_links", ["status"])

    op.create_table(
        "whatsapp_subscriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("paid_until", sa.DateTime(), nullable=False),
        sa.Column("total_paid", MONEY, nullable=False, server_default="0"),
        sa.Column("last_charged_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_whatsapp_subscriptions_user_id", "whatsapp_subscriptions", ["user_id"], unique=True
    )


def downgrade() -> None:
    op.drop_table("whatsapp_subscriptions")
    op.drop_table("whatsapp_links")
    op.drop_table("ai_messages")
    op.drop_table("ai_sessions")
    op.drop_table("ai_sections")
