"""SQLAlchemy models package.

Import model modules here so that ``app.db.Base.metadata`` is fully populated for
Alembic autogeneration. Concrete models are added by later tasks (T02+).
"""

from app.db import Base  # noqa: F401  (re-exported for Alembic target metadata)

__all__ = ["Base"]
