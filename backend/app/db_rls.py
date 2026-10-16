"""Helpers used by migrations to put a table under tenant isolation.

Policy: a row is visible/writable only when its organization_id equals the
transaction setting app.org_id, or when app.bypass_rls = 'on' (platform work).
FORCE makes the policy apply to the table owner too. The app connects as a
non-superuser role (dm_app), so it can never skip the policy.
"""

from alembic import op

POLICY_EXPR = (
    "current_setting('app.bypass_rls', true) = 'on' "
    "OR organization_id = nullif(current_setting('app.org_id', true), '')::uuid"
)


def enable_tenant_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} USING ({POLICY_EXPR}) WITH CHECK ({POLICY_EXPR})"
    )
