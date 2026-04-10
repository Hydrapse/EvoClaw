"""
Debug helpers — only active when CCR_DEBUG=1 is set.

When enabled, any response whose text content contains a sensitive token
is written to CCR_DEBUG_DIR (default: ./debug_dumps/) as a JSON file
containing the original OpenAI-format request and the raw response.

Sensitive tokens:
    <tool_call>  </tool_call>  <|user|>  <|observation|>  <|assistant|>
"""

import json
import logging
import os
import time
import uuid

logger = logging.getLogger(__name__)

SENSITIVE_TOKENS: tuple[str, ...] = (
    "<tool_call>",
    "</tool_call>",
    "<|user|>",
    "<|observation|>",
    "<|assistant|>",
)

_enabled: bool | None = None   # cached after first call


def is_enabled() -> bool:
    global _enabled
    if _enabled is None:
        _enabled = os.environ.get("CCR_DEBUG", "").strip() in ("1", "true", "yes")
    return _enabled


def has_sensitive_tokens(text: str) -> bool:
    return any(tok in text for tok in SENSITIVE_TOKENS)


def _dump_dir() -> str:
    d = os.environ.get("CCR_DEBUG_DIR", "debug_dumps")
    os.makedirs(d, exist_ok=True)
    return d


def save_dump(openai_req: dict, response_obj: object, label: str = "") -> None:
    """
    Write a JSON dump of the request + response to disk.

    response_obj can be:
      - a dict  (non-streaming OpenAI response)
      - a str   (assembled streaming text)
    """
    ts = time.strftime("%Y%m%d_%H%M%S")
    uid = uuid.uuid4().hex[:8]
    name = f"{ts}_{uid}"
    if label:
        name = f"{name}_{label}"
    path = os.path.join(_dump_dir(), f"{name}.json")

    payload = {
        "request": openai_req,
        "response": response_obj,
    }
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        logger.warning("CCR_DEBUG: sensitive token found — dump saved to %s", path)
    except Exception as exc:
        logger.error("CCR_DEBUG: failed to write dump %s: %s", path, exc)


def log_openai_request(openai_req: dict) -> None:
    """Print the converted OpenAI request to stdout when CCR_DEBUG is enabled."""
    if not is_enabled():
        return
    print(
        "CCR_DEBUG [openai_req]:\n"
        + json.dumps(openai_req, ensure_ascii=False, indent=2),
        flush=True,
    )


def check_and_save_nonstreaming(openai_req: dict, openai_resp: dict) -> None:
    """Check a non-streaming OpenAI response and dump if sensitive tokens found."""
    if not is_enabled():
        return
    text = _extract_text_from_openai_resp(openai_resp)
    if has_sensitive_tokens(text):
        save_dump(openai_req, openai_resp, label="nonstream")


def check_and_save_streaming(openai_req: dict, assembled_text: str) -> None:
    """Check the assembled streaming text and dump if sensitive tokens found."""
    if not is_enabled():
        return
    if has_sensitive_tokens(assembled_text):
        save_dump(openai_req, assembled_text, label="stream")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_text_from_openai_resp(resp: dict) -> str:
    """Pull plain text out of an OpenAI chat completion response."""
    try:
        msg = resp["choices"][0]["message"]
        parts = []
        if msg.get("content"):
            parts.append(msg["content"])
        if msg.get("reasoning_content"):
            parts.append(msg["reasoning_content"])
        thinking = msg.get("thinking")
        if isinstance(thinking, dict) and thinking.get("content"):
            parts.append(thinking["content"])
        return " ".join(parts)
    except (KeyError, IndexError, TypeError):
        return ""
