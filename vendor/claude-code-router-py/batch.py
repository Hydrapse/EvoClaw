"""
Conversion helpers between Anthropic Batch API and OpenAI Batch API formats.

Anthropic batch item:
    {"custom_id": "...", "params": {MessageCreateParams (non-streaming)}}

OpenAI batch item (JSONL input file):
    {"custom_id": "...", "method": "POST", "url": "/v1/chat/completions", "body": {...}}

Anthropic result line (JSONL):
    {"custom_id": "...", "result": {"type": "succeeded", "message": {...}}}
    {"custom_id": "...", "result": {"type": "errored",   "error": {"type": "...", "message": "..."}}}
    {"custom_id": "...", "result": {"type": "canceled"}}
    {"custom_id": "...", "result": {"type": "expired"}}

OpenAI result line (JSONL):
    {"id": "...", "custom_id": "...", "response": {"status_code": 200, "body": {...}}, "error": null}
"""

import datetime
import json

from converter import anthropic_to_openai, openai_to_anthropic

# ---------------------------------------------------------------------------
# Request conversion
# ---------------------------------------------------------------------------

def anthropic_batch_to_openai_jsonl(requests: list[dict], model: str) -> str:
    """Convert a list of Anthropic batch request objects to an OpenAI JSONL string."""
    lines = []
    for req in requests:
        params = dict(req["params"])
        params["model"] = model
        params.pop("stream", None)  # batches are never streaming

        openai_body = anthropic_to_openai(params)
        openai_body.pop("stream", None)
        openai_body.pop("stream_options", None)
        lines.append(json.dumps({
            "custom_id": req["custom_id"],
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": openai_body,
        }, ensure_ascii=False))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Response conversion
# ---------------------------------------------------------------------------

_OPENAI_STATUS_TO_ANTHROPIC = {
    "validating":  "in_progress",
    "in_progress": "in_progress",
    "finalizing":  "in_progress",
    "completed":   "ended",
    "failed":      "ended",
    "expired":     "ended",
    "cancelling":  "canceling",
    "cancelled":   "ended",
}


def openai_batch_to_anthropic(ob: dict, results_base_url: str) -> dict:
    """Convert an OpenAI batch object to Anthropic MessageBatch format."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    created = ob.get("created_at", 0)
    try:
        created_at = datetime.datetime.fromtimestamp(
            created, tz=datetime.timezone.utc
        ).isoformat()
    except Exception:
        created_at = now

    status = ob.get("status", "in_progress")
    anthropic_status = _OPENAI_STATUS_TO_ANTHROPIC.get(status, "in_progress")

    counts = ob.get("request_counts") or {}
    total     = counts.get("total", 0)
    completed = counts.get("completed", 0)
    failed    = counts.get("failed", 0)
    processing = max(0, total - completed - failed)

    batch_id = ob.get("id", "")
    results_url = None
    if ob.get("output_file_id") and anthropic_status == "ended":
        results_url = f"{results_base_url}/v1/messages/batches/{batch_id}/results"

    # expires_at: OpenAI sets this; fallback to 24h after creation
    expires = ob.get("expires_at")
    if expires:
        try:
            expires_at = datetime.datetime.fromtimestamp(
                expires, tz=datetime.timezone.utc
            ).isoformat()
        except Exception:
            expires_at = now
    else:
        expires_at = (
            datetime.datetime.fromisoformat(created_at)
            + datetime.timedelta(hours=24)
        ).isoformat()

    ended_at = None
    if ob.get("completed_at"):
        try:
            ended_at = datetime.datetime.fromtimestamp(
                ob["completed_at"], tz=datetime.timezone.utc
            ).isoformat()
        except Exception:
            pass

    cancel_at = None
    if ob.get("cancelling_at"):
        try:
            cancel_at = datetime.datetime.fromtimestamp(
                ob["cancelling_at"], tz=datetime.timezone.utc
            ).isoformat()
        except Exception:
            pass

    return {
        "id": batch_id,
        "type": "message_batch",
        "processing_status": anthropic_status,
        "request_counts": {
            "processing": processing,
            "succeeded":  completed,
            "errored":    failed,
            "canceled":   0,
            "expired":    0,
        },
        "created_at":            created_at,
        "expires_at":            expires_at,
        "ended_at":              ended_at,
        "cancel_initiated_at":   cancel_at,
        "archived_at":           None,
        "results_url":           results_url,
    }


def openai_results_line_to_anthropic(line: str, model: str) -> str | None:
    """
    Convert a single OpenAI batch result JSONL line to Anthropic format.
    Returns None if the line is empty or unparseable.
    """
    line = line.strip()
    if not line:
        return None
    try:
        item = json.loads(line)
    except json.JSONDecodeError:
        return None

    custom_id = item.get("custom_id", "")
    response  = item.get("response") or {}
    error     = item.get("error")

    if error:
        result = {"type": "errored", "error": {"type": "api_error", "message": str(error)}}
    elif response.get("status_code", 200) >= 400:
        body = response.get("body") or {}
        msg = body.get("error", {}).get("message", f"HTTP {response['status_code']}")
        result = {"type": "errored", "error": {"type": "api_error", "message": msg}}
    elif response.get("body"):
        try:
            anthropic_msg = openai_to_anthropic(response["body"], model)
            result = {"type": "succeeded", "message": anthropic_msg}
        except Exception as exc:
            result = {"type": "errored", "error": {"type": "api_error", "message": str(exc)}}
    else:
        result = {"type": "errored", "error": {"type": "api_error", "message": "empty response"}}

    return json.dumps({"custom_id": custom_id, "result": result}, ensure_ascii=False)
