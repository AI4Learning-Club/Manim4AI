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

## 教学脚本约定

解题类视频会优先做“题面导入”：先用 1 到 2 句学生能听懂的话精炼复述题目，再在紧凑题面卡上区分已知条件、目标问题、关键变量或图形关系，并用圆圈、描边框、下划线、箭头、颜色高亮或标注卡依次圈画题面重点。完成“口头精炼读题 -> 圈画题面 -> hook/roadmap”这一步后，流水线才进入正式推导和解答动画。

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

## Configuration Note

`plugins/manim/.env` has been removed.

Use `AI4Learning-Backend/config/app_settings/settings.toml` as the single runtime
configuration source for this plugin, especially:

- `[manim.service]`
- `[manim.paths]`
- `[manim.render]`
- `[manim.tts]` / `[manim.tts.*]`
- `[manim.repair]`
- `[manim.evaluation]`
- `[manim.repair_model]`
- `[manim.llm.analysis]`
- `[manim.llm.code]`
- `[manim.llm.director]`
- `[manim.llm.eval]`

其中各个 `[manim.llm.*]` 段额外支持 `reasoning_effort`（思考强度），会直接映射到 OpenAI Responses API 的 `reasoning.effort`。默认值为空字符串，表示不向 Responses API 发送 `reasoning` 参数；需要提高思考强度时再显式配置 `low`、`medium`、`high` 或 `xhigh`。

如果环境里还没有 Manim CE 和 FFmpeg，需要另外安装。

## 并发与渲染

| 配置项 | 说明 |
|--------|------|
| `manim.render.max_concurrent_jobs` | MCP 同时执行的教学视频生成任务上限（进程内全局信号量） |
| `manim.render.job_slot_wait_seconds` | 取槽策略：省略/`null` = 无限等待；`0` = 无空位立即失败；正数 = 最长等待秒数 |
| `manim.render.segment_render_workers` | Scene Pack 并行 segment 上限；默认建议有限上限（如 `2`），`0` = 与 manifest 长度一致 |
| `manim.tts.process_threads` | 双进程流水线中，TTS 子进程内线程池大小 |
| `manim.tts.edge.max_concurrency` | 同一进程内 edge-tts 并发上限（信号量，缓解 WebSocket 不稳定） |
| `manim.tts.provider` | `edge` 或 `doubao`；默认 `edge` |
| `manim.tts.doubao.app_id` | 单账号豆包 TTS App ID；没有配置账号池时作为单账号入口 |
| `manim.tts.doubao.access_token` | 单账号豆包 TTS Access Token；没有配置账号池时作为单账号入口 |
| `manim.tts.doubao.accounts` | 可选多账号池：使用 `[[manim.tts.doubao.accounts]]` 增加账号，字段为 `name` / `app_id` / `access_token` / `resource_id` / `max_concurrency` / `enabled` |
| `manim.tts.doubao.resource_id` | 豆包资源 ID，默认 `seed-tts-2.0`；账号未配置 `resource_id` 时使用该值 |
| `manim.tts.doubao.speaker_zh` | 中文 speaker ID |
| `manim.tts.doubao.speaker_en` | 英文 speaker ID |
| `manim.tts.doubao.audio_format` | 输出音频格式，当前默认 `mp3` |
| `manim.tts.doubao.sample_rate` | 豆包 TTS 采样率，默认 `24000` |
| `manim.tts.doubao.speech_rate` | 豆包基础语速，范围建议 `-50..100` |
| `manim.tts.doubao.max_concurrency` | 豆包 TTS 单账号默认并发上限；账号未配置 `max_concurrency` 时使用该值，推荐按单账号 `10` 配置 |
| `manim.tts.doubao.pool_acquire_timeout_seconds` | 可选账号池取槽超时；省略时所有账号槽位占满会排队等待 |
| `manim.tts.doubao.pool_backend` | TTS 池租约后端：`file`（默认，同机多 worker）或 `redis`（多机共享池） |
| `manim.tts.doubao.pool_state_path` | 跨 worker 租约状态文件；空值默认 `data/tts/doubao_pool_state.json` |
| `manim.tts.doubao.pool_redis_key_prefix` | Redis 模式下的共享 key 前缀；空值默认 `${redis.key_prefix}:doubao_tts_pool` |
| `manim.tts.doubao.pool_lease_ttl_seconds` | 租约过期时间；worker 崩溃后超过该时间会自动释放槽位 |
| `manim.tts.doubao.pool_poll_interval_seconds` | 所有账号满载时等待下一次重试取槽的间隔 |
| `manim.render.dual_process_pipeline_enabled` | `true` 时 TTS 与 segment 渲染分处两个子进程并各自用线程池；`false` 回退单进程预生成 + 线程池 |
| `manim.tts.merge_narration_enabled` | `true` 时在运行目录下额外生成 `tts_merged/narration_merged.mp3`（ffmpeg 拼接分片，不替代 `speak` 缓存） |

