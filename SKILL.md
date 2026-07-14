---
name: llm-call-cli
description: "Use only when the user explicitly asks to ask/call/query an LLM through llm-call, such as '问一下 llm', '调一下 llm', '用 llm-call', or asks for a clean one-shot LLM second opinion. Do not trigger for ordinary text tasks unless the user asks to call the LLM CLI."
---

# llm-call CLI

A one-shot, clean-context LLM call (`scripts/llm_call.py`): no history, no memory, no tools, no skills in the request. Use it as a disposable second opinion, **not as a second agent**. Only when the user explicitly asks to call the LLM CLI, and the task is small, independent, and mostly text-based.

Single Python entry point — no shell wrappers, no sbatch. Runs anywhere with Python on PATH.

## How to use

```bash
python scripts/llm_call.py "把这句话改得更温和：我不同意这个方案"
printf '今天很累，但还是写完了实验记录。' | python scripts/llm_call.py "提炼一句今日复盘"
printf '文章内容...' | python scripts/llm_call.py --preset summarize
printf '文章内容...' | python scripts/llm_call.py --preset extract-claims --json
python scripts/llm_call.py --list-presets
python scripts/llm_call.py --show-config      # api_key is redacted
```

Model output goes to stdout. Each call appends one JSON record (prompt, model, output; no api_key) to `~/.llm-call/log.jsonl`.

Options:

```
--system TEXT        One-call system message
--preset NAME        Preset name (see `python scripts/llm_call.py --list-presets`)
--model MODEL        Override the configured model
--temperature FLOAT  Lower for judging/extraction; higher for creative drafting
--json               Request JSON-only output
--max-tokens N       Cap completion length
--image PATH         Include an image file (can be used multiple times for multiple images)
--lint-override R    Bypass the placeholder lint with a stated reason
--timeout SECONDS    HTTP timeout (default: 900 = 15 min)
--show-config        Print resolved model/base_url with api_key redacted
```

Example with vision:

```bash
python scripts/llm_call.py --model mimo-v2.5 --image /path/to/image.png "描述这张图片"
python scripts/llm_call.py --model mimo-v2.5 --image img1.png --image img2.png "对比两张图片"
```

## Config

`python scripts/llm_call.py init` writes `~/.llm-call/config.json` (base_url, model, api_key, api_mode) and seeds built-in presets. It merges updates — pass only the flag you want to change:

```
python scripts/llm_call.py init --base-url URL --model MODEL --api-key KEY   # set up
python scripts/llm_call.py init --model new-model                            # change one field
```

Run with no flags in a shell for interactive setup. Override the directory with `LLM_CALL_HOME` (default `~/.llm-call`).

## Presets

Built-in presets live in `scripts/defaults/presets/` and are seeded into `~/.llm-call/presets/` by `init`. List them:

```
python scripts/llm_call.py --list-presets
python scripts/llm_call.py preset list
```

Manage presets:

```
python scripts/llm_call.py preset show NAME
python scripts/llm_call.py preset add NAME --system S --user-template 'Task: {{input}}' [--description D] [--temperature N] [--json]
python scripts/llm_call.py preset edit NAME
python scripts/llm_call.py preset remove NAME [--force]
python scripts/llm_call.py preset reset [--force]
```

`user_template` supports `{{input}}` (stdin text) and `{{prompt}}` (prompt arg) placeholders. The `description` field is auto-read from each preset JSON for `--list-presets` / `preset list`.

### Saving a successful call as a preset

When the user says a call's output was good and wants to reuse it,固化成 preset. The agent knows the original system message, prompt argument, stdin content, and temperature from its own memory — it just needs to generalize the variable parts:

1. Take the `--system` and `--temperature` from the original call verbatim.
2. Take the prompt argument. If the call used stdin, replace the data-dependent part with `{{input}}` (or `{{prompt}}` if the variable was in the prompt arg). If the call had no stdin, the prompt arg is the entire `user_template`.
3. Run `preset add`.

Example — this call produced good output:

```bash
printf '这段文字很模糊...' | python scripts/llm_call.py --system "你是技术评审" --temperature 0 "评估清晰度，输出结论、理由、改进建议"
```

Save it:

```bash
python scripts/llm_call.py preset add clarity-judge \
  --system "你是技术评审" \
  --user-template '评估清晰度，输出结论、理由、改进建议

Input:
"""
{{input}}
"""' \
  --temperature 0 \
  --description "评估文字清晰度，输出结论+理由+建议"
```

Reuse with different data:

```bash
printf '另一段文字...' | python scripts/llm_call.py --preset clarity-judge
```

## Vision

Use `--image` with a multimodal model. The `describe-image` preset gives a general-purpose description; or write your own prompt for specific needs.

**Requires a multimodal model** (e.g. `mimo-v2.5`). The default text-only model will return HTTP 400.

```bash
# General image description
python scripts/llm_call.py --model mimo-v2.5 --preset describe-image --image /path/to/image.png

# Custom task with vision
python scripts/llm_call.py --model mimo-v2.5 --image photo.jpg "这张图里有几个人？"

# Pipe description into a text-only model for follow-up reasoning
python scripts/llm_call.py --model mimo-v2.5 --preset describe-image --image fig.png \
  | python scripts/llm_call.py "根据以上图像描述，指出需要改进的地方"
```

## Prompting rules

1. Put the task in the prompt argument and the source text in stdin.
2. `--temperature 0` for checking/extraction/judging; higher (e.g. `0.7`) for creative drafting.
3. Treat output as model text, not verified fact; verify important claims separately.



