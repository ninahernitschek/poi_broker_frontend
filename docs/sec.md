# Security Headers for Flask Applications

This Flask implementation uses the `@app.after_request` decorator to inject security-focused HTTP headers into every response, ensuring a **"secure by default"** posture. This approach hardens the web application against common vulnerabilities such as **Cross-Site Scripting (XSS)**, **Clickjacking**, and **Protocol Downgrade attacks**.

---

## Security Headers Breakdown

### 1. HTTP Strict Transport Security (HSTS)

**Header:**  
`'Strict-Transport-Security': 'max-age=31536000; includeSubDomains; preload'`

- **Purpose:** Forces browsers to use HTTPS for all connections to your site for one year.
- **Benefit:** Prevents **Man-in-the-Middle (MITM)** attacks by blocking unencrypted HTTP connections. The `preload` directive allows inclusion in browser preload lists for built-in HTTPS enforcement.

---

### 2. MIME Sniffing Prevention

**Header:**  
`'X-Content-Type-Options': 'nosniff'`

- **Purpose:** Instructs browsers to strictly respect the `Content-Type` header.
- **Benefit:** Mitigates **MIME-sniffing attacks**, where browsers might execute files (e.g., `.txt` or images) as scripts.

---

### 3. Clickjacking Protection

**Header:**  
`'X-Frame-Options': 'DENY'`

- **Purpose:** Prevents the site from being embedded in `<iframe>`, `<frame>`, or `<object>` elements.
- **Benefit:** Eliminates **Clickjacking** risks, where attackers overlay your site to trick users into unintended actions.

---

### 4. Referrer Policy

**Header:**  
`'Referrer-Policy': 'strict-origin-when-cross-origin'`

- **Purpose:** Controls the information sent in the `Referer` header during navigation.
- **Benefit:** Protects user privacy by sending:
  - Full URL for same-origin requests.
  - Only the domain for cross-origin HTTPS requests.
  - No referrer for HTTPS-to-HTTP transitions.

---

### 5. Permissions Policy

**Header:**  
`'Permissions-Policy': 'geolocation=(), microphone=(), camera=()'`

- **Purpose:** Disables browser access to specific hardware/APIs for your site.
- **Benefit:** Reduces the **attack surface** by blocking access to camera, microphone, or location, even if malicious scripts are injected.

---

## Content Security Policy (CSP)

The CSP header acts as a gatekeeper, specifying trusted sources for content types. Below is the configuration and its purpose:


| Directive                | Configuration Summary                                                                    | Benefit                                                                                    |
| ------------------------ | ---------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `default-src 'self'`     | Only allows content from your domain by default.                                         | Blocks unauthorized data loading.                                                          |
| `script-src`             | Allows your domain, jQuery/jsDelivr CDNs, and `unsafe-inline`/`eval`. Bokeh JS is same-origin. | Enables complex libraries (e.g., Bokeh) while blocking unknown external scripts.           |
| `style-src`              | Allows your domain, Google Fonts, and jQuery styles.                                     | Ensures UI loads from trusted design repositories.                                         |
| `connect-src`            | Allows connections to scientific/astronomical data origins (CDS, IRSA, ESAC, etc.).      | **Critical for Data Apps:** Permits real-time data fetching from trusted global databases. |
| `img-src`                | Allows images from your domain, data URIs, and any HTTPS source.                         | Flexibility for dynamic images and embedded icons.                                         |
| `frame-ancestors 'none'` | Modern alternative to `X-Frame-Options`.                                                 | Secondary defense against Clickjacking.                                                    |


---

## Summary of Benefits

- **XSS Mitigation:** CSP restricts script execution to trusted sources, making XSS attacks significantly harder.
- **Protocol Security:** HSTS ensures users remain on encrypted connections.
- **Data Integrity:** Whitelisting domains in `connect-src` ensures interactions only with trusted data providers.
- **Compliance:** Aligns with security best practices (e.g., OWASP Top 10) and audit requirements.

> **Note:** The use of `'unsafe-inline'` and `'unsafe-eval'` in `script-src` is a necessary compromise for compatibility with older libraries or CDNs. Monitor these closely to minimize XSS risks.

Plot endpoints (`/query_classification`, `/query_lightcurve_data`, `/query_featureplot_data`) return JSON `{div, script}` rather than concatenated HTML. Empty plot data is HTTP 200 with a static warning `div` and empty `script` via `bokeh_warning_payload()` — **not** 404/`{error}` (that paints a red modal error). The catalog modal inserts the Bokeh `div` and evaluates only the JSON `script` field. Classification never echoes `alertId`. Observing plots return JSON `{image, moonHtml}` (moon up), `{image, moonMessage}` (moon down), or `{message}` (not visible). The client styles `message`/`moonMessage` via `textContent` (not innerHTML) and sets `img.src` only when the value is a `data:image/png;base64,` URL. Last-selected observatory is written only by authenticated `POST /api/last-observatory` with `X-CSRFToken`, not by `GET /query_observing_plot`. Export status errors use `textContent`.

