"""Registrar accounts, Namecheap sync, and the unassigned-domain queue (SPEC §3.4)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.base import ConnectorError, RegistrarConnector, RegistrarDomain
from app.connectors.godaddy import GoDaddyConnector
from app.connectors.namecheap import NamecheapConnector
from app.core import crypto
from app.core.audit import record_audit
from app.core.fqdn import InvalidDomainError, normalize_fqdn
from app.models.domain import Domain, DomainFieldHistory
from app.models.registrar import Registrar, RegistrarAccount, UnassignedDomain

log = logging.getLogger("services.registrars")

SYNC_SOURCE = "api-namecheap"


# --- Registrar / account CRUD ------------------------------------------------


async def list_accounts(session: AsyncSession) -> list[RegistrarAccount]:
    result = await session.execute(select(RegistrarAccount).order_by(RegistrarAccount.id))
    return list(result.scalars().all())


async def get_account(session: AsyncSession, account_id: int) -> RegistrarAccount | None:
    return await session.get(RegistrarAccount, account_id)


async def _get_or_create_registrar(
    session: AsyncSession, name: str, connector_type: str
) -> Registrar:
    existing = (
        await session.execute(select(Registrar).where(Registrar.name == name))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    reg = Registrar(name=name, connector_type=connector_type)
    session.add(reg)
    await session.flush()
    return reg


# Registrar display names per connector type.
REGISTRAR_NAMES = {"namecheap": "Namecheap", "godaddy": "GoDaddy"}


async def create_account(
    session: AsyncSession,
    *,
    connector_type: str,
    label: str,
    creds: dict,
    actor_id: int,
) -> RegistrarAccount:
    """Create a registrar account of any supported connector type (encrypted creds)."""
    name = REGISTRAR_NAMES.get(connector_type, connector_type.title())
    registrar = await _get_or_create_registrar(session, name, connector_type)
    account = RegistrarAccount(
        registrar_id=registrar.id,
        label=label,
        credentials_enc=crypto.encrypt(json.dumps(creds)),
    )
    session.add(account)
    await session.flush()
    await record_audit(
        session,
        actor_id=actor_id,
        action="create",
        entity_type="registrar_account",
        entity_id=account.id,
        diff={"label": label, "registrar": name},
    )
    await session.commit()
    await session.refresh(account)
    return account


async def create_namecheap_account(
    session: AsyncSession,
    *,
    label: str,
    api_user: str,
    api_key: str,
    username: str,
    client_ip: str,
    actor_id: int,
) -> RegistrarAccount:
    return await create_account(
        session,
        connector_type="namecheap",
        label=label,
        creds={
            "api_user": api_user,
            "api_key": api_key,
            "username": username,
            "client_ip": client_ip,
        },
        actor_id=actor_id,
    )


async def create_godaddy_account(
    session: AsyncSession,
    *,
    label: str,
    api_key: str,
    api_secret: str,
    actor_id: int,
) -> RegistrarAccount:
    return await create_account(
        session,
        connector_type="godaddy",
        label=label,
        creds={"api_key": api_key, "api_secret": api_secret},
        actor_id=actor_id,
    )


async def delete_account(
    session: AsyncSession, account: RegistrarAccount, *, actor_id: int
) -> None:
    await record_audit(
        session,
        actor_id=actor_id,
        action="delete",
        entity_type="registrar_account",
        entity_id=account.id,
        diff={"label": account.label},
    )
    await session.delete(account)
    await session.commit()


async def account_type_labels(session: AsyncSession) -> dict[int, str]:
    """Map registrar_id → display name, for showing an account's registrar in the UI."""
    rows = await session.execute(select(Registrar.id, Registrar.name))
    return dict(rows.all())


def account_masked_ip(account: RegistrarAccount) -> str:
    """Return the (non-secret) client IP for display; secrets stay masked."""
    if not account.credentials_enc:
        return ""
    try:
        return json.loads(crypto.decrypt(account.credentials_enc)).get("client_ip", "")
    except (crypto.CryptoError, json.JSONDecodeError):
        return ""


def build_connector(connector_type: str, creds: dict) -> RegistrarConnector:
    """Instantiate the right connector for ``connector_type`` from decrypted creds."""
    if connector_type == "namecheap":
        return NamecheapConnector(
            api_user=creds["api_user"],
            api_key=creds["api_key"],
            username=creds["username"],
            client_ip=creds["client_ip"],
        )
    if connector_type == "godaddy":
        return GoDaddyConnector(api_key=creds["api_key"], api_secret=creds["api_secret"])
    raise ConnectorError(f"unsupported connector type: {connector_type}")


