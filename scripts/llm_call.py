#!/usr/bin/env python3
"""Standalone single-shot LLM CLI.

Self-contained: reads its own ~/.llm-call/config.json (or $LLM_CALL_HOME) for
model/base_url, and ~/.llm-call/config.json api_key or the LLM_CALL_API_KEY
env var for credentials.

One clean request, then exit. No tools, no memory, no session history.
Each call writes a single human-readable markdown log under
~/.llm-call/logs/ (default on, silent to stdout, no api_key).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

# Self-contained UTF-8 for stdin/stdout/stderr so the CLI works without a
# wrapper setting PYTHONIOENCODING (e.g. on Windows where the default codepage
# would otherwise mangle non-ASCII text).
for _stream in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass


def llm_call_home() -> Path:
    return Path(os.environ.get("LLM_CALL_HOME") or Path.home() / ".llm-call").expanduser()


def llm_call_config_path() -> Path:
    return llm_call_home() / "config.json"


def llm_call_log_dir() -> Path:
    return llm_call_home() / "logs"


def llm_call_presets_dir() -> Path:
    return llm_call_home() / "presets"


def _now_display() -> str:
    """Local wall-clock time, human-readable, no microseconds/timezone suffix."""
    return _dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _safe_path_token(value: str) -> str:
    chars: list[str] = []
    for ch in value.strip():
        if ch.isalnum() or ch in {".", "-", "_"}:
            chars.append(ch)
        else:
            chars.append("-")
    token = "".join(chars).strip(".-_")
    while "--" in token:
        token = token.replace("--", "-")
    return token or "model"


def _make_log_name(model: str) -> str:
    now = _dt.datetime.now().astimezone()
    stamp = now.strftime("%y-%m-%d_%H.%M.%S")
    return f"{stamp}_{_safe_path_token(model)}.md"


def _write_run_log(model: str, base_url: str, system: str, user: str, output: str) -> None:
    """Best-effort single human-readable markdown log. Silent on failure."""
    try:
        log_dir = llm_call_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / _make_log_name(model)
        if path.exists():
            path = log_dir / f"{path.stem}_{_dt.datetime.now().strftime('%f')}.md"
        body = (
            f"# llm-call log\n\n"
            f"- time: {_now_display()}\n"
            f"- model: {model}\n"
            f"- base_url: {base_url}\n\n"
            f"## system\n\n{system.strip()}\n\n"
            f"## user\n\n{user.strip()}\n\n"
            f"## output\n\n{output.strip()}\n"
        )
        path.write_text(body, encoding="utf-8")
    except Exception:
        return


def _load_env() -> None:
    """Tiny dotenv loader to avoid depending on python-dotenv in system Python."""
    env_path = llm_call_home() / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ[key] = value


def _load_json(path: Path) -> Dict[str, Any]:
    """Read a JSON object, returning {} when missing or invalid."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _resolve_config(model_override: Optional[str], *, strict: bool = True) -> Dict[str, str]:
    _load_env()
    cfg = _load_json(llm_call_config_path())

    model = (model_override or cfg.get("model") or "").strip()
    base_url = (cfg.get("base_url") or "").strip().rstrip("/")
    api_key = (cfg.get("api_key") or os.environ.get("LLM_CALL_API_KEY", "")).strip()
    api_mode = (cfg.get("api_mode") or "chat_completions").strip()

    missing: list[str] = []
    if not model:
        missing.append("model")
    if not base_url:
        missing.append("base_url")
    if not api_key:
        missing.append("api_key")

    if strict and missing:
        # Agent-facing guidance: when config is incomplete, point the caller at
        # init.py. init.py merges updates, so to change a single field (e.g. the
        # model) pass only that flag; to set up from scratch pass all three.
        raise SystemExit(
            "llm-call: not configured (" + ", ".join(missing) + " missing).\n"
            "Run `python scripts/init.py --base-url URL --model MODEL --api-key KEY` "
            "to initialize ~/.llm-call (ask the user for the missing values; "
            "api_mode defaults to chat_completions). To change a single field, "
            "pass only that flag, e.g. `--model new-model`. Then retry this call."
        )
    if api_mode and api_mode not in {"chat_completions", "openai", "openai_chat"}:
        raise SystemExit(f"llm-call: unsupported api_mode for this tiny CLI: {api_mode}")

    return {
        "model": str(model),
        "base_url": base_url,
        "api_key": api_key,
        "api_mode": api_mode,
        "config_home": str(llm_call_home()),
        "llm_call_config": str(llm_call_config_path()),
        "missing": ",".join(missing),
    }


