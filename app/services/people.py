"""People directory, alert routing rules and @-mention rendering (T97).

* **People** — who an alert can be assigned to / who gets pinged. A person is a
  Discord user (``<@id>``) and/or a Telegram ``@username``; a *group* is a Discord role
  (``<@&id>``) such as ``@ops``. People need no DomainGuard login.
* **Routes** — "alert kind → person" per scope. Resolution is most-specific-first:
  project → company → global; inside a tier an exact kind beats ``*``.
* **Mentions** — channels render the same person differently, so text carries
  ``@handle`` tokens and :func:`render_mentions` turns them into the channel dialect.
  Discord only pings people/roles explicitly listed in ``allowed_mentions`` (the
  Discord channel derives that list from the content), so free text can never ping
  ``@everyone``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.models.company import Company, Project
from app.models.domain import Domain
from app.models.person import PERSON_KINDS, AlertRoute, Person

ROUTE_KINDS = ("*", "expiry", "ssl", "vt_malicious", "health_down", "ns_change")

# A handle may contain "_ . -" inside but must end with a letter/digit/"_", so the
# trailing dot of "передаю @vasya." is punctuation, not part of the handle.
_HANDLE_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_.-]{0,62}[a-z0-9_])?$")
_DISCORD_RE = re.compile(r"^\d{15,22}$")
_TELEGRAM_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
# An @handle token in free text: not preceded by a word char (so e-mails don't match).
MENTION_RE = re.compile(r"(?<![\w@])@([a-z0-9](?:[a-z0-9_.-]{0,62}[a-z0-9_])?)", re.IGNORECASE)


class PersonError(ValueError):
    """Invalid person / route input; the message is user-facing (Russian)."""


@dataclass(frozen=True)
class PersonRef:
    """A detached, render-ready snapshot of a person (safe to use outside a session)."""

    id: int
    kind: str
    name: str
    handle: str
    discord_id: str | None
    telegram_username: str | None

    @classmethod
    def of(cls, p: Person) -> PersonRef:
        return cls(p.id, p.kind, p.name, p.handle, p.discord_id, p.telegram_username)


# --- validation ----------------------------------------------------------------


def normalize_handle(value: str) -> str:
    handle = (value or "").strip().lstrip("@").lower()
    if not _HANDLE_RE.match(handle):
        raise PersonError(
            "Ник для упоминаний: латиница/цифры, можно «_ . -», без пробелов (например, vasya)."
        )
    return handle


def normalize_discord_id(value: str | None) -> str | None:
    v = (value or "").strip()
    # Accept a pasted mention like <@123>, <@!123> or <@&123>.
    m = re.fullmatch(r"<@[!&]?(\d+)>", v)
    if m:
        v = m.group(1)
    if not v:
        return None
    if not _DISCORD_RE.match(v):
        raise PersonError(
            "Discord ID — это число из 17–20 цифр (Режим разработчика → ПКМ по "
            "пользователю/роли → «Копировать ID»)."
        )
    return v


def normalize_telegram(value: str | None) -> str | None:
    v = (value or "").strip().lstrip("@")
    if v.startswith("https://t.me/"):
        v = v.removeprefix("https://t.me/")
    if not v:
        return None
    if not _TELEGRAM_RE.match(v):
        raise PersonError("Telegram username: 4–32 символа, латиница/цифры/«_», без @.")
    return v


# --- people CRUD ---------------------------------------------------------------


async def list_people(session: AsyncSession, *, active_only: bool = False) -> list[Person]:
    stmt = select(Person).order_by(Person.kind.desc(), func.lower(Person.name))
    if active_only:
        stmt = stmt.where(Person.is_active.is_(True))
    return list((await session.execute(stmt)).scalars().all())


async def get_person(session: AsyncSession, person_id: int) -> Person | None:
    return await session.get(Person, person_id)


async def person_for_user(session: AsyncSession, user_id: int) -> Person | None:
    return await session.scalar(select(Person).where(Person.user_id == user_id))


async def _check_unique(
    session: AsyncSession, *, handle: str, user_id: int | None, exclude_id: int | None
) -> None:
    stmt = select(Person.id).where(Person.handle == handle)
    if exclude_id is not None:
        stmt = stmt.where(Person.id != exclude_id)
    if await session.scalar(stmt) is not None:
        raise PersonError(f"Ник @{handle} уже занят.")
    if user_id is not None:
        stmt = select(Person.id).where(Person.user_id == user_id)
        if exclude_id is not None:
            stmt = stmt.where(Person.id != exclude_id)
        if await session.scalar(stmt) is not None:
            raise PersonError("Этот пользователь DomainGuard уже привязан к другому человеку.")


# Fields recorded in the audit log for people (secrets never live here).
_AUDITED = (
    "kind",
    "name",
    "handle",
    "discord_id",
    "telegram_username",
    "user_id",
    "note",
    "is_active",
)
_RACE_MESSAGE = (
    "Ник или привязанный пользователь уже заняты — обновите страницу и попробуйте снова."
)


def _clean(
    *,
    kind: str,
    name: str,
    handle: str,
    discord_id: str | None,
    telegram_username: str | None,
) -> dict:
    if kind not in PERSON_KINDS:
        raise PersonError("Тип должен быть «человек» или «группа».")
    name = (name or "").strip()
    if not name:
        raise PersonError("Укажите имя.")
    return {
        "kind": kind,
        "name": name[:128],
        "handle": normalize_handle(handle),
        "discord_id": normalize_discord_id(discord_id),
        # Telegram has no group mentions — a group keeps only its Discord role.
        "telegram_username": normalize_telegram(telegram_username) if kind == "person" else None,
    }


async def create_person(
    session: AsyncSession,
    *,
    kind: str,
    name: str,
    handle: str,
    discord_id: str | None = None,
    telegram_username: str | None = None,
    user_id: int | None = None,
    note: str | None = None,
    actor_id: int | None,
) -> Person:
    fields = _clean(
        kind=kind,
        name=name,
        handle=handle,
        discord_id=discord_id,
        telegram_username=telegram_username,
    )
    user_id = user_id if kind == "person" else None
    await _check_unique(session, handle=fields["handle"], user_id=user_id, exclude_id=None)
    person = Person(**fields, user_id=user_id, note=(note or "").strip()[:255] or None)
    session.add(person)
    try:
        await session.flush()
    except IntegrityError as exc:  # a concurrent save won the unique race
        await session.rollback()
        raise PersonError(_RACE_MESSAGE) from exc
    await record_audit(
        session,
        actor_id=actor_id,
        action="create",
        entity_type="person",
        entity_id=person.id,
        diff={key: getattr(person, key) for key in _AUDITED},
    )
    await session.commit()
    await session.refresh(person)
    return person


async def update_person(
    session: AsyncSession,
    person: Person,
    *,
    kind: str,
    name: str,
    handle: str,
    discord_id: str | None,
    telegram_username: str | None,
    user_id: int | None,
    note: str | None,
    is_active: bool,
    actor_id: int | None,
) -> Person:
    fields = _clean(
        kind=kind,
        name=name,
        handle=handle,
        discord_id=discord_id,
        telegram_username=telegram_username,
    )
    user_id = user_id if kind == "person" else None
    await _check_unique(session, handle=fields["handle"], user_id=user_id, exclude_id=person.id)
    before = {key: getattr(person, key) for key in _AUDITED}
    for key, value in fields.items():
        setattr(person, key, value)
    person.user_id = user_id
    person.note = (note or "").strip()[:255] or None
    person.is_active = is_active
    changed = {
        key: {"old": before[key], "new": getattr(person, key)}
        for key in _AUDITED
        if before[key] != getattr(person, key)
    }
    if changed:
        await record_audit(
            session,
            actor_id=actor_id,
            action="update",
            entity_type="person",
            entity_id=person.id,
            diff=changed,
        )
    try:
        await session.commit()
    except IntegrityError as exc:  # a concurrent save won the unique race
        await session.rollback()
        raise PersonError(_RACE_MESSAGE) from exc
    await session.refresh(person)
    return person


async def delete_person(session: AsyncSession, person: Person, *, actor_id: int | None) -> None:
    """Delete a person. Their routes go with them (CASCADE); alerts assigned to them
    become unassigned (SET NULL)."""
    await record_audit(
        session,
        actor_id=actor_id,
        action="delete",
        entity_type="person",
        entity_id=person.id,
        diff={"handle": person.handle},
    )
    await session.delete(person)
    await session.commit()


# --- routes --------------------------------------------------------------------


@dataclass
class RouteRow:
    route: AlertRoute
    person: Person
    scope_label: str


def parse_scope(value: str) -> tuple[int | None, int | None]:
    """``global`` | ``company:<id>`` | ``project:<id>`` → (company_id, project_id)."""
    value = (value or "global").strip()
    try:
        if value.startswith("company:"):
            return int(value.split(":", 1)[1]), None
        if value.startswith("project:"):
            return None, int(value.split(":", 1)[1])
    except ValueError as exc:
        raise PersonError("Неверная область действия правила.") from exc
    if value != "global":
        raise PersonError("Неверная область действия правила.")
    return None, None


async def list_routes(session: AsyncSession) -> list[RouteRow]:
    rows = (
        await session.execute(
            select(AlertRoute, Person, Company.name, Project.name)
            .join(Person, Person.id == AlertRoute.person_id)
            .outerjoin(Company, Company.id == AlertRoute.company_id)
            .outerjoin(Project, Project.id == AlertRoute.project_id)
            .order_by(
                AlertRoute.project_id.is_(None),
                AlertRoute.company_id.is_(None),
                AlertRoute.kind,
            )
        )
    ).all()
    out: list[RouteRow] = []
    for route, person, company, project in rows:
        if route.project_id is not None:
            label = f"проект {project}"
        elif route.company_id is not None:
            label = f"компания {company}"
        else:
            label = "по умолчанию (все)"
        out.append(RouteRow(route, person, label))
    return out


async def set_route(
    session: AsyncSession,
    *,
    company_id: int | None,
    project_id: int | None,
    kind: str,
    person_id: int,
    actor_id: int | None,
) -> AlertRoute:
    """Create or replace the rule for (scope, kind)."""
    if kind not in ROUTE_KINDS:
        raise PersonError("Неизвестный вид алерта.")
    if project_id is not None:
        company_id = None  # a project rule is keyed by the project alone
        if await session.get(Project, project_id) is None:
            raise PersonError("Проект не найден.")
    elif company_id is not None and await session.get(Company, company_id) is None:
        raise PersonError("Компания не найдена.")
    if await session.get(Person, person_id) is None:
        raise PersonError("Человек не найден.")

    stmt = select(AlertRoute).where(AlertRoute.kind == kind)
    stmt = stmt.where(
        AlertRoute.company_id.is_(None)
        if company_id is None
        else AlertRoute.company_id == company_id
    )
    stmt = stmt.where(
        AlertRoute.project_id.is_(None)
        if project_id is None
        else AlertRoute.project_id == project_id
    )
    route = await session.scalar(stmt)
    if route is None:
        route = AlertRoute(
            company_id=company_id, project_id=project_id, kind=kind, person_id=person_id
        )
        session.add(route)
        try:
            await session.flush()
        except IntegrityError:
            # A concurrent save created the same (scope, kind) rule first — update it.
            await session.rollback()
            route = await session.scalar(stmt)
            if route is None:
                raise
            route.person_id = person_id
    else:
        route.person_id = person_id
    await session.flush()
    await record_audit(
        session,
        actor_id=actor_id,
        action="set",
        entity_type="alert_route",
        entity_id=route.id,
        diff={
            "company_id": company_id,
            "project_id": project_id,
            "kind": kind,
            "person_id": person_id,
        },
    )
    await session.commit()
    await session.refresh(route)
    return route


async def delete_route(session: AsyncSession, route_id: int, *, actor_id: int | None) -> None:
    route = await session.get(AlertRoute, route_id)
    if route is None:
        return
    await record_audit(
        session,
        actor_id=actor_id,
        action="delete",
        entity_type="alert_route",
        entity_id=route.id,
        diff={"kind": route.kind, "person_id": route.person_id},
    )
    await session.execute(delete(AlertRoute).where(AlertRoute.id == route_id))
    await session.commit()


async def route_for(session: AsyncSession, domain: Domain, kind: str) -> Person | None:
    """The person responsible for ``kind`` alerts on ``domain`` (or None).

    Tiers: project → company → global; within a tier an exact kind beats ``*``.
    Rules pointing at an inactive person are skipped.
    """
    project = await session.get(Project, domain.project_id)
    company_id = project.company_id if project is not None else None
    tiers = [
        (AlertRoute.project_id == domain.project_id),
        (
            (AlertRoute.company_id == company_id) & AlertRoute.project_id.is_(None)
            if company_id is not None
            else None
        ),
        (AlertRoute.company_id.is_(None) & AlertRoute.project_id.is_(None)),
    ]
    for cond in tiers:
        if cond is None:
            continue
        rows = (
            await session.execute(
                select(AlertRoute.kind, Person)
                .join(Person, Person.id == AlertRoute.person_id)
                .where(cond, AlertRoute.kind.in_((kind, "*")), Person.is_active.is_(True))
            )
        ).all()
        by_kind = dict(rows)
        if kind in by_kind:
            return by_kind[kind]
        if "*" in by_kind:
            return by_kind["*"]
    return None


# --- mentions ------------------------------------------------------------------


def slack_escape(text: str) -> str:
    """Slack treats ``<…>`` as control sequences (``<!channel>``, ``<@U…>``) — escape
    ``& < >`` so user text is shown literally and can never ping the channel."""
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def neutralize_discord_mentions(text: str) -> str:
    """Break raw ``<@id>`` / ``<@&id>`` typed by a user so only people from the
    directory (rendered by us afterwards) can be pinged."""
    return (text or "").replace("<@", "<\u200b@")


def mention(person: Person | PersonRef | None, channel_type: str) -> str:
    """How ``person`` is written in a message for ``channel_type`` (pings where possible)."""
    if person is None:
        return ""
    if channel_type == "discord" and person.discord_id:
        return f"<@&{person.discord_id}>" if person.kind == "group" else f"<@{person.discord_id}>"
    if channel_type == "telegram" and person.telegram_username:
        return f"@{person.telegram_username}"
    if channel_type in ("discord", "telegram"):
        return person.name  # no id for this network → name only, no ping
    name = f"{person.name} (@{person.handle})"
    return slack_escape(name) if channel_type == "slack" else name


async def people_by_handle(session: AsyncSession) -> dict[str, Person]:
    return {p.handle: p for p in await list_people(session, active_only=True)}


def extract_mentions(text: str, by_handle: dict[str, Person | PersonRef]) -> list:
    """People mentioned as ``@handle`` in ``text`` (known handles only, de-duplicated)."""
    seen: dict[str, Person | PersonRef] = {}
    for m in MENTION_RE.finditer(text or ""):
        handle = m.group(1).lower()
        if handle in by_handle and handle not in seen:
            seen[handle] = by_handle[handle]
    return list(seen.values())


def render_mentions(text: str, by_handle: dict[str, Person | PersonRef], channel_type: str) -> str:
    """Replace known ``@handle`` tokens with ``channel_type`` mentions; unknown stay as-is."""

    def sub(m: re.Match) -> str:
        person = by_handle.get(m.group(1).lower())
        return mention(person, channel_type) if person is not None else m.group(0)

    return MENTION_RE.sub(sub, text or "")
