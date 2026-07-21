# DomainGuard MCP server

An MCP (Model Context Protocol) server that lets an assistant (e.g. Claude) read and
operate the domain registry over HTTP. It runs as the `mcp` container in the compose
stack; nginx proxies the public path `/mcp` to it.

## Authentication

Every request must carry a **DomainGuard API token**:

```
Authorization: Bearer dg_<token>
```

Create a token in the web UI (`API-токены` in the sidebar) or via the REST API. The
token owner's **role and scope** apply to every call:

- an **Admin** token gets full super-admin power (all read + write tools),
- a **Manager** token can read and mutate within its scope,
- a **Viewer** token is read-only.

Missing or invalid tokens get `401`.

## Endpoint

- Local (compose): `http://localhost:8080/mcp`
- Production: `https://domains.zimbabwe-inc.com/mcp`

Transport is MCP **streamable HTTP** (stateless). Point an MCP client at the endpoint
with the `Authorization` header above.

## Tools

Read (any active token, scoped):

| Tool | Purpose |
|------|---------|
| `whoami` | Identity and role of the token |
| `overview` | Dashboard totals (expiries, SSL/VT/health problems) |
| `list_domains` | Domains in scope (`q`, `expiring_days`, `page`, `page_size`) |
| `get_domain` | One domain by id |
| `list_alerts` | Active alerts in scope |
| `list_companies` | Companies and their projects |
| `costs_summary` | Annual renewal cost by company/project/registrar |

Write (Manager+ token, scoped):

| Tool | Purpose |
|------|---------|
| `create_domain` | Add a domain to a project |
| `set_domain_archived` | Archive / unarchive a domain |
| `check_domain_now` | Enqueue all checks (rdap/ssl/vt/dns) for a domain |
| `resolve_alert` | Close an active alert |
| `import_domains` | Bulk import (plain lines + `default_project_id`, or CSV with an `fqdn` header); `dry_run=true` previews |

Every mutation goes through the same services as the web UI, so scope checks and the
**audit log** apply unchanged (the token owner is recorded as the actor).

## Security notes

- The endpoint exposes admin capabilities over the internet, gated only by the bearer
  token — treat tokens as secrets, scope them to the least role needed, and revoke
  unused ones. Tokens are stored hashed (SHA-256) at rest.
- All mutations are audited. Reads and writes are filtered by the token owner's scope.
