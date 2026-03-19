from __future__ import annotations

from pathlib import Path

from ..schema import ThemeBackground, ThemeBehavior, ThemePack


BACKGROUND_DIR = Path(__file__).resolve().parent.parent / "backgrounds"


THEME = ThemePack(
    theme_id="mist_blue_focus",
    display_name="Mist Blue Focus",
    brightness="light",  # 浅色主题：整体是雾面蓝白的清爽观感
    background=ThemeBackground(
        asset_path=BACKGROUND_DIR / "mist_blue_focus.png",  # 浅蓝雾面背景图
        background_type="image",  # 图片背景
        tint_color="#F3FAFF",  # 极浅冰蓝白色：轻微提亮并统一背景色温
        tint_opacity=0.08,  # 很轻的提亮层，保留原始纹理
    ),
    tokens={
        # --- 画布 / 正文 ---
        "canvas_bg": "#E8F3FB",  # 浅雾蓝色：相机兜底背景色
        "text_main": "#0F172A",  # 深藏蓝黑色：一级正文 / 标题主色
        "text_secondary": "#334155",  # 深灰蓝色：二级说明 / 常规解释
        "text_muted": "#5B4B8A",  # 紫灰色：弱化注释 / 次要提示
        "formula_base": "#0F172A",  # 深藏蓝黑色：公式默认主色

        # --- 强调 / 反馈 ---
        "formula_highlight_primary": "#7C3AED",  # 亮紫色：公式主强调 / 主曲线
        "formula_highlight_secondary": "#D97706",  # 金橙色：公式副强调 / 对比曲线
        "accent_primary": "#7C3AED",  # 亮紫色：通用主强调 / 关键项 / 主曲线
        "accent_secondary": "#D97706",  # 金橙色：通用副强调 / 对比项 / 次曲线
        "warning_color": "#DC2626",  # 正红色：警示 / 错误 / 风险
        "success_color": "#F59E0B",  # 亮金黄色：正确 / 正向反馈

        # --- 面板 / 边框 ---
        "panel_fill_color": "#F8FCFF",  # 近白淡蓝色：信息面板底色
        "panel_fill_opacity": 0.78,  # 面板透明度，保留少量背景层次
        "panel_stroke": "#CDB9F7",  # 浅紫色：面板描边
        "border_color": "#6D28D9",  # 深紫色：一般边框 / 结构线
        "grid_or_axis_color": "#1D4ED8",  # 亮深蓝色：坐标轴 / 网格 / 参考线

        # --- 标题徽标 / 字幕 ---
        "section_badge_fill": "#EFF8FF",  # 极浅蓝白色：章节徽标底色
        "section_badge_fill_opacity": 0.94,  # 徽标透明度，接近实底
        "section_badge_stroke": "#D97706",  # 金橙色：章节徽标描边
        "section_badge_text": "#0F172A",  # 深藏蓝黑色：章节徽标文字
        "subtitle_text": "#0F172A",  # 深藏蓝黑色：底部字幕文字
        "subtitle_stroke": "#F8FAFC",  # 冷白色：字幕描边，保证浅底可读
    },
    behavior=ThemeBehavior(
        recommended_opening_style=("visual_first", "question_first"),  # 先给画面，再抛问题
        recommended_scene_density="medium",  # 中等密度，适合图文并行
        preferred_topics=("physics", "function", "coordinate"),  # 适合物理、函数、坐标系
    ),
    notes="Light mist-blue stage with clean focus for graphs and mechanism-heavy lessons.",
)
