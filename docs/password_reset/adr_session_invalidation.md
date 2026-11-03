# ADR: Monotonic credential revision and session invalidation

- **Status:** Accepted
- **Date:** 2026-08; updated 2026-10-11
- **Feature:** Auth session lifecycle (`poi_broker/auth.py`, `user_loader`)

## Context

Flask-Login sessions are stateless signed cookies validated by the
`user_loader` callback; the app keeps no server-side session store. Before
this change, neither a password reset (`POST /reset-password/<token>`) nor a
password change (`POST /change-password`) touched login state, so a session
cookie stolen before the credential change kept full access after the victim
rotated their password (insufficient session expiration on credential
change; remember-me cookies likewise survived, since flask-login restores
them through the same `user_loader`).

## Decision

Reuse the nullable INTEGER `user.password_changed_at` as a monotonic,
epoch-based credential revision. Each password-reset or password-change route
performs one conditional SQL UPDATE against the expected user ID, password hash
and old revision, then sets the new revision to
`max(now_epoch_second, old_revision + 1)` and clears legacy reset fields. Only one
competing request can match. A failed update/commit does not consume a token or
advance the revision. Closely spaced changes can advance the revision slightly
ahead of wall time; its displayed date is approximate, not an exact audit record.

New login sessions carry `password_revision`, the exact revision of the password
that was checked, plus the existing `login_at` epoch marker. The loader rejects
revision mismatches. This includes a login whose password check overlaps another
request's credential change, even if login completes at a later timestamp.

For sessions issued before this release, the existing timestamp rule remains:
`login_at` must be an integer strictly greater than the non-NULL watermark. Login
stamps it with `max(now, verified_revision + 1)` to allow a legitimate login in
the same second as a write. The monotonic advance ensures the next password
write invalidates that marker even in the same second. Both write routes call
`logout_user()` on success, also clearing the acting browser's remember cookie.

A NULL revision means the account has never rotated its password. Existing
sessions/remember-cookie behavior remains compatible until its first rotation;
deployment itself logs nobody out.

New reset links are signed, contain the user ID, revision and random nonce, and
expire after one hour. Issuance writes no account state, so a subsequent request
or missing email does not invalidate an earlier link. GET only validates the
link; the CSRF-protected POST conditionally changes the password. The revision
advance revokes all links from the previous credential and makes a successful
reset single-use. Legacy random links remain supported through their existing
hashed-token/expiry fields, with additional token/expiry predicates in the write.
No schema migration is needed for this update. See `docs/sec.md` and
`docs/password_reset/deployment.md` for the rollout and rollback requirements.

## Alternatives considered

### Server-side session store (Flask-Session + SQLAlchemy/Redis) (rejected)

Full session enumeration and per-session revocation, but a new storage
dependency and a store read on every request. Same footprint logic as the
Huey ADR (`docs/async_export/adr_huey_sqlite.md`): no new networked service
for a small team.

### Flask-Login freshness / `SESSION_PROTECTION='strong'` (rejected)

Freshness marks (`_fresh`) say when the login happened, not whether
credentials changed since; "strong" protection keys on request fingerprints
(UA/IP), which is orthogonal. Neither mechanism binds sessions to credential
changes.

### Rotating the user's identity on password change (rejected)

Bumping `user.id` (or a token the remember cookie must match) would
invalidate sessions only via the same loader check we built anyway — while
`user.id` is referenced by half the users-DB foreign keys and embedded in the
remember cookie. The monotonic revision delivers revocation with no FK churn.

## Consequences

- Stolen cookies cannot outlive a password reset/change; there is nothing
  client-side to keep them valid.
- The acting browser is signed out on password change and redirected to
  login (flash explains why). Accepted UX cost of the uniform rule.
- For accounts that have rotated their password at least once, remembered
  logins stop resurrecting sessions once the browser-side session expires
  (default 24h): a bare remember token carries no `login_at` and proves
  nothing about credential freshness. Deliberate, documented tradeoff.
- Deploy order matters: `tools/apply_password_changed_at.sql` had to be
  applied **before** restarting into the new ORM, or authenticated pages 500
  on the missing column. Runbook: `docs/password_reset/deployment.md`.
  [2026-10: applied in production; script removed — see
  `docs/adr/adr_db_schema_migration_strategy.md`.]
- The application never writes DDL. This ADR also removed the legacy
  boot-time helper (`_ensure_email_verification_expiry_column`); all schema
  upgrades are manual one-shot SQL scripts.

## Invariants (for implementers and coding agents)

1. Every code path that writes `user.password` MUST conditionally advance
   `user.password_changed_at` in the same database UPDATE (currently
   `reset_password_post`, `change_password`, through `_write_password`).
2. Every successful `POST /login` MUST record the verified `password_revision`
   and integer `login_at`. Revision checks must use the credential snapshot
   that was actually checked, not a subsequently refreshed user row.
3. The check lives in the `user_loader` only — it must not be duplicated into
   `before_request` hooks; the loader is the single point covering remember
   cookie restores too.
4. New sessions must match the stored revision exactly. The compatibility
   timestamp check rejects `login_at <= changed_at`; every password write must
   advance the revision even when the wall-clock second has not changed.
5. Never re-introduce boot-time `ALTER TABLE` (auto-migration). Manual
   `tools/*.sql` only; pinned by `tests/test_security_regressions.py::
   test_boot_does_not_alter_legacy_users_db`.
6. Automated tests: `tests/test_password_reset_security.py` (email availability,
   signed/legacy token races, same-second changes and overlapping login), plus
   `tests/test_security_regressions.py` (invalidation set:
   reset kills sessions, change signs out everywhere, remember cookie
   cleared, boot does not alter legacy DB).

## Related code

| Path | Role |
|------|------|
| `poi_broker/__init__.py` | `user_loader` epoch check |
| `poi_broker/auth.py` | `login_at` stamp; watermark + sign-out on reset/change |
| `poi_broker/models.py` | `User.password_changed_at` column |
| `poi_broker/app.py` | `epoch_utc_date` Jinja filter (Security page display) |
| `tools/apply_password_changed_at.sql` | One-shot upgrade script — applied in production; removed 2026-10 (see `docs/adr/adr_db_schema_migration_strategy.md`) |
| `tools/usersdb_schema.sql` | Fresh-install schema (column included) |
| `docs/password_reset/deployment.md` | Deploy/upgrade runbook |
| `tests/test_security_regressions.py` | Behavior guards |
