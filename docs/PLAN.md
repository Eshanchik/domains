# PLAN — DomainGuard: план работ

> Правила: одна задача = одна ветка `task/NN-name` = один PR. Definition of Done —
> в `CLAUDE.md`. После выполнения задачи: поставить `[x]`, дату, короткое примечание.
> Не выходить за рамки задачи; находки — в Backlog внизу.
>
> **Текущий фокус (сентябрь 2026):** Фазы 12–15 (T64–T96) — дорожная карта «до полностью
> удобного внутреннего продукта». Основание и прод-срез — `docs/RESEARCH-2026-09.md`.
> Порядок внутри фазы = приоритет; начинать с Фазы 12.

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

- [x] **T28. Детальная страница алерта.** _(2026-07-21)_
  Строки `/alerts` кликабельны → `/alerts/{id}`: домен (ссылка на карточку), тип,
  severity, состояние, время срабатывания/резолва, разбор `payload_json` (пороги/
  значения) и последние 10 проверок домена. Действие **«Резолв»** (`POST
  /alerts/{id}/resolve`, Manager+) → `alerts.resolve_event` закрывает событие.
  Скоуп-доступ: чужой алерт → редирект на `/alerts`. Тесты 191 (рендер полей+payload,
  ссылка из списка, out-of-scope→303, резолв только Manager+). Без миграций.
  Клик по строке в `/alerts` ведёт на `/alerts/{id}` с подробностями: домен
  (ссылка на карточку), тип (`kind`), severity, состояние, время срабатывания и
  резолва, разбор `payload_json` (пороги/значения), связанные последние проверки.
  Действие «Резолв» на странице (если ещё нет — добавить в сервис alerts).
  Скоуп-доступ как на списке.
  - DoD: тесты (открытие деталей внутри скоупа → 200 и поля; чужой скоуп →
    редирект/403; payload рендерится; резолв закрывает событие).

- [x] **T29. Бриф для Claude Design: стиль Terminal UI / CLI Aesthetics / Matrix.** _(2026-07-21)_
  Готов `docs/design/terminal-ui-brief.md` — самодостаточный промт для Claude
  Design: контекст продукта и стек, философия стиля, палитра-токены (green
  phosphor + amber на near-black), типографика (mono), раскладка/ASCII-панели,
  переопределение всех классов дизайн-системы (`.card/.btn-*/.badge-*/.input/
  .dg-table/.stat/.nav-link/.flash`), мотивы (мигающая каретка, matrix rain,
  scanline — всё под `prefers-reduced-motion`), доступность, жёсткие ограничения
  (не менять маршруты/тексты/роли/поведение), экран-за-экраном маппинг всех 20
  страниц, формат поставки (тема-скин поверх `base.html`) и ASCII-скетчи. Только
  документ, приложение не переверстывалось.
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

- [x] **T30. Реализация Terminal UI (скин из Claude Design).** _(2026-07-21)_
  Импортирован проект Claude Design «Terminal prototype DomainGuard»
  (`DomainGuard Terminal.dc.html`) и применён как реальный скин целиком в
  `base.html` — **без правок 24 страничных шаблонов и без изменения логики/
  маршрутов/текстов**. Приём: (1) `dark` всегда включён + **ремап палитры Tailwind
  на `--term-*` CSS-переменные** (slate/white/emerald/red/amber/sky/brand →
  токены темы), поэтому хардкод-утилиты `slate/white` на страницах ретинтуются
  сами (dark-вариант всегда доминирует → детерминированно); (2) переписан слой
  компонентов (`.card/.btn-*/.badge-*/.input/.dg-table/.stat/.nav-link/.flash/
  .page-title/.section-title/.link/.muted`) в терминальном стиле. Две темы
  **amber (по умолч.)/green** (переключатель `[amber]/[green]`, localStorage),
  JetBrains Mono, боковое меню `> …` + секции `# …`, топбар-промпт
  `dg@domainguard:~$` с мигающей кареткой, бейджи `[OK]/[WARN]/[FAIL]`, кнопки
  `[ … ]`, CLI-таблицы с zebra/hover, matrix-дождь на входе + scanline/vignette
  (под `prefers-reduced-motion`). Проверено вживую: вход (matrix), обзор, домены
  (бейджи+kebab-дропдаун), детали алерта — обе темы. Тесты 191 зелёные (все
  страницы рендерятся), ruff+format чисто. Без миграций/изменений API.
  Применить дизайн «DomainGuard Terminal» (проект Claude Design) как реальный скин
  поверх существующих шаблонов: перекраска на уровне `base.html` (слой компонентов
  + ремап палитры Tailwind на `--term-*`), две темы amber/green, matrix-фон на
  входе, scanline/vignette, бейджи `[OK]/[WARN]/[FAIL]`, кнопки `[ … ]`, CLI-таблицы.
  Без правок страничных шаблонов и без изменения логики/маршрутов/текстов.

- [x] **T31. Фикс: пустые int-параметры фильтра доменов → 422.** _(2026-07-21)_
  Автосабмит фильтра (T26) отправляет `project_id=&expiring=` (пустые строки),
  а параметры были `int | None` → FastAPI не парсил пустую строку → 422. Приняты
  как `str | None` + хелпер `_int_or_none` (пусто/нечисло → None) в
  `domains_list` и `domains_export`. Тест 192 (точные URL из бага → 200 и фильтр
  работает; пустая компания; CSV-экспорт с пустыми параметрами).

- [x] **T32. Реализация Terminal UI «BTOP» (скин из Claude Design).** _(2026-07-21)_
  Импортирован проект Claude Design `DomainGuard Terminal - BTOP.dc.html` и применён
  целиком в `base.html` тем же приёмом (ремап палитры Tailwind на CSS-переменные +
  переписанный слой компонентов), **без правок страничных шаблонов**. Отличия от
  T30: палитра btop (сине-чёрный фон `#0b0e14`, светло-голубой текст, акцент green
  по умолчанию / amber, cyan для рамок, magenta для FAIL/HIGH); бейджи-глифы
  `✔ OK / ◆ WARN / ● FAIL / · LOW`; топбар в btop-стиле (`net ok`, `[green]/[amber]/
  [exit]`); рамочный бокс `╭─ … ─╮` на входе; секции с `╭─`. Проверено вживую
  (вход, домены — глифы/kebab). 192 теста зелёные, ruff+format чисто.

- [x] **T33. Экраны BTOP: реальные макеты страниц (не только скин).** _(2026-07-21)_
  Реализованы конкретные экраны из макета «DomainGuard Terminal - BTOP», а не
  общая перекраска: (1) **вход** — рамочный бокс `╭─ … ─╮` + загрузочная
  последовательность (`booting … ✔ OK`) + prompt-строки login/password/2fa +
  `[ ВОЙТИ ]` (реальные поля сохранены); (2) макрос `ui.panel(title, action)` —
  рамка `╭─ ЗАГОЛОВОК ──[action]─╮ … ╰──╯`; (3) **обзор** — stat-грид с
  1px-гридлайнами + рамочная таблица по компаниям; (4) **домены** — таблица в
  рамке с инлайн `[+ Добавить]`, фильтры внутри; (5) **карточка домена** —
  DOSSIER (key:value) в две колонки + рамочные секции; (6) **алерты** —
  лог-строки; (7) **детали алерта** — EVENT | PAYLOAD_JSON в две колонки; (8)
  **расходы/импорт** — рамочные панели. Фикс CSS `min-width:0` на рамках/грид-
  детях (иначе 120-символьная линия рамки распирала колонки и вторая уезжала за
  экран). **Фикс бага:** роуты `/users` и `/users/new` не передавали `user` в
  шаблон → страницы рендерились без сайдбара/топбара (base уходил в анонимную
  ветку); переименовал редактируемого пользователя в шаблоне `user`→`subject` и
  прокинул текущего `user`. Полный web-QA после: **57/57**, 192 автотеста зелёные.

## Фаза 5 — доработки и интеграции

- [x] **T34. Фикс: сортировка/пагинация сбрасывают фильтры + чекбокс archived в стиле терминала.** _(2026-07-21)_
  Хелпер `_active_filter_qs` в `app/web/domains.py` URL-кодирует активные фильтры
  (company/project/tag/q/expiring/archived, без sort/dir/page) → передаётся в
  шаблон как `filter_qs` и добавляется к ссылкам сортировки заголовков И пагинации,
  так что смена сортировки/страницы больше не сбрасывает фильтр. Заголовки колонок
  показывают ▲/▼ активного порядка. Чекбокс «archived» заменён на терминальный
  тоггл `[ ]`/`[x]` (класс `.term-check` в base.html: скрытый `<input>` + `.box`
  через CSS `:checked`), авто-сабмит и семантика формы сохранены. Тесты 194
  (ссылки сортировки несут фильтр и список остаётся отфильтрованным; тоггл
  стилизован). Проверено вживую (`/domains?company_id=1` → href содержит
  `company_id`, тоггл рендерится). Без миграций.
  Баг: ссылки сортировки (`?sort=…&dir=…`) и пагинации (`?page=…&sort=…&dir=…`) в
  `templates/domains/list.html` не несут остальные query-параметры фильтра
  (`company_id/project_id/tag/expiring/archived`) → при клике по заголовку колонки
  или странице фильтры теряются и список показывает все домены. Фикс: строить
  ссылки, сохраняя текущие активные фильтры (хелпер, который мёржит текущие
  query-параметры и переопределяет только `sort/dir` или `page`). Плюс: чекбокс
  «archived» — сейчас сырой `<input type="checkbox">`, не в стиле терминала;
  заменить на `[x] archived` / `[ ] archived` тоггл в btop-эстетике (как в макете),
  сохранив авто-сабмит и семантику формы.
  - DoD: тест — с выбранной компанией и сортировкой в URL присутствуют оба набора
    параметров, список отфильтрован И отсортирован; пагинация сохраняет фильтры;
    чекбокс archived работает и стилизован. Без миграций.

- [x] **T35. Аудит соответствия дизайну BTOP (пройти по всем экранам макета).** _(2026-07-21)_
  Проведён аудит всех экранов против макета `DomainGuard Terminal - BTOP.dc.html`
  (17 визуальных расхождений + 6 требующих данных). Исправлено:
  **Обзор** — stat-тайлы раскрашены по метрике (green/amber/cyan/mag вместо
  всегда-acc), `Обзор`→`ОБЗОР`, таблица «по компаниям» дополнена колонками
  **SSL-проблемы** и **Продления $/год** (2 новых агрегат-запроса в
  `dashboard.py`, USD-only для стоимости). **Домены** — пагинация в стиле макета
  (`1–N of TOTAL` + `‹ [1] ›` + активный пилл), колонка «Активен» → глифы
  `● up`/`○ down`, hover пунктов kebab-меню → акцентная заливка (`.menu-item`),
  стрелки сортировки ▲/▼. **Карточка домена** — `expires` c суффиксом `(Nd)`,
  строки `rdap:`/`ssl:` из последних проверок. **Расходы** — горизонтальные
  бары `▓▓▓░░` + проценты в сводке. **Алерты** — счётчик `· N active` в
  подзаголовке. **Детали алерта** — состояние `◆ ACTIVE`/`✔ RESOLVED` (amber/
  green вместо red). **Прочее** — `warn`→amber в `status_badge`; убраны
  скруглённые углы (twofa/healthchecks-bulk/tokens) под zero-radius терминал.
  Тесты 195 (новые поля `CompanyRow.ssl_problems/cost_usd`; T34-набор). Проверено
  вживую (скриншоты входа/обзора/доменов — совпадают с макетом), web-QA 55/56
  (единственный FAIL — нет seed-алерта для клика, не регресс; детали алерта
  отрендерены отдельно 200). Более тяжёлые расхождения — в Backlog.
  Пройти по слайдам `DomainGuard Terminal - BTOP.dc.html` (login, dashboard,
  domains, domain card, alerts, alert detail, costs, import) и по остальным
  страницам, которых нет в макете (users, channels, registrars, settings, 2FA,
  api-tokens, edit-формы), найти и починить расхождения раскладки/типографики/
  бейджей/рамок с живым UI. Особое внимание — формам (`users/form.html` и т.п.),
  которые сейчас на общих классах, а не на панельных рамках. Не менять логику/
  маршруты/тексты/роли.
  - DoD: чек-лист расхождений в задаче + фиксы; полный web-QA (`scratchpad/qa.py`)
    зелёный; ruff+format чисто; скриншоты ключевых экранов до/после (вживую).

