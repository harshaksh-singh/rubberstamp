#!/usr/bin/env bash
# Start rs-api and the helpdesk UI together (Ctrl-C stops both).
#
#   bash scripts/dev_helpdesk.sh           # next dev (hot reload)
#   bash scripts/dev_helpdesk.sh --prod    # next build, then next start
#
# API: http://127.0.0.1:${RS_API_PORT:-8787}   UI: http://127.0.0.1:${HELPDESK_PORT:-3000}
# Both listen on the loopback interface only. Next.js telemetry is disabled.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
API_PORT="${RS_API_PORT:-8787}"
UI_PORT="${HELPDESK_PORT:-3000}"
MODE="dev"
case "${1:-}" in
  "") ;;
  --prod) MODE="prod" ;;
  -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
  *) echo "usage: $0 [--prod]" >&2; exit 2 ;;
esac

export NEXT_TELEMETRY_DISABLED=1
export NEXT_PUBLIC_RS_API_URL="${NEXT_PUBLIC_RS_API_URL:-http://127.0.0.1:${API_PORT}}"
export RS_API_UI_ORIGINS="${RS_API_UI_ORIGINS:-http://localhost:${UI_PORT},http://127.0.0.1:${UI_PORT}}"

for port in "$API_PORT" "$UI_PORT"; do
  if curl -s -o /dev/null --max-time 1 "http://127.0.0.1:${port}/"; then
    echo "dev_helpdesk: port ${port} is already in use; stop that server or set RS_API_PORT / HELPDESK_PORT" >&2
    exit 1
  fi
done

command -v uv >/dev/null || { echo "dev_helpdesk: uv is required (https://docs.astral.sh/uv/)" >&2; exit 1; }
command -v npm >/dev/null || { echo "dev_helpdesk: Node.js 20.9+ and npm are required" >&2; exit 1; }
if [ ! -d "$ROOT/apps/helpdesk/node_modules" ]; then
  echo "dev_helpdesk: installing the UI's dependencies (npm ci)"
  (cd "$ROOT/apps/helpdesk" && npm ci --no-audit --no-fund)
fi

kill_tree() {
  local pid="$1" child
  for child in $(pgrep -P "$pid" 2>/dev/null); do kill_tree "$child"; done
  kill "$pid" 2>/dev/null || true
}
PIDS=()
stop() {
  trap - INT TERM EXIT
  for pid in "${PIDS[@]:-}"; do [ -n "$pid" ] && kill_tree "$pid"; done
  wait 2>/dev/null || true
}
trap stop INT TERM EXIT

cd "$ROOT"
uv run rs-api --port "$API_PORT" &
PIDS+=($!)

cd "$ROOT/apps/helpdesk"
if [ "$MODE" = "prod" ]; then
  npm run build
  npx next start --hostname 127.0.0.1 --port "$UI_PORT" &
else
  npx next dev --hostname 127.0.0.1 --port "$UI_PORT" &
fi
PIDS+=($!)

for _ in $(seq 1 120); do
  if curl -sf -o /dev/null "http://127.0.0.1:${API_PORT}/health" && curl -s -o /dev/null "http://127.0.0.1:${UI_PORT}/"; then
    echo
    echo "  Helpdesk console  http://127.0.0.1:${UI_PORT}"
    echo "  rs-api            http://127.0.0.1:${API_PORT}  (OpenAPI docs at /docs)"
    echo
    break
  fi
  sleep 1
done

# Stop both as soon as either exits.
while kill -0 "${PIDS[0]}" 2>/dev/null && kill -0 "${PIDS[1]}" 2>/dev/null; do sleep 1; done
echo "dev_helpdesk: a server exited; stopping the other" >&2
exit 1
