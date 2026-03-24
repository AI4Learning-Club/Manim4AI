from __future__ import annotations

from pathlib import Path

from ..schema import ThemeBackground, ThemeBehavior, ThemePack


BACKGROUND_DIR = Path(__file__).resolve().parent.parent / "backgrounds"


THEME = ThemePack(
    theme_id="mist_blue_focus",
    display_name="Mist Blue Focus",
    brightness="dark",  # 深色主题：背景已改成深蓝雾面，需要整套深色前景
    background=ThemeBackground(
        asset_path=BACKGROUND_DIR / "mist_blue_focus.png",  # 深蓝雾面背景图
        background_type="image",  # 图片背景
        tint_color="#0A1533",  # 深蓝压暗层：统一背景并压住局部亮雾
        tint_opacity=0.24,  # 中低强度压暗，保留背景纹理但提升前景对比
    ),
    tokens={
        # --- 画布 / 正文 ---
        "canvas_bg": "#101D43",  # 深靛蓝：相机兜底背景色
        "text_main": "#EEF6FF",  # 冷白蓝：一级正文 / 标题主色
        "text_secondary": "#D7E7FF",  # 浅蓝白：二级说明 / 常规解释
        "text_muted": "#B8C6E8",  # 灰蓝紫：弱化注释 / 次要提示
        "formula_base": "#EEF6FF",  # 冷白蓝：公式默认主色

        # --- 强调 / 反馈 ---
        "formula_highlight_primary": "#7DD3FC",  # 冰青蓝：公式主强调 / 主曲线
        "formula_highlight_secondary": "#FDBA74",  # 柔暖橙：公式副强调 / 对比曲线
        "accent_primary": "#7DD3FC",  # 冰青蓝：通用主强调 / 关键项 / 主曲线
        "accent_secondary": "#FDBA74",  # 柔暖橙：通用副强调 / 对比项 / 次曲线
        "warning_color": "#F87171",  # 珊瑚红：警示 / 错误 / 风险
        "success_color": "#34D399",  # 薄荷绿：正确 / 正向反馈

        # --- 面板 / 边框 ---
        "panel_fill_color": "#122554",  # 深蓝面板底：压住背景但不发灰
        "panel_fill_opacity": 0.72,  # 保留纹理层次，同时让面板信息更稳
        "panel_stroke": "#93C5FD",  # 亮蓝描边：和背景拉开明度
        "border_color": "#C4B5FD",  # 浅紫蓝：一般边框 / 结构线
        "grid_or_axis_color": "#A5D8FF",  # 明亮天蓝：坐标轴 / 网格 / 参考线

        # --- 标题徽标 / 字幕 ---
        "section_badge_fill": "#17326B",  # 深蓝徽标底色：独立于背景主体
        "section_badge_fill_opacity": 0.94,  # 接近实底，保证徽标识别
        "section_badge_stroke": "#FDBA74",  # 柔暖橙：章节徽标描边
        "section_badge_text": "#EEF6FF",  # 冷白蓝：章节徽标文字
        "subtitle_text": "#EEF6FF",  # 冷白蓝：底部字幕文字
        "subtitle_stroke": "#0A132A",  # 深海军蓝：字幕描边，压住蓝雾背景
    },
    behavior=ThemeBehavior(
        recommended_opening_style=("visual_first", "question_first"),  # 先给画面，再抛问题
        recommended_scene_density="medium",  # 中等密度，适合图文并行
        preferred_topics=("physics", "function", "coordinate"),  # 适合物理、函数、坐标系
    ),
    notes="Dark mist-blue stage with cold-white text, ice-blue structure, and warm orange emphasis.",
)