豆包账号池会按租约轮询账号，并对每个账号分别施加并发上限；总容量等于所有启用账号的 `max_concurrency` 之和。`pool_backend=file` 时，活动租约写入共享状态文件，并用 `fcntl` 文件锁保护租号/释放，因此同一机器上的多个 uvicorn worker 会看到同一个池子，而不是各自复制一份 10 并发。`pool_backend=redis` 时，租约写入 Redis，可用于多台 Manim 实例共享同一个 TTS 池。Manim 旁白与英语听力的豆包 TTS 都走同一个池子，因此多个账号可以共同承接高并发请求。

## 工具化局部修复（可选）

在 `settings.toml` 的 `[manim.repair]` 段可开启 **patch-first** 修复：模型通过 `read_file` / `search_file` / `apply_patch` 在每次运行的目录内编辑 `scene_pack.py`，失败时再回退到原有的**整文件** `fix` / `fix_from_code_eval`。

| 配置项 | 说明 |
|--------|------|
| `manim.repair.tool_fix_enabled` | `true` 时启用（默认 `false`） |
| `manim.repair.tool_fix_max_iterations` | LLM 与工具往返的最大轮数 |
| `manim.repair.tool_fix_max_patches` | 单次修复流程中允许的成功 `apply_patch` 次数 |
| `manim.repair.tool_fix_max_patch_bytes` | 单次补丁片段的字节上限 |
| `manim.repair.tool_fix_run_dir_only` | 仅允许访问运行目录（应始终为 `true`） |

安全边界：工具只能读写**当前 `run_dir` 下**的路径（如 `round1/scene_pack.py`），无法改仓库源码。

提示词策略：启用后，修复阶段使用 **tool-first** 系统说明（见 `code_gen.py` 中 `_build_system_tool_repair`），要求先读再搜再补丁，必要时 `finish_repair(fallback_required=true)` 触发整文件回退。

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
- `A4L_USE_LOCAL_ICONS` 变量仍保留，但当前主流水线里本地图标选择默认是临时禁用状态

## 快速开始

根据文本请求生成视频：

```bash
python -m plugins.manim.agent_pipeline "Explain this problem step by step."
```

生成中文视频：

```bash
python -m plugins.manim.agent_pipeline --language zh
```

使用文本加图片生成：

```bash
python -m plugins.manim.agent_pipeline "为我讲解一下这道题" --image "E:\Ai4learning\3.18.16.25\屏幕截图 2026-03-26 212747.png" --language zh
```

仅使用图片生成：

```bash
python -m plugins.manim.agent_pipeline --image path/to/problem.png --language zh
```

指定输出目录：

```bash
python -m plugins.manim.agent_pipeline "Explain L'Hopital's rule" --run-dir runs/lhopital_demo --language en
```

本地 CLI 调试模式（实时输出 planner/codegen 增量文本、section 成片和增量预览）：

```bash
uv run python -m plugins.manim.agent_pipeline "讲解二次函数顶点式" --language zh --debug
```

CLI 参数：

- `request`：教学请求文本；如果提供了 `--image`，则可选
- `--image`：输入图片路径
- `--run-dir`：自定义输出目录
- `--language`：输出语言，`en` 或 `zh`
- `--render-backend`：交付后端，`manim` 或 `hybrid`
- `--debug`：仅限本地 CLI 调试；会把 planner/codegen 的 `analysis/code` 增量文本、LLM 请求开始/重试/完成、`response` 生命周期状态，以及“不暴露推理正文”的 reasoning 进行中状态、section 成片路径、预览视频更新写到 stdout，并在 macOS 上尝试对每个新 `preview_vNN.mp4` 调用 `open`
- planner 侧的可读性依赖当前提示词契约：它会尽量以短字段、JSON-first 的单份教学计划持续流出，因此 stdout 中更容易看到稳定的首段分析增量，而不是晚到或反复重启的长草稿

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
- `round1/scene_pack.py`（启用 `manim.repair.tool_fix_enabled` 且进入工具修复流程时写入，供局部补丁编辑）
- `round1/render_log.txt`
- `summary.json`

`summary.json` 中常用字段：

- `final_video`
- `final_video_with_audio`
- `selected_theme_id`
- `rounds`
- `final_passed`


启动 API 服务：

```bash
```

默认地址：

```text
```

接口：


请求体示例：

```json
{
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
