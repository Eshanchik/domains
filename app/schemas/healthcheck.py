"""Pydantic schemas for health-checks."""

from __future__ import annotations

from pydantic import BaseModel, Field


class HealthCheckCreate(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    method: str = "GET"
    follow_redirects: bool = False
    expected_statuses: str = "200-299"
    location_pattern: str | None = None
    body_substring: str | None = None
    timeout_s: int = Field(default=10, ge=1, le=120)
    interval_min: int = Field(default=15, ge=1, le=1440)
    fail_threshold: int = Field(default=3, ge=1, le=20)