- [x] **T36. Namecheap: тянуть цену продления через API → в расходы.** _(2026-07-21)_
  `connectors/namecheap.py`: метод `get_renewal_prices()` → `namecheap.users.getPricing`
  (ProductType=DOMAIN, ActionName=RENEW), парсер `_parse_pricing` берёт 1-летнюю
  RENEW-цену по TLD (`YourPrice`→`Price`, валюта), сетевой шов `_fetch_pricing`.
  Новый `services/pricing.py`: `get_pricing_map` (кэш TLD→цена в Redis, TTL 24ч;
  холодный кэш → фетч через токен-бакет `namecheap` + circuit breaker + `with_retry`;
  ошибка/rate-limit/circuit → `({}, error)`, не роняет) и `refresh_account_pricing`
  (применяет карту к доменам аккаунта: `registrar_account_id==acc.id & is_active`,
  ставит `renewal_price/renewal_currency`, источник `api-namecheap`, идемпотентно,
  **manual не перетирается**). Встроено в воркер `_sync_registrar_account` для
  namecheap-аккаунтов (тот же 6ч-синк; кэш экономит вызовы API). **Без миграции**
  (`renewal_price/currency` уже есть; источник — в `field_sources`). Тесты 203
  (парсер: 1yr/YourPrice/ошибка; сервис: применение по TLD, manual-safe,
  идемпотентность, кэш экономит 2-й вызов, API-ошибка→report без краха,
  circuit-open→без фетча). SPEC FR-RG-7.
  Реализовать в `connectors/namecheap.py` вызовы Namecheap API для стоимости
  продления: `namecheap.users.getPricing` (ProductType=DOMAIN, Action=RENEW) по TLD
  домена, кэш в Redis (цены меняются редко), запись в стоимость продления домена
  (источник `api`, не перетирает `manual` — правило слияния из CLAUDE.md).
  Все вызовы — через централизованный токен-бакет + retry + circuit breaker;
  ошибка API → данные `stale`, воркер не падает. API-ключ Namecheap шифруется
  at-rest (Fernet), не логируется, маскируется в UI; требует whitelisted IP
  (задокументировать в SPEC/настройках). Периодический воркер обновляет цены.
  - DoD: тесты с моками (respx) — успех, таймаут, 5xx, rate-limit; upsert цены
    не перетирает ручную; идемпотентность; миграция при изменении моделей через
    Alembic; SPEC обновлён (поле источника цены, требование IP whitelist).

- [x] **T37. Вход через Google (OAuth) — только для существующих пользователей.** _(2026-07-21)_
  Кнопка «войти через Google» на `/login` (терминальный стиль, под «или»); OAuth2
  code-flow реализован вручную на httpx (без новой зависимости, сетевой шов
  `exchange_code` мокается). `services/google_oauth.py` (authorize-URL + обмен кода
  → verified email) + `web/oauth.py`: `/auth/google/login` (state-cookie CSRF →
  redirect на Google), `/auth/google/callback` (сверка state, обмен кода, поиск
  **активного** юзера по verified email — иначе отказ, никакой саморегистрации;
  роли/скоупы из БД), `/auth/google/2fa` (если у юзера включён TOTP — pending-токен
  в Redis + форма кода, **2FA обязателен и после Google** — выбор пользователя).
  Конфиг `GOOGLE_CLIENT_ID/SECRET/REDIRECT_URI` из env (secret `repr=False`, не
  логируется; пусто → фича спит, кнопка скрыта, роуты редиректят). Проброшены в
  docker-compose (x-app-env) → работают и на проде когда заданы. Тесты 220 (+17:
  обмен кода respx — успех/5xx/нет email/нет токена; enable/disable кнопки; redirect
  на Google; существующий юзер входит; email case-insensitive; неизвестный/
  неактивный/неверифицированный/CSRF-mismatch → отказ; 2FA-поток: код обязателен,
  неверный→401, верный→сессия). Проверено вживую (кнопка на входе). SPEC AUTH-2.
  **Без миграции** (используется `User.email`). Дремлет на проде до задания
  `GOOGLE_*` (как VT/TG/Namecheap).
  Кнопка «Войти через Google» на `/login`; OAuth2 authorization-code flow
  (authlib). После колбэка ищем пользователя по verified email из Google-профиля:
  найден и активен → логиним (роли/скоупы из нашей БД); не найден/неактивен →
  отказ (никакой саморегистрации). `GOOGLE_CLIENT_ID/SECRET` из env (secret
  шифруется/не логируется), `redirect_uri` из настроек. Учесть взаимодействие с
  2FA-сессией и существующим механизмом сессий. Дизайн кнопки — в btop-стиле.
  - DoD: тесты (мок Google — успешный колбэк для существующего юзера → сессия;
    неизвестный email → отказ; невалидный state/CSRF → отказ; неверифицированный
    email → отказ); миграции при необходимости через Alembic; SPEC обновлён.

- [x] **T38. MCP-сервер DomainGuard (read + полный набор действий супер-админа).** _(2026-07-21)_
  Отдельный `mcp`-контейнер (тот же образ, `command: ["mcp"]` → `uvicorn
  app.mcp.asgi:app`), MCP поверх **streamable HTTP** (FastMCP, `mcp>=1.28`), путь
  `/mcp` через nginx (dev+prod, `proxy_buffering off` для SSE). Аутентификация —
  ASGI-middleware по **API-токену** (`api_tokens.resolve_user`), user_id в
  contextvar → инструменты грузят acting-user; нет токена → 401. `app/mcp/tools.py`
  — тестируемые функции `(session, user, …)`, вызывают **те же сервисы**, что и UI
  (скоуп + аудит автоматом); мутации требуют Manager+. 12 инструментов: read
  (whoami/overview/list_domains/get_domain/list_alerts/list_companies/costs_summary)
  + write Manager+ (create_domain/set_domain_archived/check_domain_now/resolve_alert/
  import_domains). Роль/скоуп владельца токена применяются к каждому вызову (admin →
  полный super-admin, viewer → только чтение). Тесты 230 (+10: скоуп чтения,
  Manager+-гейт, create+audit, out-of-scope→отказ, check-now enqueue, resolve,
  import dry-run, token resolve, middleware 401/контекст). **Проверено вживую**:
  контейнер поднят, initialize+tools/list (12) по токену, whoami/list_companies/
  create_domain end-to-end + запись в audit (actor=владелец токена), без токена 401.
  `docs/MCP.md` (как подключиться), SPEC FR-API-3. **Без миграции.**
  Отдельный MCP-сервер (Python, поверх нашего сервисного слоя/REST `/api/v1`),
  чтобы давать Claude задачи по сайту. Инструменты: чтение (домены/детали/алерты/
  расходы/проверки) + мутации уровня супер-админа (добавить/изменить/архивировать
  домен, «проверить сейчас», резолв алерта, импорт, управление
  пользователями/каналами/регистраторами). Аутентификация — по API-токену
  DomainGuard с ролью/скоупом (если токенов ещё нет — добавить их выпуск/хранение;
  токен-хеш at-rest). Аудит всех мутаций. Транспорт и размещение (в репо как сервис,
  запуск через compose) — определить в задаче.
  - DoD: список инструментов со схемами; авторизация по токену уважает роль/скоуп;
    мутации пишут audit; тесты (happy + отказ по правам + ошибочные входы);
    README по запуску; secret/токены не логируются.

- [x] **T39. Аудит безопасности.** _(2026-07-21)_
  Отчёт `docs/security-audit.md` (authn/authz, секреты, SSRF, инъекции, CSRF,
  заголовки, rate-limit, MCP-экспозиция, зависимости, контейнеры). Исправлены
  **high/critical**: (1) **SSRF в health-checks** — `app/core/net_guard.py`
  (`validate_public_url`: только http(s), блок приватных/reserved-адресов после
  DNS-резолва; вызывается перед каждым запросом И на каждом redirect-хопе — редиректы
  теперь следуются вручную, ≤5, с ревалидацией; `validate_scheme` на создании →
  `InvalidHealthCheckUrl`/400); (2) **security-заголовки** nginx (HSTS/nosniff/
  X-Frame-Options/Referrer-Policy); (3) **edge rate-limit** nginx на `/login` (20 r/m)
  и `/mcp` (300 r/m). Проверено вживую (заголовки на `/login`, `/mcp` 401,
  вход работает). Тесты (+9: net_guard scheme/literal/resolved-блокировка,
  worker отказывает при приватном резолве и не шлёт запрос, create отклоняет
  `file://`). Reviewed-safe: SQLAlchemy параметризован, retention DROP по
  regex-именам из pg_inherits, Jinja autoescape, argon2, cookie httponly/secure/
  samesite=lax (CSRF), токены sha256, секреты Fernet+repr=False, OAuth state+
  existing-only+2FA, non-root. Medium/low → Backlog (CSP, dep-scan, MCP IP-allowlist,
  DB least-privilege).
  Сквозной аудит: authn/authz (сессии, 2FA, роли/скоупы, новый Google-OAuth и
  MCP-токены), хранение секретов (Fernet at-rest, маскирование, отсутствие утечек
  в логах/API), внешние вызовы (SSRF в health-check URL, RDAP/WHOIS/VT/Namecheap),
  инъекции (SQLAlchemy — параметризация, шаблоны — автоэкранирование Jinja), CSRF
  на мутациях/формах, заголовки безопасности (CSP/HSTS/…), rate-limit/брутфорс
  логина, зависимости (уязвимости), права контейнеров/секреты в compose. Отчёт с
  находками и приоритетами; критичное — чинится, остальное — в Backlog.
  - DoD: `docs/security-audit.md` с находками (severity, репро, фикс/митигейшн);
    критичные исправлены с тестами; проверки зависимостей; без регрессий (тесты
    зелёные). Выполняется после T36–T38, чтобы покрыть новую поверхность.

- [x] **T40. Чистка форм/страниц под терминальный дизайн.** _(2026-07-21)_
  Жалобы по проду: (1) нативные чекбоксы у доменов выглядят чужеродно (как раньше
  archived); (2) kebab-меню `[ ⋮ ]` справа переносится по буквам вертикально;
  (3) sub-формы карточки домена (health-check/платёж) и админ-страницы (import,
  companies, tags, users, channels, registrars, settings, webhooks, tokens,
  projects, 2FA, healthchecks-bulk) — «дешёвые» плоские `.card` без рамки.
  Фиксы: глобальный ре-стайл `.checkbox` (`appearance:none` → тёмный квадрат с
  зелёной ✔) — чинит ВСЕ нативные чекбоксы разом; kebab → `[···]` в одну строку
  (`whitespace-nowrap`, `.kebab-btn` с hover); все `.card`-страницы переведены на
  рамку `{% call ui.panel(...) %}` (заголовок = панель, action-кнопка в шапке),
  убраны `text-slate-*`/`rounded-*`. Только презентация — логика/маршруты/тексты/
  роли не тронуты.
  Сделано: `.checkbox` ре-стайл + `[···]`-kebab + 15 шаблонов на `ui.panel`
  (companies/domains/projects/users/settings формы; companies/projects/users/tags/
  registrars-unassigned списки; channels/webhooks/tokens/registrars списки+формы;
  healthchecks-bulk; twofa — все ветки). Fan-out на 4 субагента по контракту +
  2 эталона. Проверено: web-QA 55/56 (все 20 страниц 200, единственный FAIL —
  нет seed-алерта), вживую скриншоты (домены: тёмные чекбоксы + `[···]`; каналы/
  регистраторы — рамочные панели). ruff чисто. Без изменений логики/маршрутов/
  текстов/ролей.
  - DoD: все страницы рендерятся (Jinja компилится), web-QA зелёный, вживую —
    чекбоксы/kebab/формы/админки в едином btop-стиле; ruff чисто. ✓

- [x] **T41. Терминальный стиль полей ввода (глобально).** _(2026-07-21)_
  Жалоба: `.input/.select/.textarea` выглядели как «тёмные квадратные куски», не
  в стиле сайта. Глобальный ре-стайл в `base.html`: inset-фон `var(--panel-2)`,
  hover→`--dim` рамка, **focus→акцентная рамка + внутренний glow + `--bg`**,
  моно-каретка `--acc`; `.select` — `appearance:none` + кастомный cyan-шеврон `▾`
  (на фокусе — акцентный); `.label::before { "> " }` — подписи читаются как
  CLI-флаги (в тон навигации). Фиксит ВСЕ поля на сайте разом (фильтры доменов,
  формы health-check/платежей/админок). Проверено вживую (фильтры доменов,
  /users/new — resting + focus-glow). Только CSS.

## Фаза 6 — продуктовые доработки

