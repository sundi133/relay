#!/bin/bash
# Relay LLM Proxy Start Script
# Usage: ./start_relay.sh [port] [config]

set -euo pipefail

# Default values
PORT=${1:-4000}
CONFIG=${2:-config.yaml}
HOST=${3:-0.0.0.0}
WORKERS=${4:-1}
LOG_LEVEL=${5:-info}

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}🚀 Starting Relay LLM Proxy...${NC}"

# Check if config file exists
if [[ ! -f "$CONFIG" ]]; then
    echo -e "${RED}✗ Config file not found: $CONFIG${NC}"
    echo -e "Expected location: $(pwd)/$CONFIG"
    exit 1
fi

# Check if virtual environment exists and activate it
if [[ -d "venv" ]]; then
    echo -e "${BLUE}ℹ Activating virtual environment...${NC}"
    source venv/bin/activate
fi

# Load environment variables from .env file if it exists
if [[ -f ".env" ]]; then
    echo -e "${BLUE}ℹ Loading environment variables from .env...${NC}"
    export $(grep -v '^#' .env | xargs)
fi

# Check if relay is installed, otherwise use source
if command -v relay &> /dev/null; then
    RELAY_CMD="relay"
else
    echo -e "${BLUE}ℹ 'relay' command not installed, running from source...${NC}"
    # Find Python executable
    if command -v python3 &> /dev/null; then
        RELAY_CMD="python3 -m unillm.cli"
    elif command -v python &> /dev/null; then
        RELAY_CMD="python -m unillm.cli"
    else
        echo -e "${RED}✗ No Python executable found${NC}"
        echo "Install Python 3.10+ or install relay with: pip install relay-llm"
        exit 1
    fi
fi

# Start the server
echo -e "${GREEN}✓ Starting relay on http://$HOST:$PORT${NC}"
echo -e "  Config: $CONFIG"
echo -e "  Workers: $WORKERS"
echo -e "  Log level: $LOG_LEVEL"
echo ""

exec $RELAY_CMD \
    --config "$CONFIG" \
    --port "$PORT" \
    --host "$HOST" \
    --workers "$WORKERS" \
    --log-level "$LOG_LEVEL"