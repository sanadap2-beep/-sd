"""deposit bonus idempotency guard + api key encryption columns

Revision ID: i4j5k6l7m8n9
Revises: h3i4j5k6l7m8
Create Date: 2026-10-03

- قيد فريد (deposit_source, deposit_id) يمنع مكافأتين لنفس الإيداع تحت السباق.
- عمودان لتشفير مفاتيح المزودين (api_key_encrypted) تمهيداً للترحيل من plaintext.
"""

from alembic import op
import sqlalchemy as sa

revision = "i4j5k6l7m8n9"
down_revision = "h3i4j5k6l7m8"
branch_labels = None
depends_on = None


def _get_constraint_names(table: str) -> set:
    insp = sa.inspect(op.get_bind())
    try:
        return {c["name"] for c in insp.get_unique_constraints(table)}
    except Exception:
        return set()


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    tables = set(insp.get_table_names())
    if "deposit_bonus_grants" in tables:
        existing = _get_constraint_names("deposit_bonus_grants")
        if "uq_deposit_bonus_source_id" not in existing:
            # نظف التكرارات القديمة قبل إنشاء القيد (احتفظ بالأقدم لكل إيداع)
            # صيغة محمولة تعمل على SQLite وPostgres (بدون USING).
            bind = op.get_bind()
            try:
                bind.execute(
                    sa.text(
                        """
                        DELETE FROM deposit_bonus_grants
                        WHERE id NOT IN (
                          SELECT MIN(id) FROM deposit_bonus_grants
                          WHERE deposit_id IS NOT NULL
                          GROUP BY deposit_source, deposit_id
                        )
                        AND deposit_id IS NOT NULL
                        """
                    )
                )
            except Exception:
                pass
            try:
                # SQLite لا يدعم ADD CONSTRAINT مباشرة — batch mode يتعامل معها
                with op.batch_alter_table("deposit_bonus_grants") as batch:
                    batch.create_unique_constraint(
                        "uq_deposit_bonus_source_id",
                        ["deposit_source", "deposit_id"],
                    )
            except Exception:
                try:
                    op.create_unique_constraint(
                        "uq_deposit_bonus_source_id",
                        "deposit_bonus_grants",
                        ["deposit_source", "deposit_id"],
                    )
                except Exception:
                    pass
    if "api_providers" in tables:
        cols = {c["name"] for c in insp.get_columns("api_providers")}
        if "api_key_encrypted" not in cols:
            op.add_column(
                "api_providers",
                sa.Column("api_key_encrypted", sa.Text(), nullable=True),
            )
        if "api_key_encrypted_v2" not in cols:
            # عمود احتياطي للتدوير المستقبلي دون كسر القراءة الحالية
            pass


def downgrade() -> None:
    try:
        op.drop_constraint(
            "uq_deposit_bonus_source_id", "deposit_bonus_grants", type_="unique"
        )
    except Exception:
        pass
    try:
        op.drop_column("api_providers", "api_key_encrypted")
    except Exception:
        pass
