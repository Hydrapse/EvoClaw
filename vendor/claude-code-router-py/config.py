import json
import os
import re
from typing import Any


def _interpolate_env_vars(obj: Any) -> Any:
    """Recursively replace $VAR or ${VAR} with environment variable values."""
    if isinstance(obj, str):
        def replace(match: re.Match) -> str:
            name = match.group(1) or match.group(2)
            return os.environ.get(name, match.group(0))
        return re.sub(r"\$\{(\w+)\}|\$(\w+)", replace, obj)
    if isinstance(obj, dict):
        return {k: _interpolate_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_interpolate_env_vars(item) for item in obj]
    return obj


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return _interpolate_env_vars(raw)


def get_provider(config: dict, name: str) -> dict | None:
    for p in config.get("Providers", []):
        if p["name"] == name:
            return p
    return None


def apply_provider_params(provider: dict, req: dict) -> dict:
    """Apply provider-level param defaults/overrides to an OpenAI-format request.

    Supported fields in provider["params"]:
      temperature  – default if not set in request
      top_p        – default if not set in request
      max_tokens   – default, or ceiling if request already has a value
      reasoning    – dict with budget_tokens; enables thinking if not already set
    """
    params = provider.get("params", {})
    if not params:
        return req

    for field in ("temperature", "top_p"):
        if params.get(field) is not None and req.get(field) is None:
            req[field] = params[field]

    if params.get("max_tokens") is not None:
        limit = params["max_tokens"]
        if req.get("max_tokens") is None:
            req["max_tokens"] = limit
        else:
            req["max_tokens"] = min(req["max_tokens"], limit)

    reasoning = params.get("reasoning")
    if reasoning and req.get("thinking") is None and not req.get("tools"):
        budget = reasoning.get("budget_tokens", 8000) if isinstance(reasoning, dict) else 8000
        req["thinking"] = {"type": "enabled", "budget_tokens": budget}

    return req


def resolve_route(config: dict, scenario: str = "default") -> tuple[str, str] | None:
    """Return (provider_name, model) for the given routing scenario."""
    router = config.get("Router", {})
    target = router.get(scenario) or router.get("default")
    if not target:
        return None
    parts = target.split(",", 1)
    if len(parts) != 2:
        return None
    return parts[0].strip(), parts[1].strip()
