"""
One-off schema migration for README.md Phase 3/4: adds `bot_id` to
mcp_connections, gmail_connections, calendar_connections, and
re-scopes their unique constraints from (user_id[, server_id]) to
include bot_id.

This project has no Alembic set up (main.py just calls
Base.metadata.create_all() at startup, which only creates missing
tables -- it never alters existing ones). This script is the manual
equivalent for this one schema change. Safe to run multiple times
(every step checks "does this already exist" first).

Data decision made here (see README.md §"Migration notes" /
"Open questions"): existing rows are left with bot_id = NULL rather
than migrated into an auto-created "default bot" per user. NULL is
exactly the "no bot" / user-level connection this app already had
before Phase 3/4 (see models.py's McpConnection/GmailConnection/
CalendarConnection docstrings) -- so every existing connection keeps
working exactly as it did, for any session that doesn't select a bot.
No data is lost or needs transforming; only the schema (new nullable
column + new constraint) changes.

Run once, after pulling this change and before starting the app:
    python scripts/migrate_phase3_4_bot_scoping.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text

from config.database import engine
from config.logging_config import get_logger

log = get_logger(__name__)


# (table, old_constraint_name, new_constraint_name, unique_columns)
_TABLES = [
    (
        "mcp_connections",
        "uq_mcp_connection_user_server",
        "uq_mcp_connection_user_server_bot",
        ["user_id", "server_id", "bot_id"],
    ),
    (
        "gmail_connections",
        None,  # was a column-level UNIQUE on user_id, not a named table constraint
        "uq_gmail_connection_user_bot",
        ["user_id", "bot_id"],
    ),
    (
        "calendar_connections",
        None,
        "uq_calendar_connection_user_bot",
        ["user_id", "bot_id"],
    ),
]


def _column_exists(conn, table: str, column: str) -> bool:
    row = conn.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :table AND column_name = :column"
        ),
        {"table": table, "column": column},
    ).first()
    return row is not None


def _constraint_exists(conn, table: str, constraint: str) -> bool:
    row = conn.execute(
        text(
            "SELECT 1 FROM information_schema.table_constraints "
            "WHERE table_name = :table AND constraint_name = :constraint"
        ),
        {"table": table, "constraint": constraint},
    ).first()
    return row is not None


def _find_unique_constraint_on_columns(conn, table: str, columns: list[str]) -> str | None:
    """Finds any existing unique/primary-key-style constraint on
    exactly this column set (used to locate gmail/calendar_connections'
    old column-level UNIQUE on user_id, whose auto-generated name we
    don't know for certain)."""
    rows = conn.execute(
        text(
            "SELECT tc.constraint_name, kcu.column_name "
            "FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu "
            "  ON tc.constraint_name = kcu.constraint_name "
            "WHERE tc.table_name = :table AND tc.constraint_type = 'UNIQUE'"
        ),
        {"table": table},
    ).fetchall()
    by_constraint: dict[str, list[str]] = {}
    for name, col in rows:
        by_constraint.setdefault(name, []).append(col)
    for name, cols in by_constraint.items():
        if sorted(cols) == sorted(columns):
            return name
    return None


def migrate():
    with engine.begin() as conn:
        for table, old_constraint, new_constraint, unique_cols in _TABLES:
            # 1. Add bot_id column if missing.
            if not _column_exists(conn, table, "bot_id"):
                log.info("Adding %s.bot_id", table)
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN bot_id VARCHAR"))
                conn.execute(
                    text(
                        f"ALTER TABLE {table} "
                        f"ADD CONSTRAINT fk_{table}_bot_id FOREIGN KEY (bot_id) "
                        f"REFERENCES bots(id)"
                    )
                )
            else:
                log.info("%s.bot_id already exists, skipping", table)

            # 2. Drop the old, narrower unique constraint (by known name,
            #    or by column-set lookup for the pre-Phase-3/4 column-level
            #    UNIQUE(user_id) on gmail/calendar_connections).
            constraint_to_drop = old_constraint
            if constraint_to_drop is None:
                constraint_to_drop = _find_unique_constraint_on_columns(conn, table, ["user_id"])
            if constraint_to_drop and _constraint_exists(conn, table, constraint_to_drop):
                log.info("Dropping old constraint %s on %s", constraint_to_drop, table)
                conn.execute(text(f"ALTER TABLE {table} DROP CONSTRAINT {constraint_to_drop}"))
            else:
                log.info("No old unique constraint found on %s to drop, skipping", table)

            # 3. Add the new, bot-scoped unique constraint.
            if not _constraint_exists(conn, table, new_constraint):
                cols_sql = ", ".join(unique_cols)
                log.info("Adding %s on %s(%s)", new_constraint, table, cols_sql)
                conn.execute(
                    text(
                        f"ALTER TABLE {table} "
                        f"ADD CONSTRAINT {new_constraint} UNIQUE ({cols_sql})"
                    )
                )
            else:
                log.info("%s already exists on %s, skipping", new_constraint, table)

    log.info("Phase 3/4 bot-scoping migration complete.")


if __name__ == "__main__":
    migrate()
