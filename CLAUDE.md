# CLAUDE.md — правила проекта DomainGuard

## Что это за проект

DomainGuard — внутренняя open-source система мониторинга доменов: единый реестр доменов
нескольких компаний и их проектов, контроль сроков истечения (RDAP/WHOIS), SSL-сертификатов,
репутации (VirusTotal), кастомных health-check URL, учёт стоимости продлений и адресные
алерты в Telegram (позже Slack/Discord). Полная спецификация — `docs/SPEC.md`.
План работ — `docs/PLAN.md`. Работай СТРОГО по задачам из PLAN.md.

## Стек (зафиксирован, не менять без согласования)

- Python 3.12, FastAPI, SQLAlchemy 2.0 (async), Pydantic v2, Alembic
- PostgreSQL 16 (данные + история), Redis 7 (брокер, кэш, rate-limit, locks)
- Dramatiq (воркеры) + periodiq/APScheduler (планировщик)
- Frontend: Jinja2 + HTMX + Tailwind (CDN на MVP). Никаких SPA/React.
  UI делается семантичной разметкой с чистыми шаблонами — позже дизайн будет
  заменён (Claude Design), поэтому: минимум inline-стилей, вся вёрстка в
  шаблонах `templates/`, логика — только в backend.
- Docker Compose: сервисы `nginx`, `api`, `worker`, `scheduler`, `postgres`, `redis`
- CI: GitHub Actions (lint + tests на каждый push/PR)

## Языки

- Код, идентификаторы, комментарии, docstrings, commit messages — **английский**.
- Тексты UI, шаблоны алертов, письма/сообщения — **русский**.
- Документация в `docs/` — русский.
- Время: в БД всегда UTC; отображение и расписания — Europe/Kyiv.

## Процесс работы (обязательно)

1. Бери следующую задачу из `docs/PLAN.md` (или указанную пользователем по номеру).
2. Перед началом: перечитай соответствующие разделы `docs/SPEC.md`.
3. Задача считается выполненной только при соблюдении Definition of Done (ниже).
4. После выполнения: обнови статус задачи в `docs/PLAN.md` (`[ ]` → `[x]`,
   добавь дату и краткое примечание что сделано).
5. Коммиты — conventional commits (`feat:`, `fix:`, `test:`, `docs:`, `chore:`),
   одна задача = одна ветка `task/NN-short-name`, мерж после зелёного CI.
6. Никогда не выходи за рамки текущей задачи «заодно». Нашёл проблему вне
   скоупа — запиши в `docs/PLAN.md` в раздел Backlog.

## Definition of Done (для каждой задачи)

- [ ] Код проходит `ruff check` и `ruff format --check`
- [ ] Написаны тесты (pytest), покрывающие happy path И ошибочные сценарии
- [ ] Для кода с внешними API — тесты с моками (respx/responses), включая
      таймауты, 5xx, rate-limit ответы
- [ ] Миграции — только через Alembic (`alembic revision --autogenerate` + ручная проверка);
      применённые миграции не редактируются
- [ ] Все тесты зелёные локально: `make test`
- [ ] Обновлён `docs/PLAN.md`; при изменении API/моделей — `docs/SPEC.md`
- [ ] `docker compose up` поднимается с нуля без ручных шагов (кроме `.env`)

## Структура проекта

```
app/
  main.py               # FastAPI app factory
  config.py             # Pydantic Settings, всё из env
  db.py                 # engine, session
  models/               # SQLAlchemy модели (по файлу на домен сущностей)
  schemas/              # Pydantic-схемы
  services/             # бизнес-логика (никакой логики в роутерах)
  api/                  # роутеры REST API (/api/v1/...)
  web/                  # роутеры HTML-страниц (HTMX)
  workers/              # Dramatiq actors: expiry, ssl, vt, healthcheck, alerter, digest
  scheduler/            # выборка «созревших» проверок, постановка в очередь
  connectors/           # base.py (RegistrarConnector) + namecheap.py, godaddy.py
  channels/             # base.py (NotificationChannel) + telegram.py, ...
  checks/               # логика проверок (rdap, whois, ssl, vt, healthcheck)
  core/                 # crypto (Fernet), rate_limiter, retry, audit
templates/              # Jinja2 (+ partials для HTMX)
static/
alembic/
tests/
  unit/  integration/  fixtures/
docs/
  SPEC.md  PLAN.md
docker-compose.yml  Dockerfile  Makefile  .env.example  .github/workflows/ci.yml
```

## Ключевые инженерные правила

- **Секреты**: API-ключи регистраторов/VT/ботов шифруются at-rest (Fernet,
  мастер-ключ `DG_MASTER_KEY` из env). Секреты никогда не логируются и не
  возвращаются в API (маскирование `***`). В репозитории — только `.env.example`.
- **Внешние вызовы**: каждый внешний сервис (RDAP, WHOIS per-TLD, VT, Namecheap,
  GoDaddy, Telegram) — только через централизованный токен-бакет в Redis +
  retry с экспоненциальным backoff + circuit breaker. Ошибка внешнего сервиса
  помечает данные `stale`, но никогда не роняет воркер и не стирает данные.
- **Идемпотентность**: повторный запуск любой задачи не создаёт дублей записей
  и дублей алертов (dedupe-key). Импорт — upsert по FQDN.
- **Слияние данных**: у полей домена хранится источник (manual/csv/api/rdap);
  автосинк не перетирает ручные правки (manual имеет приоритет).
- **История**: результаты проверок пишутся в партиционированные по месяцам
  таблицы; retention — 12 месяцев, чистка фоновой задачей.
- **Пагинация**: все списки — с пагинацией и индексами; целимся в <1с при 10k строк.
- **Тестовая симуляция сбоев обязательна**: в тестах воркеров моделируй
  недоступность RDAP/VT (таймаут, 429, 503) и проверяй backoff/stale-поведение.

## Команды (Makefile)

- `make up` / `make down` — docker compose
- `make migrate` — alembic upgrade head
- `make test` — pytest с coverage
- `make lint` — ruff check + format check
- `make seed` — тестовые данные (2 компании, проекты, десяток доменов)