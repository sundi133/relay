"""
relay CLI — start the proxy server.

Usage:
    relay --config config.yaml --port 4000
    relay --config config.yaml --port 4000 --host 0.0.0.0
    relay --config config.yaml --port 4000 --workers 4
    relay --help
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path


BANNER = r"""
  ██████  ███████ ██      █████  ██    ██
  ██   ██ ██      ██     ██   ██  ██  ██
  ██████  █████   ██     ███████   ████
  ██   ██ ██      ██     ██   ██    ██
  ██   ██ ███████ ██████ ██   ██    ██

  Relay LLM Proxy  — supply-chain-safe LiteLLM drop-in
"""


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="relay",
        description="Relay — OpenAI-compatible LLM proxy",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  relay --config config.yaml --port 4000
  relay --config config.yaml --port 4000 --host 0.0.0.0
  relay --config config.yaml --port 4000 --workers 4 --log-level warning

Then call it exactly like LiteLLM or OpenAI:
  curl http://localhost:4000/v1/chat/completions \\
    -H "Content-Type: application/json" \\
    -d '{"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}]}'
        """,
    )
    p.add_argument(
        "--config", "-c",
        required=True,
        metavar="PATH",
        help="Path to config.yaml",
    )
    p.add_argument(
        "--port", "-p",
        type=int,
        default=4000,
        metavar="PORT",
        help="Port to listen on (default: 4000)",
    )
    p.add_argument(
        "--host",
        default="0.0.0.0",
        metavar="HOST",
        help="Host to bind to (default: 0.0.0.0)",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=1,
        metavar="N",
        help="Number of uvicorn workers (default: 1)",
    )
    p.add_argument(
        "--log-level",
        default="info",
        choices=["critical", "error", "warning", "info", "debug", "trace"],
        help="Uvicorn log level (default: info)",
    )
    p.add_argument(
        "--version", "-v",
        action="store_true",
        help="Print version and exit",
    )
    return p


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.version:
        from unillm import __version__
        print(f"relay {__version__}")
        sys.exit(0)

    # ── Validate config path ─────────────────────────────────────────────────
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"\n  ✗ Config file not found: {config_path}\n", file=sys.stderr)
        sys.exit(1)

    # ── Load config ──────────────────────────────────────────────────────────
    try:
        from unillm.config import load as load_config
        config = load_config(config_path)
    except Exception as e:
        print(f"\n  ✗ Failed to load config: {e}\n", file=sys.stderr)
        sys.exit(1)

    # ── Print banner ─────────────────────────────────────────────────────────
    print(BANNER)
    print(f"  Config      : {config_path.resolve()}")
    print(f"  Models      : {', '.join(config.model_names) or '(none)'}")
    print(f"  Auth        : {'master_key set ✓' if config.master_key else 'open (no master_key)'}")
    print(f"  Listening   : http://{args.host}:{args.port}")
    print(f"  Usage DB    : ~/.unillm/usage.db")
    print()
    print(f"  Endpoints:")
    print(f"    GET  /health")
    print(f"    GET  /v1/models")
    print(f"    POST /v1/chat/completions")
    print(f"    GET  /v1/usage")
    print()

    # ── Register api_key overrides into provider registry ────────────────────
    from unillm import providers as _prov
    for m in config.models:
        if m.api_key and "/" in m.model:
            provider = m.model.split("/")[0]
            cfg = _prov._REGISTRY.get(provider, {})
            if not cfg.get("_key"):   # don't overwrite if already set
                _prov._REGISTRY.setdefault(provider, {})["_key"] = m.api_key

    # ── Start server ─────────────────────────────────────────────────────────
    try:
        import uvicorn
    except ImportError:
        print(
            "  ✗ uvicorn is required to run the server.\n"
            "    Install it with:  pip install uvicorn\n",
            file=sys.stderr,
        )
        sys.exit(1)

    from unillm.server import create_app
    app = create_app(config)

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        workers=args.workers if args.workers > 1 else None,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
