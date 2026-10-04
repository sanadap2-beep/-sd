"""admin source tracking + review columns are separate; this adds users.admin_source

Revision ID: o9p0q1r2s3t4
Revises: n8o0p1q2r3s4
Create Date: 2026-10-04

- users.admin_source (env/panel) لسحب صلاحية من أُزيل من ADMIN_IDS
  دون المساس بمدراء اللوحة.
"""

from alembic import op
import sqlalchemy as sa

revision = "o9p0q1r2s3t4"
down_revision = "n8o0p1q2r3s4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "users" in set(insp.get_table_names()):
        cols = {c["name"] for c in insp.get_columns("users")}
        if "admin_source" not in cols:
            with op.batch_alter_table("users") as batch:
                batch.add_column(sa.Column("admin_source", sa.String(8), nullable=True))


def downgrade() -> None:
    try:
        with op.batch_alter_table("users") as batch:
            batch.drop_column("admin_source")
    except Exception:
        pass
