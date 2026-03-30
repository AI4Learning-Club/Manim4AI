# Manim Edu Agent

`Manim Edu Agent` 是一个用于生成教学动画视频的自动化流程。项目接收自然语言题目描述，或“题目描述 + 图片”，自动完成教学规划、主题选择、本地图标选择、Manim 代码生成、渲染、评测与二轮改进，最终产出带配音的教学视频。

适用场景包括：

- 数学、物理、经济学等概念讲解视频
- 题目解析类短视频
- Manim 场景自动生成与自动修复
- 教学视频质量评测与回归对比

## 核心能力

- 自动教学规划：把用户需求整理成结构化 `teaching_plan.json`
- 双轮生成流程：Round 1 先生成可运行版本，Round 2 基于评测结果改进
- 自动主题系统：根据教学内容自动选择视觉主题
- 本地图标选择：从 `icon/` 中挑选与内容相关的素材
- 自动配音：渲染阶段可生成带音频的视频
- 自动评测：对生成结果做 CV、音频、VLM、融合评分
- HTTP 服务：支持异步提交任务并轮询状态

## 工作流程

完整流程由 `agent_pipeline` 驱动，默认步骤如下：

1. `TeachingPlanner` 根据请求生成教学计划
2. `ThemeResolver` 根据请求和教学计划选择主题
3. `AssetResolver` 从本地图标库中选择可用素材
4. `CodeGen` 生成 Round 1 Manim 场景代码
5. Round 1 渲染视频，并立即执行一次完整评测
6. `CodeGen` 根据评测结果和关键帧生成 Round 2 改进版代码
7. Round 2 再次渲染，作为最终交付结果
8. 如果 Round 2 失败，系统自动回退到 Round 1 视频

默认渲染策略：

- Round 1：`854x480 @ 30fps`
- Round 2：`720p @ 60fps`
- 可通过环境变量覆盖

## 项目结构

```text
agent_pipeline/          主生成链路：规划、选主题、选素材、生成、渲染、评测回环
eval_pipeline/           独立视频评测链路
colortest/               Manim 基类、主题系统、背景图、字幕与布局能力
icon/                    本地图标素材库
runs/                    每次运行的输出目录
app.py                   HTTP API 服务入口
requirements.txt         Python 依赖
README.md                项目说明
```

## 环境要求

