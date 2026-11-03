# Deployment: session invalidation (`user.password_changed_at`)

## Current release: signed reset links and atomic credential updates

This release requires **no new `apply_*.sql` script**. It reuses the existing
`password_changed_at`, `reset_token`, and `reset_token_expires` columns. No
production data rewrite is needed; do not clear existing reset hashes or change
NULL revisions as part of deployment. Previously emailed links remain valid
until their original expiry or a successful password change/reset.

1. Install the updated requirements (`itsdangerous==2.*` is now an explicit
   dependency; it is also a Flask dependency).
2. Keep the existing strong `SECRET_KEY` unchanged and identical across all web
   workers. A changed key invalidates signed links and sessions.
3. Restart the web service so **all** Gunicorn workers use the new implementation.
   Avoid a mixed old/new rolling deployment: old workers cannot validate new
   `v1.` links and still replace legacy reset hashes when sending another email.
4. Verify that two reset requests leave the earlier email link usable; using
   either link must invalidate both links and existing sessions. Repeat SMTP
   failure checks with a test mail backend, not real user accounts.

New reset requests do not write token fields. Signed links expire after one hour
and are bound to the current credential revision. Both password-write routes use
an atomic conditional UPDATE and monotonically advance that revision, including
same-second changes. Existing sessions retain the timestamp compatibility check;
new sessions also record the exact credential revision verified at login.

**Rollback:** restoring the previous code immediately makes newly issued signed
links unusable. To preserve emailed links, retain the new validator/write logic
until the last signed link has expired (one hour after the final new-format
issuance). There is no schema rollback or DB modification needed for this
release. The column-removal instructions below concern the historical migration,
not this release.

## Historical column deployment

> **Historical record — this migration has been applied.** Both one-shot
> scripts referenced below were applied to the production `users.db` and
> removed from the repository in 2026-10
> (`docs/adr/adr_db_schema_migration_strategy.md`). Fresh installations get
> both columns from `tools/usersdb_schema.sql`. Do not try to run the
> scripts; keep this page as the deployment record.

Password resets and password changes now end **every** existing session and
remembered login for that account. This is enforced by a new epoch-seconds
column, `password_changed_at`, on the `user` table of the **users database**
(`users.db` only — the alerts database is untouched), plus
`email_verification_token_expires` which replaced a legacy boot-time ALTER
helper.

There was no auto-migration: **the application never alters the database.**
The two schema upgrades were applied manually with one-shot SQL scripts
(`tools/apply_password_changed_at.sql`, `tools/apply_email_verification_expiry.sql`),
run before restarting the web service — once the app boots against the new
ORM, any authenticated request hits a SELECT referencing
`password_changed_at`, which errors out with `no such column` until the
column exists.

## What changed for users

- Nobody was logged out by the deployment itself. Accounts untouched by their
  owner (`password_changed_at` still NULL) behave exactly as before,
  remember-me included.
- The first time each user changes their password (or uses a reset link),
  every session and remember-me login for the account ends — including the
  browser performing the change. They sign back in with the new password;
  its cookie was cleared during logout.
- Remember-me cookies left behind on *other* devices remain in those browsers
  but are inert: the server rejects them because they carry no valid login
  timestamp against the newer `password_changed_at`. Browsers drop them at
  cookie expiry or the next sign-in on that device.

## Rollback (if ever needed)

The column is additive and nullable, and old code ignores it. To fully revert,
remove it with `ALTER TABLE user DROP COLUMN password_changed_at;`
(SQLite ≥ 3.35) and restart on the previous release.
