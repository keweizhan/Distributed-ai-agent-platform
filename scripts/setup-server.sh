#!/usr/bin/env bash
# setup-server.sh — provision a fresh Ubuntu 22.04 LTS server for deployment.
#
# Run as root (or with sudo):
#   curl -sSL https://raw.githubusercontent.com/.../scripts/setup-server.sh | sudo bash
#   # or after cloning the repo:
#   sudo bash scripts/setup-server.sh
#
# What this script does:
#   1. System update
#   2. Install Docker Engine + Compose plugin
#   3. Install certbot (Let's Encrypt TLS)
#   4. Configure UFW firewall (22, 80, 443 only)
#   5. Create app directory at /opt/agent
#   6. Create a non-root deploy user
#   7. Print next-step instructions
set -euo pipefail

# ─── Guard ────────────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
  echo "ERROR: This script must be run as root (use sudo)."
  exit 1
fi

UBUNTU_VERSION=$(lsb_release -rs 2>/dev/null || echo "unknown")
echo ""
echo "=== Agent Platform — Server Setup ==="
echo "  Ubuntu: ${UBUNTU_VERSION}"
echo "  $(date)"
echo ""

# ─── 1. System update ─────────────────────────────────────────────────────
echo "── 1. Updating system packages..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get upgrade -y -qq
apt-get install -y -qq \
  curl \
  git \
  ca-certificates \
  gnupg \
  lsb-release \
  ufw \
  htop \
  jq

# ─── 2. Docker Engine ─────────────────────────────────────────────────────
echo "── 2. Installing Docker Engine..."
if command -v docker &>/dev/null; then
  echo "   Docker already installed: $(docker --version)"
else
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg

  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
    https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
    > /etc/apt/sources.list.d/docker.list

  apt-get update -qq
  apt-get install -y -qq \
    docker-ce docker-ce-cli containerd.io \
    docker-buildx-plugin docker-compose-plugin

  systemctl enable --now docker
  echo "   Docker installed: $(docker --version)"
fi

# ─── 3. Certbot ───────────────────────────────────────────────────────────
echo "── 3. Installing certbot..."
if command -v certbot &>/dev/null; then
  echo "   certbot already installed: $(certbot --version)"
else
  snap install --classic certbot 2>/dev/null || apt-get install -y -qq certbot
  echo "   certbot installed: $(certbot --version)"
fi

# ─── 3b. Certbot renewal hooks ────────────────────────────────────────────
# Certbot was issued with --standalone (port 80, nothing else running yet).
# Renewals also use standalone, so nginx must briefly release port 80.
# These hooks stop nginx before certbot attempts renewal and restart it after.
echo "── 3b. Installing certbot renewal hooks..."
PRE_HOOK="/etc/letsencrypt/renewal-hooks/pre/stop-nginx.sh"
POST_HOOK="/etc/letsencrypt/renewal-hooks/post/start-nginx.sh"

cat > "$PRE_HOOK" <<'HOOK'
#!/bin/bash
# Stop nginx so certbot standalone mode can bind port 80 for renewal.
cd /opt/agent/app
docker compose -f docker-compose.prod.yml stop nginx 2>/dev/null || true
HOOK

cat > "$POST_HOOK" <<'HOOK'
#!/bin/bash
# Restart nginx after certbot has renewed the certificate.
cd /opt/agent/app
docker compose -f docker-compose.prod.yml start nginx 2>/dev/null || true
HOOK

chmod +x "$PRE_HOOK" "$POST_HOOK"
echo "   Pre-hook:  $PRE_HOOK"
echo "   Post-hook: $POST_HOOK"

# ─── 4. Firewall ──────────────────────────────────────────────────────────
echo "── 4. Configuring UFW firewall..."
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp   comment "SSH"
ufw allow 80/tcp   comment "HTTP (ACME challenge + redirect)"
ufw allow 443/tcp  comment "HTTPS (API)"
ufw --force enable
echo "   UFW status:"
ufw status verbose | grep -E "^(Status|To|ALLOW|DENY)" | sed 's/^/   /'

# ─── 5. App directory ─────────────────────────────────────────────────────
echo "── 5. Creating app directory /opt/agent..."
APP_DIR="/opt/agent"
mkdir -p "$APP_DIR"
echo "   Directory: $APP_DIR"

# ─── 6. Deploy user ───────────────────────────────────────────────────────
echo "── 6. Creating deploy user..."
DEPLOY_USER="agent"
if id "$DEPLOY_USER" &>/dev/null; then
  echo "   User '$DEPLOY_USER' already exists."
else
  useradd -m -s /bin/bash "$DEPLOY_USER"
  echo "   Created user: $DEPLOY_USER"
fi

# Add to docker group so they can run docker compose without sudo
usermod -aG docker "$DEPLOY_USER"
chown -R "$DEPLOY_USER:$DEPLOY_USER" "$APP_DIR"
echo "   Added '$DEPLOY_USER' to docker group."
echo "   Owns: $APP_DIR"

# ─── 7. Instructions ──────────────────────────────────────────────────────
echo ""
echo "=== Server setup complete ==="
echo ""
echo "Next steps:"
echo ""
echo "  1. Switch to the deploy user and clone the repo:"
echo "       su - ${DEPLOY_USER}"
echo "       git clone <your-repo-url> /opt/agent/app"
echo "       cd /opt/agent/app"
echo ""
echo "  2. Generate production secrets:"
echo "       bash scripts/gen-env.sh"
echo ""
echo "  3. Substitute your domain in the nginx config:"
echo "       DOMAIN=\$(grep SERVER_NAME .env.prod | cut -d= -f2)"
echo "       sed -i \"s/SERVER_NAME/\${DOMAIN}/g\" infra/nginx/nginx.conf"
echo ""
echo "  4. Obtain a TLS certificate (requires domain DNS → this server's IP):"
echo "       # Standalone mode — temporarily binds port 80:"
echo "       certbot certonly --standalone -d \$DOMAIN --non-interactive --agree-tos -m admin@\$DOMAIN"
echo ""
echo "  5. Deploy:"
echo "       docker compose -f docker-compose.prod.yml up -d --build"
echo ""
echo "  6. Verify:"
echo "       docker compose -f docker-compose.prod.yml ps"
echo "       curl -sf https://\${DOMAIN}/health | jq ."
echo ""
echo "  Grafana (SSH tunnel — run from your LOCAL machine):"
echo "       ssh -L 3000:localhost:3000 ${DEPLOY_USER}@<server-ip>"
echo "       open http://localhost:3000   # user: admin, pass: from .env.prod"
echo ""
