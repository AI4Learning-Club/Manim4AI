# Manim 教育视频多智能体流水线

根据学生需求（文字或图片）自动生成、渲染、评测并迭代改进 Manim 教学视频的流水线。支持一轮「生成 → 渲染 → 评测 → 改进」循环，并可选 TTS 旁白与音视频合成。

## 功能概览

- **代码生成**：使用大模型根据题目/需求生成 Manim 场景代码（支持中文+公式、图文排版、节奏与旁白）。
- **渲染**：调用 Manim 渲染视频；若失败则自动把错误交给模型修复并重试一次。
- **评测**：基于 CV（重叠、布局、运动等）与 VLM 语义判断对视频打分，并产出结构化报告。
- **改进**：若首轮未通过，根据评测报告与关键帧截图修订代码，再渲染、评测一次；取两轮中得分更高的一轮作为最终输出。
- **TTS**：可选预生成旁白音频（edge-tts），在场景中通过 `self.speak(text)` 与动画同步播放。

## 环境要求

- Python 3.10+
- [Manim Community](https://www.manim.community/)（渲染）
- FFmpeg（音视频合成，可选）
- Tesseract（OCR，评测可选）

## 安装

```bash
cd manim-edu-agent
pip install -r requirements.txt
```

安装 Manim 与系统依赖请参考 [Manim 官方文档](https://docs.manim.community/en/stable/installation.html)。

## 配置

- **API 密钥**：设置环境变量 `OPENAI_API_KEY`（或兼容的 API Key）。流水线中的代码生成与评测 VLM 均使用该密钥。
- **Base URL**（可选）：若使用自建或第三方 OpenAI 兼容接口，可设置 `OPENAI_BASE_URL`。
- **模型**（可选）：默认 `gpt-4o`，可通过 `OPENAI_MODEL` 覆盖。

示例（PowerShell）：

```powershell
$env:OPENAI_API_KEY = "your-api-key"
$env:OPENAI_BASE_URL = "https://api.example.com/v1"   # 可选
```

## 使用

从项目根目录运行：

```bash
python -m agent_pipeline "你的题目或需求描述"
```

例如：

```bash
python -m agent_pipeline "用供需曲线动画解释通货膨胀下供不应求如何导致价格上涨"
```

带图片输入时，将图片路径放在第二个参数（需在代码或脚本中传入，或修改 `main.py` 中的参数解析）。

输出目录默认为 `runs/YYYYMMDD_HHMMSS/`，包含：

- `round1/`、`round2/`：每轮生成的代码、渲染视频、评测结果与关键帧。
- `summary.json`：各轮得分与最终选用轮次。
- 最终视频路径会在控制台打印（若启用 TTS，会同时生成带旁白的版本）。

## 仅运行评测管道

若只想对已有视频做 CV+VLM 评测：

```bash
python -m eval_pipeline path/to/video.mp4 --out-dir path/to/eval_output
```

需设置 `OPENAI_API_KEY`。更多参数见 `python -m eval_pipeline --help`。

## 项目结构

```
manim-edu-agent/
├── agent_pipeline/       # 多智能体流水线
│   ├── code_gen.py       # 代码生成 / 修复 / 改进
│   ├── renderer.py       # Manim 渲染与 TTS 预生成
│   ├── evaluator.py      # 调用 eval_pipeline 做视频评测
│   ├── tts.py            # edge-tts 与 ffmpeg 音视频合成
│   └── main.py           # 主入口与循环逻辑
├── eval_pipeline/        # 视频评测（CV + VLM + Fusion）
│   ├── config.py
│   ├── cv_features.py
│   ├── vlm_judge.py
│   ├── fusion.py
│   └── run.py
├── requirements.txt
├── README.md
└── .gitignore
```

## 许可证

按需自定。
