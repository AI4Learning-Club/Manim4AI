from __future__ import annotations

from pathlib import Path

from ..schema import ThemeBackground, ThemeBehavior, ThemePack


BACKGROUND_DIR = Path(__file__).resolve().parent.parent / "backgrounds"


THEME = ThemePack(
    theme_id="soft_glass_white",
    display_name="Soft Glass White",
    brightness="light",  # 浅色主题：整体是干净的白底玻璃感
    background=ThemeBackground(
        asset_path=BACKGROUND_DIR / "soft_glass_white.png",  # 浅白底图
        background_type="image",  # 图片背景
        tint_color="#FFFFFF",  # 纯白色：保持背景极简干净
        tint_opacity=0.0,  # 不额外叠加遮罩
    ),
    tokens={
        # --- 画布 / 正文 ---
        "canvas_bg": "#F6F8FE",  # 冷白浅灰蓝色：相机兜底背景色
        "text_main": "#111827",  # 深蓝黑色：一级正文 / 标题主色
        "text_secondary": "#334155",  # 深灰蓝色：二级说明 / 常规解释
        "text_muted": "#5B5F97",  # 蓝紫色：弱化注释 / 次要提示
        "formula_base": "#111827",  # 深蓝黑色：公式默认主色

        # --- 强调 / 反馈 ---
        "formula_highlight_primary": "#2563EB",  # 亮钴蓝色：公式主强调 / 主图形色
        "formula_highlight_secondary": "#D97706",  # 金橙色：公式副强调 / 对比图形色
        "accent_primary": "#2563EB",  # 亮钴蓝色：通用主强调 / 关键项
        "accent_secondary": "#D97706",  # 金橙色：通用副强调 / 对比项
        "warning_color": "#DC2626",  # 正红色：警示 / 错误 / 风险
        "success_color": "#059669",  # 深青绿色：正确 / 正向反馈

        # --- 面板 / 边框 ---
        "panel_fill_color": "#FFFFFF",  # 纯白色：信息面板底色
        "panel_fill_opacity": 0.82,  # 面板透明度，保留轻玻璃感
        "panel_stroke": "#C7CCF4",  # 浅蓝紫色：面板描边
        "border_color": "#2563EB",  # 亮钴蓝色：一般边框 / 结构线
        "grid_or_axis_color": "#1E40AF",  # 深钴蓝色：坐标轴 / 网格 / 参考线

        # --- 标题徽标 / 字幕 ---
        "section_badge_fill": "#FFFFFF",  # 纯白色：章节徽标底色
        "section_badge_fill_opacity": 0.96,  # 徽标透明度，接近实底
        "section_badge_stroke": "#D97706",  # 金橙色：章节徽标描边
        "section_badge_text": "#111827",  # 深蓝黑色：章节徽标文字
        "subtitle_text": "#111827",  # 深蓝黑色：底部字幕文字
        "subtitle_stroke": "#FFFFFF",  # 纯白色：字幕描边，保证浅底边缘干净
    },
    behavior=ThemeBehavior(
        recommended_opening_style=("question_first", "visual_first"),  # 先抛问题，再补图示
        recommended_scene_density="medium",  # 中等密度，适合通用数学讲解
        preferred_topics=("algebra", "calculus", "general_math"),  # 适合代数、微积分、通用数学
    ),
    notes="Clean light theme for default lessons and formula-centric teaching.",
)
