#!/usr/bin/env python3
"""
Start the Relay LLM Proxy server.
High-performance, supply-chain-safe OpenAI-compatible gateway.
"""
import uvicorn
import argparse
import os
import sys
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from unillm.config import load
from unillm.server_fast import create_fast_app

def main():
    parser = argparse.ArgumentParser(description="Relay LLM Proxy - High-Performance OpenAI-Compatible Gateway")
    parser.add_argument("--config", "-c", required=True, help="Path to config.yaml")
    parser.add_argument("--port", "-p", type=int, default=4000, help="Port to listen on")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--log-level", default="warning", choices=["critical", "error", "warning", "info", "debug"])

    args = parser.parse_args()

    # Load environment variables if .env exists
    env_file = project_root / ".env"
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ[key.strip()] = value.strip()

    # Load config
    config = load(args.config)

    # Create ultra-fast app
    app = create_fast_app(config)

    print("\n" + "█" * 50)
    print("  🚀 RELAY LLM PROXY")
    print("  High-Performance • Supply-Chain-Safe")
    print("█" * 50)
    print(f"  Config: {args.config}")
    print(f"  Models: {', '.join(config.model_names)}")
    print(f"  Auth: {'✓' if config.master_key else '✗'}")
    print(f"  URL: http://{args.host}:{args.port}")
    print("")
    print("  Performance Optimizations:")
    print("  • HTTP/2 connection pooling")
    print("  • Async I/O with uvloop")
    print("  • Memory-efficient processing")
    print("  • Supply-chain security")
    print("█" * 50)

    # Ultra-fast uvicorn configuration
    try:
        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            log_level=args.log_level,
            access_log=False,       # Disable access logging
            server_header=False,    # Remove server header
            date_header=False,      # Remove date header
            loop="uvloop",          # High-performance event loop
            http="httptools",       # Fast HTTP parser
        )
    except KeyboardInterrupt:
        print("\n⏹️  Server stopped")
    except Exception as e:
        print(f"\n❌ Server failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()