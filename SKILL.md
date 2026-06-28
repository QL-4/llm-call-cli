---
name: llm-call-cli
description: "Use only when the user explicitly asks to ask/call/query an LLM through llm-call, such as '问一下 llm', '调一下 llm', '用 llm-call', or asks for a clean one-shot LLM second opinion. Do not trigger for ordinary text tasks unless the user asks to call the LLM CLI."
---

# llm-call CLI

A one-shot, clean-context LLM call (`scripts/llm_call.py`): no history, no memory, no tools, no skills in the request. Use it as a disposable second opinion, **not as a second agent**. Only when the user explicitly asks to call the LLM CLI, and the task is small, independent, and mostly text-based.

## How to use

Call the script directly. Same invocation on every platform:

```bash
python scripts/llm_call.py "把这句话改得更温和：我不同意这个方案"
printf '今天很累，但还是写完了实验记录。' | python scripts/llm_call.py "提炼一句今日复盘"
printf '文章内容...' | python scripts/llm_call.py --preset summarize
printf '文章内容...' | python scripts/llm_call.py --preset extract-claims --json
python scripts/llm_call.py --list-presets
```

Options:

```
--system TEXT        One-call system message
--preset NAME        Preset name (see `python scripts/presets.py list`)
--model MODEL        Override the configured model
--temperature FLOAT  Lower for judging/extraction; higher for creative drafting
--json               Request JSON-only output
--max-tokens N       Cap completion length
--timeout SECONDS    Optional HTTP timeout (default: none)
--lint-override R    Bypass the placeholder lint with a stated reason
```

## Config

`scripts/init.py` writes `~/.llm-call/config.json` (base_url, model, api_key, api_mode) and seeds the built-in presets. It merges updates — pass only the flag you want to change, e.g. `--model new-model` to switch models while keeping the rest:

```
python scripts/init.py --base-url URL --model MODEL --api-key KEY   # set up
python scripts/init.py --model new-model                            # change one field
```

Run with no flags in a shell for interactive setup. Override the directory with `LLM_CALL_HOME` (default `~/.llm-call`).

## Presets

`init.py` seeds built-in presets into `~/.llm-call/presets/`. List them with `python scripts/llm_call.py --list-presets` or `python scripts/presets.py list`. Manage with `scripts/presets.py`:

```
python scripts/presets.py show NAME                         # print a preset as JSON
python scripts/presets.py add NAME --system S --user-template 'Task: {{input}}' [--temperature N] [--json]
python scripts/presets.py edit NAME                         # open in $EDITOR
python scripts/presets.py remove NAME [--force]
python scripts/presets.py reset [--force]                   # overwrite all with defaults
```

`user_template` supports `{{input}}` (stdin text) and `{{prompt}}` (prompt arg) placeholders.

## Prompting rules

1. Put the task in the prompt argument and the source text in stdin.
2. `--temperature 0` for checking/extraction/judging; higher (e.g. `0.7`) for creative drafting.
3. Do not set a short `--timeout` — by default there is none; let the call finish unless the user asks to cancel.

Treat output as model text, not verified fact; verify important claims separately.
