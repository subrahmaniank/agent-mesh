#!/usr/bin/env bash
# Bring up the three products that publish their own compose files.
#
# These are deliberately NOT copied into docker-compose.yml: duplicating a
# vendor's internals (Langfuse alone needs Postgres, ClickHouse, Redis and an
# S3-compatible store) guarantees drift the first time they change it. Pulling
# their published compose keeps each product's deployment owned by its authors.
#
#   agentregistry   UI :12121   catalogue + approval workflow
#   Agent Control   :8000 API, UI :4001 (remapped — agentgateway owns 4000)
#   Langfuse        UI :3000    traces, per-session tokens and cost
#
# Usage:  ./scripts/up-vendor-stacks.sh [up|down]

set -euo pipefail

ACTION="${1:-up}"
VENDOR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.vendor"
mkdir -p "$VENDOR_DIR"

fetch() {
  local name="$1" url="$2" dest="$VENDOR_DIR/$name.yml"
  if [ ! -f "$dest" ]; then
    echo "→ fetching $name compose file"
    curl -fsSL "$url" -o "$dest" || {
      echo "  could not fetch $name from $url" >&2
      echo "  check the project's current quickstart and drop the file at $dest" >&2
      return 1
    }
  fi
  echo "  $name: $dest"
}

# ⚠️ These URLs were correct at design time but are the vendors' to change.
# If a fetch fails, take the current URL from the project's quickstart docs.
AGENTREGISTRY_URL="${AGENTREGISTRY_COMPOSE_URL:-https://raw.githubusercontent.com/agentregistry-dev/agentregistry/main/agentregistry-compose.yml}"
AGENTCONTROL_URL="${AGENTCONTROL_COMPOSE_URL:-https://raw.githubusercontent.com/agentcontrol/agent-control/refs/heads/main/docker-compose.yml}"
LANGFUSE_URL="${LANGFUSE_COMPOSE_URL:-https://raw.githubusercontent.com/langfuse/langfuse/main/docker-compose.yml}"

case "$ACTION" in
  up)
    fetch agentregistry "$AGENTREGISTRY_URL" || true
    fetch agentcontrol  "$AGENTCONTROL_URL"  || true
    fetch langfuse      "$LANGFUSE_URL"      || true

    for stack in agentregistry agentcontrol langfuse; do
      file="$VENDOR_DIR/$stack.yml"
      [ -f "$file" ] || { echo "skipping $stack (no compose file)"; continue; }
      echo "→ starting $stack"
      # Agent Control's UI defaults to 4000, which agentgateway already owns.
      if [ "$stack" = "agentcontrol" ]; then
        AGENT_CONTROL_UI_PORT=4001 docker compose -p "$stack" -f "$file" up -d
      else
        docker compose -p "$stack" -f "$file" up -d
      fi
    done

    cat <<'EOF'

Vendor stacks starting. Once healthy:
  agentregistry  http://localhost:12121
  Agent Control  http://localhost:4001   (API http://localhost:8000)
  Langfuse       http://localhost:3000

Next: create a Langfuse project, then put its keys in .env as
LANGFUSE_BASIC_AUTH=$(printf 'pk-...:sk-...' | base64) so the OTel collector
can forward traces.
EOF
    ;;
  down)
    for stack in agentregistry agentcontrol langfuse; do
      file="$VENDOR_DIR/$stack.yml"
      [ -f "$file" ] && docker compose -p "$stack" -f "$file" down || true
    done
    ;;
  *)
    echo "usage: $0 [up|down]" >&2
    exit 2
    ;;
esac
