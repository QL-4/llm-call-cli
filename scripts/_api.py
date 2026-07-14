#!/usr/bin/env python3
"""HTTP layer: POST to an OpenAI-compatible /chat/completions endpoint."""

from __future__ import annotations

import json
import http.client
from urllib.parse import urlparse
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


def _request(
    method: str,
    url: str,
    api_key: str,
    timeout: float,
    body: bytes | None = None,
) -> Dict[str, Any]:
    """Send an HTTP request using http.client.

    Uses a direct connection (does not honor HTTP(S)_PROXY env vars).
    This avoids RemoteDisconnected issues seen with urllib on Python 3.13
    behind some local reverse proxies (e.g. CPA).
    """
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise LlmCallError(
            f"Unsupported URL scheme {parsed.scheme!r} for LLM endpoint ({url}).",
            "base_url must start with http:// or https://. Use: python scripts/llm_call.py --show-config",
        )

    host = parsed.hostname or "localhost"
    port = parsed.port
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"

    conn: http.client.HTTPConnection | None = None
    raw = b""
    try:
        if scheme == "https":
            conn = http.client.HTTPSConnection(host, port, timeout=timeout)
        else:
            conn = http.client.HTTPConnection(host, port, timeout=timeout)

        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        status = resp.status

        if status >= 400:
            detail = raw.decode("utf-8", errors="replace")[:500]
            hint = _HTTP_HINTS.get(status, "Check config with: python scripts/llm_call.py --show-config")
            if status == 400:
                hint += f" Detail: {detail[:300]}"
            raise LlmCallError(
                f"HTTP {status} from LLM endpoint ({url}).\n  Detail: {detail}",
                hint,
            )

        return json.loads(raw.decode("utf-8"))
    except LlmCallError:
        raise
    except http.client.RemoteDisconnected as exc:
        raise LlmCallError(
            f"LLM endpoint closed the connection unexpectedly ({url}).\n  Reason: {exc}",
            "The server or local proxy may have dropped keep-alive. Retry, or verify base_url/proxy with: python scripts/llm_call.py --show-config",
        ) from exc
    except http.client.HTTPException as exc:
        raise LlmCallError(
            f"HTTP protocol error from LLM endpoint ({url}).\n  Reason: {exc}",
            "Check that the base_url is correct and the server is running. Use: python scripts/llm_call.py --show-config",
        ) from exc
    except (ConnectionRefusedError, ConnectionResetError) as exc:
        raise LlmCallError(
            f"Cannot connect to LLM endpoint at {url}.\n  Reason: {exc}",
            "Check that the server is running and base_url is correct. Use: python scripts/llm_call.py --show-config",
        ) from exc
    except TimeoutError as exc:
        raise LlmCallError(
            f"Connection to LLM endpoint timed out ({url}).\n  Reason: {exc}",
            "The server may be overloaded or the network is slow. Try again or increase --timeout.",
        ) from exc
    except OSError as exc:
        raise LlmCallError(
            f"Network error reaching LLM endpoint ({url}).\n  Reason: {exc}",
            "Check network connectivity and base_url. Use: python scripts/llm_call.py --show-config",
        ) from exc
    except json.JSONDecodeError as exc:
        raise LlmCallError(
            f"LLM endpoint returned invalid JSON ({url}).\n  Response: {raw.decode('utf-8', errors='replace')[:300]}",
            "The endpoint may have returned an error page or HTML instead of JSON. Check the model name and base_url.",
        ) from exc
    finally:
        if conn is not None:
            conn.close()


def post_chat_completions(
    request_url: str, api_key: str, body: Dict[str, Any], timeout: float
) -> Dict[str, Any]:
    payload = json.dumps(body).encode("utf-8")
    return _request("POST", request_url, api_key, timeout, body=payload)


def get_models(base_url: str, api_key: str, timeout: float) -> list[dict]:
    """GET /v1/models and return the 'data' list."""
    request_url = base_url.rstrip("/") + "/models"
    result = _request("GET", request_url, api_key, timeout)
    return result.get("data", [])
