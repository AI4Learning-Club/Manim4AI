from __future__ import annotations

from pathlib import Path

from ..schema import ThemeBackground, ThemeBehavior, ThemePack


BACKGROUND_DIR = Path(__file__).resolve().parent.parent / "backgrounds"


THEME = ThemePack(
    theme_id="charcoal_board",
    display_name="Charcoal Board",
    brightness="dark",  # 深色主题：整体是黑板感的深灰底
    background=ThemeBackground(
        asset_path=BACKGROUND_DIR / "charcoal_board.png",  # 深灰黑板纹理底图
        background_type="image",  # 图片背景
        tint_color="#020617",  # 近黑藏蓝色：进一步压暗背景、统一噪点
        tint_opacity=0.16,  # 轻度压暗，保留板书纹理
    ),
    tokens={
        # --- 画布 / 正文 ---
        "canvas_bg": "#111827",  # 深蓝灰色：相机兜底背景色
        "text_main": "#F8FAFC",  # 冷白色：一级正文 / 标题主色
        "text_secondary": "#E8EEF5",  # 亮灰白色：二级说明 / 常规解释
        "text_muted": "#B8B4FF",  # 浅紫色：弱化注释 / 次要提示
        "formula_base": "#F8FAFC",  # 冷白色：公式默认主色

        # --- 强调 / 反馈 ---
        "formula_highlight_primary": "#67E8F9",  # 高亮天青色：公式主强调
        "formula_highlight_secondary": "#FDE047",  # 亮柠黄色：公式副强调
        "accent_primary": "#67E8F9",  # 高亮天青色：通用主强调 / 箭头 / 关键项
        "accent_secondary": "#FDE047",  # 亮柠黄色：通用副强调 / 对比 / 结论点
        "warning_color": "#F87171",  # 珊瑚红色：警示 / 错误 / 风险
        "success_color": "#34D399",  # 薄荷绿色：正确 / 通过 / 正向反馈

        # --- 面板 / 边框 ---
        "panel_fill_color": "#0F172A",  # 深藏蓝色：信息面板底色
        "panel_fill_opacity": 0.64,  # 面板透明度，保留黑板纹理
        "panel_stroke": "#7DD3FC",  # 亮天青色：面板描边
        "border_color": "#C4B5FD",  # 浅紫色：一般边框 / 结构线
        "grid_or_axis_color": "#93C5FD",  # 亮蓝色：坐标轴 / 网格 / 参考线

        # --- 标题徽标 / 字幕 ---
        "section_badge_fill": "#1E293B",  # 深蓝灰色：章节徽标底色
        "section_badge_fill_opacity": 0.94,  # 徽标透明度，接近实底
        "section_badge_stroke": "#FBBF24",  # 亮金黄色：章节徽标描边
        "section_badge_text": "#F8FAFC",  # 冷白色：章节徽标文字
        "subtitle_text": "#F8FAFC",  # 冷白色：底部字幕文字
        "subtitle_stroke": "#020617",  # 近黑藏蓝色：字幕描边，压住背景噪点
    },
    behavior=ThemeBehavior(
        recommended_opening_style=("question_first", "board_first"),  # 先提问，再像板书一样展开
        recommended_scene_density="medium_low",  # 偏低密度，适合分步讲
        preferred_topics=("proof", "derivation", "summary"),  # 适合证明、推导、总结
    ),
    notes="Low-noise dark board theme for step-by-step explanation and recap pages.",
)
