"""Redis-backed storage for the MCP OAuth server (T46).

Holds dynamically-registered clients, pending authorization requests (handed from
the provider's ``authorize`` to our consent page), one-time authorization codes, and
access/refresh tokens. Shared by the provider (mcp container) and the consent route
(api container) — both reach the same Redis. Everything is JSON; the SDK models are
pydantic so they round-trip via ``model_dump_json`` / ``model_validate_json``.
"""

from __future__ import annotations

import json

from mcp.server.auth.provider import AccessToken, AuthorizationCode, RefreshToken
from mcp.shared.auth import OAuthClientInformationFull

from app.db import get_redis

_CLIENT = "mcpo:client:"
_AUTHREQ = "mcpo:authreq:"
_CODE = "mcpo:code:"
_ACCESS = "mcpo:access:"
_REFRESH = "mcpo:refresh:"

AUTHREQ_TTL = 600  # 10 min to complete login + consent
CODE_TTL = 300  # 5 min to exchange the code
ACCESS_TTL = 3600  # 1 h access token
REFRESH_TTL = 30 * 24 * 3600  # 30 d refresh token
CLIENT_TTL = 180 * 24 * 3600  # DCR clients expire if unused for 180 d


# --- clients (dynamic client registration) -----------------------------------


async def put_client(client: OAuthClientInformationFull) -> None:
    redis = get_redis()
    try:
        await redis.set(_CLIENT + client.client_id, client.model_dump_json(), ex=CLIENT_TTL)
    finally:
        await redis.aclose()


async def get_client(client_id: str) -> OAuthClientInformationFull | None:
    redis = get_redis()
    try:
        raw = await redis.get(_CLIENT + client_id)
    finally:
        await redis.aclose()
    return OAuthClientInformationFull.model_validate_json(raw) if raw else None


# --- pending authorization requests (provider.authorize → consent) -----------


async def put_authreq(rid: str, data: dict) -> None:
    redis = get_redis()
    try:
        await redis.set(_AUTHREQ + rid, json.dumps(data), ex=AUTHREQ_TTL)
    finally:
        await redis.aclose()


async def take_authreq(rid: str) -> dict | None:
    """Read (without deleting) a pending authorization request."""
    redis = get_redis()
    try:
        raw = await redis.get(_AUTHREQ + rid)
    finally:
        await redis.aclose()
    return json.loads(raw) if raw else None


async def drop_authreq(rid: str) -> None:
    redis = get_redis()
    try:
        await redis.delete(_AUTHREQ + rid)
    finally:
        await redis.aclose()


# --- authorization codes -----------------------------------------------------


async def put_code(code: AuthorizationCode) -> None:
    redis = get_redis()
    try:
        await redis.set(_CODE + code.code, code.model_dump_json(), ex=CODE_TTL)
    finally:
        await redis.aclose()


async def get_code(code: str) -> AuthorizationCode | None:
    redis = get_redis()
    try:
        raw = await redis.get(_CODE + code)
    finally:
        await redis.aclose()
    return AuthorizationCode.model_validate_json(raw) if raw else None


async def drop_code(code: str) -> None:
    redis = get_redis()
    try:
        await redis.delete(_CODE + code)
    finally:
        await redis.aclose()


# --- tokens ------------------------------------------------------------------


async def put_access(token: AccessToken) -> None:
    redis = get_redis()
    try:
        await redis.set(_ACCESS + token.token, token.model_dump_json(), ex=ACCESS_TTL)
    finally:
        await redis.aclose()


async def get_access(token: str) -> AccessToken | None:
    redis = get_redis()
    try:
        raw = await redis.get(_ACCESS + token)
    finally:
        await redis.aclose()
    return AccessToken.model_validate_json(raw) if raw else None


async def put_refresh(token: RefreshToken) -> None:
    redis = get_redis()
    try:
        await redis.set(_REFRESH + token.token, token.model_dump_json(), ex=REFRESH_TTL)
    finally:
        await redis.aclose()


async def get_refresh(token: str) -> RefreshToken | None:
    redis = get_redis()
    try:
        raw = await redis.get(_REFRESH + token)
    finally:
        await redis.aclose()
    return RefreshToken.model_validate_json(raw) if raw else None


async def drop_token(token: str) -> None:
    redis = get_redis()
    try:
        await redis.delete(_ACCESS + token, _REFRESH + token)
    finally:
        await redis.aclose()
