from __future__ import annotations

from pathlib import Path

from ..schema import ThemeBackground, ThemeBehavior, ThemePack

BACKGROUND_DIR = Path(__file__).resolve().parent.parent / "backgrounds"


THEME = ThemePack(
    theme_id="slate_mist",
    display_name="Slate Mist",
    brightness="dark",  # 深色主题：灰蓝雾面底图配高对比浅色前景
    background=ThemeBackground(
        asset_path=BACKGROUND_DIR / "slate_mist.png",  # 灰蓝磨砂纹理背景图
        background_type="image",  # 图片背景
        tint_color="#243242",  # 深蓝灰色轻遮罩：统一色温但不明显改色
        tint_opacity=0.12,  # 低强度遮罩，保留背景原始质感
    ),
    tokens={
        # --- 画布 / 正文 ---
        "canvas_bg": "#5F666A",  # 深灰蓝色：相机兜底背景色，接近原图平均色
        "text_main": "#F7FAFC",  # 近白色：一级正文 / 标题主色
        "text_secondary": "#E2E8F0",  # 浅灰蓝色：二级说明 / 常规解释
        "text_muted": "#CBD5E1",  # 雾灰蓝色：弱化注释 / 次要提示
        "formula_base": "#F7FAFC",  # 近白色：公式默认主色

        # --- 强调 / 反馈 ---
        "formula_highlight_primary": "#7DD3FC",  # 亮天青色：公式主强调 / 主结构
        "formula_highlight_secondary": "#FBBF24",  # 琥珀黄色：公式副强调 / 对照项
        "accent_primary": "#7DD3FC",  # 亮天青色：通用主强调 / 箭头 / 关键项
        "accent_secondary": "#FBBF24",  # 琥珀黄色：通用副强调 / 结论 / 转折
        "warning_color": "#F87171",  # 珊瑚红色：警示 / 错误 / 风险
        "success_color": "#34D399",  # 薄荷绿色：正确 / 正向反馈

        # --- 面板 / 边框 ---
        "panel_fill_color": "#243242",  # 深蓝灰色：信息面板底色
        "panel_fill_opacity": 0.82,  # 较高透明度，确保正文和公式辨识度
        "panel_stroke": "#7DD3FC",  # 亮天青色：面板描边
        "border_color": "#CBD5E1",  # 浅灰蓝色：一般边框 / 结构线
        "grid_or_axis_color": "#D6E4F0",  # 亮灰蓝色：坐标轴 / 网格 / 参考线

        # --- 标题徽标 / 字幕 ---
        "section_badge_fill": "#243242",  # 深蓝灰色：章节徽标底色
        "section_badge_fill_opacity": 0.95,  # 徽标透明度，接近实底
        "section_badge_stroke": "#FBBF24",  # 琥珀黄色：章节徽标描边
        "section_badge_text": "#F8FAFC",  # 冷白色：章节徽标文字
        "subtitle_text": "#F7FAFC",  # 近白色：底部字幕文字
    },
    behavior=ThemeBehavior(
        recommended_opening_style=("visual_first", "statement_first"),  # 先给氛围，再给判断或概念
        recommended_scene_density="medium_low",  # 偏低密度，适合概念页和稳态讲解
        preferred_topics=("concept", "summary", "general_math"),  # 适合概念、总结、通用数学
    ),
    notes="Cool gray-blue texture theme with high-contrast white text, cyan structure, and amber emphasis.",
)