- Python 3.10 或更高版本
- [Manim Community Edition](https://www.manim.community/)
- FFmpeg
- 可选：Tesseract OCR

说明：

- `FFmpeg` 是视频渲染与音频处理的基础依赖
- `Tesseract` 不是强制依赖，但开启 OCR 相关评测时建议安装
- `requirements.txt` 已包含 `manim`、`openai`、`edge-tts`、`opencv-python`、`numpy` 等 Python 依赖

## 安装

```bash
pip install -r requirements.txt
```

如果需要在虚拟环境中运行，可先创建并激活虚拟环境，再安装依赖。

## 配置

程序会自动加载仓库根目录下的 `.env` 文件。

最少需要配置：

```dotenv
OPENAI_API_KEY=your-api-key
```

常用配置项示例：

```dotenv
OPENAI_API_KEY=your-api-key
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o

A4L_ANALYSIS_API_KEY=
A4L_ANALYSIS_BASE_URL=
A4L_ANALYSIS_MODEL=

A4L_CODE_API_KEY=
A4L_CODE_BASE_URL=
A4L_CODE_MODEL=

A4L_VIDEO_LANGUAGE=en
A4L_USE_LOCAL_ICONS=1

MANIM_QUALITY=-qm --fps 60
ROUND1_MANIM_QUALITY=-r 854,480 --fps 30
ROUND2_MANIM_QUALITY=-qm --fps 60
MANIM_TIMEOUT_SEC=1200

A4L_SYNTAX_FIX_MAX_ATTEMPTS=4
A4L_RENDER_FIX_MAX_ATTEMPTS=4
A4L_LANGUAGE_FIX_MAX_ATTEMPTS=2

APP_HOST=0.0.0.0
APP_PORT=8000
```

配置说明：

- `OPENAI_*`：主生成链路默认读取的模型配置
- `A4L_ANALYSIS_*`：覆盖教学规划、素材选择等分析阶段模型
- `A4L_CODE_*`：覆盖代码生成与修复阶段模型
- `A4L_VIDEO_LANGUAGE`：默认视频语言，支持 `en` 与 `zh`
- `A4L_USE_LOCAL_ICONS`：是否启用 `icon/` 素材选择
- `MANIM_QUALITY`：Round 2 默认质量
- `ROUND1_MANIM_QUALITY`：Round 1 快速预渲染质量
- `ROUND2_MANIM_QUALITY`：Round 2 最终渲染质量

## 快速开始

### 1. 生成完整教学视频

最常用入口：

```bash
python -m agent_pipeline "
```

中文示例：

```bash
python -m agent_pipeline '用动画讲解这道题：已知椭圆$C:\dfrac{x^2}{a^2}+\dfrac{y^2}{b^2}=1(a>b>0)$的离心率为$\dfrac{2\sqrt{2}}{3}$，下顶点为$A$，右顶点为$B$，$|AB|=\sqrt{10}$.

\begin{enumerate}
    \item 求$C$的方程；
    \item 已知动点$P$不在$y$轴上，点$R$在射线$AP$上，且满足$|AP|\cdot|AR|=3$.
    \begin{enumerate}
        \item 设$P(m,n)$，求$R$的坐标（用$m,n$表示）；
        \item 设$O$为坐标原点，$Q$是$C$上的动点，直线$OR$的斜率是直线$OP$的斜率的$3$倍，求$|PQ|$的最大值.
    \end{enumerate}
\end{enumerate}' --language zh

```

带图片输入：

```bash
python -m agent_pipeline "Explain this problem step by step." --image path/to/problem.png --language en
```

只提供图片也可以：

```bash
python -m agent_pipeline --image ""E:\Ai4learning\3.18.16.25\屏幕截图 2026-03-29 152245.png"" --language zh
```

指定输出目录：

```bash
python -m agent_pipeline "用动画讲解洛必达法则" --run-dir runs/lhopital_demo --language zh
```

命令行参数：

- `request`：教学请求文本，可省略；如果省略且提供了图片，会使用默认图片讲解提示
- `--image`：输入图片路径
- `--run-dir`：自定义输出目录
- `--language`：输出语言，支持 `en` 或 `zh`

## 输出目录说明

默认输出路径：

```text
runs/YYYYMMDD_HHMMSS/
```

常见文件：

- `request.txt`：原始请求文本
- `request_image.*`：输入图片副本
- `llm_routing.json`：本次运行使用的模型配置摘要
- `teaching_plan.json`：教学规划结果
- `selected_theme.json`：选中的主题信息
- `selected_assets.json`：选中的本地图标素材
- `round1/scene.py`：Round 1 场景代码
- `round1/render_log.txt`：Round 1 渲染日志
- `round1/eval/report.json`：Round 1 评测结果
- `round2/scene.py`：Round 2 场景代码
- `round2/render_log.txt`：Round 2 渲染日志
- `summary.json`：汇总信息与最终视频路径

`summary.json` 中最重要的字段通常是：

- `final_video`
- `final_video_with_audio`
- `selected_theme_id`
- `rounds`

## 主题系统

项目当前内置 5 套主题，位于 `colortest/ai4learning_theme/themes/`。

### `mist_blue_focus`

适合：

- 函数图像
- 坐标系讲解
- 运动轨迹
- 物理量变化过程

特点：

- 深色雾蓝背景
- 冷白文字
- 适合结构清晰的图像化讲解

### `charcoal_board`

适合：

- 推导
- 证明
- 一步一步的板书式讲解
- 复盘页

特点：

- 低噪点深色背景
- 更稳的板书感
- 适合逐步展开的公式和结论

### `soft_glass_white`

适合：

- 通用数学讲解
- 代数
- 微积分
- 公式居多的内容

特点：

- 浅色干净背景
- 默认泛用性最强
- 适合信息密度中等的课堂页面

### `deep_space_board`

适合：

- 概念解释
- 总结页
- 抽象主题
- 宏观型内容

特点：

- 深蓝背景
- 氛围更强
- 适合概念型和总结型页面

### `slate_mist`

适合：

- 复习
- 对比
- 结构化梳理
- 总览型页面

特点：

- 灰蓝纹理背景
- 高对比白字
- 青色主强调、琥珀色副强调

主题选择目前由 `agent_pipeline/theme_resolver.py` 自动完成，依据请求文本与教学计划中的信号进行匹配。当前命令行接口未暴露手动指定主题参数。

## 本地图标素材

如果启用了 `A4L_USE_LOCAL_ICONS=1`，系统会从 `icon/` 目录中选择少量与教学内容相关的素材，并写入 `selected_assets.json`。

约束如下：

- 仅从本地 `icon/` 中选取
- 仅选择对教学确实有帮助的素材
- 不为抽象公式或普通几何元素强行配图
- 生成代码时只允许使用已选中的文件

## 独立视频评测

如果只需要对已有视频做评测，可直接运行 `eval_pipeline`。

基础用法：

```bash
python -m eval_pipeline path/to/video.mp4 -o eval_output
```

仅运行 CV，不调用 VLM：

```bash
python -m eval_pipeline path/to/video.mp4 --skip-vlm
```

跳过音频分析：

```bash
python -m eval_pipeline path/to/video.mp4 --skip-audio
```

常用参数：

- `-o, --output-dir`：评测输出目录
- `--skip-vlm`：仅保留 CV 与音频分析
- `--skip-audio`：跳过音频质量与音画对齐分析
- `--frame-step`：每隔多少帧取样一次，默认 `10`
- `--api-key` / `--base-url` / `--model`：覆盖评测阶段模型配置
- `--max-vlm-segments`：限制送入 VLM 的片段数
- `--vlm-all`：把所有候选片段都送给 VLM
- `--topic`：为 Task Correctness 评测提供主题文本

典型输出：

- `frame_stats.csv`
- `segments_all.csv`
- `vlm_payload/`
- `tc_keyframes/`
- `report.json`
- `batch_summary.json`

说明：

- 主生成链路中的评测由 `agent_pipeline` 内部触发
- 独立 `eval_pipeline` 也会读取 `.env`
- 独立评测 CLI 支持单独覆盖 `api-key`、`base-url` 与 `model`

## HTTP API

启动服务：

```bash
python app.py
```

默认监听地址：

```text
http://0.0.0.0:8000
```

通过环境变量覆盖监听地址：

```bash
set APP_HOST=127.0.0.1
set APP_PORT=8080
python app.py
```

### 提交任务

```bash
curl -X POST http://127.0.0.1:8000/jobs ^
  -H "Content-Type: application/json" ^
  -d "{\"request\":\"用动画讲清楚牛顿迭代法为什么会收敛\",\"language\":\"zh\"}"
```

### 查询任务状态

```bash
curl http://127.0.0.1:8000/jobs/<job_id>
```

### 下载视频

```bash
curl -L http://127.0.0.1:8000/videos/<job_id> --output lesson.mp4
```

当前 HTTP API 提供的主要接口：

- `GET /`：查看服务信息与接口说明
- `POST /jobs`：提交文本任务
- `GET /jobs/{job_id}`：查询任务状态
- `GET /videos/{job_id}`：下载最终视频

当前 API 的输入体仅支持：

- `request`
- `language`

也就是说，图片输入目前通过命令行支持，但未接入 HTTP 上传接口。

## LLM 路由说明

主生成链路使用 OpenAI 兼容接口，分为两个逻辑阶段：

- `analysis_llm`：教学规划、素材选择等分析任务
- `code_llm`：Manim 代码生成、修复与改进

这两个阶段默认读取 `OPENAI_*`，也可以分别通过 `A4L_ANALYSIS_*` 与 `A4L_CODE_*` 覆盖。

评测链路中的 VLM 调用也支持通过环境变量或命令行参数覆盖。

## 已知行为与设计约束

- 主题由解析器自动选择，不是手动指定
- 本地图标只会从 `icon/` 中挑选，不会访问外部图片站点
- 字幕区域固定在底部保留带中
- 当前字幕默认不使用描边，以避免小字号字幕发糊
- Round 2 是默认最终交付版本；只有 Round 2 失败时才回退到 Round 1

## 适合二次开发的方向

- 为 API 增加图片上传能力
- 暴露手动主题选择参数
- 扩展更多教学主题与背景包
- 增加更多评测维度和回归测试集
- 为前端或任务队列系统提供更完整的服务封装
