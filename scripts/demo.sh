#!/usr/bin/env bash
# demo.sh — end-to-end walkthrough of the Distributed AI Agent Platform
#
# Prerequisites:
#   docker compose up --build   (stack must be running)
#   jq                          (brew install jq / apt install jq)
#
# Usage:
#   bash scripts/demo.sh
#
# All output is annotated. Exit on first error.
set -euo pipefail

API="http://localhost:8000"

echo "=============================="
echo " Distributed AI Agent Platform"
echo " End-to-End Demo"
echo "=============================="
echo ""

# ─── 1. Health check ───────────────────────────────────────────────────────
echo "── 1. Verify the API is healthy"
curl -sf "$API/health" | jq .
echo ""

# ─── 2. Register ──────────────────────────────────────────────────────────
echo "── 2. Register a new user (also creates a workspace)"
REGISTER=$(curl -sf -X POST "$API/auth/register" \
  -H "Content-Type: application/json" \
  -d '{"email":"demo@example.com","password":"demopassword","workspace_name":"demo"}')
echo "$REGISTER" | jq .
echo ""

# ─── 3. Obtain a JWT ──────────────────────────────────────────────────────
echo "── 3. Obtain a JWT token"
TOKEN=$(curl -sf -X POST "$API/auth/token" \
  -d "username=demo@example.com&password=demopassword" \
  | jq -r .access_token)
echo "Token: ${TOKEN:0:60}..."
echo ""

# ─── 4. Inspect current user ──────────────────────────────────────────────
echo "── 4. Inspect current user"
curl -sf "$API/auth/me" \
  -H "Authorization: Bearer $TOKEN" | jq .
echo ""

# ─── 5. Submit a job ──────────────────────────────────────────────────────
echo "── 5. Submit a job (MockPlanner — no API key required)"
JOB_RESP=$(curl -sf -X POST "$API/jobs" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"prompt":"Research recent advances in transformer architectures and summarise the key findings"}')
echo "$JOB_RESP" | jq .
JOB_ID=$(echo "$JOB_RESP" | jq -r .id)
echo "Job ID: $JOB_ID"
echo ""

# ─── 6. Poll until terminal ───────────────────────────────────────────────
echo "── 6. Poll until the job reaches a terminal state"
for i in $(seq 1 30); do
  STATUS=$(curl -sf "$API/jobs/$JOB_ID" \
    -H "Authorization: Bearer $TOKEN" | jq -r .status)
  echo "  [${i}s] status = $STATUS"
  if [[ "$STATUS" == "succeeded" || "$STATUS" == "failed" || "$STATUS" == "cancelled" ]]; then
    break
  fi
  sleep 2
done
echo ""

# ─── 7. Print final job detail ────────────────────────────────────────────
echo "── 7. Final job detail"
JOB_DETAIL=$(curl -sf "$API/jobs/$JOB_ID" \
  -H "Authorization: Bearer $TOKEN")
echo "$JOB_DETAIL" | jq '{id, status, result, task_count: (.tasks | length)}'
echo ""

# ─── 8. Inspect the web_search task output ────────────────────────────────
echo "── 8. Inspect tool outputs from the web_search task"
TASK_ID=$(echo "$JOB_DETAIL" \
  | jq -r '.tasks[] | select(.tool_name=="web_search") | .id' | head -1)

if [[ -n "$TASK_ID" ]]; then
  curl -sf "$API/jobs/$JOB_ID/tasks/$TASK_ID" \
    -H "Authorization: Bearer $TOKEN" \
    | jq '{step_id, status, tool_name, tool_output, attempt_count, started_at, finished_at}'
else
  echo "  (no web_search task found)"
fi
echo ""

# ─── 9. Cancel demo ───────────────────────────────────────────────────────
echo "── 9. Submit a second job and cancel it immediately"
CANCEL_JOB=$(curl -sf -X POST "$API/jobs" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"prompt":"A background task to cancel"}' | jq -r .id)
echo "Submitted job: $CANCEL_JOB"

CANCEL_RESP=$(curl -sf -X POST "$API/jobs/$CANCEL_JOB/cancel" \
  -H "Authorization: Bearer $TOKEN")
echo "$CANCEL_RESP" | jq '{id, status}'
echo ""

# ─── 10. List all jobs ────────────────────────────────────────────────────
echo "── 10. List jobs in this workspace"
curl -sf "$API/jobs" \
  -H "Authorization: Bearer $TOKEN" \
  | jq '[.[] | {id, status, prompt: .prompt[:60]}]'
echo ""

# ─── 11. Prometheus metrics ───────────────────────────────────────────────
echo "── 11. Sample Prometheus metrics"
curl -sf http://localhost:9090/metrics \
  | grep -E "^agent_(jobs|task_executions|tool_calls)" | head -20
echo ""

echo "=============================="
echo " Demo complete."
echo " Grafana dashboard: http://localhost:3000"
echo " Swagger UI:        http://localhost:8000/docs"
echo "=============================="
