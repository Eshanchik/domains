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

- [x] **T10. Health-checks (кастомные URL).** _(2026-07-20)_
  Модель HealthCheck/HealthCheckResult; CRUD в карточке домена; шаблонное
  массовое добавление к выборке (`{fqdn}` в URL); воркер: запрос без/с
  редиректами, проверка статуса + Location-паттерна + body-подстроки;
  state-машина up/down/unknown, N подряд неудач → событие down,
  восстановление → recovered. Тесты: сценарий редиректа
  `/click?pid=1&offer_id=625` → 302 + Location-паттерн; флаппинг не алертит
  до порога; recovered гасит событие.
  _Сделано:_ модели HealthCheck (свой next_check_at/state/consecutive_failures) и
  HealthCheckResult (+миграция); `checks/healthcheck` (status_matches "301,302"/
  "200-299", pattern_matches regex→substring, _perform GET/HEAD с/без редиректов +
  Location + body-substring, state-машина up/down/unknown с порогом, транзишены
  down/recovered); `services/healthchecks` (CRUD + bulk-шаблон с `{fqdn}`);
  scheduler `enqueue_due_healthchecks` + актор `run_healthcheck`; UI в карточке
  (список+статус+добавить+удалить) и страница массового добавления. Тесты: 107
  (matching unit; redirect→up, флаппинг<порога не down, порог→down→recovered,
  bulk-шаблон подставляет fqdn). Проверено в Docker на реальном
  www.forgeofreason.com/click → 302, state=up.

- [x] **T11. Каналы уведомлений: Telegram + маршрутизация.** _(2026-07-20)_
  Плагинный интерфейс канала; Telegram-канал (общий бот из Setting,
  chat_id per-канал, config шифрован); привязка канала к company/project/global,
  режим instant/digest/both; резолвер domain→project→company→global;
  тест-отправка из UI; NotificationLog; отправка через очередь с ретраями.
  Тесты: резолвер по всем уровням, мок Bot API, ретраи при 429.
  _Сделано:_ `channels/base` (интерфейс NotificationChannel + Channel/Transient
  ошибки), `channels/telegram` (Bot API sendMessage, 429/5xx→transient);
  модели NotificationChannel/NotificationLog (+миграция); `services/notifications`
  (CRUD с шифрованием config, резолвер project→company→global с mode-фильтром
  instant/digest, send_to_channel с retry на transient + NotificationLog); актор
  `send_notification` (очередь notifications); UI /channels (создание с уровнем/
  режимом, тест-отправка, удаление). Тесты: 113 (резолвер по уровням+mode,
  send success/429-retry/not-configured/failed, config зашифрован). Проверено в
  Docker: канал создан, config зашифрован, тест-отправка без бота — graceful
  fail. Реальная доставка — на деплое (токен бота).

- [x] **T12. Правила алертов + события + дедуп.** _(2026-07-20)_
  AlertRule (условия expiry≤N, ssl≤N, vt_malicious≥1, health down/recovered;
  пороги по умолчанию 60/30/14/7/1 и 30/14/7/3/1); движок: оценка после каждой
  проверки; AlertEvent с dedupe-key и state active/resolved; переход порога =
  новое уведомление; severity: high (VT, health down, expiry≤7) → instant,
  остальное → digest. Русские шаблоны сообщений.
  Тесты: нет спама при повторных прогонах, переходы порогов, resolve.
  _Сделано:_ модели AlertRule/AlertEvent (+миграция; частичный UNIQUE-индекс на
  dedupe_key WHERE state='active' → дедуп); `services/alerts` (evaluate_expiry/
  ssl/vt/health с порогами и dedup-ключами, resolve при renew/clean/recovered,
  переход порога = новое событие; severity high для VT/health-down/expiry≤7;
  RU-шаблоны; dispatch_instant по резолверу каналов; evaluate_after_check/
  after_healthcheck читают последние результаты); интеграция в актор после каждой
  проверки; страница /alerts (активные события по скоупу). Тесты: 120 (fire-once/
  no-spam, переход порога→новое событие, high≤7, resolve при renew, VT high→
  resolve, health down→recovered, dispatch instant). Проверено в Docker:
  near-expiry → high expiry-событие (days=4), видно на /alerts.

