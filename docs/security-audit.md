# DomainGuard — security audit (T39)

Date: 2026-07-21. Scope: the whole application including the Phase-5 additions
(Google OAuth sign-in and the MCP server). Method: source review across
authentication/authorization, secret handling, SSRF, injection, CSRF, transport
security, rate-limiting, the internet-exposed MCP endpoint, dependencies, and
container/infra. Per the agreed scope, **critical and high findings are fixed in
this task** (each with tests); medium/low go to `docs/PLAN.md` Backlog.

## Findings

| # | Severity | Area | Finding | Status |
|---|----------|------|---------|--------|
| 1 | **High** | SSRF | Health-check URLs are operator-supplied and fetched server-side with no scheme/address validation; a Manager could target cloud metadata (`169.254.169.254`), `localhost`, or internal services, including via a redirect from a public URL. | **Fixed** |
| 2 | **High/Med** | Exposure | The MCP endpoint `/mcp` exposes admin mutations over the internet, gated only by a bearer token (no edge throttling → token brute-force / abuse). | **Mitigated** (rate-limit) + accepted residual |
| 3 | Medium | Headers | No HSTS / `X-Content-Type-Options` / `X-Frame-Options` / `Referrer-Policy`. | **Fixed** |
| 4 | Medium | Brute-force | No edge rate-limiting on `/login` (app-level lockout existed, but no network-layer throttle). | **Fixed** |
| 5 | Low | Injection | `retention.py` drops partitions with an f-string table name. | Reviewed — safe |
| 6 | Medium | Headers | No Content-Security-Policy. | Backlog |
| 7 | Low | Ops | No automated dependency-vulnerability scan in CI. | Backlog |

## Fixes applied

1. **SSRF guard (`app/core/net_guard.py`).** `validate_public_url` allows only
   `http`/`https` and rejects any host that resolves to a private/loopback/
   link-local/reserved/multicast/unspecified address. It runs **before every
   health-check fetch and on every redirect hop** (redirects are now followed
   manually, ≤5 hops, re-validating each). `validate_scheme` (no DNS) also runs at
   health-check **creation** for fast operator feedback (`InvalidHealthCheckUrl`
   → HTTP 400). The DNS resolver is an injectable seam so tests don't hit the
   network. Tests: `tests/unit/test_net_guard.py` (scheme/literal-IP/resolved-IP
   blocking) and `tests/integration/test_healthcheck.py` (worker refuses a host
   that resolves private and never issues the request; create rejects `file://`).

2. **Edge rate-limiting (nginx).** `limit_req_zone` for `/login` (20 r/m, burst 20)
   and `/mcp` (300 r/m, burst 30), keyed by client IP — defense-in-depth on top of
   the app-level login lockout and MCP token auth.

3. **Security headers (nginx).** Production adds `Strict-Transport-Security`
   (1 year, includeSubDomains), `X-Content-Type-Options: nosniff`,
   `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`
   (the last three also in dev, where HSTS is moot over plain HTTP).

## Reviewed and found sound

- **Authentication.** Passwords via argon2 with rehash-on-login; login brute-force
  lockout (`login_guard`); sessions in Redis with `HttpOnly` + `Secure` (prod) +
  `SameSite=Lax` cookies (the Lax setting is the CSRF defense for state-changing
  POSTs). API tokens are high-entropy and stored SHA-256-hashed.
- **Google OAuth (T37).** `state`-cookie CSRF check; sign-in only for a pre-existing
  **active** user matched by **verified** email (no self-registration); 2FA still
  enforced after Google; client secret from env with `repr=False`, never logged.
- **MCP (T38).** Token → role/scope applied to every tool; mutations go through the
  same services as the UI, so scope checks and the **audit log** apply; viewer
  tokens are read-only. (Residual: admin power reachable with a valid token from
  anywhere — keep tokens least-privilege and revoke unused ones; #2 adds throttling.)
- **Authorization.** RBAC (`require_role`) + company/project scope on reads and
  mutations; verified on the domains/alerts/users/costs paths.
- **Injection.** All queries go through SQLAlchemy with bound parameters. The one
  interpolated statement (`retention.py` `DROP TABLE {name}`) uses names read from
  `pg_inherits` and matched against a strict `check_result_YYYY_MM` regex — not user
  input. Jinja autoescaping is on for all templates.
- **Secrets.** Registrar/VT/bot/OAuth secrets encrypted at rest (Fernet, master key
  from env); masked in the UI; `diff=None` on password changes so they never hit the
  audit log; `dg_master_key`/`google_client_secret` marked `repr=False`.
- **Transport / container.** TLS via nginx + Let's Encrypt; app runs as a non-root
  user; datastores are not published outside the compose network.

## Backlog (medium/low — tracked in PLAN.md)

- **Content-Security-Policy.** Needs a policy that permits the Tailwind Play CDN and
  the inline styles/HTMX the templates use; deferred to avoid breaking the UI.
- **Dependency scanning.** Add `pip-audit` (or Dependabot) to CI.
- **Optional MCP IP-allowlist.** If the set of MCP clients is known, restrict `/mcp`
  by source IP in nginx for stronger-than-token protection.
- **DB least-privilege.** Review the application DB role's grants (currently broad).
