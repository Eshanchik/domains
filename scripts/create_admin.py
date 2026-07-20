"""Create (or ensure) the first admin user — idempotent.

Reads credentials from the environment so no password is baked into the image:

    DG_ADMIN_EMAIL   (default admin@example.com)
    DG_ADMIN_LOGIN   (default admin)
    DG_ADMIN_PASSWORD (required; the script refuses to create a blank password)

Run: ``python -m scripts.create_admin``
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from app.db import SessionLocal
from app.log import configure_logging
from app.services.auth import ensure_admin

log = logging.getLogger("create_admin")


async def _run() -> int:
    email = os.getenv("DG_ADMIN_EMAIL", "admin@example.com")
    login = os.getenv("DG_ADMIN_LOGIN", "admin")
    password = os.getenv("DG_ADMIN_PASSWORD", "")

    if len(password) < 8:
        log.error("DG_ADMIN_PASSWORD must be set (>= 8 chars); refusing to seed admin.")
        return 1

    async with SessionLocal() as session:
        user, created = await ensure_admin(
            session, None, email=email, login=login, password=password
        )
    log.info(
        "admin %s (login=%s, id=%s)",
        "created" if created else "already exists",
        user.login,
        user.id,
    )
    return 0


def main() -> None:
    configure_logging()
    sys.exit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