- [x] **T13. Daily digest.** _(2026-07-20)_
  Сборка сводки по каналу (истекающие домены/SSL, активные VT, health down),
  отправка по digest_time (Europe/Kyiv); идемпотентность за день.
  Тесты: состав сводки по скоупу канала, повторный запуск не дублирует.
  _Сделано:_ `services/digest` (scoped_domain_ids project/company/global,
  compose_digest группирует активные AlertEvents по kind в RU-сводку, None если
  пусто, run_digests шлёт каналам с mode digest/both и digest_time==текущей минуте
  Kyiv, идемпотентность через Redis SET NX per (channel,day)); интеграция в
  scheduler-цикл (Europe/Kyiv через zoneinfo, dep tzdata). Без новых моделей/
  миграций. Тесты: 124 (сводка по скоупу проекта, пустая→None, идемпотентность за
  день, только в свою минуту). Проверено в Docker: scheduler стартует (tzdata),
  сводка «Истекают домены (1): everness.online (25 дн.)».

- [x] **T14. Дашборд.** _(2026-07-20)_
  Обзорная страница (счётчики из SPEC FR-UI-1 с разбивкой по компаниям/проектам);
  доработка таблицы доменов (фильтры «истекает до», «VT-детект», «health down»);
  карточка домена: вкладки проверок/health/алертов заполнены.
  Приёмка: <1с на 10k синтетических доменов (seed-генератор).
  _Сделано:_ `services/dashboard.build_overview` (агрегатные счётчики по индексам:
  всего/истекает 7-30-90/SSL-проблемы/VT-детекты/health-down + разбивка по
  компаниям, со скоупом); обзорная главная (плитки + таблица по компаниям);
  фильтры доменов `vt_detect`/`health_down` (+ существующий «истекает до»);
  карточка дополнена секциями «Активные алерты», «Последние проверки»,
  «История»; `scripts.seed_bulk` — генератор N доменов batched core-insert.
  Тесты: 127 (счётчики overview, фильтры vt/health). Проверено в Docker: 10k
  доменов сгенерированы за ~3с; дашборд рендерится **0.034с**, таблица 0.057с
  (< 1с приёмка).

- [x] **T15. Учёт стоимости.** _(2026-07-20)_
  Поля цены у домена; Payment CRUD (в карточке + при CSV-импорте);
  клиент API курсов с кэшем + ручное переопределение, фиксация rate_to_usd;
  сводка расходов по компании/проекту/регистратору за период; прогноз
  ближайших продлений. Тесты: конвертация, сводки, недоступность API курсов.
  _Сделано:_ модель Payment (+миграция, фиксирует rate_to_usd/amount_usd);
  `services/rates` (exchangerate.host, кэш per (currency,day) в Redis, USD=1,
  сбой API→None); `services/payments` (add_payment: USD→1 / override / авто-курс,
  RateUnavailableError; cost_summary по company/project/registrar за период со
  скоупом; upcoming_renewals ≤N дней с ценой); web: платежи в карточке (список+
  форма) и страница /costs (сводка + прогноз). Тесты: 136 (rate USD/fetch+cache/
  API-fail, payment USD/EUR/override/rate-unavailable, summary, forecast).
  Проверено в Docker: USD 12.50 и UAH 500@0.025 → оба $12.50, итог /costs = 25.00.

- [x] **T16. Коннектор Namecheap + аккаунты регистраторов.** _(2026-07-20)_
  Registrar/RegistrarAccount (credentials шифрованы, маскирование в UI/логах);
  интерфейс RegistrarConnector; Namecheap: getList с пагинацией, expiry,
  auto-renew; синк: upsert, manual не перетирается, новые домены → очередь
  «неразобранные» с UI назначения проекта; ручной и периодический запуск синка.
  Тесты: мок API, пагинация, слияние источников, ошибки авторизации.
  _Сделано:_ модели Registrar/RegistrarAccount/UnassignedDomain (+миграция) и
  добавлены отложенные из T04 FK domains.registrar_id/registrar_account_id;
  `connectors/base` (RegistrarConnector) + `connectors/namecheap` (getList XML,
  пагинация, парсинг expiry/auto-renew, Status=ERROR→ConnectorError); `services/
  registrars` (CRUD с шифрованием creds; sync_account: merge существующих —
  manual не перетирается + история, staging новых через ON CONFLICT; ошибка API→
  status=error без падения; assign_to_project промоутит из очереди); web
  /registrars и /unassigned; актор sync_registrar_account (ручной запуск) +
  периодический enqueue в scheduler (интервал 6ч). Тесты: 144 (namecheap parse/
  pagination/auth-error; sync merge+stage/manual-safe/auth-error/assign; creds
  зашифрованы). Проверено в Docker: аккаунт (creds зашифрованы), синк против
  реального Namecheap с плохим ключом → graceful error; assign создаёт домен.

