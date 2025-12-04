#!/bin/bash

###############################################################################
# Setup Script for Rack Inteligente Dashboard
# Description: Automated setup and initialization script
# Author: Carlos Delfino
###############################################################################

set -e  # Exit on error

# Resolve script directory and project root (parent of dashboard)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Graceful shutdown on interruption
cleanup() {
    echo "\n[dashboard/setup] Interrompido pelo usuário. Encerrando com segurança..."
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
CHECK="✅"
CROSS="❌"
INFO="ℹ️"
ROCKET="🚀"
WRENCH="🔧"

echo -e "${BLUE}${ROCKET} Rack Inteligente Dashboard - Setup${NC}"
echo "=========================================="
echo ""

# Check Python version
echo -e "${INFO} Checking Python version..."
if ! command -v python3 &> /dev/null; then
    echo -e "${CROSS} ${RED}Python 3 is not installed. Please install Python 3.8 or higher.${NC}"
    exit 1
fi

PYTHON_VERSION=$(python3 --version | cut -d' ' -f2 | cut -d'.' -f1,2)
echo -e "${CHECK} ${GREEN}Python ${PYTHON_VERSION} found${NC}"
echo ""

# Create virtual environment (in project root)
echo -e "${WRENCH} Creating virtual environment in project root..."
if [ -d "${PROJECT_ROOT}/venv" ]; then
    echo -e "${YELLOW}Virtual environment already exists in project root. Skipping creation.${NC}"
else
    python3 -m venv "${PROJECT_ROOT}/venv"
    echo -e "${CHECK} ${GREEN}Virtual environment created in project root${NC}"
fi
echo ""

# Activate virtual environment
echo -e "${INFO} Activating virtual environment from project root..."
source "${PROJECT_ROOT}/venv/bin/activate"
echo -e "${CHECK} ${GREEN}Virtual environment activated${NC}"
echo ""

# Upgrade pip
echo -e "${WRENCH} Upgrading pip..."
pip install --upgrade pip > /dev/null 2>&1
echo -e "${CHECK} ${GREEN}pip upgraded${NC}"
echo ""

# Install dependencies
echo -e "${WRENCH} Installing dependencies..."
pip install -r "${SCRIPT_DIR}/requirements.txt"
echo -e "${CHECK} ${GREEN}Dependencies installed${NC}"
echo ""

# Setup .env file (in project root / workspace)
echo -e "${WRENCH} Setting up environment configuration (.env at project root)..."
if [ ! -f "${PROJECT_ROOT}/.env" ]; then
    if [ -f "${PROJECT_ROOT}/.env.example" ]; then
        cp "${PROJECT_ROOT}/.env.example" "${PROJECT_ROOT}/.env"
        echo -e "${CHECK} ${GREEN}.env file created from PROJECT_ROOT/.env.example${NC}"
    else
        cp "${SCRIPT_DIR}/.env.example" "${PROJECT_ROOT}/.env"
        echo -e "${CHECK} ${GREEN}.env file created from dashboard/.env.example at project root${NC}"
    fi
    echo -e "${YELLOW}⚠️  Please edit ${PROJECT_ROOT}/.env file with your MQTT credentials before running the applications${NC}"
else
    echo -e "${YELLOW}.env file already exists at project root. Skipping creation.${NC}"
fi
echo ""

# Check if database exists (inside dashboard directory)
if [ -f "${SCRIPT_DIR}/data.db" ]; then
    echo -e "${INFO} Database file exists: ${SCRIPT_DIR}/data.db"
else
    echo -e "${INFO} Database will be created on first run in dashboard directory"
fi
echo ""

# Summary
echo "=========================================="
echo -e "${GREEN}${CHECK} Setup completed successfully!${NC}"
echo ""
echo -e "${BLUE}Next steps:${NC}"
echo "1. Edit .env file with your MQTT credentials:"
echo "   nano .env"
echo ""
echo "2. Run the application (from dashboard directory):"
echo "   cd dashboard"
echo "   source ../venv/bin/activate  # ativa o venv na raiz do projeto"
echo "   python app.py"
echo ""
echo -e "${BLUE}For more information, see README.md${NC}"
echo "=========================================="
