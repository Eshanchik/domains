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

## Первый запуск

1. `cp .env.example .env` и **обязательно** задать `DG_MASTER_KEY` (Fernet-ключ,
   см. раздел «Секреты») и `DG_ADMIN_PASSWORD`.
2. `make up` — поднимет весь стек и применит миграции.
3. `make create-admin` — создаст первого администратора из `DG_ADMIN_*`.
4. Зайти на http://localhost:8080, войти под админом.
5. В разделе **Настройки** задать (опционально) ключ VirusTotal и токен Telegram-бота.
6. В разделе **Каналы** добавить Telegram-канал (chat_id группы) и уровень (global/company/project).
7. Завести компании/проекты, затем домены (вручную, bulk, CSV или синком Namecheap).

## Внешние интеграции

- **VirusTotal** — free-ключ (4 запроса/мин, 500/день); задаётся в Настройках. Без ключа VT-проверки не выполняются.
- **Telegram** — один бот, токен в Настройках; каналы указывают `chat_id`. Без токена алерты не отправляются (видны в UI/логах).
- **Namecheap** — раздел «Регистраторы»: `ApiUser`, `ApiKey`, `UserName` и **whitelisted IP** сервера
  (в Namecheap: Profile → Tools → API Access → Whitelisted IPs добавить внешний IP сервера).
- **API курсов валют** — exchangerate.host (без ключа на MVP), кэш на сутки; курс можно переопределить вручную при вводе платежа.

## Наблюдаемость

- Логи — структурные JSON (stdout).
- Метрики Prometheus — `GET /metrics` (кол-во доменов, активные алерты, состояние circuit breaker по сервисам).
- `GET /healthz` (liveness), `GET /readyz` (Postgres+Redis).

## Бэкапы

Основной способ — снапшоты DigitalOcean. Дополнительно есть скрипт дампа:
`bash scripts/pg_dump.sh` (пишет `dump-YYYYmmdd-HHMM.sql.gz`; автоматизировать не требуется).

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

## Переменные окружения (12-factor)

Конфигурация — только через env (см. `.env.example`). Секреты **не** коммитятся; в
деплое они приходят из `.env.sops` (ops-репозиторий).

| Переменная | Обяз. | Секрет | Назначение |
|---|---|---|---|
| `ENVIRONMENT` | нет | нет | `production` / `test` / dev |
| `DEBUG` | нет | нет | режим отладки (по умолч. false) |
| `TIMEZONE` | нет | нет | таймзона отображения (Europe/Kyiv) |
| `DATABASE_URL` | **да** | нет¹ | DSN Postgres (async, `postgresql+asyncpg://…`) |
| `REDIS_URL` | **да** | нет | подключение к Redis |
| `DG_MASTER_KEY` | **да** | **да** | Fernet-ключ шифрования секретов at-rest |
| `PUBLIC_BASE_URL` | **да** | нет | публичный https-URL (OAuth/MCP redirect) |
| `HOST` / `PORT` | нет | нет | bind приложения (по умолч. `0.0.0.0:8000`) |
| `MCP_PORT` | нет | нет | порт MCP-процесса (по умолч. 9000) |
| `WORKER_PROCESSES` / `WORKER_THREADS` | нет | нет | параллелизм воркера (1 / 4) |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | да² | пароль — **да** | для контейнера Postgres в стеке |
| `DG_ADMIN_EMAIL` / `DG_ADMIN_LOGIN` | нет | нет | первый админ (`create-admin`) |
| `DG_ADMIN_PASSWORD` | нет | **да** | пароль первого админа |
| `GOOGLE_CLIENT_ID` | нет | нет | вход через Google (пусто → выключено) |
| `GOOGLE_CLIENT_SECRET` | нет | **да** | секрет OAuth-клиента Google |
| `GOOGLE_REDIRECT_URI` | нет | нет | redirect URI Google (пусто → из запроса) |
| `HTTP_PORT` | нет | нет | порт локального nginx (только dev-compose) |

¹ содержит пароль БД — в проде хранить как секрет. ² нужны, только если Postgres поднимается в этом же стеке.

## Секреты

Секреты (ключи регистраторов/VT/бота) шифруются at-rest (Fernet, мастер-ключ
`DG_MASTER_KEY` из окружения) и никогда не попадают в git. В репозитории — только
`.env.example`. Сгенерировать мастер-ключ:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```
