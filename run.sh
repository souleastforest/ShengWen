#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

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
    if [[ -n "${!name:-}" ]]; then
      has_proxy="yes"
      break
    fi
  done

  if [[ -z "$has_proxy" ]]; then
    local candidate_ports=(7897 7890 1080)
    for port in "${candidate_ports[@]}"; do
      if python3 -c "import socket; s=socket.socket(); s.settimeout(0.2); ok=(s.connect_ex(('127.0.0.1', $port))==0); s.close(); raise SystemExit(0 if ok else 1)" >/dev/null 2>&1; then
        export HTTP_PROXY="http://127.0.0.1:${port}/"
        export http_proxy="$HTTP_PROXY"
        export HTTPS_PROXY="$HTTP_PROXY"
        export https_proxy="$HTTP_PROXY"
        export ALL_PROXY="socks5://127.0.0.1:${port}/"
        export all_proxy="$ALL_PROXY"
        echo "[INFO] Auto-detected local proxy on port ${port}."
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

echo "=========================================="
echo "  ShengWen"
echo "=========================================="
echo

if ! command -v uv >/dev/null 2>&1; then
  echo "[ERROR] uv not found. Install: https://docs.astral.sh/uv/"
  exit 1
fi

if [[ ! -d "frontend/dist" ]]; then
  echo "[ERROR] frontend/dist not found. Run ./deploy.sh first."
  exit 1
fi

ensure_proxy_env
exec uv run python ShengWen-app.py
