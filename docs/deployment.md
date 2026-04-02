# Production Deployment Guide

Single Ubuntu 22.04 server, Docker Compose, Let's Encrypt TLS.

---

## Architecture

```
Internet
    │
    │  TCP 80, 443
    ▼
┌──────────────────────────────────────────────────────┐
│  UFW Firewall  (22, 80, 443 only)                    │
└──────────────────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────────────────┐
│  nginx:1.25  (container, ports 80+443)               │
│  - HTTP  → 301 redirect to HTTPS                     │
│  - HTTPS → proxy_pass api:8000                       │
│  - TLS cert: /etc/letsencrypt (certbot-managed)      │
└───────────────────────┬──────────────────────────────┘
                        │  Docker internal network
                        ▼
┌──────────────────────────────────────────────────────┐
│  FastAPI  api:8000    (no public port)               │
└───────────┬───────────────────────────────────────────┘
            │
    ┌───────┴────────┐
    ▼                ▼
postgres:5432    redis:6379         ← internal only
                    │
                    ▼
             Celery worker          ← internal only
             ├─ planner
             ├─ executor
             └─ code_exec sandbox (docker.sock)

Observability (no public ports):
  prometheus:9090  ← scraped by Grafana internally
  grafana:3000     ← 127.0.0.1:3000 only (SSH tunnel)
  qdrant:6333      ← optional, internal only
```

Port exposure summary:

| Service | Public | How to reach |
|---------|--------|-------------|
| API (HTTPS) | 443 | `https://your-domain.com` |
| API (HTTP redirect) | 80 | → HTTPS |
| SSH | 22 | `ssh user@server` |
| Grafana | **No** | SSH tunnel (see below) |
| Prometheus | **No** | SSH tunnel |
| Postgres | **No** | `docker exec` on server |
| Redis | **No** | `docker exec` on server |
| Qdrant | **No** | `docker exec` on server |

---

## Prerequisites

- Ubuntu 22.04 LTS VPS (2 vCPU / 4 GB RAM minimum; 4 vCPU / 8 GB recommended)
- A domain name with an A record pointing at the server's IP
- SSH access as root or a user with sudo
- Git access to the repo

---

## Step 1 — Provision the server

Run the setup script **as root** on the server:

```bash
# Copy the script to the server, or clone the repo first
sudo bash scripts/setup-server.sh
```

This installs Docker Engine, certbot, configures UFW, creates `/opt/agent`, and creates an `agent` OS user in the `docker` group.

Verify UFW after the script:

```bash
sudo ufw status verbose
# Should show: 22/tcp, 80/tcp, 443/tcp ALLOW IN; everything else DENY
```

---

## Step 2 — Deploy the code

```bash
su - agent
git clone <your-repo-url> /opt/agent/app
cd /opt/agent/app
```

---

## Step 3 — Generate production secrets

```bash
bash scripts/gen-env.sh
```

The script prompts for:
- Your domain name (e.g. `api.example.com`)
- OpenAI API key (press Enter for MockPlanner)
- Whether to enable the Qdrant memory layer

It writes `.env.prod` (mode 600) with randomly generated passwords and keys. **Never commit this file.**

---

## Step 4 — Configure nginx

Substitute your domain into the nginx config:

```bash
DOMAIN=$(grep ^SERVER_NAME .env.prod | cut -d= -f2)
sed -i "s/SERVER_NAME/${DOMAIN}/g" infra/nginx/nginx.conf
```

Verify the substitution:

```bash
grep server_name infra/nginx/nginx.conf
# → server_name api.example.com;
```

---

## Step 5 — Obtain a TLS certificate

DNS must already resolve to this server before running certbot.

```bash
DOMAIN=$(grep ^SERVER_NAME .env.prod | cut -d= -f2)

# Standalone mode temporarily binds port 80 (nothing else should be on 80 yet)
sudo certbot certonly \
  --standalone \
  -d "$DOMAIN" \
  --non-interactive \
  --agree-tos \
  -m "admin@${DOMAIN}"
```

Certbot writes the certificate to `/etc/letsencrypt/live/$DOMAIN/`. The nginx container mounts `/etc/letsencrypt` read-only.

**Certificate renewal** is handled by certbot's built-in systemd timer.

Because the cert was issued with `--standalone`, renewals also use standalone mode — which requires port 80 to be free. `scripts/setup-server.sh` installs pre/post hooks that stop nginx before certbot runs and restart it after:

- `/etc/letsencrypt/renewal-hooks/pre/stop-nginx.sh`
- `/etc/letsencrypt/renewal-hooks/post/start-nginx.sh`

If you provisioned the server before this was added, create them manually:

```bash
cat > /etc/letsencrypt/renewal-hooks/pre/stop-nginx.sh <<'EOF'
#!/bin/bash
cd /opt/agent/app
docker compose -f docker-compose.prod.yml stop nginx 2>/dev/null || true
EOF

cat > /etc/letsencrypt/renewal-hooks/post/start-nginx.sh <<'EOF'
#!/bin/bash
cd /opt/agent/app
docker compose -f docker-compose.prod.yml start nginx 2>/dev/null || true
EOF

chmod +x /etc/letsencrypt/renewal-hooks/pre/stop-nginx.sh \
         /etc/letsencrypt/renewal-hooks/post/start-nginx.sh
```

Test the renewal process (dry run — no actual cert change, but exercises the hooks):

```bash
sudo certbot renew --dry-run
```

---

## Step 6 — Deploy

```bash
cd /opt/agent/app

# Build images and start all services detached
docker compose -f docker-compose.prod.yml up -d --build

# Watch startup
docker compose -f docker-compose.prod.yml logs -f --tail=50
```

