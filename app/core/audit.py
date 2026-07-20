"""Audit logging service (SPEC ACL-2 / SEC-4).

A single helper records a mutating action. Callers pass a JSON-serialisable diff;
secrets must be masked by the caller before reaching here (SEC-2).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog


async def record_audit(
    session: AsyncSession,
    *,
    actor_id: int | None,
    action: str,
    entity_type: str,
    entity_id: str | int | None = None,
    diff: dict[str, Any] | None = None,
) -> AuditLog:
    """Persist an audit entry. Does not commit — the caller controls the transaction."""
    entry = AuditLog(
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        diff_json=diff,
    )
    session.add(entry)
    return entry
