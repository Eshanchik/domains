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

- [x] **T02. Auth + пользователи + RBAC + аудит.** _(2026-07-20)_
  Модели User/UserScope; argon2; логин/логаут (сессии, secure cookies);
  rate-limit и lockout на логин; роли Admin/Manager/Viewer; декораторы проверки
  скоупа (company/project); модель AuditLog + сервис записи; CRUD пользователей
  (admin) в UI; seed-скрипт первого админа.
  Тесты: доступ по ролям и скоупам, брутфорс-лимит.
  _Сделано:_ модели User/UserScope/AuditLog (+ миграция); argon2
  (`core/security`); серверные сессии в Redis (`core/sessions`, httponly/lax,
  Secure только в prod); брутфорс-лимит с lockout (`core/login_guard`, 5 попыток
  / 15 мин); роли Admin/Manager/Viewer; зависимости `require_user/require_role/
  require_scope` (`app/deps`); резолвер скоупов company→все проекты, project→один;
  аудит-сервис (`core/audit`, пароли не логируются); web-страницы: логин/логаут,
  админ-CRUD пользователей (Jinja2/HTMX, RU); идемпотентный `scripts.create_admin`
  из env (+ `make create-admin`). UserScope.company_id/project_id — пока int без
  FK (FK добавятся в T03). Тесты: 22 шт. (argon2, скоупы, логин/локаут/сессии/
  RBAC/аудит). Проверено в Docker: create-admin → логин через nginx → /users 200,
  аноним → редирект на /login, неверный пароль → 401.

## Фаза 1 — MVP

- [x] **T03. Компании, проекты, теги.** _(2026-07-20)_
  Модели Company/Project/Tag; CRUD API + HTMX-страницы; скоупы применяются;
  аудит изменений. Seed: 2 компании, 5 проектов.
  _Сделано:_ модели Company/Project/Tag (+ миграция) и добавлены отложенные из T02
  FK `user_scopes.company_id/project_id`; сервис с фильтрацией по скоупу
  (admin — всё; иначе — свои компании/проекты) и аудитом всех мутаций; web-страницы
  (список/форма) для компаний, проектов, тегов (RU, HTMX); проверка уникальности
  кода до вставки (без «отравления» async-транзакции); валидация FK-ссылок скоупов
  при создании пользователя; `scripts.seed` — идемпотентный upsert 2 компаний /
  5 проектов. Тесты: 29 (CRUD, дубль кода → 400, RBAC 403 для viewer, фильтрация
  по скоупу, теги). Проверено в Docker: seed (идемпотентен), страницы компаний/
  проектов/тегов через nginx, viewer→403 на создание компании.

- [x] **T04. Домены: модель и CRUD.** _(2026-07-20)_
  Модель Domain (+punycode/tld нормализация, field_sources, ssl_extra_hosts),
  DomainFieldHistory; карточка домена (каркас с вкладками); таблица со
  скоупами, фильтрами (компания/проект/тег/регистратор), поиском, пагинацией,
  сортировкой; bulk-операции (проект/теги/архив); экспорт CSV.
  Тесты: дедуп FQDN, IDN, история изменений полей.
  _Сделано:_ модели Domain/DomainTag/DomainFieldHistory (+миграция); нормализация
  FQDN/IDN через `idna` (`core/fqdn`, каноничная unicode-форма + punycode + tld,
  дедуп unicode↔punycode); сервис: create с дедупом, update с записью
  DomainFieldHistory для tracked-полей и `field_sources` (manual-приоритет),
  архив, bulk (проект/теги/архив со scope-проверкой), список со scope+фильтрами+
  поиском+пагинацией+сортировкой, CSV-экспорт; web: таблица с фильтрами/bulk/
  экспортом, форма, карточка с вкладками-каркасом и историей (history грузится
  eager — иначе lazy-load в шаблоне → MissingGreenlet). registrar_id/
  registrar_account_id — пока без FK (T16). Тесты: 46 (норм./IDN unit, дедуп,
  IDN-дедуп, история+рендер карточки, scope-фильтр, RBAC/scope на создание,
  bulk-архив, CSV). Проверено в Docker: создание everness.online, карточка 200,
  история, CSV, IDN-дедуп.

- [x] **T05. Импорт: single / bulk / CSV.** _(2026-07-20)_
  Ручное добавление; bulk-textarea (по строке); CSV-импорт (формат из SPEC §3.2)
  с предпросмотром, upsert по FQDN, отчётом создано/обновлено/ошибки;
  manual-поля не перетираются. Тесты: битые строки, дубли, повторный импорт.
  _Сделано:_ сервис `import_domains` (parse_bulk/parse_csv, run_import с upsert по
  FQDN, per-row отчёт created/updated/error, dry-run через SAVEPOINT — full
  rollback экспайрил бы `user` → MissingGreenlet в шаблоне); резолв проекта:
  form-default + per-row `project_code` (в рамках видимых проектов, неоднозначность
  → ошибка); manual-поля не перетираются импортом; web: форма (textarea + CSV
  upload) → предпросмотр (dry-run) → подтверждение (commit). Тесты: 55 (парсинг,
  preview-не-персистит/commit, повторный upsert, битая строка, CSV с
  project_code/tags/price, manual-сохранение, scope). Проверено в Docker:
  preview→0, commit→2, re-import→2, битая строка → ошибка.

