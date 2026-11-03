-- Branch-only users.db migration. The original upstream main already contains
-- apply_users_indexes.sql; its housekeeping/bookmark indexes are complete.
-- This new constraint is not in that upstream script as of 2026-10-14.
--
-- sqlite3 -bail "$USERS_DB_PATH" < tools/apply_active_export_index.sql
-- Safe to re-run. Duplicate active tasks abort index creation; inspect the
-- reported users and resolve their tasks before retrying. Never drop user data.

SELECT user_id, COUNT(*) AS duplicate_count
FROM export_task
WHERE status IN ('PENDING', 'RUNNING')
GROUP BY user_id
HAVING COUNT(*) > 1;

CREATE UNIQUE INDEX IF NOT EXISTS uix_export_task_one_active_per_user
    ON export_task (user_id)
    WHERE status IN ('PENDING', 'RUNNING');

ANALYZE;
