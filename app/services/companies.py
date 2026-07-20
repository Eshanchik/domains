"""Company / Project / Tag services with scope filtering and audit.

Read access is filtered by the caller's scope (SPEC ACL-1); structural mutations
are admin-only (enforced at the router) and always audited (ACL-2).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.models.company import Company, Project, Tag
from app.models.user import Role, User
from app.schemas.company import (
    CompanyCreate,
    CompanyUpdate,
    ProjectCreate,
    ProjectUpdate,
)


class DuplicateCodeError(Exception):
    """Raised when a company/project code already exists (pre-checked, not via DB error)."""


def _scope_company_ids(user: User) -> set[int]:
    return {s.company_id for s in user.scopes if s.company_id is not None}


def _scope_project_ids(user: User) -> set[int]:
    return {s.project_id for s in user.scopes if s.project_id is not None}


# --- Companies ---------------------------------------------------------------


async def list_companies(session: AsyncSession, user: User) -> list[Company]:
    """Companies visible to ``user``.

    Admin sees all. Others see companies they are scoped to directly, plus companies
    that own a project they are scoped to.
    """
    result = await session.execute(select(Company).order_by(Company.name))
    companies = list(result.scalars().all())
    if user.role == Role.admin:
        return companies

    company_ids = _scope_company_ids(user)
    project_ids = _scope_project_ids(user)
    if project_ids:
        proj = await session.execute(select(Project.company_id).where(Project.id.in_(project_ids)))
        company_ids |= set(proj.scalars().all())
    return [c for c in companies if c.id in company_ids]


async def get_company(session: AsyncSession, company_id: int) -> Company | None:
    return await session.get(Company, company_id)


async def create_company(session: AsyncSession, data: CompanyCreate, *, actor_id: int) -> Company:
    # Pre-check keeps the transaction clean (a failed INSERT would poison the async session).
    dup = await session.execute(select(Company.id).where(Company.code == data.code))
    if dup.first() is not None:
        raise DuplicateCodeError(data.code)
    company = Company(name=data.name, code=data.code)
    session.add(company)
    await session.flush()
    await record_audit(
        session,
        actor_id=actor_id,
        action="create",
        entity_type="company",
        entity_id=company.id,
        diff={"name": data.name, "code": data.code},
    )
    await session.commit()
    await session.refresh(company)
    return company


async def update_company(
    session: AsyncSession, company: Company, data: CompanyUpdate, *, actor_id: int
) -> Company:
    diff: dict[str, object] = {}
    if data.name is not None and data.name != company.name:
        diff["name"] = {"old": company.name, "new": data.name}
        company.name = data.name
    if data.code is not None and data.code != company.code:
        diff["code"] = {"old": company.code, "new": data.code}
        company.code = data.code
    if diff:
        await record_audit(
            session,
            actor_id=actor_id,
            action="update",
            entity_type="company",
            entity_id=company.id,
            diff=diff,
        )
    await session.commit()
    await session.refresh(company)
    return company


async def delete_company(session: AsyncSession, company: Company, *, actor_id: int) -> None:
    await record_audit(
        session,
        actor_id=actor_id,
        action="delete",
        entity_type="company",
        entity_id=company.id,
        diff={"code": company.code},
    )
    await session.delete(company)
    await session.commit()


# --- Projects ----------------------------------------------------------------


async def list_projects(session: AsyncSession, user: User) -> list[Project]:
    """Projects visible to ``user`` (admin: all; else scoped company or project)."""
    result = await session.execute(select(Project).order_by(Project.company_id, Project.name))
    projects = list(result.scalars().all())
    if user.role == Role.admin:
        return projects

    company_ids = _scope_company_ids(user)
    project_ids = _scope_project_ids(user)
    return [p for p in projects if p.company_id in company_ids or p.id in project_ids]


async def get_project(session: AsyncSession, project_id: int) -> Project | None:
    return await session.get(Project, project_id)


async def create_project(session: AsyncSession, data: ProjectCreate, *, actor_id: int) -> Project:
    dup = await session.execute(
        select(Project.id).where(Project.company_id == data.company_id, Project.code == data.code)
    )
    if dup.first() is not None:
        raise DuplicateCodeError(data.code)
    project = Project(
        company_id=data.company_id,
        name=data.name,
        code=data.code,
        responsible_user_id=data.responsible_user_id,
    )
    session.add(project)
    await session.flush()
    await record_audit(
        session,
        actor_id=actor_id,
        action="create",
        entity_type="project",
        entity_id=project.id,
        diff={"company_id": data.company_id, "name": data.name, "code": data.code},
    )
    await session.commit()
    await session.refresh(project)
    return project


async def update_project(
    session: AsyncSession, project: Project, data: ProjectUpdate, *, actor_id: int
) -> Project:
    diff: dict[str, object] = {}
    if data.name is not None and data.name != project.name:
        diff["name"] = {"old": project.name, "new": data.name}
        project.name = data.name
    if data.code is not None and data.code != project.code:
        diff["code"] = {"old": project.code, "new": data.code}
        project.code = data.code
    if data.responsible_user_id != project.responsible_user_id:
        diff["responsible_user_id"] = {
            "old": project.responsible_user_id,
            "new": data.responsible_user_id,
        }
        project.responsible_user_id = data.responsible_user_id
    if diff:
        await record_audit(
            session,
            actor_id=actor_id,
            action="update",
            entity_type="project",
            entity_id=project.id,
            diff=diff,
        )
    await session.commit()
    await session.refresh(project)
    return project


async def delete_project(session: AsyncSession, project: Project, *, actor_id: int) -> None:
    await record_audit(
        session,
        actor_id=actor_id,
        action="delete",
        entity_type="project",
        entity_id=project.id,
        diff={"code": project.code},
    )
    await session.delete(project)
    await session.commit()


# --- Tags --------------------------------------------------------------------


async def list_tags(session: AsyncSession) -> list[Tag]:
    result = await session.execute(select(Tag).order_by(Tag.name))
    return list(result.scalars().all())


async def get_or_create_tag(session: AsyncSession, name: str) -> Tag:
    existing = await session.execute(select(Tag).where(Tag.name == name))
    tag = existing.scalar_one_or_none()
    if tag is not None:
        return tag
    tag = Tag(name=name)
    session.add(tag)
    await session.flush()
    return tag


async def create_tag(session: AsyncSession, name: str, *, actor_id: int) -> Tag:
    tag = await get_or_create_tag(session, name)
    await record_audit(
        session,
        actor_id=actor_id,
        action="create",
        entity_type="tag",
        entity_id=tag.id,
        diff={"name": name},
    )
    await session.commit()
    await session.refresh(tag)
    return tag


async def delete_tag(session: AsyncSession, tag: Tag, *, actor_id: int) -> None:
    await record_audit(
        session,
        actor_id=actor_id,
        action="delete",
        entity_type="tag",
        entity_id=tag.id,
        diff={"name": tag.name},
    )
    await session.delete(tag)
    await session.commit()
