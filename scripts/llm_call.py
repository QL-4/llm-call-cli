#!/usr/bin/env python3
"""Standalone single-shot LLM CLI with config init and preset management.

Self-contained: reads its own ~/.llm-call/config.json (or $LLM_CALL_HOME) for
model/base_url, and ~/.llm-call/config.json api_key or the LLM_CALL_API_KEY
env var for credentials.

One clean request, then exit. No tools, no memory, no session history.
Each call appends one JSON record to ~/.llm-call/log.jsonl (prompt, model,
output; no api_key; no image bytes).

Subcommands:
  llm_call.py [prompt] [options]   default: make an LLM call
  llm_call.py init [options]       write/merge config + seed presets
  llm_call.py preset <subcommand>  manage presets

All errors are printed to stdout (not stderr) as actionable instructions,
because the primary consumer is an agent that reads stdout.

Internal modules _config, _presets, _api live alongside this file but are
not part of the public interface — the agent only invokes llm_call.py.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import json
import mimetypes
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from _config import (
    LlmCallError, error, die,
    llm_call_home, config_path, presets_dir, defaults_presets_dir,
    utc_now, load_json,
    append_log, resolve_config,
    merge_config, write_config, seed_presets, print_resolved,
    perm_hint,
)
from _presets import (
    load_preset, read_stdin, render_template,
    lint_invocation, print_presets,
)
from _api import post_chat_completions


# ---------------------------------------------------------------------------
# Call mode
# ---------------------------------------------------------------------------

def cmd_call(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="llm-call",
        description="Single-shot LLM call: no tools, no memory, no session history.",
    )
    parser.add_argument("prompt", nargs="*", help="Prompt text. stdin is appended/used as input.")
    parser.add_argument("--system", help="System message for this one call.")
    parser.add_argument("--preset", help="Preset name (see `llm_call.py preset list`).")
    parser.add_argument("--list-presets", action="store_true", help="List available presets and exit.")
    parser.add_argument("--model", help="Override model for this call.")
    parser.add_argument("--temperature", type=float, help="Sampling temperature.")
    parser.add_argument("--max-tokens", type=int, help="Maximum completion tokens.")
    parser.add_argument("--json", action="store_true", help="Ask for JSON output and validate if possible.")
    parser.add_argument("--lint", action="store_true", help="Lint invocation and exit without calling provider")
    parser.add_argument("--lint-override", help="Allow lint errors with explicit reason")
    parser.add_argument("--image", action="append", dest="images", help="Image file path(s) to include in the request (can be used multiple times).")
    parser.add_argument("--timeout", type=float, default=900, help="Request timeout seconds (default: 900 = 15 min).")
    parser.add_argument("--show-config", action="store_true", help="Print resolved model/base_url with api_key redacted.")
    args = parser.parse_args(argv)

    if args.list_presets:
        print_presets()
        return 0

    if args.show_config:
        resolved = resolve_config(args.model, strict=False)
        print(json.dumps({k: ("[REDACTED]" if k == "api_key" and v else v) for k, v in resolved.items()}, ensure_ascii=False, indent=2))
        return 0

    prompt = " ".join(args.prompt).strip()
    stdin_text = read_stdin()
    preset: Dict[str, Any] = {}
    if args.preset:
        preset = load_preset(args.preset)

    lint_result = lint_invocation(prompt, stdin_text)
    if args.lint:
        print(json.dumps(lint_result, ensure_ascii=False, indent=2))
        return 0 if lint_result["ok"] else 2
    if not lint_result["ok"] and not args.lint_override:
        finding = lint_result["findings"][0]
        return error(
            f"lint blocked the call: {finding['message']}",
            'Review the input and remove the placeholder, or re-run with --lint-override "reason".'
        )

    system = args.system if args.system is not None else preset.get("system", "You are a concise, accurate assistant.")
    if preset.get("user_template"):
        user = render_template(str(preset["user_template"]), stdin_text, prompt)
    else:
        if stdin_text and prompt:
            user = f"{prompt}\n\nInput:\n\"\"\"\n{stdin_text}\n\"\"\""
        else:
            user = prompt or stdin_text

    if not user.strip():
        return error(
            "no prompt provided",
            'Pass a prompt argument or pipe text via stdin, e.g.: python scripts/llm_call.py "your prompt"'
        )

    resolved = resolve_config(args.model)
    temperature = args.temperature if args.temperature is not None else preset.get("temperature", 0.2)
    wants_json = bool(args.json or preset.get("json"))

    messages: list[dict] = [
        {"role": "system", "content": str(system)},
    ]

    image_paths: list[str] = []
    if args.images:
        content_parts: list[dict] = [{"type": "text", "text": user}]
        for img_path in args.images:
            path = Path(img_path)
            if not path.exists():
                return error(f"image file not found: {img_path}", "Check the path, or use an absolute path.")
            mime_type, _ = mimetypes.guess_type(str(path))
            if mime_type is None:
                mime_type = "image/png"
            b64 = base64.b64encode(path.read_bytes()).decode("ascii")
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{b64}"},
            })
            image_paths.append(str(path))
        messages.append({"role": "user", "content": content_parts})
    else:
        messages.append({"role": "user", "content": user})

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

    try:
        response = post_chat_completions(request_url, resolved["api_key"], request_body, args.timeout)
    except LlmCallError as exc:
        # Retry once without response_format if --json caused the failure
        if wants_json and "response_format" in request_body:
            request_body.pop("response_format", None)
            try:
                response = post_chat_completions(request_url, resolved["api_key"], request_body, args.timeout)
            except LlmCallError as exc2:
                msg = exc2.args[0] if exc2.args else str(exc2)
                hint = exc2.args[1] if len(exc2.args) > 1 else ""
                return error(str(msg), hint)
        else:
            msg = exc.args[0] if exc.args else str(exc)
            hint = exc.args[1] if len(exc.args) > 1 else ""
            return error(str(msg), hint)
    except Exception as exc:
        return error(f"unexpected error: {exc}", "This may be a bug. Check the command and config, then retry.")

    choices = response.get("choices") or []
    if not choices:
        return error(
            f"response has no choices: {json.dumps(response, ensure_ascii=False)[:500]}",
            "The endpoint may have returned an error page instead of a chat completion. Check the model name and base_url."
        )
    message = choices[0].get("message") or {}
    content = str(message.get("content") or "")

    rendered: Optional[str] = None
    if wants_json:
        try:
            parsed = json.loads(content)
            rendered = json.dumps(parsed, ensure_ascii=False, indent=2)
        except Exception:
            rendered = None

    print(rendered if rendered is not None else content, flush=True)

    log_record = {
        "time": utc_now(),
        "model": resolved["model"],
        "system": str(system).strip(),
        "user": user if isinstance(user, str) else json.dumps(user, ensure_ascii=False),
        "output": rendered if rendered is not None else content,
        "temperature": temperature,
    }
    if image_paths:
        log_record["images"] = image_paths
    if args.preset:
        log_record["preset"] = args.preset
    append_log(log_record)

    if wants_json and rendered is None:
        return error(
            "model output was not valid JSON despite --json",
            "The model may not support JSON mode. Try without --json, or use a different model."
        )
    return 0


# ---------------------------------------------------------------------------
# init subcommand
# ---------------------------------------------------------------------------

def _interactive_config() -> Dict[str, str]:
    current = load_json(config_path())
    print("llm-call init — interactive setup. Press Enter to keep the [current] value.\n")

    def _prompt(question: str, default: str = "") -> str:
        suffix = f" [{default}]" if default else ""
        raw = input(f"{question}{suffix}: ").strip()
        return raw or default

    base_url = _prompt("base_url", current.get("base_url", "http://localhost:8317/v1"))
    entered_key = getpass.getpass("api_key (input hidden; press Enter to keep current): ").strip()
    api_key = entered_key or current.get("api_key", "")
    model = _prompt("model", current.get("model", ""))
    api_mode = _prompt("api_mode", current.get("api_mode", "chat_completions"))
    return {"base_url": base_url, "model": model, "api_key": api_key, "api_mode": api_mode}


def cmd_init(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="llm-call init",
        description="Initialize or update ~/.llm-call: writes config.json (merge) and ensures built-in presets exist.",
    )
    parser.add_argument("--base-url", help="OpenAI-compatible base URL. Omit to keep existing.")
    parser.add_argument("--model", help="Model name. Omit to keep existing.")
    parser.add_argument("--api-key", help="API key. Omit to keep existing (use getpass in interactive mode).")
    parser.add_argument("--api-mode", help="API mode (default: chat_completions). Omit to keep existing.")
    args = parser.parse_args(argv)

    has_config_flag = any([args.base_url, args.model, args.api_key, args.api_mode])

    if has_config_flag:
        updates = {
            "base_url": args.base_url or "",
            "model": args.model or "",
            "api_key": args.api_key or "",
            "api_mode": args.api_mode or "",
        }
        cfg = merge_config(updates)
    else:
        if sys.stdin.isatty():
            cfg = _interactive_config()
        else:
            return error(
                "init: no flags given and stdin is not a tty (cannot prompt interactively)",
                "Pass the fields to update, e.g.: python scripts/llm_call.py init --base-url URL --model MODEL --api-key KEY"
            )

    try:
        write_config(cfg)
        seed_presets()
    except OSError as exc:
        hint = perm_hint(exc) or f"Check permissions on {llm_call_home()}."
        return error(f"init: write failed: {exc}", hint)

    print_resolved(cfg)
    return 0


# ---------------------------------------------------------------------------
# preset subcommand
# ---------------------------------------------------------------------------

def _confirm(prompt: str) -> bool:
    if not sys.stdin.isatty():
        return False
    return input(f"{prompt} [y/N]: ").strip().lower() in {"y", "yes"}


def cmd_preset(argv: list[str]) -> int:
    from _presets import available_preset_names

    parser = argparse.ArgumentParser(prog="llm-call preset", description="Manage ~/.llm-call/presets.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List available presets.")

    p_show = sub.add_parser("show", help="Print a preset as JSON.")
    p_show.add_argument("name")

    p_add = sub.add_parser("add", help="Create a preset from flags.")
    p_add.add_argument("name")
    p_add.add_argument("--system", required=True, help="System message.")
    p_add.add_argument("--user-template", required=True, help="User template; use {{input}} / {{prompt}} placeholders.")
    p_add.add_argument("--description", default="", help="Short description shown in preset list.")
    p_add.add_argument("--temperature", type=float, default=None)
    p_add.add_argument("--json", action="store_true", help="Request JSON-only output.")
    p_add.add_argument("--force", action="store_true", help="Overwrite if exists.")

    p_edit = sub.add_parser("edit", help="Open a preset in $EDITOR.")
    p_edit.add_argument("name")

    p_remove = sub.add_parser("remove", help="Delete a preset file.")
    p_remove.add_argument("name")
    p_remove.add_argument("--force", action="store_true", help="Skip confirmation.")

    p_reset = sub.add_parser("reset", help="Overwrite all presets with defaults.")
    p_reset.add_argument("--force", action="store_true", help="Skip confirmation.")

    args = parser.parse_args(argv)

    if args.command == "list":
        names = available_preset_names()
        if not names:
            print(f"No presets in {presets_dir()}.")
            print("Run `python scripts/llm_call.py init` to seed built-ins, or `preset add` to create one.")
            return 0
        print("Presets (use with `llm_call.py --preset NAME`):")
        for name in names:
            data = load_json(presets_dir() / f"{name}.json")
            desc = data.get("description") or ""
            print(f"  {name}" + (f" — {desc}" if desc else ""))
        return 0

    if args.command == "show":
        path = presets_dir() / f"{args.name}.json"
        data = load_json(path)
        if not data:
            return error(
                f"preset {args.name!r} not found (or file is invalid)",
                "Run: python scripts/llm_call.py preset list"
            )
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    if args.command == "add":
        path = presets_dir() / f"{args.name}.json"
        if path.exists() and not args.force:
            return error(
                f"preset {args.name!r} already exists",
                "Use --force to overwrite."
            )
        data: Dict[str, Any] = {"system": args.system, "user_template": args.user_template}
        if args.description:
            data["description"] = args.description
        if args.temperature is not None:
            data["temperature"] = args.temperature
        if args.json:
            data["json"] = True
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {path}")
        return 0

    if args.command == "edit":
        path = presets_dir() / f"{args.name}.json"
        if not path.exists():
            return error(
                f"preset {args.name!r} not found",
                "Run: python scripts/llm_call.py preset list"
            )
        editor = os.environ.get("EDITOR") or ("notepad" if os.name == "nt" else "vi")
        try:
            subprocess.call([editor, str(path)])
        except OSError as exc:
            print(f"could not launch editor {editor!r}: {exc}")
            print(f"Edit the file directly: {path}")
            return 1
        return 0

    if args.command == "remove":
        path = presets_dir() / f"{args.name}.json"
        if not path.exists():
            return error(
                f"preset {args.name!r} not found",
                "Run: python scripts/llm_call.py preset list"
            )
        if not args.force and not _confirm(f"Remove preset {args.name!r}?"):
            print("aborted.")
            return 0
        path.unlink()
        print(f"removed {path}")
        return 0

    if args.command == "reset":
        src = defaults_presets_dir()
        if not src.exists():
            return error(f"seed presets not found at {src}")
        dst = presets_dir()
        dst.mkdir(parents=True, exist_ok=True)
        targets = sorted(src.glob("*.json"))
        existing = [p.stem for p in targets if (dst / p.name).exists()]
        if existing and not args.force:
            if not _confirm(f"Reset will overwrite: {', '.join(existing)}?"):
                print("aborted (pass --force to skip this prompt).")
                return 0
        for src_file in targets:
            shutil.copyfile(src_file, dst / src_file.name)
        print(f"reset presets to defaults: {', '.join(p.stem for p in targets)}")
        return 0

    return 2


# ---------------------------------------------------------------------------
# Top-level help and entry point
# ---------------------------------------------------------------------------

def _print_top_help() -> None:
    print("""usage: llm_call.py [-h | --help]
       llm_call.py [PROMPT ...] [options]
       llm_call.py init [options]
       llm_call.py preset <list|show|add|edit|remove|reset> [options]

