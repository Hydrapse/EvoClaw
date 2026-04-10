"""Entry point — configure via CLI args (or fallback to --config FILE)."""

import argparse
import json
import logging
import os
import sys

import uvicorn

from config import load_config
from server import app, set_config


def _build_config(args: argparse.Namespace) -> dict:
    """Construct a config dict from parsed CLI arguments."""
    params: dict = {}
    if args.temperature is not None:
        params["temperature"] = args.temperature
    if args.top_p is not None:
        params["top_p"] = args.top_p
    if args.max_tokens is not None:
        params["max_tokens"] = args.max_tokens
    if args.budget_tokens is not None:
        params["reasoning"] = {"budget_tokens": args.budget_tokens}

    provider: dict = {
        "name": "default",
        "api_base_url": args.api_base_url,
        "api_key": args.api_key or os.environ.get("API_KEY", ""),
        "max_retries": args.max_retries,
    }
    if params:
        provider["params"] = params

    tokenizer_path = args.tokenizer_path or os.environ.get("CCR_TOKENIZER_PATH") or os.environ.get("TOKENIZER_PATH")
    if tokenizer_path:
        provider["tokenizer_path"] = tokenizer_path

    cfg: dict = {
        "PORT": args.port,
        "API_TIMEOUT_MS": args.api_timeout_ms,
        "Providers": [provider],
        "Router": {"default": f"default,{args.model}"},
    }
    if tokenizer_path:
        cfg["tokenizer_path"] = tokenizer_path
    return cfg


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Claude Code Router (Python)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # ── Provider ──────────────────────────────────────────────────────────────
    parser.add_argument("--api-base-url", metavar="URL",
                        help="Provider chat-completions URL "
                             "(e.g. http://host:port/v1/chat/completions)")
    parser.add_argument("--api-key", default="", metavar="KEY",
                        help="Provider API key (falls back to $API_KEY)")
    parser.add_argument("--model", default="/model", metavar="MODEL",
                        help="Model name/path forwarded to the provider")
    parser.add_argument("--max-retries", type=int, default=3)

    # ── Provider params ───────────────────────────────────────────────────────
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--top-p",       type=float, default=None)
    parser.add_argument("--max-tokens",  type=int,   default=None)
    parser.add_argument("--budget-tokens", type=int, default=None,
                        help="Reasoning budget tokens (enables thinking mode)")
    parser.add_argument("--tokenizer-path", default=None, metavar="PATH",
                        help="HuggingFace tokenizer for /v1/messages/count_tokens "
                             "(falls back to $CCR_TOKENIZER_PATH or $TOKENIZER_PATH)")

    # ── Server ────────────────────────────────────────────────────────────────
    parser.add_argument("--host",          default="0.0.0.0")
    parser.add_argument("--port",          type=int, default=3456)
    parser.add_argument("--workers",       type=int, default=1)
    parser.add_argument("--api-timeout-ms", type=int, default=120_000)
    parser.add_argument("--log-level",     default="info",
                        choices=["debug", "info", "warning", "error"])

    # ── Optional file-based config (legacy / complex setups) ─────────────────
    parser.add_argument("--config", default=None, metavar="FILE",
                        help="Load config from a JSON file instead of CLI flags")

    args = parser.parse_args()

    # Build or load config
    if args.config:
        try:
            cfg = load_config(args.config)
        except FileNotFoundError:
            print(f"Config file not found: {args.config}", file=sys.stderr)
            sys.exit(1)
        except Exception as exc:
            print(f"Failed to load config: {exc}", file=sys.stderr)
            sys.exit(1)
        host      = args.host
        port      = int(cfg.get("PORT", args.port))
        log_level = args.log_level
        workers   = args.workers
    else:
        if not args.api_base_url:
            parser.error("--api-base-url is required (or use --config FILE)")
        cfg       = _build_config(args)
        host      = args.host
        port      = args.port
        log_level = args.log_level
        workers   = args.workers

    logging.basicConfig(
        level=log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    print(f"Starting Claude Code Router on {host}:{port} (workers={workers})", flush=True)

    if workers == 1:
        set_config(cfg)
        uvicorn.run(app, host=host, port=port, log_level=log_level)
    else:
        if args.config:
            os.environ["CCR_CONFIG"] = args.config
        else:
            # Pass the inline config to each worker process via env
            os.environ["CCR_CONFIG_JSON"] = json.dumps(cfg)
        uvicorn.run(
            "server:app",
            host=host,
            port=port,
            workers=workers,
            log_level=log_level,
        )


if __name__ == "__main__":
    main()
