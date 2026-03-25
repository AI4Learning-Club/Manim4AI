# Manim 教育视频多智能体流水线

根据用户输入的教学需求，自动完成教学编排、主题选择、素材选择、Manim 代码生成、渲染、评测和二轮改进。

当前主流程是：

1. `TeachingPlanner` 先生成结构化教学计划
2. `ThemeResolver` 从 4 套背景主题中选择 1 套
3. `AssetResolver` 选择本地图标素材
4. `CodeGen` 生成 Round 1 场景代码
5. Round 1 用 TTS 渲染视频，并跑一次 full eval
6. `CodeGen` 根据 Round 1 的评测结果和关键帧做 Round 2 改进
7. Round 2 再次渲染，作为最终交付视频

如果 Round 2 渲染失败，系统会回退到 Round 1 视频。

## 功能概览

- 教学编排：把自然语言需求整理成可执行的 `teaching_plan.json`
- 背景主题系统：支持 `mist_blue_focus`、`soft_glass_white`、`charcoal_board`、`deep_space_board`
- 主题驱动配色：文本、公式、边框、面板、图表颜色都从 theme token 获取
- 本地图标解析：优先从 `icon/` 中选择与主题内容相关的素材
- 代码生成与修复：支持 Round 1 生成、渲染失败修复、Round 2 改进
- 视频评测：`eval_pipeline` 支持 CV、VLM、音频和融合评分
- HTTP API：支持通过简单接口提交任务、查询状态、下载视频

## 环境要求