def _is_valid_preset(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return isinstance(data, dict) and isinstance(data.get("user_template"), str)


def _available_preset_names() -> list[str]:
    d = llm_call_presets_dir()
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.json") if _is_valid_preset(p))


def _load_preset(name: str) -> Dict[str, Any]:
    path = llm_call_presets_dir() / f"{name}.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            raise SystemExit(f"llm-call: preset {name!r} is not valid JSON at {path}")
        if not isinstance(data, dict) or not isinstance(data.get("user_template"), str):
            raise SystemExit(f"llm-call: preset {name!r} must be a JSON object with a 'user_template' string")
        return data
    known = ", ".join(_available_preset_names())
    hint = f" Available: {known}." if known else " Run `python scripts/init.py` to seed built-ins."
    raise SystemExit(f"llm-call: preset {name!r} not found.{hint}")


def _read_stdin() -> str:
    if sys.stdin is not None and not sys.stdin.isatty():
        try:
            return sys.stdin.read()
        except OSError:
            # Pytest capture and some gateways expose a non-tty stdin that must not be read.
            return ""
    return ""


def _render_template(template: str, input_text: str, prompt: str) -> str:
    return template.replace("{{input}}", input_text).replace("{{prompt}}", prompt)


PLACEHOLDER_PATTERNS = [r"\$\(cat\s+[^)]+\)", r"<insert[^>]*>", r"TODO\s*:?"]


def lint_invocation(prompt: str, input_text: str) -> Dict[str, Any]:
    """Generic preflight lint: block unresolved artifact placeholders."""
    findings = []
    text = (prompt or "") + "\n" + (input_text or "")
    for pat in PLACEHOLDER_PATTERNS:
        if re.search(pat, text, flags=re.I):
            findings.append({"rule": "unresolved_artifact_placeholder", "severity": "error", "message": f"Unresolved artifact placeholder matched: {pat}"})
            break
    ok = not findings
    return {"ok": ok, "findings": findings}


_PRESET_DESCRIPTIONS: Dict[str, str] = {
    "summarize": "faithful concise bullet summary",
    "judge": "independent evaluator: verdict, rationale, improvement",
    "concept-check": "check whether an explanation/analogy is conceptually wrong or misleading",
    "extract-claims": "extract checkable factual claims as JSON",
    "prompt-lint": "check a prompt for ambiguity, conflicting constraints, hallucination/output-format risk",
}


def _print_presets() -> None:
    names = _available_preset_names()
    if not names:
        print(f"No presets found in {llm_call_presets_dir()}.")
        print("Run `python scripts/init.py` to seed built-ins.")
        return
    print("Presets (use with --preset NAME):")
    for name in names:
        desc = _PRESET_DESCRIPTIONS.get(name)
        print(f"  {name}" + (f" — {desc}" if desc else ""))
    print("Manage presets with `python scripts/presets.py`.")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="llm-call",
        description="Single-shot LLM call: no tools, no memory, no session history.",
    )
    parser.add_argument("prompt", nargs="*", help="Prompt text. stdin is appended/used as input.")
    parser.add_argument("--system", help="System message for this one call.")
    parser.add_argument("--preset", help="Preset name (see `python scripts/presets.py list`).")
    parser.add_argument("--list-presets", action="store_true", help="List available presets and exit.")
    parser.add_argument("--model", help="Override model for this call.")
    parser.add_argument("--temperature", type=float, help="Sampling temperature.")
    parser.add_argument("--max-tokens", type=int, help="Maximum completion tokens.")
    parser.add_argument("--json", action="store_true", help="Ask for JSON output and validate if possible.")
    parser.add_argument("--lint", action="store_true", help="Lint invocation and exit without calling provider")
    parser.add_argument("--lint-override", help="Allow lint errors with explicit reason")
    parser.add_argument("--timeout", type=float, default=None, help="Request timeout seconds (default: no HTTP timeout).")
    parser.add_argument("--show-config", action="store_true", help="Print resolved model/base_url with api_key redacted.")
    args = parser.parse_args(argv)

    if args.list_presets:
        _print_presets()
        return 0

    if args.show_config:
        resolved = _resolve_config(args.model, strict=False)
        print(json.dumps({k: ("[REDACTED]" if k == "api_key" and v else v) for k, v in resolved.items()}, ensure_ascii=False, indent=2))
        return 0

    prompt = " ".join(args.prompt).strip()
    stdin_text = _read_stdin()
    preset: Dict[str, Any] = {}
    if args.preset:
        preset = _load_preset(args.preset)

    lint_result = lint_invocation(prompt, stdin_text)
    if args.lint:
        print(json.dumps(lint_result, ensure_ascii=False, indent=2))
        return 0 if lint_result["ok"] else 2
    if not lint_result["ok"] and not args.lint_override:
        print(json.dumps(lint_result, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2

    system = args.system if args.system is not None else preset.get("system", "You are a concise, accurate assistant.")
    if preset.get("user_template"):
        user = _render_template(str(preset["user_template"]), stdin_text, prompt)
    else:
        if stdin_text and prompt:
            user = f"{prompt}\n\nInput:\n\"\"\"\n{stdin_text}\n\"\"\""
        else:
            user = prompt or stdin_text

    if not user.strip():
        raise SystemExit("llm-call: provide a prompt argument or stdin")

    resolved = _resolve_config(args.model)
    temperature = args.temperature if args.temperature is not None else preset.get("temperature", 0.2)
    wants_json = bool(args.json or preset.get("json"))

    messages = [
        {"role": "system", "content": str(system)},
        {"role": "user", "content": user},
    ]
    if wants_json:
        messages[0]["content"] += "\nReturn valid JSON only. Do not wrap it in Markdown."

    request_body: Dict[str, Any] = {
        "model": resolved["model"],
        "messages": messages,
        "temperature": temperature,
    }
    if args.max_tokens:
        request_body["max_completion_tokens"] = args.max_tokens
    if wants_json:
        request_body["response_format"] = {"type": "json_object"}

    request_url = resolved["base_url"].rstrip("/") + "/chat/completions"

    def _post_chat_completions(body: Dict[str, Any]) -> Dict[str, Any]:
        payload = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            request_url,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {resolved['api_key']}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=args.timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code} from LLM endpoint: {detail}") from exc

    try:
        response = _post_chat_completions(request_body)
    except Exception:
        if wants_json and "response_format" in request_body:
            request_body.pop("response_format", None)
            response = _post_chat_completions(request_body)
        else:
            raise

    choices = response.get("choices") or []
    if not choices:
        raise SystemExit(f"llm-call: response has no choices: {json.dumps(response, ensure_ascii=False)[:500]}")
    message = choices[0].get("message") or {}
    content = str(message.get("content") or "")

    rendered: Optional[str] = None
    if wants_json:
        try:
            parsed = json.loads(content)
            rendered = json.dumps(parsed, ensure_ascii=False, indent=2)
        except Exception:
            rendered = None

    print(rendered if rendered is not None else content)

    _write_run_log(resolved["model"], resolved["base_url"], str(system), user, rendered if rendered is not None else content)

    if wants_json and rendered is None:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
