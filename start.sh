#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

NODE_MIN_MAJOR=20

info() { echo "[INFO] $*"; }
warn() { echo "[WARN] $*"; }
err() { echo "[ERROR] $*" >&2; }

# ── Proxy helpers ──

normalize_proxy_url() {
  local value="$1"
  if [[ "$value" == socks://* ]]; then
    printf 'socks5://%s' "${value#socks://}"
    return
  fi
  printf '%s' "$value"
}

ensure_proxy_env() {
  local has_proxy=""
  for name in ALL_PROXY all_proxy HTTPS_PROXY https_proxy HTTP_PROXY http_proxy; do
    if [[ -n "${!name:-}" ]]; then has_proxy="yes"; break; fi
  done

  if [[ -z "$has_proxy" ]]; then
    for port in 7897 7890 1080; do
      if python3 -c "import socket; s=socket.socket(); s.settimeout(0.2); ok=(s.connect_ex(('127.0.0.1', $port))==0); s.close(); raise SystemExit(0 if ok else 1)" >/dev/null 2>&1; then
        export HTTP_PROXY="http://127.0.0.1:${port}/"
        export http_proxy="$HTTP_PROXY"
        export HTTPS_PROXY="$HTTP_PROXY"
        export https_proxy="$HTTP_PROXY"
        export ALL_PROXY="socks5://127.0.0.1:${port}/"
        export all_proxy="$ALL_PROXY"
        info "Auto-detected local proxy on port ${port}."
        break
      fi
    done
  fi

  for name in ALL_PROXY all_proxy HTTPS_PROXY https_proxy HTTP_PROXY http_proxy; do
    if [[ -n "${!name:-}" ]]; then
      export "$name=$(normalize_proxy_url "${!name}")"
    fi
  done
}

# ── Deploy helpers ──

ensure_uv() {
  if command -v uv >/dev/null 2>&1; then return 0; fi
  warn "uv not found, installing..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
  if ! command -v uv >/dev/null 2>&1; then
    err "uv install failed. Install manually: https://docs.astral.sh/uv/"
    exit 1
  fi
}

need_deploy() {
  [[ ! -d "frontend/dist" ]] && return 0
  [[ ! -d ".venv" ]] && return 0
  return 1
}

do_deploy() {
  info "First run detected, running setup..."

  # Check basic tools
  if ! command -v node >/dev/null 2>&1; then
    err "Node.js ${NODE_MIN_MAJOR}+ required. Install: https://nodejs.org/"
    exit 1
  fi
  local node_major
  node_major="$(node -p "process.versions.node.split('.')[0]")"
  if [[ "$node_major" -lt "$NODE_MIN_MAJOR" ]]; then
    err "Node.js too old: $(node -v), need >= ${NODE_MIN_MAJOR}.x"
    exit 1
  fi

  ensure_uv

  # Backend deps
  info "[1/3] Installing backend dependencies..."
  uv sync

  # Frontend
  info "[2/3] Building frontend..."
  pushd frontend >/dev/null
  if [[ -f package-lock.json ]]; then
    npm ci --no-audit --fund=false || npm install --no-audit --fund=false
  else
    npm install --no-audit --fund=false
  fi
  npm run build
  popd >/dev/null

  # Config
  mkdir -p config
  if [[ ! -f config/settings.json && -f config/settings.example.json ]]; then
    cp config/settings.example.json config/settings.json
    info "Created config/settings.json"
  fi

  info "Setup complete."
}

# ── Main ──

echo "=========================================="
echo "  ShengWen"
echo "=========================================="
echo

if need_deploy; then
  do_deploy
  echo
fi

if [[ ! -d "frontend/dist" ]]; then
  err "frontend/dist not found. Fix: cd frontend && npm run build"
  exit 1
fi

ensure_proxy_env
info "Starting server..."
exec uv run python ShengWen-app.py
