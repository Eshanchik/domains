# DEPLOY — DomainGuard (production)

Развёртывание на одном сервере (DigitalOcean / Debian 13) через Docker Compose с
TLS от Let's Encrypt. Целевой хост: `root@64.226.118.46`, домен
`domains.zimbabwe-inc.com`.

## 0. Предпосылки

- DNS: `domains.zimbabwe-inc.com` → IP сервера (A-запись). Проверить:
  `dig +short domains.zimbabwe-inc.com`.
- Открыты порты 80 и 443 (HTTP-01 challenge + HTTPS).
- SSH-доступ root.

## 1. Архитектура развёртывания

```
Интернет ─80/443→ nginx (TLS, Let's Encrypt) → api (FastAPI, :8000)
                                              worker (Dramatiq)
                                              scheduler
                     postgres:16   redis:7    certbot (авто-renew)
```

- Базовый `docker-compose.yml` + оверлей `docker-compose.prod.yml`
  (nginx с TLS-конфигом `docker/nginx/prod.conf`, порты 80/443, сервис `certbot`).
- Postgres и Redis **не публикуются** наружу (доступны только внутри compose-сети).
- Секреты (VT/Telegram/Namecheap) шифруются at-rest (Fernet, `DG_MASTER_KEY`).

## 2. Быстрый деплой (скрипт)

На сервере, из каталога репозитория:

```bash
git clone https://github.com/Eshanchik/domains.git /opt/domainguard
cd /opt/domainguard
bash scripts/deploy.sh
```

Скрипт идемпотентен и делает:

1. Ставит Docker (если нет).
2. Генерирует `.env`: `ENVIRONMENT=production`, случайный `DG_MASTER_KEY`
   (Fernet), случайный `DG_ADMIN_PASSWORD` (сохраняется в `.deploy-secrets`,
   права 600).
3. Собирает образы.
4. Выпускает TLS-сертификат Let's Encrypt для домена (certbot standalone на :80,
   пока nginx не поднят), если его ещё нет.
5. Поднимает весь стек (`migrate` применяет миграции перед `api`/`worker`/`scheduler`).
6. Создаёт первого администратора (`scripts.create_admin` из `DG_ADMIN_*`).

После — открыть `https://domains.zimbabwe-inc.com`, войти под `admin` (пароль из
`.deploy-secrets`).

## 3. Пост-настройка (в UI, под админом)

- **Настройки** — VirusTotal API key (free) и Telegram bot token (шифруются).
- **Каналы** — Telegram-канал: `chat_id` группы, уровень (global/company/project),
  режим instant/digest/both, время дайджеста (Europe/Kyiv).
- **Регистраторы** — аккаунт Namecheap (`ApiUser`/`ApiKey`/`UserName` + **whitelist
  IP сервера** в Namecheap). Синк наполняет очередь «Неразобранные».
- Завести компании/проекты, добавить домены (вручную / bulk / CSV / синк).

Без этих ключей приложение работает: RDAP/WHOIS/SSL/health-checks идут по
расписанию, алерты видны в UI; VT/Telegram/Namecheap «спят» до настройки.

## 4. Обновление версии

```bash
cd /opt/domainguard && git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml build
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

Миграции применит сервис `migrate` при старте.

## 5. TLS-продление

Сервис `certbot` в prod-оверлее запускает `certbot renew` каждые 12 ч; nginx
перечитывает сертификаты (reload каждые 6 ч). Ручная проверка:
`docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm certbot renew --dry-run`.

## 6. Бэкапы

Основное — снапшоты DigitalOcean. Дополнительно: `bash scripts/pg_dump.sh`
(создаёт `dump-YYYYmmdd-HHMM.sql.gz`).

## 7. Наблюдаемость и проверки

- `https://domains.zimbabwe-inc.com/healthz` — liveness.
- `.../readyz` — Postgres+Redis.
- `.../metrics` — Prometheus (кол-во доменов, активные алерты, circuit breaker).
- Логи: `docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f`.

## 8. Откат

```bash
cd /opt/domainguard && git checkout <предыдущий-тег/коммит>
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Откат миграций (если требуется) — `alembic downgrade <revision>` в контейнере
(осторожно: удаляет данные соответствующих таблиц).