- [x] **T42. Регистратор: проект по умолчанию для всех доменов аккаунта.** _(2026-07-21)_
  Колонка `RegistrarAccount.default_project_id` (nullable FK→projects, ON DELETE
  SET NULL; миграция `124616430a20`, up/down проверены). `default_project_id`
  проброшен в `create_account`/namecheap/godaddy + формы (select «Проект по
  умолчанию» с дефолтом «— в неразобранные —») + web-роуты (`_int_or_none`).
  `sync_account`: новый домен → если у аккаунта задан дефолт-проект,
  `_create_in_project` (Domain сразу в проекте, `field_sources project_id=manual`,
  аудит, `report.created`), иначе — unassigned как раньше. Колонка «Проект по
  умолч.» в таблице аккаунтов. Тесты (синк с дефолтом → домены в проекте, не в
  unassigned; без дефолта → unassigned; manual-safe). SPEC FR-RG-8. Проверено
  вживую (формы+колонка).
  При добавлении аккаунта регистратора выбрать «Проект по умолчанию»: все домены
  этого аккаунта при синке идут сразу в этот проект (а не в «неразобранные»).
  Реализация: колонка `RegistrarAccount.default_project_id` (nullable FK →
  projects, ON DELETE SET NULL; **миграция Alembic**); select на обе формы
  добавления (namecheap/godaddy); `sync_account`: новый домен → если у аккаунта
  задан `default_project_id`, создаём Domain сразу в этом проекте
  (`field_sources project_id=manual`, аудит), иначе — как раньше в unassigned.
  Пусто = текущее поведение.
  - DoD: миграция; тесты (аккаунт с дефолт-проектом → синк создаёт домены в
    проекте, не в unassigned; без дефолта → unassigned; идемпотентность); SPEC.

- [x] **T43. Домены: быстрые кнопки-проекты вместо/поверх фильтра.** _(2026-07-21)_
  Над таблицей `/domains` — строка чип-кнопок `[ Все ]` + `[ <проект> ]` (термин.
  `.btn btn-sm`, активная = `btn-primary`). Ссылки сохраняют прочие фильтры+сортировку
  (`filter_qs_no_project` в роуте, project_id переопределяется). Селект «проект»
  из фильтр-формы убран → `hidden project_id` (сохраняется при смене др. фильтров).
  Тест (чип-ссылки на проекты, фильтрация по проекту, активная кнопка). Проверено
  (login 303 → чипы, project_id=3 → активна ACME Blog).
  На `/domains` над таблицей — строка кликабельных кнопок-проектов («Все» + по
  кнопке на проект) для мгновенного переключения фильтра по проекту (сохраняя
  прочие фильтры и сортировку). Активная кнопка подсвечена. Реализация — на
  существующем `project_id`-фильтре (кнопки = ссылки с `filter_qs`).
  - DoD: тест (клик по кнопке проекта фильтрует список; «Все» сбрасывает проект;
    активная кнопка помечена); вживую.

- [x] **T44. Алерты: показывать проект и возраст алерта.** _(2026-07-21)_
  `/alerts`: запрос джойнит Project+Company (name), роут считает возраст
  `format_age(now - fired_at)` → «3д 4ч»/«5ч 12м»/«8м». Строка лога дополнена
  колонками **PROJECT** (проект · компания, cyan/faint) и **STATE/AGE**
  (`◆ ACTIVE · <age>`), добавлена шапка колонок. Тесты (unit `format_age`
  дни/часы/минуты/clamp; integration — проект/компания/возраст в списке).
  Проверено вживую (строки: `ACME Web · ACME Corp … ◆ ACTIVE · 2д 5ч`).
  На `/alerts` для каждого алерта — из какого он **проекта** (и компании) и **как
  долго активен** (возраст от `fired_at`, компактно: «3д 4ч» / «12м»). Добавить
  колонку/поле проекта (join domain→project→company) и возраст.
  - DoD: тест (проект отображается, возраст считается корректно); вживую.

## Фаза 7 — OAuth-доступ к MCP

- [x] **T45. Права на MCP у пользователя (админка).** _(2026-07-21)_
  Колонка `User.mcp_allowed` (bool, default false; миграция `c309a4097dc4` со
  `server_default false` + снятие дефолта, up/down проверены). Хелпер
  `auth.user_may_use_mcp(user)` = `role==admin or mcp_allowed` (**админы — всегда**).
  Чекбокс «Разрешён MCP» на форме пользователя (create+edit), колонка «MCP» в списке.
  Схемы `UserCreate/UserUpdate` + роуты (`mcp_allowed` из формы) + аудит diff.
  Тесты (unit helper: админ всегда / флаг гейтит не-админа; integration: create с
  флагом → true, edit без чекбокса → false). Основа для T46.

- [x] **T46. OAuth-сервер для MCP (claude.ai custom connector).** _(2026-07-21)_
  `/mcp` теперь OAuth-защищён (FastMCP `auth_server_provider`+`AuthSettings`).
  `oauth_provider.DomainGuardOAuthProvider` (реализует все 9 методов; клиенты/коды/
  токены в Redis через `oauth_store`; `load_access_token` принимает **и** OAuth-токены,
  **и** `dg_` API-токены). `authorize` кладёт запрос в Redis и редиректит на наш
  `/oauth/consent` (api-app): требует логин (`?next=`), проверяет `user_may_use_mcp`,
  «Разрешить/Отклонить» → минтит код → редирект на redirect_uri клиента. Токены несут
  `subject=user_id`; инструменты берут юзера из `get_access_token().subject`.
  Старый `TokenAuthMiddleware`/contextvar удалены. nginx: `/.well-known/oauth-*`,
  `/authorize|token|register|revoke` → mcp-контейнер. DNS-rebinding guard SDK выключен
  (nginx enforces server_name+TLS, клиент подключается server-side). `PUBLIC_BASE_URL`
  в env. Проверено вживую: 401+RFC9728 metadata, AS-metadata, DCR, `/authorize`→consent,
  consent рендерится (login+flag), **API-токен по-прежнему работает** + tool-вызов
  резолвит юзера. Тесты (provider load_access_token api-fallback + store; consent:
  login-gate/flag-deny/approve-issues-code/deny-error; login `next` local-only).
  `/mcp` становится OAuth-защищённым: в claude.ai вписываешь только URL → редирект
  на наш сервис → логин + экран согласия (проверка `user_may_use_mcp`) →
  «Разрешить/Отклонить» → Claude получает токен. Реализация: провайдер
  `OAuthAuthorizationServerProvider` (клиенты через DCR, коды/токены в Redis),
  `AuthSettings`/`auth_server_provider` у FastMCP (mounts metadata/authorize/token/
  register/revoke), экран согласия на нашем UI (сессия+флаг), `load_access_token`
  принимает **и** OAuth-токены, **и** существующие API-токены (`dg_…`) — оба способа
  (реш. пользователя). nginx: проброс `/.well-known/oauth-*`, `/authorize`, `/token`,
  `/register`, `/revoke` на mcp-контейнер. Токен привязан к юзеру → роль/скоуп/аудит
  как сейчас.
  - DoD: metadata-discovery отдаётся; DCR регистрирует клиента; неавторизованный →
    login; юзер без права → отказ на согласии; выданный токен работает на `/mcp`;
    API-токен по-прежнему работает; тесты; вживую подключение из claude.ai.

- [x] **T47. GoDaddy: синк только ACTIVE + чистка мёртвых + метка источника.** _(2026-07-21)_
  Находка: GoDaddy API (`GET /v1/domains`) отдаёт **все** домены аккаунта, включая
  истёкшие/отменённые (которые UI прячет) → в систему заезжало «кладбище» с датами
  2018–2021, раздувая «истекает ≤N». Фиксы: (1) коннектор GoDaddy пропускает
  домены со `status != ACTIVE` (отсутствие статуса → берём, backward-safe);
  (2) `registrars.archive_expired(connector_type/account_id, apply)` — архивирует
  активные домены с `expiry_date < now` (скоуп по коннектору/аккаунту), + скрипт
  `scripts/archive_dead.py` (dry-run по умолчанию, `--apply`); (3) метка источника
  теперь per-connector (`api-godaddy`/`api-namecheap` через `_account_source`),
  а не захардкоженный `api-namecheap`. Тесты (коннектор пропускает EXPIRED/CANCELLED,
  берёт ACTIVE+без-статуса; синк godaddy → source `api-godaddy`; archive_expired —
  прошлые архивируются, будущие нет). На проде: dry-run → `--apply` для аккаунта
  GoDaddy.

## Фаза 8 — UX алертов и каналов

- [x] **T48. Каналы: «Отправить сейчас» + переслать алерт в канал.** _(2026-07-22)_
  `POST /channels/{id}/send-now` (compose_digest → send_to_channel, flash sent=ok/fail/none)
  + кнопка «Отправить сейчас» в списке каналов. `POST /alerts/{id}/notify` (build_message →
  resolve_channels(instant) → send, Manager+) + кнопка «Переслать в канал» на детали алерта
  + flash `notified=N`. Тесты 4 (send-now none/ok/admin-gate; alert notify → notified=1).
  Кнопка «Отправить сейчас» у канала (`POST /channels/{id}/send-now` → `compose_digest`
  → `send_notification`), чтобы дёрнуть сводку вручную (сейчас это делается скриптом).
  На детали алерта — «Переслать в канал» (`build_message` → resolved-каналы домена),
  Manager+. Тесты (send-now ставит в очередь; resend диспатчит; права).
  - DoD: кнопки в UI, дистпатч в очередь, скоуп/роль, тесты.

- [x] **T49. Фильтры на вкладке Алерты.** _(2026-07-22)_
  `/alerts` принимает `company_id/project_id/severity/kind` (str→int coerce, пустые →
  без фильтра), фильтрует запрос (join уже есть). Строка селектов над списком
  (авто-сабмит) + «Сбросить». Тест (фильтрация по компании/проекту/severity/типу +
  пустые params не 422).
  На `/alerts` — фильтры по компании/проекту/severity/типу (селекты над списком),
  на существующем запросе (join уже есть). Пустые значения → без фильтра.
  - DoD: тест (фильтрация по каждому измерению), вживую.

- [x] **T50. Нормальный/понятный формат алертов и сводки.** _(2026-07-22)_
  `build_message` — многострочный формат: `🔴 HIGH · истекает домен` / 🌐 fqdn /
  ⏳ через N дн. — YYYY-MM-DD (порог ≤T) / 📁 проект · компания. Обогащён
  `domain_location(session, domain)` (проект+компания), вызывается в
  `dispatch_instant` и web `alert_notify`. `compose_digest` — заголовок с общим
  счётчиком, секции с эмодзи и датами истечения (`Domain.expiry_date` в select).
  Plain-text+эмодзи (без markdown-диалектов) → одинаково в Telegram/Discord/webhook.
  Тесты (severity/дата/порог/локация в сообщении; ns/vt/ssl; локация опускается без
  проекта; дайджест-тесты по-прежнему зелёные).
  Переписать `alerts.build_message` и `digest.compose_digest` в читаемый вид:
  severity + тип в заголовке, человекочитаемая дата (не только «N дн.»), проект ·
  компания, порог/значения, домен. Единый набор эмодзи/структуры, работающий во
  всех каналах (Telegram/Discord/webhook). Обогатить контекст проектом/компанией.
  - DoD: тесты (в тексте есть severity/дата/проект/порог), вживую в Discord.

- [x] **T51. Карточка домена: явная панель проверок + VirusTotal.** _(2026-07-22)_
  Блок «Проверки» на карточке: строки RDAP/WHOIS, SSL, **VirusTotal** (явно:
  `✔ чисто (H/total)` / `◆ подозрительно` / `● N/total детектов`), DNS/NS,
  Health-check (rollup up/down/unknown/не настроен) — статус + когда проверялось.
  Роут обогащён: последний `CheckResult` по каждому типу (`checks_status`),
  последний `VtResult` (`vt`), rollup health. Тест (в карточке видны панель, VT
  «чисто 85/89», «не проверялся» для непройденных). Проверено вживую (curl).
  На карточке — блок «Проверки» с явным статусом каждого типа (RDAP, SSL,
  VirusTotal, DNS, health): пройдено/нет + когда проверялось; отдельная строка
  VirusTotal (напр. «✔ чисто 0/89» или «● N детектов»). Обогатить контекст
  последним результатом по каждому типу + VT.
  - DoD: тест (в карточке видны статусы проверок и VT), вживую.

## Фаза 9 — права доступа per-user

