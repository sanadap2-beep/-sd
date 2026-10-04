"""merge parallel migration heads into a single head

Revision ID: p0q1r2s3t4u5
Revises: 7e1c2d9f8a3b, c5d6e7f8a9b0, o9p0q1r2s3t4, k1l2m3n4o5p6
Create Date: 2026-10-04

الفروع المتوازية نشأت من مزايا دُمجت دون توحيد السلسلة. هذا الترحيل
فارغ (لا تغيير مخطط) ويوحّد الرؤوس الأربعة في رأس واحد.
"""

revision = "p0q1r2s3t4u5"
down_revision = (
    "o9p0q1r2s3t4",
    "k1l2m3n4o5p6",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
