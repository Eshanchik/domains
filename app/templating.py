"""Shared Jinja2 template environment.

All UI text is Russian (CLAUDE.md). Templates live in ``templates/`` and stay clean
(no business logic) so the design can be replaced later.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
