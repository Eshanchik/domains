"""SQLAlchemy models package.

Importing the model modules here ensures every table is registered on
``app.db.Base.metadata`` for Alembic autogeneration.
"""

from app.db import Base
from app.models.audit import AuditLog
from app.models.user import Role, User, UserScope

__all__ = ["Base", "User", "UserScope", "Role", "AuditLog"]
