#!/usr/bin/env python3
"""Manage ~/.llm-call/presets/*.json.

Subcommands: list, show, add, edit, remove, reset.
Built-in seeds live in scripts/defaults/presets/ and are installed by init.py.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
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


def presets_dir() -> Path:
    return llm_call_home() / "presets"


def defaults_presets_dir() -> Path:
    return Path(__file__).resolve().parent / "defaults" / "presets"


def _preset_path(name: str) -> Path:
    return presets_dir() / f"{name}.json"


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _is_valid_preset(path: Path) -> bool:
    data = _read_json(path)
    return bool(data and isinstance(data.get("user_template"), str) and isinstance(data.get("system"), str))


def _list_names() -> list[str]:
    d = presets_dir()
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.json") if _is_valid_preset(p))


def _confirm(prompt: str) -> bool:
    if not sys.stdin.isatty():
        return False
    return input(f"{prompt} [y/N]: ").strip().lower() in {"y", "yes"}


_PRESET_DESCRIPTIONS: Dict[str, str] = {
    "summarize": "faithful concise bullet summary",
    "judge": "independent evaluator: verdict, rationale, improvement",
    "concept-check": "check whether an explanation/analogy is conceptually wrong or misleading",
    "extract-claims": "extract checkable factual claims as JSON",
    "prompt-lint": "check a prompt for ambiguity, conflicting constraints, hallucination/output-format risk",
}


def cmd_list(args: argparse.Namespace) -> int:
    names = _list_names()
    if not names:
        print(f"No presets in {presets_dir()}.")
        print("Run `python scripts/init.py` to seed built-ins, or `presets.py add` to create one.")
        return 0
    print("Presets (use with `llm_call.py --preset NAME`):")
    for name in names:
        desc = _PRESET_DESCRIPTIONS.get(name)
        print(f"  {name}" + (f" — {desc}" if desc else ""))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    path = _preset_path(args.name)
    data = _read_json(path)
    if data is None:
        print(f"llm-call presets: no such preset {args.name!r} (or file is invalid).", file=sys.stderr)
        print("Run `presets.py list` to see available presets.", file=sys.stderr)
        return 2
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    path = _preset_path(args.name)
    if path.exists() and not args.force:
        print(f"llm-call presets: preset {args.name!r} already exists (use --force to overwrite).", file=sys.stderr)
        return 2
    data: Dict[str, Any] = {"system": args.system, "user_template": args.user_template}
    if args.temperature is not None:
        data["temperature"] = args.temperature
    if args.json:
        data["json"] = True
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path}")
    return 0


def cmd_edit(args: argparse.Namespace) -> int:
    path = _preset_path(args.name)
    if not path.exists():
        print(f"llm-call presets: no such preset {args.name!r}.", file=sys.stderr)
        return 2
    editor = os.environ.get("EDITOR") or ("notepad" if os.name == "nt" else "vi")
    try:
        subprocess.call([editor, str(path)])
    except OSError as exc:
        print(f"llm-call presets: could not launch editor {editor!r}: {exc}", file=sys.stderr)
        print(f"Edit the file directly: {path}", file=sys.stderr)
        return 1
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    path = _preset_path(args.name)
    if not path.exists():
        print(f"llm-call presets: no such preset {args.name!r}.", file=sys.stderr)
        return 2
    if not args.force and not _confirm(f"Remove preset {args.name!r}?"):
        print("aborted.")
        return 0
    path.unlink()
    print(f"removed {path}")
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    src = defaults_presets_dir()
    if not src.exists():
        print(f"llm-call presets: seed presets not found at {src}.", file=sys.stderr)
        return 1
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


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="llm-call-presets", description="Manage ~/.llm-call/presets.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List available presets.")

    p_show = sub.add_parser("show", help="Print a preset as JSON.")
    p_show.add_argument("name")

    p_add = sub.add_parser("add", help="Create a preset from flags.")
    p_add.add_argument("name")
    p_add.add_argument("--system", required=True, help="System message.")
    p_add.add_argument("--user-template", required=True, help="User template; use {{input}} / {{prompt}} placeholders.")
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
    handlers = {
        "list": cmd_list,
        "show": cmd_show,
        "add": cmd_add,
        "edit": cmd_edit,
        "remove": cmd_remove,
        "reset": cmd_reset,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
