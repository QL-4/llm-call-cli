#!/usr/bin/env python3
"""Bootstrap or update ~/.llm-call: write config.json (merge) and seed built-in presets.

Flag mode is the primary path (agent-driven): pass only the fields you want to set
or change, e.g. --model new-model to switch models while keeping the rest.
Interactive mode (no flags) prompts via input() / getpass for a human at a shell.

Existing preset files are never overwritten; use presets.py reset to restore defaults.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, Optional


for _stream in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass


def llm_call_home() -> Path:
    return Path(os.environ.get("LLM_CALL_HOME") or Path.home() / ".llm-call").expanduser()


def config_path() -> Path:
    return llm_call_home() / "config.json"


def presets_dir() -> Path:
    return llm_call_home() / "presets"


def defaults_presets_dir() -> Path:
    return Path(__file__).resolve().parent / "defaults" / "presets"


def _prompt(question: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    raw = input(f"{question}{suffix}: ").strip()
    return raw or default


def _interactive_config() -> Dict[str, str]:
    current = _read_config_or_empty()
    print("llm-call init — interactive setup. Press Enter to keep the [current] value.\n")
    base_url = _prompt("base_url (OpenAI-compatible, e.g. http://localhost:PORT/v1)", current.get("base_url", ""))
    model = _prompt("model", current.get("model", ""))
    entered_key = getpass.getpass("api_key (input hidden; press Enter to keep current): ").strip()
    api_key = entered_key or current.get("api_key", "")
    api_mode = _prompt("api_mode", current.get("api_mode", "chat_completions"))
    return {"base_url": base_url, "model": model, "api_key": api_key, "api_mode": api_mode}


def _merge_config(updates: Dict[str, str]) -> Dict[str, str]:
    """Merge flag updates into the on-disk config; missing keys keep existing values."""
    current = _read_config_or_empty()
    merged = {**current, **{k: v for k, v in updates.items() if v}}
    # api_mode has a default; only set it if absent.
    if not merged.get("api_mode"):
        merged["api_mode"] = "chat_completions"
    return merged


def _write_config(cfg: Dict[str, str]) -> int:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path}")
    return 0


def _seed_presets() -> int:
    """Ensure built-in presets exist; seed only the missing ones. Never overwrites
    existing presets (use presets.py reset to restore defaults)."""
    src = defaults_presets_dir()
    if not src.exists():
        print(f"warning: seed presets not found at {src}; skipping preset seeding.", file=sys.stderr)
        return 0
    dst = presets_dir()
    dst.mkdir(parents=True, exist_ok=True)
    seeded, present = [], []
    for src_file in sorted(src.glob("*.json")):
        dst_file = dst / src_file.name
        if dst_file.exists():
            present.append(src_file.stem)
            continue
        shutil.copyfile(src_file, dst_file)
        seeded.append(src_file.stem)
    if seeded:
        print(f"seeded presets: {', '.join(seeded)}")
    if present:
        print(f"built-in presets already present: {', '.join(present)}")
    return 0


def _print_resolved(cfg: Dict[str, str]) -> None:
    model = cfg.get("model", "").strip()
    base_url = cfg.get("base_url", "").strip().rstrip("/")
    api_key = cfg.get("api_key", "").strip()
    api_mode = cfg.get("api_mode", "chat_completions").strip()
    missing = [k for k, v in (("model", model), ("base_url", base_url), ("api_key", api_key)) if not v]
    resolved = {
        "model": model,
        "base_url": base_url,
        "api_key": "[REDACTED]" if api_key else "",
        "api_mode": api_mode,
        "config_home": str(llm_call_home()),
        "llm_call_config": str(config_path()),
        "missing": ",".join(missing),
    }
    print("\nresolved config:")
    print(json.dumps(resolved, ensure_ascii=False, indent=2))
    if missing:
        print(f"\nstill missing: {', '.join(missing)}", file=sys.stderr)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="llm-call-init",
        description="Initialize or update ~/.llm-call: writes config.json (merge) and ensures built-in presets exist. "
                    "Pass only the fields you want to change, e.g. `--model new-model` to switch models.",
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
        cfg = _merge_config(updates)
    else:
        if sys.stdin.isatty():
            cfg = _interactive_config()
        else:
            print("llm-call init: no flags given and stdin is not a tty (cannot prompt).", file=sys.stderr)
            print("Pass the fields to update, e.g. --model new-model, or run with no flags in a shell for interactive setup.", file=sys.stderr)
            return 2

    try:
        if has_config_flag:
            _write_config(cfg)
        _seed_presets()
    except OSError as exc:
        print(f"llm-call init: write failed: {exc}", file=sys.stderr)
        return 1

    _print_resolved(cfg)
    return 0


def _read_config_or_empty() -> Dict[str, str]:
    path = config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


if __name__ == "__main__":
    raise SystemExit(main())