- [x] **T52. Пул прав пользователя: видимость только назначенных компаний.** _(2026-07-22)_
  Сделано: (1) **UI назначения прав** — на форме пользователя (create+edit) textarea
  `company:1`/`project:2` заменена на список компаний с чекбоксами + вложенными
  проектами (`name="company_scopes"`/`project_scopes"` списками; company-scope = вся
  компания, project-scope = один проект). Роуты собирают `list[ScopeIn]` напрямую
  (`_scopes_from_form`), `_form_context` отдаёт компании/проекты (admin видит все) и
  множества уже выданных id для отметки; пустой набор очищает скоупы; аудит diff как
  прежде. (2) **Сквозной аудит enforcement** (3 параллельных агента по web-роутам /
  сервисам+MCP / UI) — read-поверхность оказалась полностью защищена (все by-id роуты
  редиректят чужой id, списки/дашборд/расходы/CSV фильтруются через
  `allowed_project_ids`, MCP-инструменты используют те же сервисы). Найдены и закрыты
  **2 write-дыры**: `bulk_assign_project` теперь проверяет **целевой** проект на scope
  (scoped-Manager не перекинет домены в чужой проект → refuse/0); `import.run_import`
  при апдейте существующего по FQDN домена проверяет проект **самого домена** в scope
  (иначе строка-ошибка «домен вне доступа», без мутации/re-home). Роль ортогональна
  скоупу — Admin по-прежнему видит всё. Тесты 291 (+7: create/edit чекбоксами, очистка
  скоупов, «видит только свою компанию» + чужой by-id→redirect, bulk-assign в чужой
  проект отказ / в свой ок, import чужого домена→«вне доступа»). ruff+format чисто.
  **Без миграций** (модель `UserScope` уже была). Мелкое (by-design): `/tags` —
  глобальный список (у тегов нет привязки к компании).
  Цель: админ создаёт пользователя и назначает ему набор компаний (и/или проектов);
  такой пользователь видит **только** домены/алерты/проекты/расходы/регистраторов
  этих компаний и ничего больше — нигде в интерфейсе, экспортах, прямых ссылках
  по id и MCP не всплывают чужие данные. Пример: пользователь со скоупом на `GT1`
  видит только GT1.
  Модель `UserScope` (company/project) и хелперы `user_in_scope` / фильтрация в
  сервисах уже есть (T02–T03) — задача **не** переизобретает scope, а: (1) даёт
  **человеческий UI** назначения прав вместо сырого textarea `company:1`; (2) делает
  **сквозной аудит** и закрывает пробелы enforcement, чтобы гарантировать «видит
  только своё».
  Скоуп работ:
  - **UI назначения прав** на форме пользователя (create+edit): вместо textarea —
    список компаний с чекбоксами (термин. `.checkbox`), опционально раскрытие
    проектов компании для более узкого скоупа (company-scope = все проекты компании,
    project-scope = один проект). Показ текущего набора при редактировании; аудит
    diff. Роуты парсят чекбоксы в `list[ScopeIn]` (валидация как в `_apply_scopes`).
  - **Сквозной аудит enforcement** по всем поверхностям для не-админа со скоупом:
    навигация/сайдбар (не показывать разделы/пункты, где нет данных или прав —
    напр. Пользователи/Настройки только admin, что уже так; проверить), списки
    доменов/алертов/проектов/компаний/расходов/регистраторов/неразобранных,
    чип-кнопки проектов (T43), CSV-экспорт доменов, дашборд-счётчики, прямой заход
    по id (`/domains/{id}`, `/alerts/{id}`, `/projects/{id}`, карточки) → чужой →
    403/redirect, MCP-инструменты (роль/скоуп владельца токена/сессии). Найденные
    дыры — починить; что нашлось вне скоупа — в Backlog.
  - Без изменения ролей Admin/Manager/Viewer — скоуп ортогонален роли (роль = что
    можно делать, скоуп = что видно). Admin по-прежнему видит всё.
  - DoD: миграций не требуется (модель есть); UI-назначение прав чекбоксами
    (create+edit, аудит); тесты — пользователь со скоупом на одну компанию: списки
    доменов/алертов/проектов/расходов/регистраторов содержат только её данные,
    прямой заход на чужой домен/алерт → 403/redirect, экспорт CSV отфильтрован,
    MCP `list_domains`/`list_companies` под таким пользователем отдают только своё,
    Manager+ мутации вне скоупа → отказ; вживую (создать тест-пользователя со
    скоупом на GT1, залогиниться, убедиться что видно только GT1). ruff+format чисто.

## Фаза 10 — расширение MCP

