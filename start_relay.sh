#!/bin/bash
# Relay LLM Proxy Start Script
# Usage:
#   ./start_relay.sh                                    # Use defaults
#   ./start_relay.sh 4001                              # Custom port
#   ./start_relay.sh --config config-with-guardrails.yaml --port 4001
#   ./start_relay.sh --config my-config.yaml

set -euo pipefail

# Default values (only used if no arguments provided)
DEFAULT_PORT=4000
DEFAULT_CONFIG="config.yaml"
DEFAULT_HOST="0.0.0.0"
DEFAULT_WORKERS=1
DEFAULT_LOG_LEVEL="info"

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}🚀 Starting Relay LLM Proxy...${NC}"

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

# Parse arguments
if [ $# -eq 0 ]; then
    # No arguments - use defaults
    ARGS=(--config "$DEFAULT_CONFIG" --port "$DEFAULT_PORT" --host "$DEFAULT_HOST" --workers "$DEFAULT_WORKERS" --log-level "$DEFAULT_LOG_LEVEL")
elif [ $# -eq 1 ] && [[ "$1" =~ ^[0-9]+$ ]]; then
    # Single numeric argument - treat as port (backward compatibility)
    ARGS=(--config "$DEFAULT_CONFIG" --port "$1" --host "$DEFAULT_HOST" --workers "$DEFAULT_WORKERS" --log-level "$DEFAULT_LOG_LEVEL")
elif [ $# -eq 2 ] && [[ "$1" =~ ^[0-9]+$ ]]; then
    # Two arguments with first being numeric - port and config (backward compatibility)
    ARGS=(--config "$2" --port "$1" --host "$DEFAULT_HOST" --workers "$DEFAULT_WORKERS" --log-level "$DEFAULT_LOG_LEVEL")
else
    # Named arguments - pass through to relay
    ARGS=("$@")
    # Add defaults for missing required args
    if [[ ! " $@ " =~ " --config " ]]; then
        ARGS=(--config "$DEFAULT_CONFIG" "${ARGS[@]}")
    fi
    if [[ ! " $@ " =~ " --port " ]]; then
        ARGS=(--port "$DEFAULT_PORT" "${ARGS[@]}")
    fi
fi

# Extract values for display (best effort)
CONFIG_VALUE="$DEFAULT_CONFIG"
PORT_VALUE="$DEFAULT_PORT"
for i in "${!ARGS[@]}"; do
    if [[ "${ARGS[i]}" == "--config" ]] && [[ $((i+1)) -lt ${#ARGS[@]} ]]; then
        CONFIG_VALUE="${ARGS[$((i+1))]}"
    elif [[ "${ARGS[i]}" == "--port" ]] && [[ $((i+1)) -lt ${#ARGS[@]} ]]; then
        PORT_VALUE="${ARGS[$((i+1))]}"
    fi
done

# Check if config file exists
if [[ ! -f "$CONFIG_VALUE" ]]; then
    echo -e "${RED}✗ Config file not found: $CONFIG_VALUE${NC}"
    echo -e "Expected location: $(pwd)/$CONFIG_VALUE"
    exit 1
fi

# Start the server
echo -e "${GREEN}✓ Starting relay on http://0.0.0.0:$PORT_VALUE${NC}"
echo -e "  Config: $CONFIG_VALUE"
echo -e "  Full args: ${ARGS[*]}"
echo ""

exec $RELAY_CMD "${ARGS[@]}"