- [x] **T17. Retention, метрики, полировка MVP.** _(2026-07-20)_
  Фоновая чистка партиций >12 мес.; Prometheus-эндпоинт (глубина очередей,
  задержки проверок, ошибки внешних API, срабатывания circuit breaker);
  структурные JSON-логи; прогон всех AC из SPEC §10; README (установка,
  .env, первый запуск, whitelist IP для Namecheap); опциональный скрипт pg_dump.
  _Сделано:_ `services/retention` (drop_old_partitions по pg_inherits >12 мес.,
  prune_health_results, run_retention) + ежедневный запуск в scheduler (~03:00
  Kyiv, идемпотентно); `/metrics` (Prometheus-текст: dg_domains_total,
  dg_active_alerts_total, circuit_breaker open/failures по сервисам);
  JSON-логи включены в app factory (`configure_logging`); README расширен
  (первый запуск, внешние интеграции, whitelist IP Namecheap, наблюдаемость,
  бэкапы); `scripts/pg_dump.sh`. Тесты: 148 (retention drop/prune/ensure,
  /metrics). Проверено в Docker: `docker compose up` с нуля — все сервисы healthy,
  /metrics/healthz/readyz 200.

## Фаза 2

- [x] **T18. Коннектор GoDaddy** (через тот же интерфейс). _(2026-07-21)_
  `connectors/godaddy` (GET /v1/domains, sso-key auth, marker-пагинация, parse
  expires/renewAuto, 401/403/429/5xx→ConnectorError); сервис регистраторов
  обобщён (create_account по connector_type, build_connector диспатчит namecheap/
  godaddy, build_account_connector по registrar.connector_type); UI /registrars —
  отдельная форма GoDaddy + колонка «Регистратор». Тесты: 153 (godaddy parse/
  pagination/auth/sso-key header, dispatch на GoDaddyConnector, creds зашифрованы).
- [x] **T19. DNS/NS-мониторинг** (резолвинг A/AAAA/NS/MX, алерт на смену NS). _(2026-07-21)_
  `checks/dns_check` (dnspython async, резолв A/AAAA/NS/MX, снапшот в check_result
  type='dns', пусто→stale); `alerts.evaluate_dns` (сравнение NS последних двух
  снапшотов → событие `ns_change` high, дедуп по новому NS-набору, прошлое
  резолвится) + RU-шаблон; тип `dns` добавлен в актор-диспатч, scheduler
  DEFAULT_TYPES и интервалы (1 день). Тесты: 157 (снапшот, ns_change→high alert,
  стабильные NS→без алерта, unresolvable→stale). Без миграции (dns — строковое
  значение enum).
- [x] **T20. Каналы Slack, Discord, generic Webhook.** _(2026-07-21)_
  `channels/webhook` (Slack `{text}`, Discord `{content}`, generic `{text}`; общий
  POST, 429/5xx→transient, 4xx→ChannelError); сервис уведомлений обобщён:
  `create_channel_typed` + `channel_config`/`channel_target`, `_build_impl`
  диспатчит по типу канала (telegram/slack/discord/webhook), URL вебхука шифруется;
  UI /channels — селектор типа + поле webhook_url, показ типа/назначения (host
  без токена). Тесты: 166 (payload-формы, success/204/429-500-503→transient/4xx,
  отправка через slack-вебхук, скрытие токена в target). Без миграции.
- [x] **T21. API-токены + исходящие вебхуки на события.** _(2026-07-21)_
  Модели ApiToken (SHA-256 hash, префикс, revoke) и WebhookEndpoint (URL, секрет
  шифрован, фильтр событий) + миграция; `services/api_tokens` (create→plaintext
  один раз, resolve_user по хешу, last_used); Bearer-auth `deps.api_user`; REST
  `/api/v1` (me/domains/alerts, токен-auth, скоуп); `services/webhooks` (deliver с
  HMAC-подписью `X-DomainGuard-Signature`, фильтр по kind); актор `deliver_webhooks`
  + фан-аут в worker на новые AlertEvent; UI /tokens (свои токены) и /webhooks
  (admin). Тесты: 175 (token auth valid/invalid/revoked/scoped API, webhook
  sign/filter/secret-encrypted/delete).