Signup (`POST /signup`) uses one generic flash and a `/login` redirect for a new account, a duplicate email, and a uniqueness race, so the response is not an account-existence oracle. Forgot-password (`POST /forgot-password`) uses one generic flash for an existing email, a missing email, and a mail/signing failure. Invalid email/password/name stay distinct validation errors; a signup mail send failure stays an operational error. Verification tokens expire after 24 hours (`email_verification_token_expires`); reset tokens expire after 1 hour. Set `PUBLIC_BASE_URL` (e.g. `https://poibroker.example.edu`) so verification/reset emails do not take their host from `Host` / `X-Forwarded-Host`. `ProxyFix(..., x_host=1)` trusting one hop of `X-Forwarded-Host` is an accepted risk: the documented deployment binds Gunicorn to loopback behind nginx (`proxy_pass http://127.0.0.1:8000`, with nginx setting `X-Forwarded-Host: $server_name`), in-app redirects use relative `url_for()`, and a set `PUBLIC_BASE_URL` overrides the host for the only host-sensitive output (emailed links) — so a spoofed forwarded host has no effect unless the proxy is bypassed or misconfigured. `RATELIMIT_STORAGE_URI` defaults to `memory://` (per-process); multi-worker Gunicorn must set a shared backend URI. HTML CSRF failures redirect to `request.path`, not `Referer`. Catalog modal query strings encode `locusId` / `alertId` with `encodeURIComponent`. Bokeh JS is served same-origin from the installed package (`GET /bokeh.min.js`); QueryBuilder JS/CSS are local static snapshots with no CDN fallback. Do not attach SRI to a jsDelivr minify URL for those files — jsDelivr may re-minify and the hash will not stay valid.

## Password reset links and credential changes

New reset links are signed with `itsdangerous.URLSafeTimedSerializer`, a reset-only salt
(`poi-broker-password-reset-v1`), HMAC-SHA256 and the configured `SECRET_KEY`.
The signed payload contains the user ID, the current credential revision, and a
cryptographically random 32-byte nonce. Links expire one hour after issuance;
signing provides integrity, not encryption, and a reset link remains a bearer
credential. All workers must use the same strong `SECRET_KEY`. Changing that key
invalidates outstanding signed links as well as Flask sessions.

Requesting another reset email **does not invalidate earlier links or change
account state**. Every link keeps its own original expiry. SMTP acceptance is not
proof of delivery, so neither successful sending nor sending failure replaces an
existing link. Reset requests retain the generic response and existing rate
limits. A valid GET only displays the form and sets `Referrer-Policy: no-referrer`;
the CSRF-protected POST performs the credential change.

Both password-reset and authenticated password-change POSTs use one conditional
SQL UPDATE against the user ID, previously read password hash, and credential
revision. The winner changes the password and advances `password_changed_at` to
`max(current_epoch_second, previous_revision + 1)` in the same transaction. A
stale competing request changes zero rows and cannot overwrite the winner. Expiry
is checked again after hashing the proposed password. Signing/expiry alone do
not provide single-use protection: the conditional database write and revision
advance invalidate every reset link from the previous revision.

`password_changed_at` reuses the existing INTEGER column as a **monotonic,
epoch-based credential revision**. It remains suitable for the displayed UTC date,
but can advance ahead of wall-clock seconds after closely spaced changes; it is
not an exact event-time audit trail. New logins store the exact `password_revision`
of the credential they checked, as well as the existing `login_at` timestamp. The
loader rejects revision mismatches, including a login overlapping a credential
change. Sessions issued before this release keep the timestamp fallback: when a
watermark exists, `login_at` must be an integer strictly greater than it. Every
successful password write ends all existing sessions and remembered logins,
including the acting browser, which is explicitly logged out. Accounts with a
NULL revision retain their pre-existing session/remember behavior until rotation.

Previously emailed random reset links remain supported using their stored
SHA-256 hashes and original `reset_token_expires`. New reset requests leave those
legacy fields untouched. A successful reset or password change atomically clears
them; legacy-token consumption also includes the stored token hash and expiry in
the conditional UPDATE. Neither failed writes nor SMTP failures consume a valid
link. There is no account lock held while waiting for email or user interaction.

**No schema migration is required for this release.** Existing deployments already
have `password_changed_at`, `reset_token` and `reset_token_expires`. The application
never alters the database; retain the historical migration record and the
`test_boot_does_not_alter_legacy_users_db` invariant. See
`docs/password_reset/deployment.md` for coordinated rollout/rollback notes and
`docs/password_reset/adr_session_invalidation.md` for the session design.

This design follows the relevant expiration, single-use, account-lockout and
session-revocation principles in the
[OWASP Forgot Password Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html).
Keeping older links valid until their original expiry is an availability policy
for unreliable email. Losing access to the email account still requires an
independently verified support/recovery process.


Remember-me (`REMEMBER_COOKIE_DURATION`, 14 days, `settings.py`) is deliberately **not** bound to IP or User-Agent and its token is **not** rotated on use: a captured cookie replays unchanged for the full window with no fingerprint check to detect it. This is an accepted trade-off, not an oversight: the cookie is `HttpOnly`/`Secure`/`SameSite`, the lifetime is capped, and a password change invalidates every remembered login via the `password_changed_at` watermark above — the user has a one-step kill switch if compromise is suspected. Do not "harden" this by hashing User-Agent or IP into the token without revisiting the decision: mobile clients behind rotating NAT would be logged out mid-window, and it adds code paths this threat model does not require.

jQuery is **3.7.1** (CVE-2020-11022 / CVE-2020-11023 in `.html()` / `.append()` were fixed in 3.5.0). jQuery 4 is blocked by Bootstrap 4. Frontend versions and the Bootstrap 4 / Popper v1+v2 split are in `docs/spec.md` §1.5.