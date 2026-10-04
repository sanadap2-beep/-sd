"""uniqueness guards: spin daily + deposit proof reference

Revision ID: n8o0p1q2r3s4
Revises: m7n8o0p1q2r3
Create Date: 2026-10-04

- spin_history: قيد فريد (user_id, day_key) يمنع لفتين متزامنتين.
- deposit_requests: قيد فريد على proof_tx_number (NULLs مستثناة ضمنياً)
  يمنع طلبين بنفس الإثبات تحت السباق.
"""

from alembic import op
import sqlalchemy as sa

revision = "n8o0p1q2r3s4"
down_revision = "m7n8o0p1q2r3"
branch_labels = None
depends_on = None


def _unique_names(table: str) -> set:
    try:
        return {c.get("name") for c in sa.inspect(op.get_bind()).get_unique_constraints(table)}
    except Exception:
        return set()


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())

    if "spin_history" in tables and "uq_spin_user_day" not in _unique_names("spin_history"):
        # نظف التكرارات القديمة (احتفظ بالأقدم لكل مستخدم/يوم)
        try:
            bind.execute(
                sa.text(
                    """
                    DELETE FROM spin_history
                    WHERE id NOT IN (
                      SELECT MIN(id) FROM spin_history GROUP BY user_id, day_key
                    )
                    """
                )
            )
        except Exception:
            pass
        try:
            with op.batch_alter_table("spin_history") as batch:
                batch.create_unique_constraint("uq_spin_user_day", ["user_id", "day_key"])
        except Exception:
            pass

    if "deposit_requests" in tables and "uq_deposit_proof_tx" not in _unique_names("deposit_requests"):
        try:
            bind.execute(
                sa.text(
                    """
                    DELETE FROM deposit_requests a
                    WHERE a.proof_tx_number IS NOT NULL AND a.id NOT IN (
                      SELECT MIN(id) FROM deposit_requests
                      WHERE proof_tx_number IS NOT NULL
                      GROUP BY proof_tx_number
                    )
                    """
                )
            )
        except Exception:
            # SQLite لا يدعم DELETE مع alias — بديل محمول
            try:
                bind.execute(
                    sa.text(
                        """
                        DELETE FROM deposit_requests
                        WHERE proof_tx_number IS NOT NULL AND id NOT IN (
                          SELECT MIN(id) FROM deposit_requests
                          WHERE proof_tx_number IS NOT NULL
                          GROUP BY proof_tx_number
                        )
                        """
                    )
                )
            except Exception:
                pass
        try:
            with op.batch_alter_table("deposit_requests") as batch:
                batch.create_unique_constraint("uq_deposit_proof_tx", ["proof_tx_number"])
        except Exception:
            pass


def downgrade() -> None:
    for table, name in (
        ("spin_history", "uq_spin_user_day"),
        ("deposit_requests", "uq_deposit_proof_tx"),
    ):
        try:
            with op.batch_alter_table(table) as batch:
                batch.drop_constraint(name, type_="unique")
        except Exception:
            pass