- [x] **T22. 2FA (TOTP) для admin; наблюдаемость (Grafana-дэшборды).** _(2026-07-21)_
  2FA: поля User.totp_secret_enc (шифрован) + totp_enabled (+миграция с
  server_default для существующих строк); `services/twofa` (pyotp: секрет,
  provisioning URI, verify, begin/enable/disable); интеграция в `authenticate`
  (totp_required/totp_invalid, брутфорс-счётчик на неверный код); login-форма с
  полем кода + self-service страница /2fa (QR-секрет, включить/отключить).
  Наблюдаемость: `docker-compose.observability.yml` (Prometheus скрейпит /metrics
  + Grafana), `monitoring/` (prometheus.yml, provisioning datasource/dashboards,
  дашборд domainguard.json). Тесты: 180 (verify, enable-требует-код, секрет
  зашифрован, login 2-факторный поток, без-2FA-обычный вход).

## Фаза 3

- [x] **T23. Новый UI** (единый дизайн поверх существующих шаблонов/API). _(2026-07-21)_
  Введена дизайн-система на том же стеке (Jinja2 + HTMX + Tailwind, Play CDN, без
  шага сборки): `base.html` переписан — боковое меню (сгруппированное по разделам,
  с иконками и подсветкой активного пункта), липкий топбар, переключатель тёмной
  темы (сохраняется в localStorage, без мигания при загрузке), а весь дизайн
  вынесен в Tailwind-слой компонентов (`.card`, `.btn-*`, `.badge-*`, `.input`,
  `.dg-table`, `.stat`, `.nav-link` …). Новый `templates/_components.html` —
  библиотека макросов (`icon`, `badge`, `status_badge`, `page_header`, `flash`,
  `stat`, `empty_row`). Все 24 контентных шаблона переведены на дизайн-систему
  без изменения логики/переменных/маршрутов/русских текстов; страница входа —
  отдельная центрированная раскладка. Проверено вживую (все 20 страниц → 200,
  светлая и тёмная темы, маскирование секретов сохранено). Тесты: 180 зелёных,
  ruff+format чисто. Без миграций и изменений API.

## Фаза 4 — доработки существующего

- [x] **T25. Список доменов: колонки + строчные действия.** _(2026-07-21)_
  Колонки `/domains`: Домен, **Проект(название)**, Истекает, **SSL**, **Auto-renew**,
  Активен + столбец действий. `services/domains.ssl_status_map` (DISTINCT ON —
  последний серт на домен, без N+1) → бейдж ok/скоро/истёк/проблема; auto_renew
  да/нет/неизвестно; имя проекта через `project_names`. Меню **«⋮»** (нативный
  `<details>`, без клиппинга) — Открыть, Изменить, **Проверить сейчас** (HTMX,
  ставит rdap/ssl/vt/dns в очередь, аудит `check_now`), В архив/Из архива; действия
  на HTMX, чтобы не вкладывать формы в bulk-форму; Manager+ на мутации.
  Новый endpoint `POST /domains/{id}/check`. Тесты 184 (ssl-классификация,
  enqueue+audit, новые колонки/имя проекта, check-now только Manager+). Проверено
  вживую (бейджи, дропдаун, «Поставлено в очередь ✓»). Без миграций.
  Колонки таблицы `/domains` меняются с «Домен, Проект(ID), Истекает, Теги,
  Активен» на **«Домен, Проект(название), Истекает, SSL, Auto-renew, Активен»**:
  - Проект — по имени (map `project_id → name`, а не сырой ID).
  - SSL — бейдж по последнему `ssl_certificates` домена (ok / скоро истекает /
    проблема-или-нет данных); в `list_domains` добавить подзапрос последнего
    серта (без N+1).
  - Auto-renew — да / нет / неизвестно (`Domain.auto_renew`).
  - Меню **«⋮»** в каждой строке (HTMX-dropdown) с быстрыми действиями без
    захода в домен: Открыть, Изменить, **Проверить сейчас**, В архив / Из архива —
    с учётом роли (Manager+ для мутаций). Нужен новый endpoint «проверить сейчас»
    (ставит rdap/ssl/vt/dns в очередь для домена, скоуп-проверка).
  - DoD: тесты (рендер новых колонок, имя проекта, SSL-бейдж по данным,
    «проверить сейчас» ставит задачи в очередь и уважает скоуп, archive из меню);
    без изменения схемы БД (только чтение ssl_certificates).