async def build_account_connector(
    session: AsyncSession, account: RegistrarAccount
) -> RegistrarConnector:
    registrar = await session.get(Registrar, account.registrar_id)
    connector_type = registrar.connector_type if registrar else "namecheap"
    creds = json.loads(crypto.decrypt(account.credentials_enc))
    return build_connector(connector_type, creds)


# --- Sync --------------------------------------------------------------------


@dataclass
class SyncReport:
    updated: int = 0
    staged: int = 0
    error: str | None = None


async def sync_account(
    session: AsyncSession,
    account: RegistrarAccount,
    *,
    connector: RegistrarConnector | None = None,
    now: datetime | None = None,
) -> SyncReport:
    """Pull the account's domains: update existing (manual-safe), stage new ones."""
    ts = now or datetime.now(UTC)
    report = SyncReport()
    conn = connector or await build_account_connector(session, account)
    try:
        domains = await conn.list_domains()
    except ConnectorError as exc:
        account.status = "error"
        account.last_error = str(exc)
        account.last_sync_at = ts
        await session.commit()
        report.error = str(exc)
        return report

    for rd in domains:
        try:
            norm = normalize_fqdn(rd.fqdn)
        except InvalidDomainError:
            continue
        existing = (
            await session.execute(select(Domain).where(Domain.fqdn == norm.fqdn))
        ).scalar_one_or_none()
        if existing is not None:
            _merge_into_domain(session, existing, rd, account)
            report.updated += 1
        else:
            await _stage_unassigned(session, account, rd, norm.fqdn)
            report.staged += 1

    account.status = "ok"
    account.last_error = None
    account.last_sync_at = ts
    await session.commit()
    return report


def _merge_into_domain(
    session: AsyncSession, domain: Domain, rd: RegistrarDomain, account: RegistrarAccount
) -> None:
    field_sources = dict(domain.field_sources or {})
    domain.registrar_id = account.registrar_id
    domain.registrar_account_id = account.id

    def _set(field: str, value) -> None:
        if value is None or field_sources.get(field) == "manual":
            return  # manual wins over autosync (FR-RG-5)
        if getattr(domain, field) != value:
            if field == "expiry_date":
                session.add(
                    DomainFieldHistory(
                        domain_id=domain.id,
                        field=field,
                        old=domain.expiry_date.isoformat() if domain.expiry_date else None,
                        new=value.isoformat(),
                        source=SYNC_SOURCE,
                    )
                )
            setattr(domain, field, value)
            field_sources[field] = SYNC_SOURCE

    _set("expiry_date", rd.expiry_date)
    _set("auto_renew", rd.auto_renew)
    domain.field_sources = field_sources


async def _stage_unassigned(
    session: AsyncSession, account: RegistrarAccount, rd: RegistrarDomain, fqdn: str
) -> None:
    stmt = (
        pg_insert(UnassignedDomain)
        .values(
            registrar_account_id=account.id,
            fqdn=fqdn,
            expiry_date=rd.expiry_date,
            auto_renew=rd.auto_renew,
        )
        .on_conflict_do_update(
            index_elements=["fqdn"],
            set_={
                "registrar_account_id": account.id,
                "expiry_date": rd.expiry_date,
                "auto_renew": rd.auto_renew,
            },
        )
    )
    await session.execute(stmt)


# --- Unassigned queue --------------------------------------------------------


async def list_unassigned(session: AsyncSession) -> list[UnassignedDomain]:
    result = await session.execute(select(UnassignedDomain).order_by(UnassignedDomain.fqdn))
    return list(result.scalars().all())


async def assign_to_project(
    session: AsyncSession, unassigned_id: int, project_id: int, *, actor_id: int
) -> Domain | None:
    """Promote an unassigned domain into a real Domain in ``project_id``."""
    staged = await session.get(UnassignedDomain, unassigned_id)
    if staged is None:
        return None
    norm = normalize_fqdn(staged.fqdn)
    existing = (
        await session.execute(select(Domain).where(Domain.fqdn == norm.fqdn))
    ).scalar_one_or_none()
    if existing is None:
        domain = Domain(
            project_id=project_id,
            fqdn=norm.fqdn,
            punycode=norm.punycode,
            tld=norm.tld,
            expiry_date=staged.expiry_date,
            auto_renew=staged.auto_renew,
            registrar_account_id=staged.registrar_account_id,
            field_sources={"fqdn": SYNC_SOURCE, "project_id": "manual"},
        )
        session.add(domain)
        await session.flush()
        await record_audit(
            session,
            actor_id=actor_id,
            action="assign",
            entity_type="domain",
            entity_id=domain.id,
            diff={"fqdn": norm.fqdn, "project_id": project_id},
        )
    else:
        domain = existing
    await session.delete(staged)
    await session.commit()
    return domain
