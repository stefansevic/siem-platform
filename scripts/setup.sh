#!/usr/bin/env bash
# Setup script for the SIEM platform.
# Verifies required tools, copies .env, and installs Python deps
# needed by the experiment framework. Safe to re-run.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

ok()   { echo -e "${GREEN}[OK]${NC}    $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail() { echo -e "${RED}[FAIL]${NC}  $*"; exit 1; }
info() { echo "        $*"; }


echo "=== Step 1: Tooling checks ==="

command -v docker >/dev/null 2>&1 || fail "docker not found in PATH"
ok "docker $(docker --version | awk '{print $3}' | tr -d ',')"

if docker compose version >/dev/null 2>&1; then
    ok "docker compose $(docker compose version --short)"
else
    fail "docker compose v2 not available (legacy docker-compose is unsupported)"
fi

command -v python3 >/dev/null 2>&1 || fail "python3 not found in PATH"
PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
    fail "python 3.11+ required, found $PY_VERSION"
fi
ok "python $PY_VERSION"


echo
echo "=== Step 2: Environment file ==="

if [ -f .env ]; then
    ok ".env already exists"
else
    cp .env.example .env
    ok "Created .env from .env.example"
fi


echo
echo "=== Step 3: Python dependencies ==="

PIP_FLAGS=""
if pip3 install --help 2>/dev/null | grep -q -- "--break-system-packages"; then
    PIP_FLAGS="--break-system-packages"
fi

REQUIRED_PKGS=("requests" "pyyaml" "matplotlib" "numpy")
for pkg in "${REQUIRED_PKGS[@]}"; do
    if python3 -c "import ${pkg/-/_}" 2>/dev/null; then
        ok "$pkg already installed"
    else
        info "Installing $pkg..."
        pip3 install $PIP_FLAGS "$pkg" >/dev/null
        ok "$pkg installed"
    fi
done


echo
echo "=== Step 4: Linux-only kernel parameter for Elasticsearch ==="

if [ "$(uname -s)" = "Linux" ]; then
    CURRENT_VM=$(sysctl -n vm.max_map_count 2>/dev/null || echo 0)
    if [ "$CURRENT_VM" -lt 262144 ]; then
        warn "vm.max_map_count = $CURRENT_VM (Elasticsearch needs >= 262144)"
        info "To fix this, run: sudo sysctl -w vm.max_map_count=262144"
        info "Or persist it in /etc/sysctl.conf"
    else
        ok "vm.max_map_count = $CURRENT_VM"
    fi
else
    info "Skipped (only relevant on Linux hosts; Docker Desktop manages this on macOS/Windows)"
fi


echo
echo "=== Setup complete ==="
echo
echo "Next steps:"
echo "  1. Bring up the stack:    docker compose up -d --build"
echo "  2. Wait ~30 seconds for services to become healthy"
echo "  3. Open the dashboard:    http://localhost:3000"
echo
echo "Run an experiment:"
echo "  python3 experiments/run_scenario.py \\"
echo "    experiments/scenarios/basic_brute_force.yaml --reset-db"
