#!/usr/bin/env python3
"""HTTP layer: POST to an OpenAI-compatible /chat/completions endpoint."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict

from _config import LlmCallError


# Status-code → actionable hint for the agent.
_HTTP_HINTS: Dict[int, str] = {
    401: "The API key is wrong or expired. Ask the user for a new key, then run: python scripts/llm_call.py init --api-key NEW_KEY",
    403: "The API key may lack permissions for this model, or the endpoint refuses access.",
    404: "The base_url or model may be wrong. Check with: python scripts/llm_call.py --show-config",
    400: "The endpoint rejected the request. If using --image, make sure the model supports vision. If --json failed, the retry already removed response_format.",
    429: "Rate limited. Wait and retry, or reduce call frequency.",
    500: "Server-side error. The endpoint may be down or the model name may be unrecognized. Check with: python scripts/llm_call.py --show-config",
    502: "Bad gateway — the endpoint proxy could not reach the upstream model. Check that the model name is correct with: python scripts/llm_call.py --show-config",
    503: "Service unavailable. The endpoint may be starting up or overloaded. Retry shortly.",
}


def post_chat_completions(request_url: str, api_key: str, body: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        request_url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        hint = _HTTP_HINTS.get(exc.code, "Check config with: python scripts/llm_call.py --show-config")
        if exc.code == 400:
            hint += f" Detail: {detail[:300]}"
        raise LlmCallError(
            f"HTTP {exc.code} from LLM endpoint ({request_url}).\n  Detail: {detail[:500]}",
            hint,
        ) from exc
    except urllib.error.URLError as exc:
        raise LlmCallError(
            f"Cannot reach LLM endpoint at {request_url}.\n  Reason: {exc.reason}",
            "Check that the base_url is correct and the server is running. Use: python scripts/llm_call.py --show-config"
        ) from exc
