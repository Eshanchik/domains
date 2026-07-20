# PLAN — DomainGuard: план работ

> Правила: одна задача = одна ветка `task/NN-name` = один PR. Definition of Done —
> в `CLAUDE.md`. После выполнения задачи: поставить `[x]`, дату, короткое примечание.
> Не выходить за рамки задачи; находки — в Backlog внизу.

## Фаза 0 — Каркас

- [x] **T01. Скелет проекта и инфраструктура.** _(2026-07-20)_
  Repo layout по CLAUDE.md; Docker Compose (nginx, api, worker, scheduler,
  postgres:16, redis:7); Dockerfile (multi-stage, non-root); FastAPI app factory,
  `/healthz`, `/readyz`; Pydantic Settings из env + `.env.example`; Alembic init;
  ruff + pytest конфиги; Makefile (up/down/migrate/test/lint/seed);
  GitHub Actions CI (lint + tests с сервисами postgres/redis).
  Приёмка: `make up` с нуля работает, CI зелёный.
  _Сделано:_ полный каркас `app/` + пакеты подсистем; app factory с `/healthz`
  (liveness) и `/readyz` (проверяет Postgres+Redis, 503 при сбое); Pydantic
  Settings (секреты не в repr, SEC-2); async engine + Redis client; JSON-логи;
  плейсхолдер-энтрипоинты worker/scheduler (полноценная очередь — T06);
  Alembic (async env, target metadata из `app.models.Base`); multi-stage
  non-root Dockerfile + один entrypoint на роли (api/worker/scheduler/migrate);
  compose с healthchecks и одноразовым `migrate`; nginx-реверс-прокси;
  Makefile, `.env.example`, `.dockerignore`/`.gitignore`, seed-стаб, README.
  Проверено локально: `docker compose up` с нуля — все сервисы healthy,
  `/healthz`→200, `/readyz`→`{database:ok,redis:ok}`; `ruff check`+`format`
  чисто; `pytest` 6/6 зелёные (py3.12).

- [ ] **T02. Auth + пользователи + RBAC + аудит.**
  Модели User/UserScope; argon2; логин/логаут (сессии, secure cookies);
  rate-limit и lockout на логин; роли Admin/Manager/Viewer; декораторы проверки
  скоупа (company/project); модель AuditLog + сервис записи; CRUD пользователей
  (admin) в UI; seed-скрипт первого админа.
  Тесты: доступ по ролям и скоупам, брутфорс-лимит.

## Фаза 1 — MVP

- [ ] **T03. Компании, проекты, теги.**
  Модели Company/Project/Tag; CRUD API + HTMX-страницы; скоупы применяются;
  аудит изменений. Seed: 2 компании, 5 проектов.

- [ ] **T04. Домены: модель и CRUD.**
  Модель Domain (+punycode/tld нормализация, field_sources, ssl_extra_hosts),
  DomainFieldHistory; карточка домена (каркас с вкладками); таблица со
  скоупами, фильтрами (компания/проект/тег/регистратор), поиском, пагинацией,
  сортировкой; bulk-операции (проект/теги/архив); экспорт CSV.
  Тесты: дедуп FQDN, IDN, история изменений полей.

- [ ] **T05. Импорт: single / bulk / CSV.**
  Ручное добавление; bulk-textarea (по строке); CSV-импорт (формат из SPEC §3.2)
  с предпросмотром, upsert по FQDN, отчётом создано/обновлено/ошибки;
  manual-поля не перетираются. Тесты: битые строки, дубли, повторный импорт.

- [ ] **T06. Инфраструктура задач: очередь, планировщик, лимитер.**
  Dramatiq + Redis; CheckSchedule + scheduler-цикл (выборка созревших по
  (type, next_check_at), джиттер, батчи); модуль rate_limiter (токен-бакеты в
  Redis: per-service и per-TLD, дневные бюджеты); retry с экспоненциальным
  backoff; circuit breaker; идемпотентность задач (locks).
  Тесты: лимитер под конкуренцией, backoff, повтор задачи без дублей.

- [ ] **T07. Проверка expiry: RDAP + WHOIS fallback.**
  IANA bootstrap с кэшем; парсинг RDAP (expiry, статусы, NS, registrant);
  WHOIS-fallback через библиотеку; запись CheckResult (партиции по месяцам) +
  обновление Domain c source=rdap; `stale` при сбоях.
  Тесты: моки RDAP/WHOIS, таймауты/429/503 → stale, смена expiry → history.

- [ ] **T08. Проверка SSL.**
  Хосты: apex + www + ssl_extra_hosts; получение серта (даты, издатель, SAN,
  ошибки цепочки); SslCertificate + CheckResult; ежедневное расписание.
  Тесты: мок TLS-эндпоинтов, истёкший/самоподписанный/недоступный.

