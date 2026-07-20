# DomainGuard

Внутренняя система мониторинга доменов: единый реестр доменов нескольких компаний
и их проектов, контроль сроков истечения (RDAP/WHOIS), SSL-сертификатов, репутации
(VirusTotal), кастомных health-check URL, учёт стоимости продлений и адресные алерты
в Telegram.

Полная спецификация — [`docs/SPEC.md`](docs/SPEC.md). План работ — [`docs/PLAN.md`](docs/PLAN.md).
Правила разработки — [`CLAUDE.md`](CLAUDE.md).

## Стек

Python 3.12 · FastAPI · SQLAlchemy 2.0 (async) · PostgreSQL 16 · Redis 7 ·
Dramatiq · Alembic · Jinja2 + HTMX. Развёртывание — Docker Compose.

## Быстрый старт (Docker)

```bash
cp .env.example .env        # при необходимости отредактировать
make up                     # собрать и поднять весь стек
```

После старта:

- API за nginx — http://localhost:8080 (порт настраивается через `HTTP_PORT`)
- liveness — http://localhost:8080/healthz
- readiness (проверяет Postgres и Redis) — http://localhost:8080/readyz

Миграции применяются автоматически одноразовым сервисом `migrate` перед запуском
`api`/`worker`/`scheduler`. Остановить стек: `make down`.

Сервисы compose: `nginx`, `api`, `worker`, `scheduler`, `postgres`, `redis`
(+ одноразовый `migrate`).

## Разработка (локально, без Docker)

Требуется Python 3.12 и доступные Postgres/Redis (можно поднять только их через
`docker compose up postgres redis -d`).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

export DATABASE_URL=postgresql+asyncpg://domainguard:domainguard@localhost:5432/domainguard
export REDIS_URL=redis://localhost:6379/0

alembic upgrade head        # применить миграции
uvicorn app.main:app --reload
```

## Команды (Makefile)

| Команда | Действие |
|---|---|
| `make up` / `make down` | поднять / остановить стек |
| `make migrate` | `alembic upgrade head` |
| `make test` | тесты (pytest + coverage) |
| `make lint` | `ruff check` + `ruff format --check` |
| `make fmt` | автоформатирование |
| `make seed` | демо-данные (появятся с T03) |

## Секреты

Секреты (ключи регистраторов/VT/бота) шифруются at-rest (Fernet, мастер-ключ
`DG_MASTER_KEY` из окружения) и никогда не попадают в git. В репозитории — только
`.env.example`. Сгенерировать мастер-ключ:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```