- Python 3.10+
- [Manim Community](https://www.manim.community/)
- FFmpeg
- Tesseract OCR

说明：

- Tesseract 不是严格必需，但开启 OCR 评测时建议安装
- TTS 依赖 `edge-tts`，已在 `requirements.txt` 中声明

## 安装

```bash
pip install -r requirements.txt
```

## 配置

程序启动时会自动加载仓库根目录下的 `.env`。

最少需要：

```dotenv
OPENAI_API_KEY=your-api-key
```

常用可选项：

```dotenv
OPENAI_API_KEY=your-api-key
OPENAI_BASE_URL=https://api.example.com/v1
OPENAI_MODEL=gpt-4o

A4L_ANALYSIS_MODEL=gpt-4o
A4L_CODE_MODEL=gpt-4o

MANIM_QUALITY=-qm --fps 60
ROUND1_MANIM_QUALITY=-r 854,480 --fps 30
ROUND2_MANIM_QUALITY=-qm --fps 60
MANIM_TIMEOUT_SEC=1200

A4L_SYNTAX_FIX_MAX_ATTEMPTS=4
A4L_RENDER_FIX_MAX_ATTEMPTS=4

APP_HOST=0.0.0.0
APP_PORT=8000
```

说明：

- `agent_pipeline` 默认模型读取 `OPENAI_MODEL`
- `analysis_llm` 可通过 `A4L_ANALYSIS_*` 单独覆盖
- `code_llm` 可通过 `A4L_CODE_*` 单独覆盖
- `eval_pipeline` 默认模型读取同一个 `OPENAI_MODEL`
- `MANIM_QUALITY` 默认是 `-qm --fps 60`，也就是 `720p60`
- `eval_pipeline` 当前默认 `frame_step=10`，即每 10 帧抽 1 帧做 CV 评测

## 使用

### 1. 生成完整视频

最常用入口：

```bash
python -m agent_pipeline "用动画讲解一下微积分的基本原理"
```

更多示例：

```bash
python -m agent_pipeline "用供需曲线动画解释通货膨胀下供不应求如何导致价格上涨"
python -m agent_pipeline "用动画直观讲解勾股定理，简单一点，视频时间不超过一分钟"
python -m agent_pipeline "用板书感动画一步一步讲解完全平方公式是怎么推导出来的"
```

带图片输入：

```bash
python -m agent_pipeline "用动画讲解这道题" --image "E:\Ai4learning\3.18.16.25\1.png"
```

只给图片也可以：

```bash
python -m agent_pipeline --image path/to/problem.png
```

自定义输出目录：

```bash
python -m agent_pipeline "讲解洛必达法则" --run-dir runs/lhopital_demo
```

### 2. 主题选择说明

当前主题是自动选择的。现有 4 套主题：

- `mist_blue_focus`
  适合供需曲线、函数图像、坐标系、物理轨迹
- `soft_glass_white`
  适合清爽的数学讲解、定理说明、图文并排
- `charcoal_board`
  适合板书感推导、逐步证明、公式演算
- `deep_space_board`
  适合宏观概念、抽象主题、总结页、叙事型内容

### 3. 单独运行评测

如果只想对已有视频做评测：

```bash
python -m eval_pipeline path/to/video.mp4 -o path/to/eval_output
```

CV-only：

```bash
python -m eval_pipeline path/to/video.mp4 --skip-vlm
```

常用参数：

- `-o, --output-dir`：评测输出目录
- `--skip-vlm`：跳过 VLM，只做 CV 与音频评测
- `--skip-audio`：跳过音频分析
- `--frame-step`：设置抽帧步长，当前默认 `10`
- `--api-key` / `--base-url` / `--model`：覆盖评测阶段模型配置
- `--max-vlm-segments`：限制送去 VLM 的片段数量
- `--vlm-all`：把所有片段都送给 VLM

完整参数：

```bash
python -m eval_pipeline --help
```

## 当前生成与评测逻辑

`agent_pipeline` 现在的行为和早期版本不同，核心是：

- Round 1 会生成视频，并立即跑一次 full eval
- Round 2 一定会执行一次，用来根据 Round 1 的反馈提质量
- Round 2 视频直接作为最终交付视频
- 不再额外做第三次 final render

这意味着：

- `round1/eval/` 是主要的评测依据
- `round2/` 是最终交付版本
- 如果 `round2` 失败，系统会回退到 `round1`

## 输出目录

默认输出到：

```text
runs/YYYYMMDD_HHMMSS/
```

常见文件：

- `request.txt`
  本次请求文本
- `request_image.*`
  输入图片副本
- `teaching_plan.json`
  教学计划
- `selected_theme.json`
  本次选中的主题
- `selected_assets.json`
  本次选中的本地图标素材
- `round1/scene.py`
  Round 1 代码
- `round1/render_log.txt`
  Round 1 渲染日志
- `round1/eval/report.json`
  Round 1 评测结果
- `round2/scene.py`
  Round 2 改进代码
- `round2/render_log.txt`
  Round 2 渲染日志
- `summary.json`
  总结信息、最终视频路径、每轮状态

`summary.json` 里最重要的字段通常是：

- `final_video`
- `final_video_with_audio`
- `selected_theme_id`
- `rounds`

## HTTP API

启动服务：

```bash
python app.py
```

默认监听：

```text
http://0.0.0.0:8000
```

可通过环境变量覆盖：

```bash
set APP_HOST=127.0.0.1
set APP_PORT=8080
python app.py
```

### 提交任务

```bash
curl -X POST http://127.0.0.1:8000/jobs ^
  -H "Content-Type: application/json" ^
  -d "{\"request\":\"用动画讲解牛顿迭代法为什么会收敛\"}"
```

### 查询任务状态

```bash
curl http://127.0.0.1:8000/jobs/<job_id>
```

### 下载视频

```bash
curl -L http://127.0.0.1:8000/videos/<job_id> --output lesson.mp4
```

接口会优先返回带音频的视频；如果没有带音频版本，则回退到 `final_video`。

## 项目结构

```text
manim-edu-agent/
├─ agent_pipeline/
│  ├─ __main__.py
│  ├─ main.py
│  ├─ teaching_planner.py
│  ├─ theme_resolver.py
│  ├─ asset_resolver.py
│  ├─ code_gen.py
│  ├─ renderer.py
│  ├─ evaluator.py
│  ├─ llm.py
│  └─ tts.py
├─ eval_pipeline/
│  ├─ __main__.py
│  ├─ run.py
│  ├─ config.py
│  ├─ cv_features.py
│  ├─ audio_features.py
│  ├─ vlm_judge.py
│  ├─ fusion.py
│  └─ utils.py
├─ icon/
├─ runs/
├─ app.py
├─ requirements.txt
└─ README.md
```

## 当前 LLM 路由

当前实现是 OpenAI-only。

- `analysis_llm` 用于 `TeachingPlanner` 和 `AssetResolver`
- `code_llm` 用于 `CodeGen`
- 两个阶段默认都读取 `OPENAI_*`
- 如需单独覆盖，可在 `.env` 中设置：
  `A4L_ANALYSIS_MODEL=...`
  `A4L_ANALYSIS_BASE_URL=...`
  `A4L_ANALYSIS_API_KEY=...`
  `A4L_CODE_MODEL=...`
  `A4L_CODE_BASE_URL=...`
  `A4L_CODE_API_KEY=...`

## 备注

- 当前 README 反映的是现在仓库里的真实代码结构，而不是早期单文件主题版本
- 当前默认字体策略是：中文 `SimSun`，英文 `Times New Roman`
- 当前默认渲染质量是 `720p60`
