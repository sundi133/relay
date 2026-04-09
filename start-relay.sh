#!/bin/bash

# Relay LLM Proxy Startup Script
# Usage: ./start-relay.sh [port]

PORT=${1:-4000}

echo "🚀 Starting Relay LLM Proxy on port $PORT..."

# Activate virtual environment
source .venv/bin/activate

# Load all environment variables from .env
set -a
source .env
set +a

# Start the relay server
relay --config config.yaml --port $PORT