- [x] **T53. Больше инструментов MCP: health-checks, редактирование доменов, платежи, структура.** _(2026-07-22)_
  Сделано (PR #56): +9 инструментов (12→**21**), все через существующие сервисы (scope+аудит),
  паттерн `tools.py (session, user, …)` + обёртки `server.py`. Health-checks:
  `list_health_checks`/`add_health_check`/`delete_health_check`/`bulk_add_health_check`
  (`{fqdn}`-шаблон, только домены в скоупе, отчёт applied/skipped). `update_domain` (правит
  только переданные поля; смена `project_id` — с проверкой целевого проекта на scope как в T52).
  `list_payments`/`add_payment` (USD-конверсия/`rate_override`). Admin: `create_company`/
  `create_project`. Хелперы `_require_admin`/`_domain_in_scope`/`_hc_dict`/`_payment_dict`;
  `INSTRUCTIONS`+`docs/MCP.md` обновлены. Тесты +9 (~300 всего), ruff чисто, без миграций.
  **Задеплоено**, вживую 21 инструмент зарегистрирован на проде. Примечание: клиенту claude.ai
  надо обновить/переподключить коннектор, чтобы новые инструменты появились в списке.
  Находка (в проде): у MCP-коннектора нет инструментов для **health-check'ов** —
  ассистент не мог массово завести HTTP-проверки (напр. `{fqdn}/click?pid=1&offer_id=625`
  на 19 доменах), только домены/теги/импорт/алерты/запуск rdap-ssl-vt-dns. Плюс не
  хватало правки полей домена, платежей и создания компаний/проектов. Расширить набор
  MCP-инструментов, **всё — через существующие сервисы** (scope+аудит автоматом), тем же
  паттерном `tools.py` (`(session, user, …)`) + тонкие обёртки в `server.py`.
  Новые инструменты (9), поверх текущих 12:
  - **Health-checks:** `list_health_checks(domain_id)` (read, scope), `add_health_check(domain_id,
    url, method?, follow_redirects?, expected_statuses?, location_pattern?, body_substring?,
    timeout_s?, interval_min?, fail_threshold?)` (Manager+, `net_guard` валидация),
    `delete_health_check(healthcheck_id)` (Manager+, scope по домену чека),
    `bulk_add_health_check(domain_ids, url_template, …)` (Manager+, `{fqdn}`-шаблон, применяет
    **только к доменам в скоупе**, отчёт applied/skipped) — закрывает исходный кейс на 19 доменов.
  - **Домен:** `update_domain(domain_id, notes?, auto_renew?, expiry_date?, renewal_price?,
    renewal_currency?, nameservers?, tags?, project_id?)` (Manager+; правит только переданные
    поля; смена `project_id` — с проверкой **целевого** проекта на scope, как в T52).
  - **Платежи:** `list_payments(domain_id)` (read, scope), `add_payment(domain_id, amount,
    currency, note?, rate_override?, paid_at?)` (Manager+, конверсия в USD как в UI).
  - **Структура (Admin):** `create_company(code, name)`, `create_project(company_id, code, name)`.
  - Хелперы: `_require_admin`, `_hc_dict`/`_payment_dict`; обновить `INSTRUCTIONS` и `docs/MCP.md`.
  - DoD: тесты (add/list/delete health-check + отказ вне скоупа; bulk фильтрует по скоупу;
    update_domain правит поля и блокирует смену проекта вне скоупа; add/list payment;
    create_company/project только Admin; health-check на чужой домен → отказ); ruff+format
    чисто; **без миграций** (модели есть); вживую через коннектор claude.ai (завести
    health-check'и на реальные домены). Затем — исходный кейс: массово завести
    `{fqdn}/click?pid=1&offer_id=625` на 19 доменов.

## Фаза 11 — фиксы доставки

- [x] **T54. Discord/Telegram: длинные сводки режутся под лимит сообщения.** _(2026-07-22)_
  Баг с прода: кнопка «Отправить сейчас» для канала GT1 → «Не удалось отправить сводку».
  В `notification_log` — `webhook status 400: {"content": ["Must be 2000 or fewer in length."]}`.
  Причина: у Discord лимит **2000** символов на `content`, а сводка GT1 (много доменов) длиннее;
  Adera слался, т.к. короткая. Каналы не били длинный текст под лимит. Фикс: `channels/base.py`
  `chunk_message(text, limit)` (режет по границам строк, длинную строку — жёстко) + базовый
  `NotificationChannel.send` теперь бьёт на части `MAX_LEN` и шлёт по одной через абстрактный
  `_send_one`; подклассы (`Telegram`/`_Webhook`→Slack/Discord/Generic) переименовали `send`→
  `_send_one` и объявили `MAX_LEN` (Discord 2000, Telegram 4096; Slack/generic — без лимита).
  Ретрай оборачивает весь `send` как прежде. Тесты (+4: chunk within/boundaries/hard-split; Discord
  >2000 → несколько POST, каждый ≤2000). Без миграций. Задеплоено, сводка GT1 ушла в Discord.

- [x] **T55. Алерты/сводка: исключить архив, группировка по срочности, аккаунт регистратора.** _(2026-07-22)_
  Жалоба с прода: дайджест GT1 — 101 строка, среди них домены, «истёкшие» 1000+ дней назад
  (`bbhpromo3.com -1638 дн`, `claimhotbonus.com -2622` и т.п.), формат сырой. Расследование:
  это мёртвые домены аккаунта **GodaddyKGB**, заархивированные ещё в T47 (`is_active=false`), но их
  **активные алерты никто не закрыл**, а `compose_digest` джойнил алерты с доменами **без фильтра
  `is_active`** → 42 архивных домена лезли в сводку. Фиксы: (1) `compose_digest` фильтрует
  `Domain.is_active`, то же в web `/alerts` и MCP `list_alerts`; (2) при архивации домена его активные
  алерты резолвятся — `alerts.resolve_domain_alerts`, вызывается в `domains.set_archived`/`bulk_archive`
  и `registrars.archive_expired`; (3) новый формат дайджеста: заголовок со **scope-именем** (компания/
  проект), истечения **сгруппированы по срочности** (💀 просрочены / 🔴 ≤7 / 🟠 8–30 / 🟡 31–60 / ⚪ 60+),
  сортировка по дням, в каждой строке — **аккаунт регистратора** (`· Kingbilly`); (4) мгновенные
  сообщения (`build_message`) тоже показывают аккаунт (🏷). Один-раз на проде: заресолвил 42 зависших
  алерта на архивных доменах. Тесты +5 (архив исключён; архивация резолвит алерты; группировка+аккаунт+
  заголовок; account в build_message). Без миграций.

- [x] **T63. Пороги expiry 30/7/1 + доставка каждого алерта ровно один раз.** _(2026-08-24)_
  Жалоба с прода: алерт «домен истекает» приходил **каждый день** — `compose_digest` брал все
  `state='active'` события, поэтому одно и то же висело в каждой суточной сводке; плюс порогов было
  слишком много (60/30/14/7/1). Фиксы: (1) `EXPIRY_THRESHOLDS = (30, 7, 1)` — предупреждаем ровно за
  30/7/1 день (SSL без изменений); (2) новое поле `AlertEvent.notified_at` (миграция
  `a1b2c3d4e5f6`, nullable, backfill `= fired_at` для существующих, чтобы первая сводка не переслала
  весь бэклог); (3) `compose_digest` фильтрует `notified_at IS NULL` и возвращает `event_ids`;
  после успешной отправки — `mark_events_notified` (в воркере `_send_digest` и в web «Отправить
  сейчас»); (4) `dispatch_instant` помечает high-события `notified_at` сразу после отправки, так что
  сводка их не повторяет; при отсутствии instant-канала событие остаётся не помеченным и уходит один
  раз в ближайшую сводку. Итог: каждый переход порога = одно новое событие = одна доставка. Тесты +2
  (`test_digest_delivers_each_alert_once`, `test_expiry_no_alert_beyond_30_days`) + обновлён
  crossing-тест под новые бэнды. Миграция `a1b2c3d4e5f6`.


### Пропущенные в журнале задачи (восстановлено по PR, сентябрь 2026)

_Номера T57–T59 не использовались. Задачи ниже были сделаны в августе, но не записаны в PLAN.md._

- [ ] **T56. Деплой через ops-adera (Komodo + Ansible), переезд DigitalOcean → OVH.** _(ветка `task/56-deploy-komodo`, не смержена)_
  Прод развёрнут из отдельного репозитория `Eshanchik/ops-adera` (роль `domains`, `compose.yml.j2`,
  Semaphore «Deploy domains», образ `ghcr.io/eshanchik/domains`). Инцидент после переезда: воркер
  был только в `internal`-сети без egress → `[Errno -3]` на RDAP/регистраторах; исправлено в
  ops-adera PR #1 (сеть `egress` для worker). Внешний IP для whitelist Namecheap — `141.95.34.21`
  (egress), не `51.89.46.32` (inbound). Документация приложения (DEPLOY.md, SPEC NFR-6) ещё
  описывает DigitalOcean — закрывается в T74.
- [x] **T60. Редактирование аккаунта регистратора (Client IP + креды).** _(2026-08-24)_
  После переезда сменился IP → Namecheap отвечал ошибкой. `registrars.update_account` (пустые
  креды = оставить прежние; `CredentialDecryptError` вместо затирания нечитаемого блоба; сброс
  `status/last_error`), GET/POST `/registrars/{id}/edit`, валидация IP. Тесты happy/error.
- [x] **T61. Богатая сводка алертов (Severity-Board).** _(2026-08-24)_
  Структурный `Digest` (tiers/groups/rows) + рендеры под канал: Discord embeds по тирам с
  цветом, Telegram HTML, plain; дни пересчитываются на момент отправки; auto-renew/аккаунт/ссылка
  в каждой строке. Планировщик только ставит `send_digest` в очередь, воркер (с egress) компонует
  и шлёт. Ревью: лимит 6000 символов Discord, экранирование href. Тесты +7.
- [x] **T62. ns_change: не сравнивать с пустым NS-baseline.** _(2026-08-24)_
  Шторм из 282 ложных `ns_change` (`old_ns=[]`) — снапшоты без NS в окне без egress становились
  базой сравнения. `evaluate_dns` сравнивает два последних **непустых** снапшота. На проде
  ложные события заресолвлены SQL-ом. Тесты +2.

## Фаза 12 — Гигиена и безопасность (P0)

_Цель: после этой фазы «тихо в канале» означает «всё хорошо», а не «сломалось». Мониторинг не
останавливается молча, алерты не теряются, мёртвый парк не маскирует живые проблемы, доступ
восстановим, деплой и бэкапы подтверждены, документация совпадает с продом. Основание —
`docs/RESEARCH-2026-09.md`. Порядок = приоритет. Оценка: 4–5 недель._

- [x] **T98. Хотфикс: DISTINCT ON → row_number() (SQLAlchemy 2.1).** _(2026-09-28, PR #66)_
  CI начал ставить SQLAlchemy 2.1.1 (lock-файла нет) — там `select().distinct(expr)` устарел,
  pytest превращает это в ошибку → 10 тестов красные на всех ветках, сборка образа из main
  заблокирована. `domains.ssl_status_map` выбирает свежую строку через `row_number()`; работает
  на 2.0 и 2.1, тай-брейк детерминированный. Корень (нет lock-файла) закрывает T91.
- [ ] **T64. Архив = полная остановка мониторинга домена.** _(P0 · S)_
  `enqueue_due` выбирает CheckSchedule без join на `Domain.is_active`, при архивации расписание не
  удаляется, `evaluate_after_check`/`dispatch_instant` не проверяют архив → на следующий день
  RDAP заново рождает HIGH «истекает через −1638 дн.», VT/RDAP-квоты жгутся на кладбище.
  Скоуп: `set_archived`/`bulk_archive`/`archive_expired`/MCP `set_domain_archived` удаляют
  CheckSchedule и выключают HealthCheck; unarchive → `backfill_schedules`; join `is_active` в
  `enqueue_due*`; ранний return в `_run`/`evaluate_after_*`/`dispatch_instant`;
  `scripts/prune_archived_schedules.py` (dry-run/--apply) + резолв скрытых active-событий на архиве.
  Приёмка: тесты «архив → нет enqueue/событий/диспатча; unarchive → расписание есть»; на проде
  0 строк `check_schedule` у `is_active=false`.
- [ ] **T65. Жизненный цикл домена: liveness, авто-архив по правилу, «кандидаты в архив».** _(P0 · L)_
  Прод: **128 из 333 активных доменов (38%) неделю не резолвятся** — ~33 не зарегистрированы
  (RDAP/WHOIS «No match»), ~70 без единой DNS-записи; они дают 49% SSL-«ошибок», 21% DNS-stale,
  ложные expiry-алерты и три ручные чистки (T47, T55, 24.08). У домена нет жизненного цикла кроме
  `is_active`; синк не замечает домены, исчезнувшие из аккаунта; `archive_expired` — ручной dry-run.
  Скоуп: миграция `Domain.liveness (live|undelegated|unregistered|expired)`, `renewal_decision
  (renew|let_expire|undecided)`, `last_seen_in_registrar_at`, `archived_at`, `archive_reason`;
  liveness выводится из проверок (RDAP 404/«No match» N раз подряд → unregistered; DNS без записей
  N дней → undelegated; NXDOMAIN в SSL/DNS различать от таймаута); для `undelegated/unregistered`
  не планировать SSL/health/VT (только RDAP раз в неделю); синк пишет `last_seen`, 2 синка подряд
  без домена → кандидат; учёт Namecheap `IsExpired`; ежедневная задача авто-архива с grace
  (Setting, дефолт 30 дн.; условие: expiry < now−N **или** unregistered ≥ N дн., и не auto_renew)
  с аудитом `archive_auto` и строкой «🗄 автоархив: N» в дайджесте; `let_expire` → без expiry-
  алертов + архив на expiry+1d; аномалия «auto_renew=true, но истёк» как alert kind; тайл
  «Просрочено» + expiring только для будущих дат; фильтр/колонка liveness в /domains; страница
  «Кандидаты в архив» (причина, «Архивировать все») вместо CLI; SPEC §3.2. Связано с T81
  (недостижимый хост ≠ warn). Приёмка: тесты правила по каждому сигналу и grace; на проде после
  прогона доля SSL-«ошибок» < 10%, в Discord нет алертов по кладбищу.
- [ ] **T66. Покрытие каналами: компании без канала, баннер, глобальный fallback.** _(P0 · S)_
  Прод: единственный канал — Adera; **GT1 (266 доменов, 80% парка) и Antares без канала, 104 из
  122 активных алертов не доставлены никому**; `resolve_channels` молча уходит в пусто. Канал GT1
  существовал (T54/T55) и был удалён — судя по всему, из-за спама; возвращать после T64/T65.
  Скоуп: баннер на дашборде «N активных алертов без канала доставки» со ссылкой; на /channels —
  панель «Покрытие» (компания/проект → каналы; красная строка «нет канала» + число алертов в
  скоупе); опция «глобальный fallback-канал получает алерты компаний без своего канала» (Setting,
  явно, по умолчанию выкл.) — с пометкой компании в сообщении; при создании канала — чекбокс
  «отправить текущее состояние сейчас»; MCP `system_status` отдаёт `companies_without_channel`.
  Приёмка: тесты покрытия и fallback-маршрутизации; для GT1 без канала баннер виден.
- [ ] **T67. Источники полей: manual-ловушка expiry_date, порядок доверия, «вернуть авто».** _(P0 · M)_
  `web/domains.py:406-411` кладёт naive datetime, `services/domains.py:162` сравнивает aware≠naive →
  любое сохранение формы (даже ради тега) помечает `expiry_date=manual`, после чего RDAP и синк
  никогда не обновляют дату (`checks/expiry.py:55`, `registrars.py:341`); источники нигде не
  показаны. Параллельно RDAP и API регистратора перетирают друг друга по дате до 5 раз в день.
  Скоуп: парсинг даты формы tz-aware и сравнение по календарному дню; форма шлёт только
  изменённые поля; `services/merge.py` с приоритетом `manual > api-<registrar> > rdap > whois >
  csv`, толерантность по дню для дат, сортировка nameservers; `expiry_verified_at/by`; бейдж
  источника у каждого авто-поля в DOSSIER + «вернуть авто-обновление» (сброс ключа, enqueue rdap,
  аудит) и MCP `reset_field_source`; `scripts/repair_manual_expiry.py` (dry-run/--apply); SPEC §3.2.
  Приёмка: тест «сохранение notes не меняет expiry_date/field_sources/history»; кросс-источниковые
  тесты; скрипт-ремонт прогнан на проде с отчётом.
- [ ] **T68. Доставка at-least-once: учёт per-(канал, событие), ретраи, подхват в дайджест.** _(P0 · M)_
  `dispatch_instant` ставит `notified_at` до реальной отправки (`alerts.py:370-376`), а
  `send_to_channel` глотает ошибки и возвращает False — при сбое канала/потере egress high-алерт
  теряется навсегда. Глобальный `notified_at` не различает каналы (граница из Backlog T63).
  Скоуп: `NotificationLog` — источник правды (индекс `(channel_id, alert_event_id,
  delivery_status)`, per-event строки, для дайджеста через `digest_id`); `dispatch_instant` не
  трогает `notified_at`; актор `send_notification` бросает на транзиентных ошибках → Dramatiq
  retries (max 5, backoff 60 с…1 ч); `compose_digest(channel)` = активные события в скоупе без
  строки `sent` для **этого** канала; claim дайджеста снимается при неудаче; `notified_at` →
  deprecated; метрика `dg_notifications_total{status,channel_type}`; бейдж «не доставлено» +
  «повторить» на алерте. Приёмка: тесты «канал 5xx → событие в следующем дайджесте», «новый канал
  получает открытый бэклог ровно один раз», «два пересекающихся скоупа — каждый ровно один раз».
- [ ] **T69. Планировщик: defer-not-drop, равномерный spread, VT-бюджет в настройках.** _(P0 · M)_
  `next_check_at` сдвигается на interval+jitter до диспатча, а при `rate_limited`/
  `budget_exhausted`/`circuit_open` проверка ничего не пишет и не переставляется → после любого
  бэклога большинство VT-проверок выпадает на неделю без сигнала; backfill даёт всем один
  `next_check_at`. Скоуп: лимитер/breaker возвращают `retry_after`; воркер пишет `deferred` и
  ставит `next_check_at = now + retry_after` (cap 1 ч); backfill раскладывает по [0, interval);
  бюджет типа за тик (vt ≤ 3/мин); VT PER_MIN/DAILY в Settings (пресеты free/premium); VT 404 →
  «нет данных», не «✔ чисто»; `dg_check_results_total{type,status}` + тайл «очередь проверок /
  VT N из 500» на дашборде; обработка `checks.DQ`. Приёмка: тест «1000 VT при 4/мин проходят
  ≤7 дней без потерь»; старый drop-тест переписан.
- [ ] **T70. Hardening проверок: catch-all → stale, bootstrap fallback, WHOIS-лимит, punycode, синк с retry.** _(P0 · M)_
  `load_bootstrap` не оборачивает ConnectError в RdapError, в `_run` воркера нет catch-all → при
  потере egress актор падает, stale не пишется, breaker не открывается (инцидент после переезда);
  WHOIS-fallback без лимитера; IDN уходят наружу как Unicode; один 5xx регистратора красит
  аккаунт в error на 6 часов. Скоуп: транспортные/JSON-ошибки → RdapError/VtError; last-good
  bootstrap в Redis; Retry-After на 429; catch-all в `run_*_check`/`_run`/`_run_healthcheck` →
  `write_result(stale)` + `record_failure` + `log.exception`; лимитер `rl:whois:{tld}` + breaker;
  `domain.punycode` во все внешние вызовы; результат без expiry → `warn` с причиной; DNS: NXDOMAIN /
  SERVFAIL / timeout различать (нужно T65); `conn.list_domains()` через limiter/breaker/retry,
  транзиентные ≠ error (подсказка «добавьте IP в whitelist»); dedupe_key ns_change через
  `sha1(sorted ns)[:16]`. Приёмка: тесты на каждый сценарий; правило CLAUDE.md «ошибка внешнего
  сервиса никогда не роняет воркер» покрыто акторным тестом через StubBroker.
- [ ] **T71. Наблюдаемость самого DomainGuard: heartbeat, метрики, канарейка egress, dead-man, правила Grafana.** _(P0 · M)_
  /metrics отдаёт только counts и breaker'ы; healthcheck воркера = `redis.ping()`; в ops-adera нет
  ни одного правила на `dg_*` — зависший планировщик или воркер без сети обнаруживаются по шторму
  или по тишине. Скоуп: `scheduler:last_tick` в Redis; метрики `dg_scheduler_last_tick_age_seconds`,
  `dg_queue_depth{queue}` (+DQ), `dg_checks_overdue_total`, `dg_domains_stale_total{type}`,
  `dg_notifications_failed_total`, `dg_retention_last_run_timestamp`, `dg_egress_ok`; канарейка
  (DNS+HTTPS к 2 известным хостам раз в тик → флаг `net:ok`): при красной проверки пишут
  `stale(worker_offline)`, HC не инкрементируют счётчик, SSL unreachable → stale, `evaluate_ssl`
  не резолвит при `valid_to=None`; один ops-алерт «воркер без сети»/«восстановлено»; dead-man
  (пинг healthchecks.io раз в 5 мин, URL в Settings). ops-adera: scrape `domains-worker:9191`,
  правила (tick age > 5m, overdue > 50, stale > 10%, CB open > 30m, DQ > 0, failed notifications),
  панели; раздел «как понять, что проверки идут» в docs. Приёмка: тест «egress пропал на час →
  0 ложных событий, 1 ops-алерт»; на проде правило срабатывает при остановке scheduler.
- [ ] **T72. Break-glass восстановление доступа: `scripts/manage.py`, защита последнего админа.** _(P0 · S)_
  `ensure_admin` не меняет пароль существующему логину, сброс — только другим админом; админ уже
  запирался (выход — psql + ручной argon2). `update_user` позволяет деактивировать себя и
  последнего admin. Скоуп: `scripts/manage.py` (`reset-password <login>` из stdin/env, `set-role`,
  `unlock`, `disable-2fa`, `activate`, `list-users`) с аудитом `source=cli`; глагол `manage` в
  entrypoint + `make manage`; `create_admin --reset-password`; guard «нельзя деактивировать/
  понизить себя и последнего admin»; при CryptoError TOTP — понятная ошибка без lockout; раздел
  «Аварийное восстановление доступа» в README/DEPLOY/ops-adera. Приёмка: тест на каждую команду
  и guard.
- [ ] **T73. Безопасный деплой и подтверждённые бэкапы: sha-теги, дамп перед миграцией, canary мастер-ключа, restore-drill, ротация ключа.** _(P0 · L)_
  build.yml тегирует только latest/branch; migrate = безусловный `upgrade head` без дампа;
  post-deploy smoke = /healthz без БД; все CryptoError глотаются, /readyz не знает о ключе — с
  новым `.env` система «здорова» при молча мёртвых VT/Telegram/регистраторах/2FA; restore ни разу
  не репетировался. Скоуп: `type=sha` в build.yml; ops-adera: pg_dump `predeploy-<tag>` (3
  генерации) перед compose up, поллинг /readyz==200 и `tick_age < 90`; правило «data-миграции
  репетируются на копии дампа»; crypto: пустой/невалидный ключ в production → отказ старта,
  Setting `crypto_canary` + `/readyz master_key: ok|mismatch` (503) + метрика,
  `MultiFernet([KEY, KEY_PREVIOUS])` + `scripts/rotate_master_key.py`; WARNING со счётчиком
  нерасшифровываемых записей при старте; `DG_ADMIN_*` только в migrate; `.deploy-secrets` в
  .gitignore; restore-drill с записью результата и квартальным шаблоном; docs «Ротация ключа».
  Приёмка: тесты canary/readyz/rotate; drill выполнен; в GHCR есть sha-тег.
- [ ] **T74. Синхронизировать документацию с кодом и продом; убрать опасные устаревшие скрипты деплоя.** _(P0 · S)_
  SPEC/README/DEPLOY/pg_dump.sh описывают DigitalOcean и `deploy.sh` с certbot на :80 (на OVH
  сломает Traefik); SPEC — пороги 60/30/14/7/1 и «Telegram MVP» при коде 30/7/1 + Discord;
  .env.example неполный; MCP.md не знает про OAuth. Скоуп: SPEC §3 FR-AL-3/4 (30/7/1, deliver-
  once, напоминания crit в дайджесте — решение), NFR-6 → OVH/pg_dump+restic, §11 (пороги,
  NS-baseline, архив, Discord, 2FA/Google/MCP OAuth/scopes, авто-архив с grace — решение);
  DEPLOY.md → «прод из ops-adera», runbook «после переезда: проверить egress воркера»; README
  (локальный запуск, каналы, seed, восстановление доступа); `scripts/deploy.sh`,
  `docker-compose.prod.yml`, `docker/nginx/prod.conf` — удалить или DEPRECATED с отказом
  запускаться; `.env.example` полный; Backlog: убрать «обёртку админ-страниц в ui.panel» (сделано
  в T40); DoD-правило «ветка task/* меняет PLAN.md» + CI-проверка; ops-adera: таблица «кто ходит
  наружу». Приёмка: новый разработчик поднимает локально и понимает прод по README/DEPLOY.

## Фаза 13 — Ежедневное удобство (P1)

_Цель: UI из «реестра с CRUD» становится рабочим местом ops-инженера и менеджера: обратная связь
на каждое действие, реальный триаж алертов, списки и дайджест без кладбища, карточка с причинами
и действиями, self-service профиль. Оценка: 6–8 недель. Задачи L — по 2 PR._

- [ ] **T75. Слой обратной связи и ошибок: flash, HTML 403/404/500, дружелюбные ошибки форм, confirm.** _(P1 · M)_
  Почти все POST → 303 молча; ошибки — raw PlainText/JSON; удаление компании/проекта с доменами
  → 500; дубликат login/email → 500; `?sync=`/`?pay=` не читаются; bulk-архив и «Отправить
  сейчас» без confirm. Скоуп: `core/flash.py` (подписанная одноразовая cookie) + вывод в base.html
  во всех роутерах; exception handlers 403/404/500/IntegrityError в терминальном стиле; русские
  ошибки форм (невалидный scope → 422); блокировка удаления с доменами; confirm с числом строк
  на bulk/архив/тег, с именем канала на «Отправить сейчас»/«Переслать»; hx-on проверяет
  `successful`; return-to-URL после bulk; select-all. Приёмка: happy+error тест на каждую форму;
  ни один web-роут не отдаёт PlainText/JSON-ошибку.
- [ ] **T76. Workflow алертов: ack/snooze/ответственный, массовые действия, история, напоминания crit в дайджесте.** _(P1 · L)_
  _Частично сделано в T97: ответственный, «взял в работу», лента, комментарии, резолв с причиной,
  фильтр «мои». Осталось: snooze, массовые действия, вкладки Активные/Отложенные/История с
  пагинацией, секция crit-напоминаний в дайджесте, send-now «полная сводка», MCP ack/assign._
  «Резолв» на expiry/VT/SSL = 24-часовой snooze с повторной доставкой (`_ensure_active`
  пересоздаёт по тому же dedupe_key); состояния только active|resolved; /alerts без пагинации/
  поиска/чекбоксов; deliver-once превратил дайджест в «дельту»: просроченные/≤1 день/VT/health
  down приходят один раз и замолкают, «Отправить сейчас → нечего» врёт. Скоуп: миграция
  `acked_at/acked_by_id/snoozed_until/assignee_id/resolution_note/resolved_by`; `_ensure_active` не
  пересоздаёт при `snoozed_until > now`, если порог не ужесточился; вкладки «Активные | Отложенные
  | История» с пагинацией/поиском, массовые ack/резолв/snooze 3/7/30/архивировать; inline «Взял в
  работу / Отложить / Продлили-ожидаемо»; колонка «Кто», дефолтный assignee =
  `Project.responsible_user_id`; словарь `alert_kind_ru/severity_ru`; тайл SSL → `/alerts?kind=ssl`.
  Дайджест: секция «Всё ещё открыто (crit)» с возрастом и ответственным (без snoozed/acked, medium
  не повторять); шапка «Новых: N · Открытых критичных: M»; send-now: «новых нет, активных N —
  отправить полную сводку» (режим full, ничего не помечает). MCP `ack_alert/snooze_alert/
  assign_alert/resolve_alerts(dry_run)`. Приёмка: тесты snooze/секции crit/скоупа массовых
  операций; 500 активных алертов рендерятся < 1 с.
- [ ] **T77. Содержательные уведомления: ссылка, ответственный, next action, auto-renew/аккаунт, embed; события восстановления.** _(P1 · S)_
  _Частично сделано в T97: ссылка на карточку и упоминание ответственного в мгновенном алерте.
  Осталось: next action/auto-renew в тексте, Discord embed для instant, `health_recovered`._
  `build_message` без URL/auto-renew/аккаунта/ответственного; health_down — «check #17» без URL и
  ошибки; recovered/продление/VT-чист только резолвят событие — в канале висят незакрытые тревоги.
  Скоуп: `🔗 {base}/alerts/{id}`, `🔄/🚫/❔ auto-renew`, «👤 Ответственный», подсказка действия по
  виду; Discord instant как embed с цветом severity, Telegram HTML; событие `health_recovered`
  (instant); «продлён до …»/«VT чист» — low-строки дайджеста; полный `last_error` синка в title.
  Приёмка: тесты состава по каждому виду; из Discord на телефоне одно нажатие ведёт на алерт.
- [x] **T97. Подробная карточка алерта + ответственные и @упоминания.** _(2026-09-28)_
  Запрос пользователя: «более подробная карточка алерта + возможность тегать человека».
  Решения: справочник людей (не только пользователи DomainGuard, плюс Discord-роли как группы),
  авто-назначение **по виду алерта**, лента + комментарии с @. Сделано: модели `Person`,
  `AlertRoute`, `AlertActivity` + поля `AlertEvent.assignee_person_id/acked_at/acked_by_id/
  resolved_by_id/resolution_note` (миграция `b7d2e9a41c35`); `services/people.py` (валидация
  Discord ID/Telegram/ника, CRUD, правила «вид → кто» проект → компания → по умолчанию, рендер
  упоминаний по типу канала); `services/alert_workflow.py` (авто-назначение при срабатывании с
  переносом ответственного на более срочный порог, assign с пингом, «Взял в работу», комментарии
  с @ник → в канал, лента, контекст карточки); мгновенный алерт упоминает ответственного и
  ссылается на карточку; дайджест — ответственный в строке и пинг ответственных в шапке;
  Discord `allowed_mentions` из содержимого (`@everyone` не пингуется); резолв с причиной и
  аудитом (в т.ч. MCP). UI: `/people` (люди + правила), карточка `/alerts/{id}` — «что
  случилось» по каждому виду + «что делать», ответственный/статус/действия, домен, доставка
  (журнал `NotificationLog`), лента с комментариями и подсветкой @, проверки с причиной ошибки,
  сырой payload свёрнут; список алертов — колонка «КТО», фильтр «мои / без ответственного /
  человек», «◆ В РАБОТЕ». Тесты +48 (unit: валидация, упоминания, allowed_mentions, owners line;
  integration: тиры маршрутизации, авто-назначение и пинги, перенос при эскалации, assign/ack/
  comment/resolve, лента, дайджест; web: /people admin-only и валидация, разделы карточки,
  действия с ролями и скоупом, XSS в комментарии, фильтр списка). Мульти-агентное ревью нашло 10
  дефектов — исправлены: перенос/пинг отключённого ответственного, `@ник.` с пунктуацией, Slack
  `<!channel>` и сырые `<@id>` в комментарии, XSS через имя в `confirm()`, неполный аудит правки
  человека, 500 на гонке уникальности, ложное «взят в работу», честная лента (закрыт более
  срочным порогом / проблема ушла / архив; «поставлено в отправку» вместо «доставлено»).
  Остаток T76/T77 — ниже.
- [x] **T99. Трекинг доменов: вкладка мониторинга + страница статуса для сотрудников.** _(2026-09-28)_
  Запрос: «отдельная вкладка с мониторингом „трекинг доменов"» + «страница, в которую может войти
  любой с доменом @adera.agency и увидеть трекинг-домены и их состояние». На проде это 19 доменов
  Adera с тегом `tracking` и health-check `/click?pid=1&offer_id=625`, 4 лежат ~10 дней. Сделано
  без миграции: `services/tracking.py` (доска: статус, «лежит/работает N», аптайм 24ч/7д, 24
  последних проверки, причина сбоя по-человечески, SSL/VT/DNS/срок, сортировка «лежащие сверху»;
  добавить (тег + health-check по шаблону `{fqdn}`, идемпотентно, шаблон проверяется заранее) /
  убрать / проверить сейчас; правила доступа «домен почты → компания» в Settings); вкладка
  `/tracking` (фильтры, HTMX-автообновление, управление, правила доступа для admin); страница
  `/status/tracking` + Google-вход (`/status/login/google`, общий callback с purpose-cookie,
  проверка домена почты **и** claim `hd` Workspace, отдельная сессия `dg_status` на 12 ч с path
  `/status`, правило перепроверяется на каждый запрос, аудит `status_login`); health-check больше
  не пишет пустую причину (`request failed: ConnectError`). Настройка Google — docs/DEPLOY.md §9.
- [ ] **T78. Система и доставка в UI: реальный статус в шапке, панель «Система», журнал отправок, здоровье и редактирование каналов.** _(P1 · M)_
  «net ok» в шапке — статичный текст; `NotificationLog` пишется, но нигде не показан (SPEC FR-AL-7);
  на алерте нет «куда/когда ушло»; каналы нельзя редактировать/выключить, `digest_time` без
  валидации («9:00» ≠ «09:00»). Скоуп: `services/system_status.py` (redis, канарейка, просрочки,
  возраст последних результатов по типам, breaker'ы, очереди, VT-бюджет, сбои доставки за 24 ч,
  компании без канала) → индикатор red/amber/green с tooltip + панель «Система» на дашборде;
  `/notifications` (журнал с фильтрами/пагинацией); блок «Доставка» на алерте с «повторить»;
  /channels: последняя отправка/ошибка, toggle, edit, названия вместо `#id`; нормализация
  HH:MM; `run_digests` «должен был уйти сегодня и ещё не ушёл»; тихие часы per-канал для instant
  expiry/ssl; MCP `system_status/list_channels/test_channel/send_digest_now(dry_run)`. Приёмка:
  тесты метрик/валидации/догона; SPEC FR-AL-7 закрыт.
- [ ] **T79. Список доменов v2 + импорт/экспорт v2.** _(P1 · L)_
  vt_detect/health_down теряются при сортировке/пагинации/экспорте; нет колонок компания/
  регистратор/дней/алерты (SPEC FR-UI-2); «● up/○ down» — флаг архива; CSV из Excel (BOM, `;`)
  даёт 100% «пустой FQDN»; re-import не переносит домен в другой проект; экспорт отдаёт
  project_id числом. Скоуп: все фильтры в `_active_filter_qs`/экспорте + чипы с ×; фильтры
  регистратор/источник/expired/has_alerts/без цены/ответственный/auto_renew/liveness; колонки
  компания·проект, регистратор/аккаунт, дней (цвет), цена, алерты, «Статус», свежесть RDAP;
  счётчики в фильтрах; ссылка на bulk health-check с выборкой; экспорт с именами/заметками/
  датами; импорт `utf-8-sig` + `csv.Sniffer`, нормализация заголовков, статус `moved`, новые
  колонки (expiry_date, auto_renew, registrar, responsible_email, renewal_decision, price);
  теги lower-case + уникальный индекс. Приёмка: цикл «экспорт → Excel → импорт» без ошибок; список
  отвечает «чьи, где продлевать, сколько дней, есть ли алерты» без клика.
- [ ] **T80. Карточка и форма домена v2: полный DOSSIER с источниками и причинами, «Проверить сейчас», форма с ответственным/регистратором/auto-renew/ценой, адаптив.** _(P1 · L)_
  Карточка — посадочная из дайджеста, но не показывает notes/регистратора/аккаунт/компанию/EPP/
  источники/ответственного; `data_json.error` и per-host SSL нигде не рендерятся; форма не даёт
  править project/auto_renew/цену/ответственного (`responsible_user_id` на проде = 0 из 333 —
  поле недостижимо); с телефона — две сжатые колонки. Скоуп: DOSSIER (компания → проект,
  регистратор/аккаунт, notes, даты, EPP-бейджи, registrant, ответственный, dns_provider, бейдж
  источника, «подтверждено RDAP N дн. назад»); причина под каждым badge; таблица SSL по хостам;
  мини-история HC; «последняя удачная проверка» > 3 дн. подсветкой; одна панель алертов с
  действиями; «Проверить сейчас» с HTMX; форма: проект (скоуп), ответственный, регистратор +
  аккаунт, auto_renew, блок «Стоимость», `purpose`, `owner_contact`; `parse_rdap` извлекает
  registrar entity → `registrar_id` source=rdap; авто-`dns_provider` из NS; адаптив
  (`overflow-x:auto`, `grid-cols-1 md:grid-cols-2`, backdrop сайдбара). Приёмка: менеджер с
  телефона видит причину «ssl: fail» без логов; ops видит, где продлевать и кому писать.
- [ ] **T81. SSL и health-check v2: режим проверки, недостижимый хост ≠ warn, вид `ssl_error`; HC с headers/UA/auth, edit/pause/test-run, латентность, идемпотентность.** _(P1 · L)_
  `hosts_for` всегда добавляет www., недоступный хост → warn, badge списка выбирает произвольную
  строку — домены без www/сайта вечно «проблема» (прод: 47% ssl warn), а ошибки цепочки при
  валидных датах не алертятся; HC с UA `python-httpx` режутся WAF → ложные down; повторный
  `bulk_add_health_check` удваивает чеки. Скоуп: `Domain.ssl_mode (auto|apex_only|custom|off)`;
  недоступный хост при наличии рабочего → skip; overall по худшему из достижимых; детерминированный
  `ssl_status_map`; `_latest_ssl_valid_to` игнорирует снапшоты без valid_to; вид `ssl_error`
  (verify/handshake/unreachable ≥2 подряд, high) с dedupe по типу; HC: headers/user_agent/
  basic-auth (Fernet)/max_latency_ms; «Проверить» в форме с синхронным превью; edit/pause;
  идемпотентность `create/bulk_add_template` по (domain_id, url, method). Приёмка: тесты каждого
  режима и `ssl_error`; на проде число «SSL проблема» падает до реальных сертификатов.
- [ ] **T82. Профиль и 2FA v2: смена пароля, сессии и «выйти везде», invite-ссылки, backup-коды, сброс 2FA, обязательность для admin.** _(P1 · L)_
  Пользователи не могут сменить пароль (только admin-only маршрут); сессии без индекса user →
  смена пароля не отзывает чужие; 2FA без recovery-кодов/сброса, отключается одним POST; на проде
  2FA у 0 из 3 пользователей при admin-мутациях из интернета. Скоуп: `/profile` (пароль, 2FA,
  токены, MCP-подключения, сессии «Завершить все»); `session:user:{id}` + `revoke_all` при смене/
  сбросе пароля, деактивации, отключении 2FA, смене роли; абсолютный TTL 30 дн.; создание
  пользователя → одноразовая invite-ссылка (TTL 72 ч); `must_change_password`; `last_login_at/ip/
  method` + колонки в /users; кнопки «Сбросить пароль (ссылка)/Сбросить 2FA/Завершить сессии/
  Разблокировать»; backup-коды (10, хеши, показ один раз); `/2fa/disable` требует пароль+код;
  Setting `require_2fa_roles` (admin, grace N дн.); QR inline-SVG; `login_guard` для TOTP-шага;
  nginx limit_req на `/auth/google/*`; аудит. Приёмка: «пароль сменён → вторая сессия на /login»,
  invite-поток, backup-код, обязательность 2FA для admin после grace.
- [ ] **T83. Страница компании для менеджера: состояние, покрытие, «Чего не хватает», названия вместо ID.** _(P2 · M)_
  Маршрута `/companies/{id}` нет; каналы/пользователи/проекты показывают «компания #3 /
  project:12»; персона «менеджер компании» не имеет стартовой точки. Скоуп: `/companies/{id}`:
  проекты, домены (всего/истекает/просрочено), активные алерты, каналы, аккаунты регистраторов,
  стоимость за год, блок «Чего не хватает» (нет канала / аккаунта / пользователей со скоупом /
  N без цены / без ответственного) с прямыми ссылками; ссылки с дашборда и /companies; названия
  вместо ID во всех шаблонах; onboarding-подсказка на пустой компании. Приёмка: тесты агрегатов и
  скоупа (менеджер видит только свою компанию).

## Фаза 14 — Финансы и отчётность

_Цель: /costs — рабочий инструмент финансиста. Прод: платежей 0, `renewal_price` есть у 202 из
333 доменов (Namecheap), у GoDaddy и ручных — нет. Оценка: 3 недели._

- [ ] **T84. Курсы валют через НБУ на дату платежа + видимые ошибки формы платежа.** _(P1 · S)_
  exchangerate.host требует access_key: живой запрос отдаёт 200 с `success:false` → `rates.py`
  возвращает None → платёж в UAH/EUR молча не сохраняется (`?pay=norate` нигде не рендерится);
  тесты мокают старый формат. Скоуп: НБУ (`bank.gov.ua/NBUStatService/v1/statdirectory/exchange`)
  как основной источник на дату платежа, exchangerate.host — опциональный fallback с ключом в
  Setting; кэш per (currency, day); `Payment.rate_source`; HTMX-превью курса в форме; запрет
  сохранить без курса; вывод ошибки через flash (T75). Приёмка: respx-тесты ок/таймаут/5xx/
  `success:false`; интеграционные тесты POST платежа и GET /costs.
- [ ] **T85. Цены продления для всех регистраторов: справочник цен по TLD на аккаунте, точность Namecheap, bulk «задать цену».** _(P1 · M)_
  Пайплайн цен только для `namecheap`; 2-уровневые TLD ищутся по последнему лейблу (`co.uk` →
  `uk`), `AdditionalCost` (ICANN) не учитывается, `force=True` не вызывается. Скоуп: таблица
  `registrar_account_tld_prices` + форма справочника на аккаунте (и для ручных регистраторов),
  source `pricebook` (manual приоритетнее); `get_renewal_prices` как опциональный метод
  коннектора; сопоставление по самому длинному суффиксу; `AdditionalCost`; бейдж «≈ оценка по
  TLD»; bulk «задать цену/валюту/период»; кнопка «Обновить цены» (force); строка «без цены: N».
  Приёмка: тесты сопоставления/справочника/bulk; на проде доля активных без цены < 10%.
- [ ] **T86. Прогноз продлений v2 + экспорт CSV: горизонт, компания × месяц/квартал, итог в USD, авто vs ручная оплата.** _(P1 · M)_
  `upcoming_renewals` — 30 дней без нижней границы (истёкшие в «ближайших»), только домены с
  ценой, без итога/конвертации/компании/auto_renew; «по регистраторам» показывает `1, 2, None`;
  экспорта нет (SPEC решение 8). «Прогноз по кварталам» — главный вопрос финансов. Скоуп:
  `renewal_forecast(period, group_by)` с `expiry >= now`, конвертацией в USD (≈), нормировкой на
  12 мес., разрезами компания × месяц/квартал / проект / регистратор / аккаунт, «без цены»,
  «просрочено»; `/costs` с селекторами и тремя секциями (автопродление / ручная оплата / нет
  цены); дашборд `cost_usd` с учётом валют и периода; `/costs/payments.csv`, `/costs/summary.csv`,
  `/costs/forecast.csv` со скоупом; MCP `renewal_forecast`. Приёмка: финансист получает «сколько
  нужно на квартал по каждой компании» одним экраном и одним CSV.
- [ ] **T87. Платёж v2 и операция «Продлён»: реквизиты, редактирование/удаление, CSV-импорт, `mark_renewed` в одной транзакции.** _(P1 · L)_
  `Payment` без invoice_ref/аккаунта/периода/created_by; edit/delete нет; CSV-ввод платежей (T15)
  не реализован; «отметить продление» — два неатомарных шага, после которых expiry заблокирован
  как manual; `add_payment` не идемпотентен. Скоуп: миграция `invoice_ref`, `paid_from_account_id`,
  `period_years`, `created_by_id`; `update/delete_payment` + HTMX-формы + MCP; `import_payments`
  (CSV с dry-run, идемпотентность по (fqdn, paid_at, amount, invoice_ref)); мягкий дедуп
  `add_payment` + `idempotency_key`; `mark_renewed(domain_id, …)` в UI (кнопка «Продлён» на
  карточке и в expiry-алерте) и MCP: платёж → expiry со source `renewal` (перезаписываемый RDAP) →
  `evaluate_expiry` (алерт закрывается сразу) → enqueue rdap; REST `/api/v1/payments`.
  Приёмка: тесты edit/delete/импорта/mark_renewed (алерт закрыт сразу, RDAP позже подтверждает,
  повтор не создаёт второй платёж).
- [ ] **T88. Сверка «продлён ↔ оплачен» и напоминания «пора платить» с ценой и аккаунтом.** _(P2 · M)_
  Expiry-алерт не различает автопродление и ручную оплату, не показывает цену/аккаунт; отчёта
  «продлён без платежа / оплачен, но не продлён» нет. Скоуп: секция «Сверка» на /costs
  (expiry сдвинулся ≥300 дн. без Payment ±45 дн. → «вероятно продлён» + «Записать платёж»;
  платёж есть, expiry не изменился 14 дн. → alert `payment_unconfirmed`); строка `💳 цена ·
  аккаунт · авто/ручная` в expiry-алерте и дайджесте; группа «требует оплаты»; CSV сверки.
  Приёмка: список «продлены без платежа» за месяц совпадает с выпиской.

## Фаза 15 — Платформа и рост

_Цель: доступ и аудит без дыр, MCP как помощник оператора, REST/вебхуки для интеграций,
воспроизводимая сборка и честные тесты, данные и коннекторы готовы к 10k доменов. Оценка:
6–8 недель._

- [ ] **T89. Гигиена доступа и аудит: реальный гейт `mcp_allowed`, срок и скоуп токенов, отзыв MCP-грантов, rate-limit OAuth/API, страница /audit.** _(P1 · L)_
  `user_may_use_mcp` проверяется только на consent — dg_-токен и refresh обходят флаг; токены
  бессрочные с полной ролью; `/api`, `/register`, `/token` без rate-limit; аудит нигде не
  читается, в нём нет входов/resolve/смены секретов, нельзя отличить человека от ассистента.
  Скоуп: гейт в `_acting_user`/`exchange_refresh_token`/dg_-fallback; индекс `mcpo:user:{id}` +
  блок «MCP-подключения» с «Отозвать», revoke при деактивации/снятии флага; `ApiToken.expires_at`
  (90 дн.), `scope(read|write)`; админ-вкладка «Все токены»; nginx limit_req на `/api/`,
  `/register`, `/token`; contextvar `audit_via (ui|api|mcp|worker)` + `ip/user_agent`;
  `record_audit` в resolve/sync/mark_notified/login/logout/settings/consent/session_revoke;
  страница `/audit` (фильтры, поиск, CSV, пагинация), вкладка «История» на карточке домена;
  retention audit 24 мес; MCP `list_audit`. Приёмка: «кто заархивировал домен» — из UI за минуту.
- [ ] **T90. MCP как помощник оператора: `attention`, enriched `DomainOut`, полные фильтры, `get_domain` по fqdn, операционные инструменты, аннотации и коды ошибок.** _(P1 · L)_
  На «что горит?» ассистент отвечает через 3–4 вызова без приоритизации; `list_alerts` без
  пагинации переполняет контекст при шторме; `_domain_dict` «голый»; bulk/синк/unassigned/теги
  недоступны; инструменты без аннотаций. Скоуп: `attention(company_id?, project_id?, limit)`;
  `list_alerts` с фильтрами/пагинацией/`age_days`; единый `DomainOut` (теги, регистратор, аккаунт,
  проект/компания, ответственный, days_left, статусы проверок, field_sources) для MCP и REST;
  `list_domains` с полным `DomainFilter`; `get_domain(fqdn?, include=[…])`; `create_domain` при
  дубле → `created:false`; `bulk_archive/bulk_assign_project/bulk_add_tags(dry_run)`,
  `list_registrar_accounts`, `sync_registrar`, `list_unassigned/assign_unassigned`, `notify_alert`,
  `list_tags`, `list_users`; `ToolAnnotations` на всех; ошибки `{code, message}`; snapshot-тест
  `build_mcp().list_tools()`. Приёмка: «что горит у Antares?» — один вызов; повторный bulk без
  дублей.
- [ ] **T91. Воспроизводимая сборка и supply chain: lock-файл, smoke-импорт entrypoint-ов, compose-smoke в CI, pip-audit, DB-роль least-privilege, CSP, self-hosted ассеты.** _(P1 · M)_
  pyproject только `>=` — пересборка подтянула mcp 2.0 и mcp-контейнер ушёл в crash-loop при
  зелёном CI (entrypoint-ы не импортируются тестами, образ не запускается); приложение ходит в
  Postgres суперпользователем; UI зависит от трёх CDN. Скоуп: `requirements.lock` (+dev), установка
  из lock; `dependabot.yml` + job `pip-audit`; `tests/unit/test_entrypoints.py`; CI job `smoke`
  (`compose up --build` → /readyz → healthy, импорт `app.mcp.server`, worker без Traceback);
  удалить мёртвый `app/workers/main.py`; роль `domainguard_app` DML-only + owner-DSN для migrate;
  `CSP Report-Only` → enforce; Tailwind CLI → `static/app.css`, htmx и шрифт в static/. Приёмка:
  CI падает на ImportError entrypoint'а; страницы рендерятся без CDN.
- [ ] **T92. Честные тесты и локальный стенд: один прогон CI, cov-fail-under, alembic check, mypy, тесты склейки/планировщика/форм/data-миграций, seed_demo, канал `log`, логи воркера.** _(P1 · L)_
  Локально `make test` зелёный при недоступных БД (235 из 323 тихо skip); три одинаковых test-job на
  пуш; 0% на `app/workers/checks.py`; `POST /channels` не покрыт; data-миграция T63 не
  тестировалась; локально нельзя увидеть дашборд/сводку. Скоуп: ci.yml `pull_request +
  workflow_call`, `concurrency`, `cache: pip`, `--cov-fail-under=75` с ratchet, `alembic check` +
  `downgrade base && upgrade head`, `mypy app/` (warn → блокирующий), pre-commit, pytest-timeout/
  socket; `docker-compose.override.yml`, Makefile `dev-db/test-unit/test-int`; conftest: fail
  вместо skip, схема через alembic; акторные тесты через StubBroker (run_check, fan-out,
  send_digest), `enqueue_due_*`, POST /channels всех типов, формы регистраторов/платежей/HC,
  oauth refresh/revoke; шаблон теста data-миграции; `scripts/seed_demo.py` + тип канала `log`
  (notification_log + stdout) для локального QA; `app/log.py` для воркера (ts/pid/extra, uvicorn
  access в JSON); README «Локальная разработка». Приёмка: `make test` без БД падает с понятным
  текстом; один test-job; покрытие ≥75%; `make seed-demo` показывает сводку в канале `log`.
- [ ] **T93. Интеграции наружу: REST v1 поверх tools.py, вебхуки с resolved и подписью с timestamp, OpenAPI с securityScheme, гайд подключения MCP/API.** _(P2 · L)_
  REST — 3 read-only эндпоинта без `response_model`/securityScheme, `/docs` открыт; вебхуки — один
  POST без ретраев/лога/replay-защиты, только «created»; MCP.md не знает про OAuth. Скоуп: роутер
  `/api/v1` из `tools.py` (маппинг ошибок → 403/404/409/422), `response_model`, `HTTPBearer`,
  `/docs` за логином; `webhooks.deliver`: 2xx-проверка, ретраи актора, `NotificationLog` с
  `endpoint_id`, `X-DomainGuard-Timestamp/Event-Id`, `HMAC(ts + body)`, события `resolved/acked`,
  `net_guard` на URL, кнопка «Тест»; `docs/MCP.md` (claude.ai custom connector, Claude Code/Desktop/
  Cursor, dg_ + curl, роли, troubleshooting), `docs/API.md`, `docs/WEBHOOKS.md`. Приёмка: тесты
  REST-паритета с MCP; respx-тесты вебхуков; n8n получает платежи и прогноз без разработчика.
- [ ] **T94. Правила алертов: глобальные дефолты + переопределение на уровне компании, алерт на падение репутации VT.** _(P2 · M)_
  Пороги — константы, `AlertRule` — мёртвая таблица; финансам нужен горизонт 60 дней, ops 7/1, у
  Antares SSL-алерты на парковке — шум; SPEC FR-CK-3 требует алерт на падение reputation. Скоуп:
  `/rules` (admin): глобальные дефолты + строка на компанию; `rules.effective(company_id)` с кэшем
  и инвалидацией; `evaluate_*` читают эффективное правило, проставляют `rule_id`; новые dedupe-
  ключи только при ужесточении; вид `vt_reputation`; SPEC FR-AL-3, §11. Приёмка: для GT1
  выставлен горизонт 60 дней без деплоя; для Antares SSL по проекту «парковка» выключен.
- [ ] **T95. Регистраторы: коннекторы по фактическому списку компаний, цены через интерфейс коннектора, отчёт синка о пропущенных.** _(P2 · L)_
  `build_connector` знает два типа; прод: 56 доменов без регистратора (Marcaria/Hostinger/
  Regway/…) — ops не видят «где продлевать», auto_renew и цены нет; `sync_account` молча
  `continue` на InvalidDomainError. Скоуп: уточнить список регистраторов; 2–3 коннектора
  (Cloudflare / Porkbun / NameSilo / Hostinger — проверить API) с respx-тестами timeout/429/5xx/401;
  цены и синк через один интерфейс; `SyncReport.skipped` в UI; выбор регистратора из справочника
  при ручном создании/импорте. Приёмка: доля «без регистратора» на проде < 5%.
- [ ] **T96. Данные на масштабе: retention ssl/vt/history/логов, композитные индексы, предсоздание партиций, Public Suffix List.** _(P2 · L)_
  `run_retention` чистит только `check_result`/`health_check_results`; прод: `ssl_certificates`
  **40 746 строк (49% с ошибкой), ~2 строки/домен/день, без retention**; `vt_results`,
  `domain_field_history`, `notification_log`, `audit_log` растут бесконечно; `ssl_status_map` без
  индекса `(domain_id, checked_at DESC)`; `ensure_partition` делает DDL на каждой записи;
  `tld` = последний лейбл — поддомены принимаются как домены. Скоуп: retention ssl/vt/
  notification_log 12 мес, field_history 12 мес или N на домен, audit 24 мес; индексы; предсоздание
  партиции в retention-задаче, DDL из `write_result` убран; пагинация таймлайна карточки;
  `registrable_domain` + `kind (apex|subdomain)` через PSL (backfill), для subdomain не планировать
  rdap/vt. Приёмка: EXPLAIN на /domains и карточке при 10k доменов и годовой истории < 1 с; ноль
  DDL в горячем пути.

## Backlog / находки

- Точная глубина очередей Dramatiq и латентность проверок в `/metrics` — требуют
  инструментации акторов; пока экспонируются counts + состояние circuit breaker.
- Uvicorn access-логи не в JSON (свой логгер); логи приложения/воркеров/планировщика — JSON.
- `health_check_results` — обычная таблица с retention через DELETE; при росте можно
  партиционировать по месяцам как `check_result`.
- **Deliver-once (T63) — граница:** `AlertEvent.notified_at` — глобальный на событие флаг.
  Instant-путь резолвит каналы most-specific (project→company→global, один тир на домен), а
  дайджест-путь шлёт каждому digest-каналу его scoped-сводку. Если оператор заведёт
  **пересекающиеся** digest-скоупы (глобальный digest-канал + скоупные), один поглотит алерт
  раньше другого. На практике такой конфиг уже сегодня даёт дубли дайджестов, поэтому не
  используется. Если понадобится — перейти на дедуп per-(channel, event) (таблица связи или
  per-event `NotificationLog`) вместо глобального `notified_at`.

- **T35 отложенные расхождения дизайна (нужны данные/крупнее скоупа):** декоративные
  ⣿-спарклайны в stat-тайлах обзора; мини-полоска статов (TOTAL/EXPIRING<30/SSL FAIL)
  над таблицей `/domains` (нужны counts в роуте доменов); «ПРОГНОЗ ПО КВАРТАЛАМ» на
  `/costs` (сейчас — ближайшие продления ≤30д по доменам); строка `channel` в EVENT
  деталей алерта (нужны данные доставки); латентность health-check (ms) и строка
  `reputation` (VT n/m) в DOSSIER карточки; обёртка админ-страниц (users/channels/
  registrars/settings/companies/projects/tags/webhooks/tokens) в рамки `ui.panel`;
  строка фильтров доменов как терминальный промпт + per-row select-чекбоксы как
  `[x]`-тоглы.

- **T39 отложенные (medium/low безопасность):** Content-Security-Policy (нужна
  политика под Tailwind Play CDN + inline-стили/HTMX, чтобы не сломать UI);
  автоскан уязвимостей зависимостей в CI (`pip-audit`/Dependabot); опциональный
  IP-allowlist на `/mcp` в nginx (если список клиентов известен); ревизия
  минимальных прав роли БД приложения.