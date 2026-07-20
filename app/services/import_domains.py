"""Domain import: bulk textarea and CSV (SPEC FR-DM-2, FR-DM-3).

Upsert by normalized FQDN (re-import updates, never duplicates). Fields whose
provenance is ``manual`` are never overwritten by an import (SPEC merge rules).
Supports a dry-run (preview) that reports created/updated/errors without committing.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.fqdn import InvalidDomainError, normalize_fqdn
from app.models.company import Project
from app.models.domain import Domain, DomainFieldHistory
from app.models.user import User
from app.services import companies as companies_svc
from app.services import domains as domains_svc

CSV_COLUMNS = ("fqdn", "project_code", "tags", "notes", "renewal_price", "currency")


@dataclass
class RowResult:
    line: int
    fqdn: str
    action: str  # "created" | "updated" | "error"
    message: str = ""


@dataclass
class ImportReport:
    rows: list[RowResult] = field(default_factory=list)

    @property
    def created(self) -> int:
        return sum(1 for r in self.rows if r.action == "created")

    @property
    def updated(self) -> int:
        return sum(1 for r in self.rows if r.action == "updated")

    @property
    def errors(self) -> int:
        return sum(1 for r in self.rows if r.action == "error")


@dataclass
class ParsedRow:
    line: int
    raw_fqdn: str
    project_code: str | None = None
    tags: list[str] = field(default_factory=list)
    notes: str | None = None
    renewal_price: str | None = None
    currency: str | None = None


def parse_bulk(text: str) -> list[ParsedRow]:
    """One domain per line; blank lines and '#' comments ignored."""
    rows: list[ParsedRow] = []
    for i, line in enumerate(text.splitlines(), start=1):
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        rows.append(ParsedRow(line=i, raw_fqdn=value))
    return rows


def parse_csv(text: str) -> list[ParsedRow]:
    """Parse CSV with a header row; ``fqdn`` required, other columns optional."""
    rows: list[ParsedRow] = []
    reader = csv.DictReader(io.StringIO(text))
    for i, record in enumerate(reader, start=2):  # line 1 is the header
        normalized = {(k or "").strip().lower(): (v or "").strip() for k, v in record.items()}
        fqdn = normalized.get("fqdn", "")
        if not fqdn:
            rows.append(ParsedRow(line=i, raw_fqdn=""))
            continue
        tags = [t.strip() for t in normalized.get("tags", "").split(",") if t.strip()]
        rows.append(
            ParsedRow(
                line=i,
                raw_fqdn=fqdn,
                project_code=normalized.get("project_code") or None,
                tags=tags,
                notes=normalized.get("notes") or None,
                renewal_price=normalized.get("renewal_price") or None,
                currency=normalized.get("currency") or None,
            )
        )
    return rows


async def _visible_projects_by_code(session: AsyncSession, user: User) -> dict[str, list[Project]]:
    projects = await companies_svc.list_projects(session, user)
    by_code: dict[str, list[Project]] = {}
    for p in projects:
        by_code.setdefault(p.code, []).append(p)
    return by_code


async def run_import(
    session: AsyncSession,
    user: User,
    rows: list[ParsedRow],
    *,
    default_project_id: int | None,
    source: str,
    actor_id: int,
    dry_run: bool,
) -> ImportReport:
    """Upsert ``rows`` and return a per-row report. Commits only if not dry_run."""
    report = ImportReport()
    allowed = await domains_svc.allowed_project_ids(session, user)
    by_code = await _visible_projects_by_code(session, user)

    def _project_visible(pid: int) -> bool:
        return allowed is None or pid in allowed

    # For a dry run, do all writes inside a SAVEPOINT and roll only that back. A full
    # session.rollback() would expire every object in the session — including the
    # request's `user` — and the template's later access to it would raise MissingGreenlet.
    savepoint = await session.begin_nested() if dry_run else None

    for row in rows:
        if not row.raw_fqdn:
            report.rows.append(RowResult(row.line, "", "error", "пустой FQDN"))
            continue
        try:
            norm = normalize_fqdn(row.raw_fqdn)
        except InvalidDomainError as exc:
            report.rows.append(RowResult(row.line, row.raw_fqdn, "error", str(exc)))
            continue

        # Resolve target project: per-row code overrides the form default.
        project_id = default_project_id
        if row.project_code:
            matches = by_code.get(row.project_code, [])
            if len(matches) == 1:
                project_id = matches[0].id
            elif len(matches) == 0:
                report.rows.append(
                    RowResult(
                        row.line, norm.fqdn, "error", f"проект '{row.project_code}' не найден"
                    )
                )
                continue
            else:
                report.rows.append(
                    RowResult(
                        row.line,
                        norm.fqdn,
                        "error",
                        f"код проекта '{row.project_code}' неоднозначен",
                    )
                )
                continue

        if project_id is None:
            report.rows.append(RowResult(row.line, norm.fqdn, "error", "не указан проект"))
            continue
        if not _project_visible(project_id):
            report.rows.append(RowResult(row.line, norm.fqdn, "error", "проект вне доступа"))
            continue

        price: Decimal | None = None
        if row.renewal_price:
            try:
                price = Decimal(row.renewal_price)
            except (InvalidOperation, ValueError):
                report.rows.append(RowResult(row.line, norm.fqdn, "error", "неверная цена"))
                continue

        existing = await domains_svc.get_by_fqdn(session, norm.fqdn)
        if existing is None:
            await _create(session, norm, project_id, row, price, source, actor_id)
            report.rows.append(RowResult(row.line, norm.fqdn, "created"))
        else:
            await _update(session, existing, row, price, source)
            report.rows.append(RowResult(row.line, norm.fqdn, "updated"))

    if dry_run:
        await savepoint.rollback()
    else:
        await record_audit(
            session,
            actor_id=actor_id,
            action="import",
            entity_type="domain",
            entity_id=None,
            diff={"source": source, "created": report.created, "updated": report.updated},
        )
        await session.commit()
    return report


async def _create(session, norm, project_id, row, price, source, actor_id) -> None:
    sources = {"fqdn": source, "project_id": source}
    if row.notes:
        sources["notes"] = source
    if price is not None:
        sources["renewal_price"] = source
    domain = Domain(
        project_id=project_id,
        fqdn=norm.fqdn,
        punycode=norm.punycode,
        tld=norm.tld,
        notes=row.notes,
        renewal_price=price,
        renewal_currency=(row.currency or "USD").upper()[:3],
        field_sources=sources,
    )
    if row.tags:
        domain.tags = [await companies_svc.get_or_create_tag(session, t) for t in row.tags]
    session.add(domain)
    await session.flush()


async def _update(session, domain: Domain, row: ParsedRow, price, source) -> None:
    """Update a domain from an import row, never overwriting manual-sourced fields."""
    field_sources = dict(domain.field_sources or {})

    def _can_write(fieldname: str) -> bool:
        return field_sources.get(fieldname) != "manual" or source == "manual"

    if row.notes is not None and _can_write("notes") and domain.notes != row.notes:
        domain.notes = row.notes
        field_sources["notes"] = source
    if price is not None and _can_write("renewal_price") and domain.renewal_price != price:
        session.add(
            DomainFieldHistory(
                domain_id=domain.id,
                field="renewal_price",
                old=str(domain.renewal_price) if domain.renewal_price is not None else None,
                new=str(price),
                source=source,
            )
        )
        domain.renewal_price = price
        field_sources["renewal_price"] = source
    if row.currency and _can_write("renewal_currency"):
        domain.renewal_currency = row.currency.upper()[:3]
    if row.tags:
        existing = {t.name for t in domain.tags}
        for name in row.tags:
            if name not in existing:
                domain.tags = domain.tags + [await companies_svc.get_or_create_tag(session, name)]
    domain.field_sources = field_sources
