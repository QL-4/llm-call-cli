#!/usr/bin/env python3
"""Config, paths, logging, and error helpers for llm-call.

All errors are printed to stdout (not stderr) as actionable instructions,
because the primary consumer is an agent that reads stdout.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, Optional

# Self-contained UTF-8 for stdin/stdout/stderr so the CLI works without a
# wrapper setting PYTHONIOENCODING (e.g. on Windows where the default codepage
# would otherwise mangle non-ASCII text).
for _stream in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8')
    except Exception:
        pass



# ---------------------------------------------------------------------------
# Error helpers
# ---------------------------------------------------------------------------

class LlmCallError(Exception):
    """Carries an actionable error message to be printed to stdout.

    args[0] = message, args[1] = hint (optional).
    """


def error(message: str, hint: str = "", code: int = 1) -> int:
    """Print an actionable error to stdout and return exit code."""
    lines = [f"[llm-call error] {message}"]
    if hint:
        lines.append(f"[llm-call hint] {hint}")
    print("\n".join(lines), flush=True)
    return code


def die(message: str, hint: str = "", code: int = 1) -> None:
    """Print error to stdout and exit."""
    raise SystemExit(error(message, hint, code))

def is_perm_error(exc: Exception) -> bool:
    """True if exc looks like a write permission / access denied error."""
    if isinstance(exc, PermissionError):
        return True
    if isinstance(exc, OSError) and getattr(exc, "errno", None) in (13, 5):
        return True
    msg = str(exc).lower()
    return "permission denied" in msg or "access" in msg and "denied" in msg


def perm_hint(exc: Exception) -> str:
    """Hint to escalate when exc is a permission error, empty string otherwise."""
    if is_perm_error(exc):
        return ("Permission denied. Re-run this command with sandbox escalation "
                "(elevated privileges) so it can write to the llm-call home directory.")
    return ""



# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def llm_call_home() -> Path:
    return Path(os.environ.get("LLM_CALL_HOME") or Path.home() / ".llm-call").expanduser()


def config_path() -> Path:
    return llm_call_home() / "config.json"


def log_path() -> Path:
    return llm_call_home() / "log.jsonl"


def presets_dir() -> Path:
    return llm_call_home() / "presets"


def defaults_presets_dir() -> Path:
    return Path(__file__).resolve().parent / "defaults" / "presets"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def utc_now() -> str:
    """Beijing time (UTC+8), human-readable."""
    tz = _dt.timezone(_dt.timedelta(hours=8))
    return _dt.datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")


def load_env() -> None:
    """Tiny dotenv loader to avoid depending on python-dotenv."""
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


def load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def append_log(record: Dict[str, Any]) -> None:
    """Best-effort append-only JSONL log. Silent on failure. Never writes api_key."""
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception:
        return


# ---------------------------------------------------------------------------
# Config resolution
# ---------------------------------------------------------------------------

def resolve_config(model_override: Optional[str], *, strict: bool = True) -> Dict[str, str]:
    load_env()
    cfg = load_json(config_path())

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
        hint = (
            "Ask the user for the missing values, then run: "
            "python scripts/llm_call.py init --base-url URL --model MODEL --api-key KEY"
        )
        die(
            f"not configured ({', '.join(missing)} missing). "
            f"Config file: {config_path()}",
            hint,
        )
    if api_mode and api_mode not in {"chat_completions", "openai", "openai_chat"}:
        die(f"unsupported api_mode: {api_mode}", "Set api_mode to chat_completions in the config file.")

    return {
        "model": str(model),
        "base_url": base_url,
        "api_key": api_key,
        "api_mode": api_mode,
        "config_home": str(llm_call_home()),
        "llm_call_config": str(config_path()),
        **({"missing": ",".join(missing)} if missing else {}),
    }


# ---------------------------------------------------------------------------
# Config writing (used by init subcommand)
# ---------------------------------------------------------------------------

def merge_config(updates: Dict[str, str]) -> Dict[str, str]:
    current = load_json(config_path())
    merged = {**current, **{k: v for k, v in updates.items() if v}}
    if not merged.get("api_mode"):
        merged["api_mode"] = "chat_completions"
    return merged


def write_config(cfg: Dict[str, str]) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path}")


def seed_presets() -> None:
    src = defaults_presets_dir()
    if not src.exists():
        print(f"warning: seed presets not found at {src}; skipping preset seeding.")
        return
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


def print_resolved(cfg: Dict[str, str]) -> None:
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
        **({"missing": ",".join(missing)} if missing else {}),
    }
    print("\nresolved config:")
    print(json.dumps(resolved, ensure_ascii=False, indent=2))
    if missing:
        flags = " ".join(f"--{m.replace('_', '-')} VALUE" for m in missing)
        print(f"\n[llm-call hint] still missing: {', '.join(missing)}. "
              f"Ask the user for these values, then run: "
              f"python scripts/llm_call.py init {flags}")




