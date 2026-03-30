# Manim Edu Agent

Manim Edu Agent is a pipeline for generating narrated educational videos with Manim from a text prompt or a problem image.

The repository contains:

- a single-round generation pipeline in `agent_pipeline/`
- an optional standalone evaluation pipeline in `eval_pipeline/`
- shared scene/layout/theme utilities in `colortest/`

## What It Does

Given a lesson request, the pipeline will:

1. build a teaching plan
2. choose a visual theme
3. choose local icon assets when enabled
4. generate Manim Scene Pack code
5. run syntax/code-structure/render repairs before rendering
6. render the final video with TTS audio

The current default flow is single-round:

- only `round1` is produced
- `round1` is rendered at final-delivery quality
- automatic `eval` and `round2` are not part of the default main pipeline

`eval_pipeline/` is still available as a separate tool for evaluating existing videos.

## Repository Layout

```text
agent_pipeline/          Main generation pipeline
eval_pipeline/           Standalone video evaluation pipeline
colortest/               Shared Manim scene, theme, subtitle, and layout utilities
icon/                    Local icon assets
runs/                    Per-run outputs
app.py                   HTTP API entrypoint
requirements.txt         Python dependencies
README.md                Project documentation
```

## Requirements

- Python 3.10 or newer
- Manim Community Edition
- FFmpeg
- Optional: Tesseract OCR

## Installation

```bash
pip install -r requirements.txt
```

Install Manim CE and FFmpeg separately if they are not already available in the environment.

## Environment Variables

Common configuration:

```env
OPENAI_API_KEY=
OPENAI_BASE_URL=
OPENAI_MODEL=

A4L_ANALYSIS_API_KEY=
A4L_ANALYSIS_BASE_URL=
A4L_ANALYSIS_MODEL=

A4L_CODE_API_KEY=
A4L_CODE_BASE_URL=
A4L_CODE_MODEL=

A4L_VIDEO_LANGUAGE=en
A4L_USE_LOCAL_ICONS=1

MANIM_QUALITY=-qm --fps 60
ROUND1_MANIM_QUALITY=-qm --fps 60
MANIM_TIMEOUT_SEC=1200

A4L_SYNTAX_FIX_MAX_ATTEMPTS=4
A4L_RENDER_FIX_MAX_ATTEMPTS=4
A4L_CODE_EVAL_FIX_MAX_ATTEMPTS=2
A4L_LATEX_TEXT_FIX_MAX_ATTEMPTS=2

APP_HOST=0.0.0.0
APP_PORT=8000
```

Notes:

- `OPENAI_*` provides the default model configuration.
- `A4L_ANALYSIS_*` overrides the analysis/planning side of the pipeline.
- `A4L_CODE_*` overrides code generation and repair.
- `A4L_VIDEO_LANGUAGE` sets the default output language. Supported values: `en`, `zh`.
- `A4L_USE_LOCAL_ICONS` controls whether local assets under `icon/` are used.
- `ROUND1_MANIM_QUALITY` is the effective render quality for the main pipeline.

## Quick Start

Generate a video from a text request:

```bash
python -m agent_pipeline "Explain this problem step by step."
```

Generate a Chinese video:

```bash
python -m agent_pipeline ""E:\Ai4learning\3.18.16.25\屏幕截图 2026-03-26 212747.png"" --language zh
```

Generate from text plus an image:

```bash
python -m agent_pipeline "Explain this problem step by step." --image path/to/problem.png --language en
```

Generate from image only:

```bash
python -m agent_pipeline --image path/to/problem.png --language zh
```

Use a custom output directory:

```bash
python -m agent_pipeline "Explain L'Hopital's rule" --run-dir runs/lhopital_demo --language en
```

CLI arguments:

- `request`: lesson request text; optional if `--image` is provided
- `--image`: input image path
- `--run-dir`: custom output directory
- `--language`: output language, `en` or `zh`

## Output Structure

Default output root:

```text
runs/YYYYMMDD_HHMMSS/
```

Common files:

- `request.txt`
- `request_image.*`
- `llm_routing.json`
- `teaching_plan.json`
- `selected_theme.json`
- `selected_assets.json`
- `round1/scene.py`
- `round1/render_log.txt`
- `summary.json`

Typical fields of interest in `summary.json`:

- `final_video`
- `final_video_with_audio`
- `selected_theme_id`
- `rounds`
- `final_passed`

## HTTP API

Start the API server:

```bash
python app.py
```

Default address:

```text
http://0.0.0.0:8000
```

Endpoints:

- `POST /jobs`
- `GET /jobs/{job_id}`
- `GET /videos/{job_id}`

Example request body:

```json
{
  "request": "Explain the lesson topic",
  "language": "en"
}
```

## Standalone Evaluation

The main pipeline no longer runs evaluation by default. To evaluate an existing video directly:

```bash
python -m eval_pipeline path/to/video.mp4 -o eval_output
```

Optional flags:

```bash
python -m eval_pipeline path/to/video.mp4 --skip-vlm
python -m eval_pipeline path/to/video.mp4 --skip-audio
```

## Current Behavior

The repository currently assumes:

- the main generation path is single-round
- `round1` is the final render pass
- evaluation is decoupled from the main pipeline
- local icons are selected only from `icon/`

## Development Notes

Likely extension directions:

- make evaluation an explicit optional flag in the main pipeline
- improve page/state/layout planning in generated scenes
- add more themes and reusable local assets
- add stronger regression tests around render and repair stages