- [x] **T06. Инфраструктура задач: очередь, планировщик, лимитер.** _(2026-07-20)_
  Dramatiq + Redis; CheckSchedule + scheduler-цикл (выборка созревших по
  (type, next_check_at), джиттер, батчи); модуль rate_limiter (токен-бакеты в
  Redis: per-service и per-TLD, дневные бюджеты); retry с экспоненциальным
  backoff; circuit breaker; идемпотентность задач (locks).
  Тесты: лимитер под конкуренцией, backoff, повтор задачи без дублей.
  _Сделано:_ `core/rate_limiter` (атомарные Lua токен-бакет + дневной бюджет),
  `core/retry` (async backoff+jitter, RetryError), `core/circuit_breaker`
  (Redis, closed/open/half-open), `core/locks` (SET NX + токен, compare-del
  release, ctx-manager); модель `CheckSchedule` (PK domain_id+type, индекс
  (type,next_check_at)) + миграция; Dramatiq RedisBroker + актор `run_check`
  (no-op до T07); `scheduler/service` (enqueue_due с локами и джиттером +
  backfill) и рабочий цикл `scheduler/main`; worker-entrypoint → `dramatiq`.
  Тесты: 72 (лимитер: capacity/refill/бюджет/конкуренция=exactly-N; retry;
  breaker; locks; scheduler: backfill идемпотентен, dispatch+advance, повтор
  без дублей). Проверено в Docker: домен → scheduler enqueue → worker
  обрабатывает run_check(rdap/ssl/vt) через очередь Redis.

- [x] **T07. Проверка expiry: RDAP + WHOIS fallback.** _(2026-07-20)_
  IANA bootstrap с кэшем; парсинг RDAP (expiry, статусы, NS, registrant);
  WHOIS-fallback через библиотеку; запись CheckResult (партиции по месяцам) +
  обновление Domain c source=rdap; `stale` при сбоях.
  Тесты: моки RDAP/WHOIS, таймауты/429/503 → stale, смена expiry → history.
  _Сделано:_ `checks/rdap` (IANA bootstrap с кэшем в Redis, base_for_tld,
  query_domain с 404→NotFound / 429,5xx,timeout→RdapError, parse_rdap), `checks/
  whois` (python-whois в потоке, mockable `_whois_lookup`), `checks/expiry`
  (RDAP→WHOIS fallback с токен-бакетом + circuit breaker + retry; stale не стирает
  данные; manual-поля не перетираются; DomainFieldHistory на tracked-поля);
  партиционированная по месяцам `check_result` (ручная миграция PARTITION BY RANGE,
  runtime `ensure_partition`, env.include_object скрывает от autogenerate);
  актор `run_check` диспатчит rdap → expiry. Тесты: 86 (RDAP parse/404/429/503/
  timeout, WHOIS parse/ошибки, expiry success/whois-fallback/stale-без-стирания/
  manual-preserve). Проверено в Docker на реальном RDAP: everness.online →
  expiry 2027-01-29, NS Cloudflare, source=rdap, check_result записан.

- [x] **T08. Проверка SSL.** _(2026-07-20)_
  Хосты: apex + www + ssl_extra_hosts; получение серта (даты, издатель, SAN,
  ошибки цепочки); SslCertificate + CheckResult; ежедневное расписание.
  Тесты: мок TLS-эндпоинтов, истёкший/самоподписанный/недоступный.
  _Сделано:_ `checks/ssl_check` — `hosts_for` (apex+www+extra, punycode, дедуп),
  `_fetch_der` (unverified handshake для получения серта даже при ошибке +
  отдельный verifying handshake для chain/verify-ошибки; network-seam для моков),
  `parse_cert` (cryptography: issuer/valid_from/valid_to/SAN), `check_host`
  (expired→fail, verify/handshake/unreachable→warn, иначе ok), `run_ssl_check`
  (токен-бакет, per-host SslCertificate + summary CheckResult, overall=worst);
  модель SslCertificate (+миграция); актор диспатчит ssl. Тесты: 92 (parse/hosts
  unit; valid→ok, expired→fail, self-signed→warn, unreachable→warn+записан).
  Проверено в Docker на реальном TLS: everness.online + www → серт Google Trust
  Services, valid_to 2026-08-25, status ok.

- [x] **T09. VirusTotal.** _(2026-07-20)_
  Клиент `GET /domains/{fqdn}`; глобальная очередь под бюджет free-ключа
  (4/мин, 500/день) — воркер тянет следующий домен по кругу; VtResult +
  CheckResult; ключ — в Setting (шифрован). Тесты: соблюдение бюджета
  (fake clock), 429 → пауза, детект → событие для алертера.
  _Сделано:_ `core/crypto` (Fernet at-rest, mask); модели Setting/VtResult
  (+миграция); `services/settings_store` (get/set/masked зашифрованных секретов);
  `checks/vt` (query_vt: 429/401/5xx/timeout→VtError, 404→нет детектов; бюджет:
  per-minute токен-бакет 4/мин + дневной 500/день; circuit breaker; malicious≥1→
  fail, suspicious≥1→warn; VtResult+CheckResult; сбой→stale); актор диспатчит vt;
  админ-страница `/settings` (VT-ключ и TG-токен, маскирование, пустое поле не
  затирает). Тесты: 100 (crypto; vt not_configured/ok/detection→fail/429→stale/
  per-min budget=4). Проверено в Docker: сохранение VT-ключа → маска `MYSE***`,
  в БД зашифровано (без утечки plaintext). Реальный вызов VT — на деплое (ключ).

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