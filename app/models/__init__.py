"""SQLAlchemy models package.

Importing the model modules here ensures every table is registered on
``app.db.Base.metadata`` for Alembic autogeneration.
"""

from app.db import Base
from app.models.audit import AuditLog
from app.models.check import CheckSchedule, CheckType
from app.models.check_result import CheckResult, CheckStatus
from app.models.company import Company, Project, Tag
from app.models.domain import Domain, DomainFieldHistory, DomainTag
from app.models.setting import Setting
from app.models.ssl_certificate import SslCertificate
from app.models.user import Role, User, UserScope
from app.models.vt_result import VtResult

__all__ = [
    "Base",
    "User",
    "UserScope",
    "Role",
    "AuditLog",
    "Company",
    "Project",
    "Tag",
    "Domain",
    "DomainTag",
    "DomainFieldHistory",
    "CheckSchedule",
    "CheckType",
    "CheckResult",
    "CheckStatus",
    "SslCertificate",
    "Setting",
    "VtResult",
]
