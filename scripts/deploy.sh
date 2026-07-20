#!/usr/bin/env bash
# One-shot / idempotent production deploy for a single Debian host.
# Run ON THE SERVER from the repo root: bash scripts/deploy.sh
set -euo pipefail

DOMAIN="${DEPLOY_DOMAIN:-domains.zimbabwe-inc.com}"
EMAIL="${LETSENCRYPT_EMAIL:-admin@${DOMAIN#*.}}"
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

echo "==> DomainGuard deploy — domain=$DOMAIN"

# 1. Docker (install if missing).
if ! command -v docker >/dev/null 2>&1; then
  echo "==> installing Docker"
  curl -fsSL https://get.docker.com | sh
fi

# 2. .env with generated secrets (only on first run).
if [ ! -f .env ]; then
  echo "==> generating .env"
  cp .env.example .env
  MASTER="$(docker run --rm python:3.12-slim sh -c \
    'pip -q install cryptography >/dev/null 2>&1; python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"')"
  ADMINPW="$(openssl rand -base64 18)"
  sed -i "s|^ENVIRONMENT=.*|ENVIRONMENT=production|" .env
  sed -i "s|^DEBUG=.*|DEBUG=false|" .env
  sed -i "s|^DG_MASTER_KEY=.*|DG_MASTER_KEY=${MASTER}|" .env
  sed -i "s|^DG_ADMIN_PASSWORD=.*|DG_ADMIN_PASSWORD=${ADMINPW}|" .env
  umask 077
  printf 'ADMIN_LOGIN=admin\nADMIN_PASSWORD=%s\n' "$ADMINPW" > .deploy-secrets
  echo "==> admin password saved to $(pwd)/.deploy-secrets"
fi

# 3. Build images.
echo "==> building images"
$COMPOSE build

# 4. Issue the TLS certificate if we don't have one yet (needs port 80 free).
docker volume create domainguard_certbot-etc >/dev/null
docker volume create domainguard_certbot-www >/dev/null
if ! docker run --rm -v domainguard_certbot-etc:/etc/letsencrypt certbot/certbot \
      certificates 2>/dev/null | grep -q "$DOMAIN"; then
  echo "==> obtaining Let's Encrypt certificate for $DOMAIN"
  $COMPOSE stop nginx 2>/dev/null || true
  docker run --rm -p 80:80 \
    -v domainguard_certbot-etc:/etc/letsencrypt \
    -v domainguard_certbot-www:/var/www/certbot \
    certbot/certbot certonly --standalone -d "$DOMAIN" \
    --email "$EMAIL" --agree-tos --non-interactive
fi

# 5. Bring up the full stack (migrations run via the one-shot `migrate` service).
echo "==> starting stack"
$COMPOSE up -d

# 6. Ensure the first admin exists.
sleep 8
$COMPOSE run --rm api python -m scripts.create_admin || true

echo "==> done. https://$DOMAIN"
