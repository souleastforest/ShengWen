#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

NODE_MIN_MAJOR=20
PYTHON_MIN_MAJOR=3
PYTHON_MIN_MINOR=10

info() { echo "[INFO] $*"; }
warn() { echo "[WARN] $*"; }
err() { echo "[ERROR] $*" >&2; }

require_cmd() {
  local cmd="$1"
  local hint="${2:-}"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    err "Missing command: $cmd"
    if [[ -n "$hint" ]]; then
      err "$hint"
    fi
    exit 1
  fi
}

detect_linux_pkg_manager() {
  if command -v apt-get >/dev/null 2>&1; then echo "apt"; return; fi
  if command -v dnf >/dev/null 2>&1; then echo "dnf"; return; fi
  if command -v yum >/dev/null 2>&1; then echo "yum"; return; fi
  if command -v pacman >/dev/null 2>&1; then echo "pacman"; return; fi
  if command -v zypper >/dev/null 2>&1; then echo "zypper"; return; fi
  echo ""
}

install_linux_deps() {
  local mgr
  mgr="$(detect_linux_pkg_manager)"
  if [[ -z "$mgr" ]]; then
    warn "Unknown package manager, skipping system deps. Please install: git python3-venv"
    return
  fi

  local no_sudo_action="skip"
  if ! command -v sudo >/dev/null 2>&1 || ! sudo -n true >/dev/null 2>&1; then
    warn "No sudo access, cannot auto-install system deps."
    if [[ -t 0 ]]; then
      echo "Choose:"
      echo "1) Skip and continue (default)"
      echo "2) Exit"
      echo "3) Print manual commands and continue"
      read -r -p "Enter 1/2/3 [1]: " choice
      case "${choice:-1}" in
        2) no_sudo_action="exit" ;;
        3) no_sudo_action="print" ;;
        *) no_sudo_action="skip" ;;
      esac
    fi
  fi

  if [[ "$no_sudo_action" == "exit" ]]; then exit 1; fi

  if [[ "$no_sudo_action" == "print" ]]; then
    echo "Manual install commands:"
    case "$mgr" in
      apt) echo "sudo apt-get update && sudo apt-get install -y git python3-venv" ;;
      dnf) echo "sudo dnf install -y git python3 python3-pip python3-virtualenv" ;;
      yum) echo "sudo yum install -y git python3 python3-pip" ;;
      pacman) echo "sudo pacman -Sy --noconfirm git python python-virtualenv" ;;
      zypper) echo "sudo zypper --non-interactive install git python3 python3-pip python3-virtualenv" ;;
    esac
  fi

  if ! command -v sudo >/dev/null 2>&1 || ! sudo -n true >/dev/null 2>&1; then
    warn "Skipping system deps. Please ensure git and python3-venv are installed."
    return
  fi

  info "Installing missing system deps (requires sudo)..."
  case "$mgr" in
    apt) sudo apt-get update && sudo apt-get install -y git python3-venv ;;
    dnf) sudo dnf install -y git python3 python3-pip python3-virtualenv ;;
    yum) sudo yum install -y git python3 python3-pip ;;
    pacman) sudo pacman -Sy --noconfirm git python python-virtualenv ;;
    zypper) sudo zypper --non-interactive install git python3 python3-pip python3-virtualenv ;;
  esac
}

select_python_cmd() {
  if command -v python3 >/dev/null 2>&1; then echo "python3"; return; fi
  if command -v python >/dev/null 2>&1; then echo "python"; return; fi
  echo ""
}

check_python_version() {
  local py_cmd="$1"
  "$py_cmd" - <<'PY'
import sys
major, minor = sys.version_info[:2]
need_major, need_minor = 3, 10
if (major, minor) < (need_major, need_minor):
    raise SystemExit(f"Python too old: {major}.{minor}, need >= {need_major}.{need_minor}")
print(f"Python version: {major}.{minor} (ok)")
if (major, minor) >= (3, 14):
    print("[WARN] Python > 3.13 may have dependency issues, consider using 3.12 or 3.13")
PY
}

check_node_version() {
  local node_major
  node_major="$(node -p "process.versions.node.split('.')[0]")"
  if [[ "$node_major" -lt "$NODE_MIN_MAJOR" ]]; then
    err "Node.js too old: $(node -v), need >= ${NODE_MIN_MAJOR}.x (Vite 7 required)"
    exit 1
  fi
  info "Node.js version: $(node -v) (ok)"
}

ensure_uv() {
  if command -v uv >/dev/null 2>&1; then
    info "uv found: $(uv --version)"
    return 0
  fi

  warn "uv not found, installing..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
  if ! command -v uv >/dev/null 2>&1; then
    err "uv installation failed. Install manually: https://docs.astral.sh/uv/"
    exit 1
  fi
  info "uv installed: $(uv --version)"
}

has_proxy_env() {
  for var in http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy; do
    if [[ -n "${!var:-}" ]]; then return 0; fi
  done
  return 1
}

install_frontend_deps() {
  local npm_cmd=(npm ci --no-audit --fund=false)
  if [[ ! -f package-lock.json ]]; then
    npm_cmd=(npm install --no-audit --fund=false)
  fi

  info "Installing frontend dependencies..."
  if "${npm_cmd[@]}"; then return 0; fi

  if has_proxy_env; then
    warn "Proxy detected, retrying without proxy..."
    if env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u all_proxy -u NO_PROXY -u no_proxy "${npm_cmd[@]}"; then
      return 0
    fi
  fi

  err "Frontend dependency install failed"
  return 1
}

# ── Main ──

echo "=========================================="
echo "  ShengWen Deploy Script (Linux/macOS)"
echo "=========================================="
echo

if [[ "$(id -u)" -eq 0 ]]; then
  err "Do not run this script as root or with sudo."
  err "Run as normal user: ./deploy.sh"
  exit 1
fi

require_cmd git "Please install Git."
require_cmd node "Please install Node.js ${NODE_MIN_MAJOR}+."
require_cmd npm "Please install npm (comes with Node.js)."

PYTHON_CMD="$(select_python_cmd)"
if [[ -z "$PYTHON_CMD" ]]; then
  err "Python not found. Please install Python ${PYTHON_MIN_MAJOR}.${PYTHON_MIN_MINOR}+"
  exit 1
fi

if [[ "$(uname -s)" == "Linux" ]]; then
  if ! "$PYTHON_CMD" -m venv --help >/dev/null 2>&1; then
    install_linux_deps
  fi
fi

check_python_version "$PYTHON_CMD"
check_node_version
ensure_uv

info "Step 1/4: Installing backend dependencies (uv sync)"
uv sync

info "Step 2/4: Installing frontend dependencies"
pushd frontend >/dev/null
install_frontend_deps

info "Step 3/4: Building frontend"
npm run build
popd >/dev/null

info "Step 4/4: Preparing config"
mkdir -p config
if [[ ! -f config/settings.json && -f config/settings.example.json ]]; then
  cp config/settings.example.json config/settings.json
  info "Created config/settings.json"
else
  info "Keeping existing config"
fi

echo
echo "=========================================="
echo "  Deploy complete!"
echo "=========================================="
echo
echo "Next: ./run.sh"
echo "Then open http://localhost:21010/ (or your configured port)"
