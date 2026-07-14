#!/usr/bin/env python3
"""Preset discovery, loading, template rendering, and lint."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict

from _config import presets_dir, load_json, die


def is_valid_preset(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return isinstance(data, dict) and isinstance(data.get("user_template"), str)


def available_preset_names() -> list[str]:
    d = presets_dir()
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.json") if is_valid_preset(p))


def load_preset(name: str) -> Dict[str, Any]:
    path = presets_dir() / f"{name}.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            die(f"preset {name!r} is not valid JSON at {path}")
        if not isinstance(data, dict) or not isinstance(data.get("user_template"), str):
            die(f"preset {name!r} must be a JSON object with a 'user_template' string")
        return data
    known = ", ".join(available_preset_names())
    hint = f"Available presets: {known}." if known else "Run `python scripts/llm_call.py init` to seed built-ins."
    die(f"preset {name!r} not found", hint)


def read_stdin() -> str:
    if sys.stdin is not None and not sys.stdin.isatty():
        try:
            return sys.stdin.read()
        except OSError:
            return ""
    return ""


def render_template(template: str, input_text: str, prompt: str) -> str:
    return template.replace("{{input}}", input_text).replace("{{prompt}}", prompt)


PLACEHOLDER_PATTERNS = [r"\$\(cat\s+[^)]+\)", r"<insert[^>]*>", r"TODO\s*:?"]


def lint_invocation(prompt: str, input_text: str) -> Dict[str, Any]:
    findings = []
    text = (prompt or "") + "\n" + (input_text or "")
    for pat in PLACEHOLDER_PATTERNS:
        if re.search(pat, text, flags=re.I):
            findings.append({"rule": "unresolved_artifact_placeholder", "severity": "error", "message": f"Unresolved artifact placeholder matched: {pat}"})
            break
    ok = not findings
    return {"ok": ok, "findings": findings}


def print_presets() -> None:
    names = available_preset_names()
    if not names:
        print(f"No presets found in {presets_dir()}.")
        print("Run `python scripts/llm_call.py init` to seed built-ins.")
        return
    print("Presets (use with --preset NAME):")
    for name in names:
        data = load_json(presets_dir() / f"{name}.json")
        desc = data.get("description") or ""
        print(f"  {name}" + (f" — {desc}" if desc else ""))
    print("Manage presets with `python scripts/llm_call.py preset`.")
