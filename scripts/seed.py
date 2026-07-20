"""Seed demo data.

The real seed (2 companies, ~5 projects, a dozen domains — SPEC §10 / T03) lands
once the domain models exist. For now this is a working, idempotent entrypoint so
``make seed`` is wired end-to-end from T01.

Run: ``python -m scripts.seed``
"""

from __future__ import annotations

import logging

from app.log import configure_logging

log = logging.getLogger("seed")


def main() -> None:
    configure_logging()
    log.info("seed: no models yet — demo data is added in T03 (companies/projects).")


if __name__ == "__main__":
    main()
