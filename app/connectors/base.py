"""Registrar connector plugin interface (SPEC FR-RG-2)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


class ConnectorError(Exception):
    """Registrar API failure (auth, network, malformed response)."""


@dataclass
class RegistrarDomain:
    """Normalized domain record from a registrar."""

    fqdn: str
    expiry_date: datetime | None = None
    auto_renew: bool | None = None


class RegistrarConnector(ABC):
    @abstractmethod
    async def list_domains(self) -> list[RegistrarDomain]:
        """Return all domains in the account (handling pagination internally)."""
