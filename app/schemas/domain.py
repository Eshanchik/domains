"""Pydantic schemas for domains."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class DomainCreate(BaseModel):
    fqdn: str = Field(min_length=1, max_length=253)
    project_id: int
    notes: str | None = None
    expiry_date: datetime | None = None
    renewal_price: Decimal | None = None
    renewal_currency: str = "USD"
    renewal_period_months: int = 12
    ssl_extra_hosts: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    responsible_user_id: int | None = None


class DomainUpdate(BaseModel):
    project_id: int | None = None
    notes: str | None = None
    is_active: bool | None = None
    expiry_date: datetime | None = None
    auto_renew: bool | None = None
    nameservers: list[str] | None = None
    epp_statuses: list[str] | None = None
    registrant: str | None = None
    renewal_price: Decimal | None = None
    renewal_currency: str | None = None
    renewal_period_months: int | None = None
    ssl_extra_hosts: list[str] | None = None
    tags: list[str] | None = None
    responsible_user_id: int | None = None


class DomainRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    fqdn: str
    punycode: str
    tld: str
    expiry_date: datetime | None
    auto_renew: bool | None
    is_active: bool
    renewal_price: Decimal | None
    renewal_currency: str
    created_at: datetime
