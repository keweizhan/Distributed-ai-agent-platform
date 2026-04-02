#!/usr/bin/env bash
# gen-env.sh — generate .env.prod with random secrets for production deployment.
#
# Usage:
#   bash scripts/gen-env.sh
#
# Outputs: .env.prod (never commit this file)
set -euo pipefail

OUT=".env.prod"

if [[ -f "$OUT" ]]; then
  echo "ERROR: $OUT already exists. Remove it first if you want to regenerate."
  exit 1
fi

require_cmd() {
  command -v "$1" &>/dev/null || { echo "ERROR: '$1' is required but not found."; exit 1; }
}
require_cmd python3

rand_hex() {
  python3 -c "import secrets; print(secrets.token_hex($1))"
}
rand_pass() {
  python3 -c "import secrets, string; print(''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range($1)))"
}

echo ""
echo "=== Distributed AI Agent Platform — Production Environment Generator ==="
echo ""

# Domain
read -rp "Server domain name (e.g. api.example.com): " SERVER_NAME
[[ -z "$SERVER_NAME" ]] && { echo "ERROR: Domain name is required."; exit 1; }

# OpenAI key (optional)
read -rp "OpenAI API key (press Enter to use MockPlanner): " OPENAI_API_KEY
OPENAI_API_KEY="${OPENAI_API_KEY:-sk-not-set}"

# Memory
read -rp "Enable Qdrant memory layer? [y/N]: " MEMORY_CHOICE
MEMORY_ENABLED="false"
[[ "${MEMORY_CHOICE,,}" == "y" ]] && MEMORY_ENABLED="true"

# Generate secrets
echo ""
echo "Generating secrets..."
POSTGRES_PASSWORD=$(rand_pass 24)
REDIS_PASSWORD=$(rand_pass 24)
JWT_SECRET_KEY=$(rand_hex 32)
GRAFANA_ADMIN_PASSWORD=$(rand_pass 20)

cat > "$OUT" <<EOF
# ============================================================
#  Production environment — KEEP THIS FILE SECRET
#  Generated: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
# ============================================================

# Server
SERVER_NAME=${SERVER_NAME}

# Database
POSTGRES_USER=agent
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
POSTGRES_DB=agentdb
DATABASE_URL=postgresql+asyncpg://agent:${POSTGRES_PASSWORD}@postgres:5432/agentdb

# Redis / Celery  (password applied by docker-compose.prod.yml redis command)
REDIS_PASSWORD=${REDIS_PASSWORD}
REDIS_URL=redis://:${REDIS_PASSWORD}@redis:6379/0
CELERY_BROKER_URL=redis://:${REDIS_PASSWORD}@redis:6379/0
CELERY_RESULT_BACKEND=redis://:${REDIS_PASSWORD}@redis:6379/1

# LLM
OPENAI_API_KEY=${OPENAI_API_KEY}
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini

# Sandbox — docker requires /var/run/docker.sock mount (already in docker-compose.prod.yml)
SANDBOX_BACKEND=docker
SANDBOX_IMAGE=python:3.11-slim
SANDBOX_TIMEOUT_SECONDS=30

# Worker metrics
WORKER_METRICS_PORT=9090

# JWT auth
JWT_SECRET_KEY=${JWT_SECRET_KEY}
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=60

# Grafana
GRAFANA_ADMIN_PASSWORD=${GRAFANA_ADMIN_PASSWORD}

# Memory layer
MEMORY_ENABLED=${MEMORY_ENABLED}
QDRANT_URL=http://qdrant:6333
QDRANT_COLLECTION=agent_memory
EMBEDDING_MODEL=text-embedding-3-small
EOF

chmod 600 "$OUT"

echo ""
echo "✓ Written: $OUT  (mode 600)"
echo ""
echo "Next: update infra/nginx/nginx.conf — replace SERVER_NAME with: ${SERVER_NAME}"
echo "  sed -i 's/SERVER_NAME/${SERVER_NAME}/g' infra/nginx/nginx.conf"
echo ""
echo "Grafana admin password: ${GRAFANA_ADMIN_PASSWORD}"
echo "  (save this somewhere safe)"
echo ""
