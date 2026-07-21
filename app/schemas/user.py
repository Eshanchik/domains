"""Pydantic schemas for users and scopes."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.user import Role


class ScopeIn(BaseModel):
    company_id: int | None = None
    project_id: int | None = None


class ScopeRead(ScopeIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


class UserCreate(BaseModel):
    email: EmailStr
    login: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    role: Role = Role.viewer
    mcp_allowed: bool = False
    scopes: list[ScopeIn] = Field(default_factory=list)


class UserUpdate(BaseModel):
    email: EmailStr | None = None
    role: Role | None = None
    is_active: bool | None = None
    mcp_allowed: bool | None = None
    # When present, replaces the whole scope set for the user.
    scopes: list[ScopeIn] | None = None


class PasswordUpdate(BaseModel):
    password: str = Field(min_length=8, max_length=128)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    login: str
    role: Role
    is_active: bool
    created_at: datetime
    scopes: list[ScopeRead] = Field(default_factory=list)
