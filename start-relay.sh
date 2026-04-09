#!/bin/bash

# Relay LLM Proxy Startup Script
# High-performance, supply-chain-safe LLM proxy
set -e

echo "🚀 Starting Relay LLM Proxy"
echo "==========================="

PORT=${1:-4000}

# Activate virtual environment
source .venv/bin/activate

# Load all environment variables from .env
set -a
source .env
set +a

echo "⚡ Starting high-performance server on port $PORT..."

# Start relay server
python start-relay.py --config config.yaml --port $PORT