- [x] **T26. Фильтры доменов применяются сразу + явный пустой список.** _(2026-07-21)_
  Селекты (компания/проект/тег/истекает) и чекбокс «архив» авто-сабмитятся при
  изменении (`onchange="this.form.requestSubmit()"`); текстовый поиск — по Enter/
  кнопке. Пустой результат показывает «Домены не найдены.» (серверная фильтрация
  по `project_id` и так корректна — чинился UX «ничего не переключается»). Тест 185
  (пустой проект → 200 + текст пустого состояния; проект с доменом → только он).
  Селекты фильтра (компания/проект/тег/истекает) авто-сабмитятся при выборе
  (`onchange`), не требуя кнопки «Фильтр»; при пустом результате всегда виден
  текст «Домены не найдены.». (Серверная фильтрация по `project_id` уже корректна —
  чинится именно UX «ничего не переключается».)
  - DoD: тест — фильтр по проекту без доменов → 200 и текст пустого состояния;
    фильтр по проекту с доменами → в списке только они.

- [x] **T27. Удаление демо/мок-данных.** _(2026-07-21)_
  `scripts/purge_demo.py` — идемпотентно удаляет демо ACME/Globex: проект удаляется
  только если у него **0 доменов**, компания — только когда не осталось проектов
  (Core-delete, без ORM-каскада). Реальные данные не трогаются. На проде: удалены
  Globex (2 пустых проекта) + пустые ACME Shop/Blog; **ACME Web оставлен** (в нём
  реальный домен, значит и компания ACME Corp сохранена) — Adera и 47 доменов
  нетронуты. Seed-скрипты (`seed.py`, `seed_bulk.py`) помечены DEV ONLY и не
  запускаются при `ENVIRONMENT=production` без `DG_ALLOW_SEED=1`. Тесты 188
  (удаляются только пустые демо, реальные проекты/домены сохранены, идемпотентность,
  guard блокирует прод).
  Убрать демо-компании **ACME Corp** и **Globex** и их проекты (данные `seed.py`)
  из прода безопасным идемпотентным скриптом: удалять только проекты/компании
  **без доменов**, реальные данные (Adera и 47 доменов) не трогать. Seed-скрипты
  (`seed.py`, `seed_bulk.py`, `make seed`) явно помечаются dev-only и остаются вне
  пути деплоя.
  - DoD: скрипт `scripts/purge_demo.py` с проверкой «нет доменов у проекта»,
    идемпотентный; тест на скрипт; после запуска в проде — только реальные данные.

- [ ] **T28. Детальная страница алерта.**
  Клик по строке в `/alerts` ведёт на `/alerts/{id}` с подробностями: домен
  (ссылка на карточку), тип (`kind`), severity, состояние, время срабатывания и
  резолва, разбор `payload_json` (пороги/значения), связанные последние проверки.
  Действие «Резолв» на странице (если ещё нет — добавить в сервис alerts).
  Скоуп-доступ как на списке.
  - DoD: тесты (открытие деталей внутри скоупа → 200 и поля; чужой скоуп →
    редирект/403; payload рендерится; резолв закрывает событие).

- [ ] **T29. Бриф для Claude Design: стиль Terminal UI / CLI Aesthetics / Matrix.**
  Документ `docs/design/terminal-ui-brief.md` — подробный промт/бриф для Claude
  Design в эстетике «терминал/CLI/Matrix»: философия стиля, палитра (фосфор-зелёный
  и янтарь на near-black, matrix-акценты), типографика (моноширинный шрифт,
  лигатуры), сетка и плотность, компоненты (таблицы как вывод CLI, строка-подсказка
  `$`, ASCII-рамки, мигающая каретка, опц. scanline/CRT), состояния и бейджи как
  `[OK]`/`[WARN]`/`[FAIL]`, доступность и ограничения (сохранить семантику,
  русские тексты, роли), маппинг на конкретные страницы DomainGuard. Это
  deliverable-документ (бриф), не переверстка приложения.
  - DoD: `docs/design/terminal-ui-brief.md` готов, самодостаточен (его можно
    отдать в Claude Design как есть), покрывает все ключевые экраны.

## Backlog / находки

- Точная глубина очередей Dramatiq и латентность проверок в `/metrics` — требуют
  инструментации акторов; пока экспонируются counts + состояние circuit breaker.
- Uvicorn access-логи не в JSON (свой логгер); логи приложения/воркеров/планировщика — JSON.
- `health_check_results` — обычная таблица с retention через DELETE; при росте можно
  партиционировать по месяцам как `check_result`.