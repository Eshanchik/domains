"""Archive expired ("dead") domains a registrar API surfaces but its UI hides (T47).

The GoDaddy API returns every domain ever held by the account, including long-expired
ones, which land in DomainGuard with old expiry dates and inflate the "expiring soon"
counters. This archives active domains whose expiry is already in the past, scoped to a
registrar connector type (default ``godaddy``) and/or a single account.

Dry run by default — prints what would be archived. Pass ``--apply`` to archive.

Run:
  python -m scripts.archive_dead                       # dry run, all godaddy accounts
  python -m scripts.archive_dead --apply               # archive them
  python -m scripts.archive_dead --connector all --apply
  python -m scripts.archive_dead --account-id 6 --apply
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from app.db import SessionLocal
from app.log import configure_logging
from app.services import registrars as reg

log = logging.getLogger("archive_dead")


async def _run(connector_type: str | None, account_id: int | None, apply: bool) -> None:
    async with SessionLocal() as session:
        fqdns = await reg.archive_expired(
            session,
            connector_type=connector_type,
            account_id=account_id,
            apply=apply,
        )
    verb = "archived" if apply else "would archive"
    log.info(
        "%s %d expired domain(s) [connector=%s account=%s]",
        verb,
        len(fqdns),
        connector_type,
        account_id,
    )
    for fqdn in fqdns[:50]:
        log.info("  %s", fqdn)
    if len(fqdns) > 50:
        log.info("  … and %d more", len(fqdns) - 50)


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description="Archive expired registrar domains.")
    parser.add_argument(
        "--connector",
        default="godaddy",
        help="registrar connector type to scope to (default: godaddy; 'all' = no filter)",
    )
    parser.add_argument("--account-id", type=int, default=None, help="single registrar account id")
    parser.add_argument("--apply", action="store_true", help="actually archive (default: dry run)")
    args = parser.parse_args()
    connector = None if args.connector == "all" else args.connector
    asyncio.run(_run(connector, args.account_id, args.apply))


if __name__ == "__main__":
    main()
