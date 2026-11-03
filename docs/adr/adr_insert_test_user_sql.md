# ADR: Keep `tools/insert_test_user.sql` with its known credential

- **Status:** Accepted
- **Date:** 2026-10
- **Scope:** developer tooling (`tools/insert_test_user.sql`)
- **Disposition of:** auditSBA.md finding 17 (wontfix — accepted risk)

## Context

auditSBA.md finding 17 flagged that `tools/insert_test_user.sql` commits a
werkzeug scrypt hash for the password published in its own header
(`testpass123`) and inserts the account with `email_verified = 1`, which
bypasses the email-confirmation gate in `auth.py`. Run against a production
`users.db`, it would create a ready-to-login account. The audit suggested
requiring the password at run time, dropping `email_verified = 1`, and
guarding against the configured `USERS_DB_PATH`.

## Decision

Keep the file as-is. The maintainer accepts the residual risk (2026-10-04)
because:

- It is an internal testing tool for local/scratch databases only, used by
  people who also have full database access anyway. Anyone able to run it
  against `users.db` already holds the production credentials that matter.
- The risk is operational (a human pointing it at the wrong DB), not a code
  path: nothing in the app, docs, or tests references or invokes the file.
- The credential is publicly known by construction, so the account can never
  be a real user's account and must never be treated as secure.

## Alternatives considered

- **Replace with a Python CLI tool** (runtime hash via
  `generate_password_hash`, `email_verified=0` default, target-DB guard):
  rejected — YAGNI for an internal convenience script; it adds a maintained
  surface to solve a discipline problem.
- **Harden the SQL in place** (`.parameter` binding, placeholder hash):
  rejected — sqlite3 cannot scrypt-hash, so it only relocates the same
  discipline requirement while making the tool clumsier.
- **Delete the file**: rejected — it is used for local testing; deletion can
  be revisited if it ever becomes unnecessary or enters documented setup.

## Consequences

- The `testuser@example.com` / `testpass123` account is a known, low-value
  fixture: treat any environment containing it as non-production.
- If this file is ever referenced from documentation, deployment scripts, or
  CI, that is a trigger to revisit this decision (the tool would then be part
  of a real deployment path and needs the hardening the audit described).

## Related

| Path | Role |
|------|------|
| `tools/insert_test_user.sql` | the tool itself |
| `poi_broker/auth.py` | email-verification gate the fixture bypasses |
| `tools/usersdb_schema.sql` | `user` table schema it inserts into |