---

## Step 7 — Verify

```bash
DOMAIN=$(grep ^SERVER_NAME .env.prod | cut -d= -f2)

# API health
curl -sf "https://${DOMAIN}/health" | jq .
# → {"status": "ok"}

# API readiness (checks DB)
curl -sf "https://${DOMAIN}/ready" | jq .
# → {"status": "ready", "db": "ok"}

# Service health
docker compose -f docker-compose.prod.yml ps
# All services should show "healthy" or "running"
```

End-to-end smoke test (uses the demo script):

```bash
API="https://${DOMAIN}" bash scripts/demo.sh
```

> **Note:** `scripts/demo.sh` step 11 attempts to scrape `http://localhost:9090/metrics` directly.
> Prometheus is not exposed on the public server, so that step will fail and abort the script.
> Comment out or delete step 11 in `demo.sh` when running the smoke test against the production server.

---

## Accessing Grafana (SSH tunnel)

Grafana binds to `127.0.0.1:3000` on the server — not reachable from the internet.

**From your local machine:**

```bash
ssh -L 3000:localhost:3000 agent@<server-ip>
```

Then open `http://localhost:3000` in your browser.

Login: `admin` / the `GRAFANA_ADMIN_PASSWORD` from `.env.prod`.

The **Agent Platform** dashboard is pre-provisioned and loads automatically.

To also access Prometheus directly:

```bash
ssh -L 9091:localhost:9091 agent@<server-ip>
# Then open http://localhost:9091
```

(Requires adding `- "127.0.0.1:9091:9090"` to prometheus ports in docker-compose.prod.yml first.)

---

## Upgrades

```bash
cd /opt/agent/app
git pull

# Rebuild images and restart changed services only
docker compose -f docker-compose.prod.yml up -d --build

# If DB schema changed:
docker compose -f docker-compose.prod.yml exec postgres \
  psql -U agent -d agentdb < infra/migrate_<version>.sql
```

Zero-downtime upgrades are not in scope for a single-VM deployment — expect a brief restart gap during `up -d --build`.

---

## Backup

### Postgres

```bash
docker compose -f docker-compose.prod.yml exec -T postgres \
  pg_dump -U agent agentdb | gzip > "backup_$(date +%Y%m%d_%H%M%S).sql.gz"
```

### Volumes

Docker named volumes live at `/var/lib/docker/volumes/`. Back them up with:

```bash
# Docker volume names are prefixed with the Compose project name (defaults to the
# directory name — "app" if the repo is at /opt/agent/app).
# Verify with: docker volume ls | grep -E 'postgres|redis|grafana|qdrant'
for vol in app_postgres_data app_redis_data app_grafana_data app_qdrant_data; do
  docker run --rm \
    -v "${vol}:/data:ro" \
    -v "$(pwd)/backups:/backup" \
    alpine tar czf "/backup/${vol}_$(date +%Y%m%d).tar.gz" /data
done
```

---

## Logs

```bash
# All services
docker compose -f docker-compose.prod.yml logs -f

# Single service
docker compose -f docker-compose.prod.yml logs -f api
docker compose -f docker-compose.prod.yml logs -f worker

# Nginx access log
docker compose -f docker-compose.prod.yml exec nginx tail -f /var/log/nginx/access.log
```

Log files are rotated automatically (50 MB / 5 files per container, configured in docker-compose.prod.yml).

---

## Troubleshooting

| Symptom | Check |
|---------|-------|
| `curl https://domain/health` returns connection refused | `docker compose ps` — is nginx running? Is port 443 open in UFW? |
| nginx 502 Bad Gateway | `docker compose logs api` — is the api container healthy? Did it start? |
| Worker not processing jobs | `docker compose logs worker` — look for Celery startup errors or Redis auth failures |
| Postgres connection errors | Check DATABASE_URL in .env.prod matches POSTGRES_PASSWORD |
| Redis auth failure | Check REDIS_PASSWORD matches in all three URLs (REDIS_URL, CELERY_BROKER_URL, CELERY_RESULT_BACKEND) |
| TLS cert not found | Verify `/etc/letsencrypt/live/$DOMAIN/fullchain.pem` exists; re-run certbot if not |
| Grafana login fails | Check GRAFANA_ADMIN_PASSWORD in .env.prod; try `docker compose restart grafana` |

---

## Known Limitations

1. **Single point of failure** — one VM means downtime during OS updates or hardware failure. Mitigation: nightly Postgres backups off-server; VM snapshot.

2. **Worker `--concurrency=2`** — Prometheus metrics are captured in the main Celery process. Child workers report metrics only if `PROMETHEUS_MULTIPROC_DIR` is set. For now, metrics from child processes may be missing under high concurrency. Safe to leave as-is at this scale.

3. **`docker.sock` mount** — the worker container has access to the Docker socket for sandbox execution. This is a known privileged operation. If the worker is compromised, it can control the Docker daemon. Mitigate by using a Docker socket proxy (e.g. `tecnativa/docker-socket-proxy`) to restrict allowed API calls.

4. **No rate limiting** — nginx does not apply request rate limits. For public deployments, add `limit_req_zone` to nginx.conf.

5. **JWT secret rotation** — changing `JWT_SECRET_KEY` invalidates all active sessions. Acceptable for V1; a key rotation strategy is needed for production with persistent users.

6. **Certbot renewal requires port 80** — if nginx is down during renewal, certbot's webroot challenge may fail. The renewal hook in Step 5 mitigates this, but monitor `certbot renew --dry-run` periodically.
