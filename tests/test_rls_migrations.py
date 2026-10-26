"""P4-D-136: every table a migration creates (after 007) must enable RLS in the same file."""
import re
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"
CREATE = re.compile(r"create\s+table\s+(?:if\s+not\s+exists\s+)?(?:public\.)?(\w+)", re.I)


def test_rls_lockdown_migration_is_dynamic_and_covers_public():
    sql = (MIGRATIONS / "007_rls_lockdown.sql").read_text().lower()
    assert "enable row level security" in sql and "nspname = 'public'" in sql and "relrowsecurity" in sql


def test_new_tables_enable_rls_in_their_own_migration():
    for path in sorted(MIGRATIONS.glob("*.sql")):
        if path.name <= "007_rls_lockdown.sql":
            continue
        sql = path.read_text()
        for table in CREATE.findall(sql):
            pat = rf"alter\s+table\s+(?:public\.)?{table}\s+enable\s+row\s+level\s+security"
            assert re.search(pat, sql, re.I), f"{path.name}: {table} has no ENABLE ROW LEVEL SECURITY"


def test_check_rls_script_exits_nonzero_on_unprotected_tables():
    src = (MIGRATIONS.parent / "scripts" / "check_rls.py").read_text()
    assert "relrowsecurity" in src and "return 1 if bad else 0" in src
