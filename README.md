# Manim Edu Agent

Manim Edu Agent 是一个基于 Manim 的教学视频生成流水线。它可以根据文本请求或题目图片，自动生成带讲解、配音和动画的教学视频。

当前仓库主要包含：

- `agent_pipeline/`：主生成流水线
- `eval_pipeline/`：独立的视频评估流水线
- `colortest/`：共享的场景、布局、字幕和主题工具

## 项目能力

给定一个教学请求，主流水线会依次完成：

1. 生成教学计划
2. 选择视觉主题
3. 生成 Manim Scene Pack 代码
4. 在正式渲染前执行语法修复、代码结构修复和渲染修复
5. 用 TTS 生成音频并输出最终视频

当前默认工作流是单轮：

- 只生成 `round1`
- `round1` 直接按最终交付质量渲染
- 主流水线默认不自动执行 `eval` 和 `round2`

如果需要单独评估已有视频，可以直接使用 `eval_pipeline/`。

## 仓库结构

```text
agent_pipeline/          主生成流水线
eval_pipeline/           独立视频评估流水线
colortest/               共享 Manim 场景、主题、字幕与布局工具
icon/                    本地图标素材
runs/                    每次运行的输出目录
app.py                   HTTP API 入口
requirements.txt         Python 依赖
README.md                项目说明文档
```

## 环境要求

- Python 3.10 及以上
- Manim Community Edition
- FFmpeg
- 可选：Tesseract OCR

## 安装

```bash
pip install -r requirements.txt
```

如果环境里还没有 Manim CE 和 FFmpeg，需要另外安装。

## 环境变量

常用配置如下：

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

A4L_RENDER_WORKERS=0
A4L_TTS_WORKERS=8

A4L_SYNTAX_FIX_MAX_ATTEMPTS=4
A4L_RENDER_FIX_MAX_ATTEMPTS=4
A4L_CODE_EVAL_FIX_MAX_ATTEMPTS=2
A4L_LATEX_TEXT_FIX_MAX_ATTEMPTS=2

APP_HOST=0.0.0.0
APP_PORT=8000
```

说明：

- `OPENAI_*` 提供默认模型配置
- `A4L_ANALYSIS_*` 覆盖分析与规划阶段模型
- `A4L_CODE_*` 覆盖代码生成与修复阶段模型
- `A4L_VIDEO_LANGUAGE` 设置默认输出语言，目前支持 `en`、`zh`
- `ROUND1_MANIM_QUALITY` 是主流水线实际使用的渲染质量
- `A4L_RENDER_WORKERS` 限制多段 Manim 渲染的**最大并发**；为 `0` 或未设置时，并发数等于 manifest 段数（每段可同时起一个 `manim` 子进程）。设为正整数时，同时最多运行该数量的段；先完成的段会释放槽位，由 `ThreadPoolExecutor` 自动调度队列中的下一段，无需额外“资源转移”逻辑
- `A4L_TTS_WORKERS` 限制 TTS 预生成时的线程池大小（默认 8）
- `A4L_USE_LOCAL_ICONS` 变量仍保留，但当前主流水线里本地图标选择默认是临时禁用状态

## 快速开始

根据文本请求生成视频：

```bash
python -m agent_pipeline "Explain this problem step by step."
```

生成中文视频：

```bash
python -m agent_pipeline --language zh
```

使用文本加图片生成：

```bash
python -m agent_pipeline "为我讲解一下这道题" --image "E:\Ai4learning\3.18.16.25\屏幕截图 2026-03-26 212747.png" --language zh
```

仅使用图片生成：

```bash
python -m agent_pipeline --image path/to/problem.png --language zh
```

指定输出目录：

```bash
python -m agent_pipeline "Explain L'Hopital's rule" --run-dir runs/lhopital_demo --language en
```

CLI 参数：

- `request`：教学请求文本；如果提供了 `--image`，则可选
- `--image`：输入图片路径
- `--run-dir`：自定义输出目录
- `--language`：输出语言，`en` 或 `zh`

## 输出目录

默认输出根目录为：

```text
runs/YYYYMMDD_HHMMSS/
```

常见产物包括：

- `request.txt`
- `request_image.*`
- `llm_routing.json`
- `teaching_plan.json`
- `selected_theme.json`
- `selected_assets.json`
- `round1/scene.py`
- `round1/render_log.txt`
- `summary.json`

`summary.json` 中常用字段：

- `final_video`
- `final_video_with_audio`
- `selected_theme_id`
- `rounds`
- `final_passed`

## HTTP API

启动 API 服务：

```bash
python app.py
```

默认地址：

```text
http://0.0.0.0:8000
```

接口：

- `POST /jobs`
- `GET /jobs/{job_id}`
- `GET /videos/{job_id}`

请求体示例：

```json
{
  "request": "Explain the lesson topic",
  "language": "en"
}
```

## 独立评估

主流水线默认不执行视频评估。如果要直接评估现有视频，可以运行：

```bash
python -m eval_pipeline path/to/video.mp4 -o eval_output
```

可选参数示例：

```bash
python -m eval_pipeline path/to/video.mp4 --skip-vlm
python -m eval_pipeline path/to/video.mp4 --skip-audio
```

## 当前行为说明

当前仓库默认假设：

- 主生成路径是单轮
- `round1` 就是最终渲染轮次
- 评估与主流水线解耦
- 本地图标选择逻辑当前默认关闭

## 开发说明

后续比较自然的扩展方向包括：

- 把评估作为主流水线的显式可选步骤
- 继续提升页面状态规划和布局稳定性
- 增加更多主题和可复用素材
- 增强渲染与修复流程的回归测试
