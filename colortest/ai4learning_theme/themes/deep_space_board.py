from __future__ import annotations

from pathlib import Path

from ..schema import ThemeBackground, ThemeBehavior, ThemePack


BACKGROUND_DIR = Path(__file__).resolve().parent.parent / "backgrounds"


THEME = ThemePack(
    theme_id="deep_space_board",
    display_name="Deep Space Board",
    brightness="dark",  # 深色主题：整体是深蓝宇宙感的高聚焦画面
    background=ThemeBackground(
        asset_path=BACKGROUND_DIR / "deep_space_board.png",  # 深蓝星空背景图
        background_type="image",  # 图片背景
        tint_color="#081634",  # 深夜蓝色：压住星点噪声，统一整体色调
        tint_opacity=0.46,  # 明显压暗层，让内容区更稳
    ),
    tokens={
        # --- 画布 / 正文 ---
        "canvas_bg": "#0B1735",  # 深夜蓝色：相机兜底背景色
        "text_main": "#EFF6FF",  # 冷白淡蓝色：一级正文 / 标题主色
        "text_secondary": "#E2ECFF",  # 亮蓝白色：二级说明 / 常规解释
        "text_muted": "#C4B5FD",  # 浅紫色：弱化注释 / 次要提示
        "formula_base": "#EFF6FF",  # 冷白淡蓝色：公式默认主色

        # --- 强调 / 反馈 ---
        "formula_highlight_primary": "#FBBF24",  # 亮金黄色：公式主强调 / 主图形色
        "formula_highlight_secondary": "#67E8F9",  # 亮天青色：公式副强调 / 对比图形色
        "accent_primary": "#FBBF24",  # 亮金黄色：通用主强调 / 关键项
        "accent_secondary": "#67E8F9",  # 亮天青色：通用副强调 / 对比项
        "warning_color": "#F87171",  # 珊瑚红色：警示 / 错误 / 风险
        "success_color": "#34D399",  # 薄荷绿色：正确 / 正向反馈

        # --- 面板 / 边框 ---
        "panel_fill_color": "#102045",  # 深靛蓝色：信息面板底色
        "panel_fill_opacity": 0.72,  # 面板透明度，压住背景同时保留层次
        "panel_stroke": "#818CF8",  # 亮紫蓝色：面板描边
        "border_color": "#C4B5FD",  # 浅紫色：一般边框 / 结构线
        "grid_or_axis_color": "#93C5FD",  # 亮蓝色：坐标轴 / 网格 / 参考线

        # --- 标题徽标 / 字幕 ---
        "section_badge_fill": "#13264F",  # 深蓝色：章节徽标底色
        "section_badge_fill_opacity": 0.94,  # 徽标透明度，接近实底
        "section_badge_stroke": "#FBBF24",  # 亮金黄色：章节徽标描边
        "section_badge_text": "#EFF6FF",  # 冷白淡蓝色：章节徽标文字
        "subtitle_text": "#EFF6FF",  # 冷白淡蓝色：底部字幕文字
    },
    behavior=ThemeBehavior(
        recommended_opening_style=("visual_first", "statement_first"),  # 先给视觉氛围，再给论断
        recommended_scene_density="low_medium",  # 偏低到中密度，适合概念讲述
        preferred_topics=("concept", "macro_view", "ai", "physics"),  # 适合概念、宏观、AI、物理
    ),
    notes="Deep blue concept theme for abstract lessons, summaries, and high-focus pages.",
)
