#!/bin/bash

###############################################################################
# Run Script for Rack Inteligente Dashboard
# Description: Quick start script for the dashboard application
# Author: EmbarcaTech Project
###############################################################################

# Resolve script directory and project root (parent of dashboard)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Graceful shutdown on interruption
cleanup() {
    echo "\n[dashboard/run] Interrompido pelo usuário. Encerrando com segurança..."
    exit 1
}

trap cleanup INT TERM

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Emojis
ROCKET="🚀"
CROSS="❌"
INFO="ℹ️"

echo -e "${BLUE}${ROCKET} Starting Rack Inteligente Dashboard...${NC}"
echo ""

# Check if virtual environment exists in project root
if [ ! -d "${PROJECT_ROOT}/venv" ]; then
    echo -e "${CROSS} ${RED}Virtual environment not found in project root!${NC}"
    echo -e "${INFO} Please run setup.sh first:"
    echo "   cd ${SCRIPT_DIR} && ./setup.sh"
    exit 1
fi

# Check if .env exists in project root (workspace)
if [ ! -f "${PROJECT_ROOT}/.env" ]; then
    echo -e "${CROSS} ${RED}.env file not found in project root (workspace)!${NC}"
    echo -e "${INFO} The dashboard expects MQTT settings in ${PROJECT_ROOT}/.env."
    echo "   cd ${PROJECT_ROOT}"
    echo "   cp .env.example .env   # ou copie de dashboard/.env.example se for compartilhado"
    echo "   nano .env"
    exit 1
fi

# Activate virtual environment from project root and run app from dashboard
cd "${SCRIPT_DIR}"
source "${PROJECT_ROOT}/venv/bin/activate"
python app.py