Standalone single-shot LLM CLI: no tools, no memory, no session history.
Model output goes to stdout. Each call is logged to ~/.llm-call/log.jsonl.

subcommands:
  (default)    Make an LLM call.
               Options: --system, --preset, --model, --temperature, --json,
               --max-tokens, --image, --lint, --lint-override, --timeout,
               --show-config, --list-presets
  init         Write or merge ~/.llm-call/config.json and seed built-in presets.
  preset       Manage presets: list, show, add, edit, remove, reset.

Run `llm_call.py <subcommand> --help` for subcommand-specific options.

examples:
  python scripts/llm_call.py "把这句话改得更温和：我不同意这个方案"
  echo "text" | python scripts/llm_call.py --preset summarize --json
  python scripts/llm_call.py init --base-url URL --model MODEL --api-key KEY
  python scripts/llm_call.py preset list
  python scripts/llm_call.py preset add my-preset --system S --user-template '{{input}}'
""")


def main(argv: Optional[list[str]] = None) -> int:
    args = sys.argv[1:] if argv is None else argv

    if args and args[0] in ("-h", "--help", "help"):
        _print_top_help()
        return 0
    if args and args[0] == "init":
        return cmd_init(args[1:])
    if args and args[0] == "preset":
        return cmd_preset(args[1:])
    return cmd_call(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        # Catch-all: no raw tracebacks leak to the agent. Print a clean
        # actionable error to stdout instead.
        hint = perm_hint(exc) or "This may be a bug. Run with --show-config to verify setup, or check the command syntax with --help."
        print(f"[llm-call error] unexpected failure: {exc}", flush=True)
        print(f"[llm-call hint] {hint}", flush=True)
        raise SystemExit(1)
