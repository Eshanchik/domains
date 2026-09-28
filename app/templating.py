"""Shared Jinja2 template environment.

All UI text is Russian (CLAUDE.md). Templates live in ``templates/`` and stay clean
(no business logic) so the design can be replaced later.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _register_filters() -> None:
    # Imported lazily: services import models/config, not the templating layer.
    from app.services.alert_workflow import kind_label, severity_label

    templates.env.filters["kind_ru"] = kind_label
    templates.env.filters["severity_ru"] = severity_label
    templates.env.filters["mentions_html"] = _mentions_html


def _mentions_html(text: str | None):
    """Escape comment text and highlight ``@handle`` tokens (safe: handles are
    ``[a-z0-9_.-]`` and the text is escaped first)."""
    from markupsafe import Markup, escape

    from app.services.people import MENTION_RE

    escaped = str(escape(text or ""))
    return Markup(MENTION_RE.sub(r'<span class="mention">@\1</span>', escaped))


_register_filters()
