---
name: llm-call-cli
description: "Use only when the user explicitly asks to ask/call/query an LLM through llm-call, such as '问一下 llm', '调一下 llm', '用 llm-call', or asks for a clean one-shot LLM second opinion. Do not trigger for ordinary text tasks unless the user asks to call the LLM CLI."
---

# llm-call CLI

A one-shot, clean-context LLM call (`scripts/llm_call.py`): no history, no memory, no tools, no skills in the request. Use it as a disposable second opinion, **not as a second agent**. Only when the user explicitly asks to call the LLM CLI, and the task is small, independent, and mostly text-based.

## How to use

Submit as an sbatch job via the wrapper script:

```bash
bash scripts/llm_call.sh "把这句话改得更温和：我不同意这个方案"
printf '今天很累，但还是写完了实验记录。' | bash scripts/llm_call.sh "提炼一句今日复盘"
printf '文章内容...' | bash scripts/llm_call.sh --preset summarize
printf '文章内容...' | bash scripts/llm_call.sh --preset extract-claims --json
bash scripts/llm_call.sh --list-presets
```

The wrapper creates an sbatch job that runs `scripts/llm_call.py` on a compute node. Output is written to `scripts/sbatch_output/<jobid>-<jobname>.md`.

Options (passed through to `llm_call.py`):

```
--system TEXT        One-call system message
--preset NAME        Preset name (see `bash scripts/llm_call.sh --list-presets`)
--model MODEL        Override the configured model
--temperature FLOAT  Lower for judging/extraction; higher for creative drafting
--json               Request JSON-only output
--max-tokens N       Cap completion length
--image PATH         Include an image file (can be used multiple times for multiple images)
--lint-override R    Bypass the placeholder lint with a stated reason
```

Example with vision:

```bash
bash scripts/llm_call.sh --model mimo-v2.5 --image /path/to/image.png "描述这张图片"
bash scripts/llm_call.sh --model mimo-v2.5 --image img1.png --image img2.png "对比两张图片"
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

## Vision preset: describe-figure

`describe-figure` converts any image into an exhaustive structured plain-text description for downstream text-only models. It works for all image types: charts/plots, diagrams, pipeline visualizations, annotated renders, floor plans, photographs, and composites.

**Requires a multimodal model** (e.g. `mimo-v2.5`). The default text-only model will return HTTP 400.

```bash
# Describe a single image
bash scripts/llm_call.sh --model mimo-v2.5 --preset describe-figure --image /path/to/figure.png

# Pipe the description into a text-only model for follow-up reasoning
bash scripts/llm_call.sh --model mimo-v2.5 --preset describe-figure --image fig.png \
  | bash scripts/llm_call.sh "根据以上图像描述，指出需要改进的地方"
```

Output structure (auto-selected by image type):
- **Step 1** — image type classification (chart/plot, diagram, spatial/scene, annotated image, composite, etc.)
- **A** Layout (shape, panels, background, titles)
- **B** Chart/plot content (axes, series, colorbars, legend) — if applicable
- **C** Spatial/scene content (viewpoint, objects, positions, relationships) — if applicable
- **D** Overlays and annotations (bounding boxes, masks, graph overlays, arrows)
- **E** All text and labels (exact strings, colors, positions)
- **F** Color inventory
- **G** Visual quality notes
- **H** Concise summary (3–5 sentences)

## Prompting rules

1. Put the task in the prompt argument and the source text in stdin.
2. `--temperature 0` for checking/extraction/judging; higher (e.g. `0.7`) for creative drafting.
3. Output lands in `scripts/sbatch_output/` — monitor with `tail -f <path>`.

Treat output as model text, not verified fact; verify important claims separately.