- [ ] **T09. VirusTotal.**
  Клиент `GET /domains/{fqdn}`; глобальная очередь под бюджет free-ключа
  (4/мин, 500/день) — воркер тянет следующий домен по кругу; VtResult +
  CheckResult; ключ — в Setting (шифрован). Тесты: соблюдение бюджета
  (fake clock), 429 → пауза, детект → событие для алертера.

- [ ] **T10. Health-checks (кастомные URL).**
  Модель HealthCheck/HealthCheckResult; CRUD в карточке домена; шаблонное
  массовое добавление к выборке (`{fqdn}` в URL); воркер: запрос без/с
  редиректами, проверка статуса + Location-паттерна + body-подстроки;
  state-машина up/down/unknown, N подряд неудач → событие down,
  восстановление → recovered. Тесты: сценарий редиректа
  `/click?pid=1&offer_id=625` → 302 + Location-паттерн; флаппинг не алертит
  до порога; recovered гасит событие.

- [ ] **T11. Каналы уведомлений: Telegram + маршрутизация.**
  Плагинный интерфейс канала; Telegram-канал (общий бот из Setting,
  chat_id per-канал, config шифрован); привязка канала к company/project/global,
  режим instant/digest/both; резолвер domain→project→company→global;
  тест-отправка из UI; NotificationLog; отправка через очередь с ретраями.
  Тесты: резолвер по всем уровням, мок Bot API, ретраи при 429.

- [ ] **T12. Правила алертов + события + дедуп.**
  AlertRule (условия expiry≤N, ssl≤N, vt_malicious≥1, health down/recovered;
  пороги по умолчанию 60/30/14/7/1 и 30/14/7/3/1); движок: оценка после каждой
  проверки; AlertEvent с dedupe-key и state active/resolved; переход порога =
  новое уведомление; severity: high (VT, health down, expiry≤7) → instant,
  остальное → digest. Русские шаблоны сообщений.
  Тесты: нет спама при повторных прогонах, переходы порогов, resolve.

- [ ] **T13. Daily digest.**
  Сборка сводки по каналу (истекающие домены/SSL, активные VT, health down),
  отправка по digest_time (Europe/Kyiv); идемпотентность за день.
  Тесты: состав сводки по скоупу канала, повторный запуск не дублирует.

- [ ] **T14. Дашборд.**
  Обзорная страница (счётчики из SPEC FR-UI-1 с разбивкой по компаниям/проектам);
  доработка таблицы доменов (фильтры «истекает до», «VT-детект», «health down»);
  карточка домена: вкладки проверок/health/алертов заполнены.
  Приёмка: <1с на 10k синтетических доменов (seed-генератор).

- [ ] **T15. Учёт стоимости.**
  Поля цены у домена; Payment CRUD (в карточке + при CSV-импорте);
  клиент API курсов с кэшем + ручное переопределение, фиксация rate_to_usd;
  сводка расходов по компании/проекту/регистратору за период; прогноз
  ближайших продлений. Тесты: конвертация, сводки, недоступность API курсов.

- [ ] **T16. Коннектор Namecheap + аккаунты регистраторов.**
  Registrar/RegistrarAccount (credentials шифрованы, маскирование в UI/логах);
  интерфейс RegistrarConnector; Namecheap: getList с пагинацией, expiry,
  auto-renew; синк: upsert, manual не перетирается, новые домены → очередь
  «неразобранные» с UI назначения проекта; ручной и периодический запуск синка.
  Тесты: мок API, пагинация, слияние источников, ошибки авторизации.

- [ ] **T17. Retention, метрики, полировка MVP.**
  Фоновая чистка партиций >12 мес.; Prometheus-эндпоинт (глубина очередей,
  задержки проверок, ошибки внешних API, срабатывания circuit breaker);
  структурные JSON-логи; прогон всех AC из SPEC §10; README (установка,
  .env, первый запуск, whitelist IP для Namecheap); опциональный скрипт pg_dump.

## Фаза 2

- [ ] **T18. Коннектор GoDaddy** (через тот же интерфейс).
- [ ] **T19. DNS/NS-мониторинг** (резолвинг A/AAAA/NS/MX, алерт на смену NS).
- [ ] **T20. Каналы Slack, Discord, generic Webhook.**
- [ ] **T21. API-токены + исходящие вебхуки на события.**
- [ ] **T22. 2FA (TOTP) для admin; наблюдаемость (Grafana-дэшборды).**

## Фаза 3

- [ ] **T23. Новый UI** (Claude Design поверх существующих шаблонов/API).
- [ ] **T24. Доп. коннекторы/каналы по потребности.**

## Backlog / находки

- (пусто — пополняется в процессе)