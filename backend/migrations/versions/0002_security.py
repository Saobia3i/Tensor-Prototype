"""Row-level security, least-privilege grants, append-only triggers, full-text search trigger.

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TENANT_TABLES = [
    "memberships", "engagements", "engagement_controls", "evidence_requests", "evidence_items",
    "evidence_chunks", "evidence_control_links", "ai_runs", "ai_artifacts", "review_decisions",
    "remediation_tasks", "signoffs", "questionnaires", "questionnaire_items", "snapshots", "policies", "reports",
    "download_tokens", "connectors", "secret_records", "time_entries", "deletion_requests",
]
APPEND_ONLY = ["audit_events", "signoffs", "review_decisions"]
SCOPE = "(string_to_array(nullif(current_setting('app.tenant_ids', true), ''), ','))::uuid[]"
USER = "nullif(current_setting('app.user_id', true), '')::uuid"
APP_ROLE = "tensor_app"


def upgrade() -> None:
    op.execute(f"""
    DO $$ BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
        CREATE ROLE {APP_ROLE} LOGIN;
      END IF;
    END $$;
    """)
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}")
    # Reference data is read-only for the app role (changed via migrations/seed by the owner).
    for t in ["frameworks", "framework_requirements", "controls", "control_mappings"]:
        op.execute(f"REVOKE INSERT, UPDATE, DELETE ON {t} FROM {APP_ROLE}")
    op.execute(f"REVOKE ALL ON alembic_version FROM {APP_ROLE}")

    # Append-only tables: revoke and enforce with a trigger (defends even against grant mistakes).
    op.execute("""
    CREATE OR REPLACE FUNCTION tensor_block_mutation() RETURNS trigger AS $$
    BEGIN
      IF current_setting('app.allow_purge', true) = 'on' AND TG_OP = 'DELETE' THEN
        RETURN OLD;  -- only the owner-run, approved deletion workflow sets this
      END IF;
      RAISE EXCEPTION 'table % is append-only', TG_TABLE_NAME;
    END $$ LANGUAGE plpgsql;
    """)
    for t in APPEND_ONLY:
        op.execute(f"REVOKE UPDATE, DELETE ON {t} FROM {APP_ROLE}")
        op.execute(f"""CREATE TRIGGER {t}_append_only BEFORE UPDATE OR DELETE ON {t}
                       FOR EACH ROW EXECUTE FUNCTION tensor_block_mutation()""")

    # Row-level security.
    for t in TENANT_TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        extra = f" OR user_id = {USER}" if t == "memberships" else ""
        op.execute(f"""CREATE POLICY {t}_tenant_isolation ON {t}
                       USING (tenant_id = ANY({SCOPE}){extra})
                       WITH CHECK (tenant_id = ANY({SCOPE}))""")
    op.execute("ALTER TABLE tenants ENABLE ROW LEVEL SECURITY")
    op.execute(f"""CREATE POLICY tenants_isolation ON tenants USING (id = ANY({SCOPE})) WITH CHECK (id = ANY({SCOPE}))""")
    op.execute("ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY")
    op.execute(f"""CREATE POLICY audit_read ON audit_events FOR SELECT
                   USING (tenant_id = ANY({SCOPE}) OR (tenant_id IS NULL AND actor_id = {USER}))""")
    op.execute("CREATE POLICY audit_insert ON audit_events FOR INSERT WITH CHECK (true)")

    # Full-text search vector maintained by trigger.
    op.execute("""
    CREATE OR REPLACE FUNCTION tensor_chunk_tsv() RETURNS trigger AS $$
    BEGIN NEW.tsv := to_tsvector('english', coalesce(NEW.text, '')); RETURN NEW; END $$ LANGUAGE plpgsql;
    """)
    op.execute("""CREATE TRIGGER evidence_chunks_tsv BEFORE INSERT OR UPDATE OF text ON evidence_chunks
                  FOR EACH ROW EXECUTE FUNCTION tensor_chunk_tsv()""")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS evidence_chunks_tsv ON evidence_chunks")
    op.execute("DROP FUNCTION IF EXISTS tensor_chunk_tsv")
    for t in APPEND_ONLY:
        op.execute(f"DROP TRIGGER IF EXISTS {t}_append_only ON {t}")
    op.execute("DROP FUNCTION IF EXISTS tensor_block_mutation")
    for t in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {t}_tenant_isolation ON {t}")
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenants_isolation ON tenants")
    op.execute("ALTER TABLE tenants DISABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS audit_read ON audit_events")
    op.execute("DROP POLICY IF EXISTS audit_insert ON audit_events")
    op.execute("ALTER TABLE audit_events DISABLE ROW LEVEL SECURITY")
