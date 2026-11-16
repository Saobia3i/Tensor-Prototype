"""Type II testing, exceptions, milestones, control ownership/risk and self-assessment (with RLS).

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None
NEW = ["control_tests", "audit_exceptions", "milestones"]
SCOPE = "(string_to_array(nullif(current_setting('app.tenant_ids', true), ''), ','))::uuid[]"
U = postgresql.UUID(as_uuid=True)


def _ts():
    return [sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)]


def upgrade() -> None:
    op.add_column("engagement_controls", sa.Column("owner_name", sa.String(200), server_default="", nullable=False))
    op.add_column("engagement_controls", sa.Column("risk_rating", sa.String(10), server_default="medium", nullable=False))
    op.add_column("engagement_controls", sa.Column("self_assessment", sa.String(20)))
    op.add_column("engagement_controls", sa.Column("assessment_notes", sa.Text(), server_default="", nullable=False))
    op.create_table(
        "control_tests", sa.Column("id", U, primary_key=True),
        sa.Column("tenant_id", U, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("engagement_id", U, sa.ForeignKey("engagements.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("engagement_control_id", U, sa.ForeignKey("engagement_controls.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("test_date", sa.Date(), nullable=False), sa.Column("period_start", sa.Date()), sa.Column("period_end", sa.Date()),
        sa.Column("population", sa.Integer()), sa.Column("samples_tested", sa.Integer(), nullable=False),
        sa.Column("deviations", sa.Integer(), nullable=False), sa.Column("result", sa.String(30), nullable=False),
        sa.Column("procedure", sa.Text(), nullable=False), sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("tester_id", U, sa.ForeignKey("users.id")), sa.Column("evidence_id", U, sa.ForeignKey("evidence_items.id")),
        *_ts())
    op.create_table(
        "audit_exceptions", sa.Column("id", U, primary_key=True),
        sa.Column("tenant_id", U, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("engagement_id", U, sa.ForeignKey("engagements.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("engagement_control_id", U, sa.ForeignKey("engagement_controls.id", ondelete="CASCADE")),
        sa.Column("test_id", U, sa.ForeignKey("control_tests.id", ondelete="SET NULL")),
        sa.Column("code", sa.String(20), nullable=False), sa.Column("description", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False), sa.Column("status", sa.String(20), nullable=False),
        sa.Column("opened_on", sa.Date(), nullable=False), sa.Column("target_date", sa.Date()), sa.Column("closed_on", sa.Date()),
        sa.Column("owner_name", sa.String(200), nullable=False), sa.Column("audit_impact", sa.String(300), nullable=False),
        sa.Column("remediation_task_id", U, sa.ForeignKey("remediation_tasks.id", ondelete="SET NULL")),
        sa.Column("closed_by", U, sa.ForeignKey("users.id")), *_ts())
    op.create_table(
        "milestones", sa.Column("id", U, primary_key=True),
        sa.Column("tenant_id", U, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("engagement_id", U, sa.ForeignKey("engagements.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("title", sa.String(200), nullable=False), sa.Column("planned_date", sa.Date(), nullable=False),
        sa.Column("completed_on", sa.Date()), sa.Column("ordinal", sa.Integer(), nullable=False), *_ts())
    for t in NEW:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {t} TO tensor_app")
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {t}_tenant_isolation ON {t} USING (tenant_id = ANY({SCOPE})) WITH CHECK (tenant_id = ANY({SCOPE}))")


def downgrade() -> None:
    for t in reversed(NEW):
        op.drop_table(t)
    for c in ("assessment_notes", "self_assessment", "risk_rating", "owner_name"):
        op.drop_column("engagement_controls", c)
