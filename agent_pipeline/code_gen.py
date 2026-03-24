"""
Agent for generating, fixing, and improving Manim scene code via LLM.

Supports text and image inputs.  Uses the OpenAI-compatible API with
streaming to handle long responses.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Dict, List, Optional

from .llm import LLMClient, LLMConfig
from .output_language import normalize_output_language, output_language_name

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

_COMMON_RUNTIME_SAFETY_RULES = """\
COMMON RUNTIME SAFETY RULES (shared across generate / fix / improve):

THEME & COLOR SAFETY:
- You MUST import our custom base class from `colortest.ai4learning_theme`.
- Your main Scene class MUST inherit from `AI4LearningBaseScene`, NOT `Scene`.
- When a selected theme is provided, the Scene class MUST declare
  `theme_id = "selected_theme_id"` at class scope.
- NEVER use pure `WHITE` or pure black body text.
- Do NOT hardcode hex colors for text, formulas, panels, or shapes.
- Prefer theme helpers over raw token lookups whenever possible:
  `self.get_text(...)`, `self.get_secondary_text(...)`,
  `self.get_muted_text(...)`, `self.get_warning_text(...)`,
  `self.get_success_text(...)`, `self.get_math(...)`,
  `self.get_highlighted_math(..., level="primary"|"secondary")`,
  `self.highlight_formula_parts(...)`, `self.make_panel(...)`,
  `self.get_warning_color()`, `self.get_success_color()`,
  `self.get_border_color()`, and `self.get_axis_color()`.
- When you truly need a semantic token not covered by a helper, use
  `self.theme_token(...)` with names such as `text_secondary`, `text_muted`,
  `accent_primary`, `accent_secondary`, `formula_highlight_primary`,
  `formula_highlight_secondary`, `warning_color`, `success_color`,
  `panel_stroke`, `panel_fill_color`, `panel_fill_opacity`,
  `border_color`, and `grid_or_axis_color`.
- If the scene already sets `theme_id = "..."`, preserve that theme selection.
- Do NOT replace `self.theme_token(...)`, `self.get_text(...)`,
  `self.get_secondary_text(...)`, `self.get_muted_text(...)`,
  `self.get_math(...)`, `self.get_highlighted_math(...)`,
  `self.highlight_formula_parts(...)`, or `self.make_panel(...)`
  with hardcoded hex colors.

LOCAL ICON SAFETY:
- The teaching plan may include a `selected_assets` list. Those are the ONLY
  local icon files you may use.
- If `selected_assets` is empty or absent, do NOT invent icons, image paths,
  URLs, or external assets.
- If you use a selected local icon, load it with
  `self.load_local_icon("filename.png", height=0.9)`.
- Do NOT switch icon loading to raw file paths or URLs during fixes.

GROUP / VGROUP / CREATE SAFETY:
- Default to `Group(...)` for page layout containers and mixed-object layouts.
- Use `VGroup(...)` ONLY when every child is guaranteed to be a `VMobject`
  such as `Text`, `MathTex`, `Line`, `Circle`, `Polygon`, or `SVGMobject`.
- `self.load_local_icon(...)` may return `ImageMobject`, so never place its
  result inside `VGroup(...)`. Use `Group(...)` instead.
- If a variable was already built with `Group(...)`, never pass that variable
  into `VGroup(...)` later.
- `self.make_panel(...)` returns `Group(panel, content)`, so never place the
  result of `self.make_panel(...)` inside `VGroup(...)`.
- `self.make_page(...)`, `self.make_two_panel_page(...)`, and
  `self.make_graph_text_page(...)` build `Group(...)`-based page containers.
  If a variable contains one of those helper results, the outer wrapper must
  also be `Group(...)`, not `VGroup(...)`.
- Typical bad pattern: `VGroup(title, summary, transfer)` when `summary` or
  `transfer` already comes from `self.make_panel(...)` or another `Group(...)`.
  In that case, use `Group(...).arrange(...)` instead.
- `VGroup(*self.mobjects)` is unsafe. Use `Group(*self.mobjects)`.
- Never call `Create(...)` on a `Group(...)`.
- For `Group(...)`, use `FadeIn(...)` or animate child VMobjects separately.
- Only use `Create(...)`, `Write(...)`, or `GrowArrow(...)` on actual
  `VMobject` instances.
- Use `GrowArrow(...)` only for plain `Arrow`-like VMobjects. For `CurvedArrow`,
  `Arc`, or path-like objects, prefer `Create(...)` or animate children
  separately.
- If you define a helper that returns an arrow/path pair and you want to use
  `Create(...)` on the whole result, return `VGroup(...)` ONLY if every child
  is a `VMobject`; otherwise animate the children separately.

LANGUAGE / API SAFETY:
- `from manim import *` at the top.
- Use `Text(...)` or theme text helpers for natural-language titles, labels,
  captions, subtitles, and narration-related screen text.
- Do NOT put full natural-language phrases inside `MathTex(...)` / `Tex(...)`.
- Chinese text must NEVER appear inside `MathTex(...)` / `Tex(...)`.
- Pure math: `MathTex(r"...", font_size=...)`.
- MIXED natural language + math: split into parts and arrange them with
  `Group(...)` or `VGroup(...)` as appropriate.
- Any variable with subscripts/superscripts (e.g. `x_t`, `Q_d`) MUST use
  `MathTex`, not plain `Text`.
- Do not use nonexistent APIs such as `get_tangent_vector(...)` on `VMobject`.
- `get_tangent_line` does NOT accept `color=`. Build the tangent manually.
- There is no `self.get_text_color()` helper here. Use `self.get_text(...)`,
  `self.get_math(...)`, or `self.theme_token("text_main")` /
  `self.theme_token("formula_base")` directly.

STATE / CLEARING SAFETY:
- If the scene inherits from `AI4LearningBaseScene`, prefer
  `self.clear_scene_keep_bg()` over `FadeOut(Group(*self.mobjects))` so the
  persistent background is not removed.
- Use the available `AI4LearningBaseScene` layout helpers before stacking many
  manual `.shift()` / `.to_edge()` calls.

STAGED REVEAL SAFETY:
- Do NOT put all future text, formulas, arrows, labels, captions, examples,
  and conclusions on screen at the start of a section.
- For each page, define the persistent page objects before the first reveal of
  that page. Then reveal those objects beat by beat.
- A section may reserve stable final positions, but only the elements being
  discussed right now may be visible.
- Reveal each teaching beat in sync with narration: usually main visual or
  title first, then local labels, then formulas, then the takeaway.
- If you use `self.make_page(...)`, `self.make_two_panel_page(...)`, or
  `self.make_graph_text_page(...)`, do NOT reveal the whole returned page or
  layout container at once.
- Avoid patterns like `self.play(FadeIn(page))`, `self.play(Write(page))`,
  `self.play(Create(page))`, or `self.speak_with_subtitle(..., FadeIn(page))`
  when `page` is a page/layout container.
- If an object is already visible, do NOT "show it again" when narration
  reaches that part. Keep it on screen and highlight it, transform it, or add
  only the new local element.
- If a later persistent element would change the current page structure, start
  a new page instead of repacking the old one.
- Good rhythm: build the board like a teacher in real time, not like a fully
  finished slide that gets explained afterward.
"""

_PAGE_BLOCK_LAYOUT_CONTRACT = """\
PAGE / BLOCK AUTHORING CONTRACT:
- A section may contain multiple pages.
- End one page with `self.clear_scene_keep_bg()`, then define the next page
  from scratch.
- Compose each page before its first reveal.
- A page is organized by three fixed bands: top band, body band, and subtitle
  band.
- The top band is shared by the page title and the section badge.
- The subtitle band is permanently reserved for subtitles only.
- Top band content: page title, section badge, and nothing else substantial.
- Subtitle band content: only the subtitle module created by
  `self.speak_with_subtitle(...)`, `self.set_subtitle(...)`, or
  `self.make_subtitle_panel(...)`.
- Body band content: all actual teaching content, including graphs, diagrams,
  formulas, comparison columns, task rows, prompt panels, roadmap lines,
  promise lines, takeaway panels, mechanism strips, misconception strips,
  summary lines, queue/stack visuals, example rows, and other persistent
  teaching text.
- Any standalone natural-language sentence, question, prompt, takeaway,
  summary, roadmap line, promise line, mechanism line, misconception line, or
  other sentence-level explanatory text should be treated as a body-band block
  by default.
- Notes, takeaways, roadmap lines, and other persistent teaching text belong in
  the body band, not in a separate subtitle-like band.
- The basic layout unit of a page is a block, not a loose leaf object.
- A block may be a graph, panel, task row, queue/stack visual, comparison
  column, takeaway sentence, or note.
- If a takeaway or note occupies its own stable page region, treat it as a
  block.
- Late persistent text such as roadmap lines, promises, takeaways, summaries,
  or check-for-understanding prompts must be pre-defined as a note block for
  that page, not attached ad hoc after the page is already visible.
- Decide block placement explicitly with `Group(...).arrange(...)`,
  `next_to(...)`, `align_to(...)`, and targeted `move_to(...)` when needed.
- Inside each block, arrange leaf objects locally.
- Textual labels are intentionally restricted. Only symbolic identifiers or
  very short object names may be placed as local labels near graphics, such as
  `A`, `B`, `C`, `D`, `T`, `x`, `y`, `q1`, `layer 1`, or other similarly short
  symbolic markers.
- Use `next_to(...)` primarily for those symbolic labels and for non-text
  geometric overlays such as arrows, rings, and braces.
- Do NOT use `next_to(...)` to place sentence-like teaching text. Questions,
  prompts, takeaways, summaries, mechanism lines, roadmap lines, and other
  explanatory text must be body blocks.
- The number of body blocks is not fixed. Optimize for readability,
  non-overlap, and clear hierarchy instead of chasing an arbitrary count.
- Reference templates are suggestions only. Adapt, combine, or vary them based
  on the lesson content.
- `self.fit_group(...)` only scales and clamps a finished body block into the
  body band.
- Use `self.fit_to_top_band(...)` or `self.safe_top_title(...)` for title-like
  objects in the top band.
- In a normal page, `self.fit_group(...)` should be called once on the main
  finished body block before that page is revealed.
- Do NOT call `self.fit_group(...)` on late prompt panels, takeaway panels,
  misconception strips, single-line summary rows, or local callout labels after
  the page is already visible.
- Do NOT position persistent text relative to the whole `body` or whole `page`
  after the page has been composed. Ban patterns such as
  `note.next_to(body, ...)`, `summary.next_to(body, ...)`,
  `prompt.next_to(body, ...)`, `takeaway.align_to(body, ...)`, or any similar
  placement relative to the whole fitted body/page.
- Do NOT use raw absolute placement such as `.move_to(DOWN * ...)` for late
  persistent teaching text. If that text should remain on the page, it must be
  planned as part of the body-band layout before reveal.
- Graphical overlays and symbolic labels may use `next_to(...)`, `align_to(...)`,
  and small offsets, but they must remain local and must not collide with the
  graphic, other labels, or body blocks.
- Do NOT use `self.fit_group(...)` to invent page layout.
- After a page starts, do NOT refit or reposition the whole page.
- If a new persistent element would change the page structure, start a new page
  instead of repacking the current page.

Correct / incorrect examples:

Bad:
```python
body = Group(graph_block, compare_block).arrange(RIGHT, buff=0.5)
self.fit_group(body, max_width=11.6, center=UP * 0.2)

prompt_panel = self.make_panel(self.get_warning_text("Pause and predict", font_size=20))
prompt_panel.next_to(body, DOWN, buff=0.2)
takeaway_panel = self.make_panel(self.get_success_text("Key idea", font_size=20))
takeaway_panel.next_to(prompt_panel, UP, buff=0.14)
```

Good:
```python
prompt_panel = self.make_panel(self.get_warning_text("Pause and predict", font_size=20))
takeaway_panel = self.make_panel(self.get_success_text("Key idea", font_size=20))
note_block = Group(takeaway_panel, prompt_panel).arrange(DOWN, buff=0.14, aligned_edge=LEFT)

body = Group(main_visual_block, note_block).arrange(DOWN, buff=0.24)
self.fit_group(body, max_width=11.6, center=UP * 0.15)
```

Good for a symbolic local label:
```python
target_label = self.get_secondary_text("T", font_size=18)
target_label.next_to(target_node, RIGHT, buff=0.08).align_to(target_node, UP)
```

Good when the new persistent text would change structure too much:
```python
self.clear_scene_keep_bg()

title = self.safe_top_title("Next Step", font_size=28)
body = Group(new_visual_block, new_note_block).arrange(DOWN, buff=0.24)
self.fit_group(body, max_width=11.6, center=UP * 0.15)
```
"""

_VISUAL_CLARITY_CONTRACT = """\
VISUAL CLARITY / SIMPLICITY CONTRACT:
- The goal of a teaching visual is NOT maximal complexity. The goal is dynamic
  clarity, easy comprehension, and at-a-glance legibility.
- Prefer the simplest visual that makes the current teaching point obvious.
- Do NOT add extra nodes, edges, branches, labels, panels, arrows, icons,
  or decorative shapes unless they materially improve the current explanation.
- Every visible element must earn its place by clarifying a relation, change,
  comparison, motion, or causal step that the student needs right now.
- If the lesson is about how a process moves or changes, make that motion or
  state change the main visual. Let the diagram or graph act as supporting
  skeleton rather than stealing attention through unnecessary detail.
- If a simplified graph, diagram, path, or comparison can teach the same point
  more clearly, use the simplified version.
- Do NOT duplicate a full complex diagram on multiple sides of the page unless
  the full duplication is necessary for the comparison. Prefer lighter
  comparison copies that keep only the structure needed for that contrast.
- If some detail matters only later, introduce it later on the same stable page
  or move it to a follow-up page. Do NOT front-load completeness.
- Prefer one clear visual idea per beat. If several details compete for
  attention, simplify the figure or split the explanation into more beats/pages.
"""

_CLAUDE_REVIEW_NOTICE = (
    "Claude will review your work and code afterward, so every decision must be "
    "rigorous, defensible, and implementation-ready.\n\n"
)

_SYSTEM_GENERATE = (
_CLAUDE_REVIEW_NOTICE
    + """\
You are an expert educational animation designer AND Manim CE (v0.18+) developer.
Your job is to create animations that help students truly UNDERSTAND math/physics
concepts, not just show formulas.

------------------------------------------------------------
PART 1: PEDAGOGICAL DESIGN (think like a great teacher)
------------------------------------------------------------

You will receive a teaching plan from a teaching-planner agent.
You MUST follow that plan closely and preserve its teacher logic.
The video should feel like a teacher guiding the student step by step,
not a slideshow that states definitions directly.

When a teaching plan is provided, treat it as a TEACHER SCRIPT, not as metadata.
That means:
- use `hook`, `teaching_promise`, and `opening` to shape the opening tone,
- use each section's `teacher_move` to decide how the teacher acts,
- use each section's `student_question` as the confusion you are answering,
- use `misconceptions` to create explicit correction moments,
- use each section's `transition` so the lesson flows naturally,
- use `key_takeaway` to end each section with one clear sentence students can keep.

Each major section should feel like this classroom loop:
1. Raise the student's real question or prediction.
2. Show a visual or concrete example.
3. Explain the mechanism in plain language.
4. Land on one memorable takeaway.
5. Bridge naturally into the next section.

Do NOT sound like a textbook outline such as "定义是..., 性质是..., 应用是...".
Instead, sound like a live teacher responding to a student's current confusion:
- start from what the student is likely to think at this moment,
- use the current visual or example to test that intuition,
- then explain what actually matters in plain classroom language.
Keep the wording specific to THIS lesson. Do NOT copy stock phrases or sample
sentences from this prompt verbatim.

Before writing any code, plan a multi-step teaching flow:

STEP 1 - OPENING HOOK (5-10 seconds):
  What is the problem?  Why should the student care?
  Start with the planned `opening.style` and `opening.hook_line` when provided.
  Every lesson still needs a roadmap, but the roadmap must follow
  `opening.roadmap_style` instead of defaulting to one numbered outline.
  Valid roadmap styles include:
    - `task_line`: one short task-oriented path for this lesson
    - `question_chain`: 2 linked questions that define the route
    - `visual_tags`: 2-3 short screen labels that define the route
    - `two_step`: a concise two-step path
    - `result_path`: start from the result, then state the route back to it
    - `classic_outline`: a true outline, used only when it really fits
  The roadmap must explain how THIS lesson will proceed.
  Do NOT write empty slogans.
  Do NOT default to "我们将看懂三件事" or any fixed numbered outline.
  Use simple language.  Make the student feel "I want to know the answer."
STEP 2+ - TEACH EACH CONCEPT with VISUAL + FORMULA TOGETHER:
  This is the CORE of the animation.  For EACH concept in the planned lesson path:
  A section may use multiple pages when the content needs it. When one page
  ends, clear it and build the next page fresh instead of squeezing new
  persistent content into the old page.
  A) TOP title + MIDDLE visual + BOTTOM formula/text
     Best for: wide diagrams, process flows, timelines
  B) LEFT visual + RIGHT formula/text (each ~half width)
     Best for: a single diagram that needs explanation
  C) TOP text/question + BOTTOM visual reveal
      Best for: first asking the student to predict, then answering with the figure
  D) TOP title + FULL-WIDTH visual, then formula overlaid or below
     Best for: graphs with labels, network diagrams
  E) FULL-WIDTH formula slide (no visual)
     Best for: pure derivation steps with no diagram needed
  F) CENTER visual + small caption block below or beside it
      Best for: intuition-heavy pages where the picture should dominate

  Example for "diffusion forward process":
    TOP: title  MIDDLE: row of images (noise -> clean)  BOTTOM: formula
  Example for "forces on sliding block":
    LEFT: block diagram  RIGHT: equations
  Example for "Punnett square":
    TOP: title  CENTER: 4x4 grid  BOTTOM: ratio summary

  These are REFERENCE PATTERNS, not fixed templates. Choose, adapt, or combine
  them according to the lesson content. Do not force every section into one of
  these layouts literally.

  KEY PRINCIPLE: never show a formula without context.  The student should
  see what the formula describes - either a visual next to it, or a clear
  text explanation of what each symbol means.

    Between major concepts: clear the transient page content, keep the persistent
    background, then build the next layout fresh.
    When neighboring sections teach different kinds of content, often switch to
    a different layout rhythm so the lesson does not feel templated.

FINAL STEP - CONCLUSION (5-8 seconds):
  Summarize the key result with a highlighted box.
  Can be full-screen centered (no need for left/right split here).

TEACHER-LIKE DELIVERY RULES:
- Open with the student's confusion, not the formal definition.
- Before any abstract formula, first give the student a visible or causal picture.
- At least twice in the video, let the narration ask the student to predict,
    compare, or notice something before giving the answer.
- When correcting a misconception, first acknowledge why it feels plausible,
    then overturn it with the visual.
- Use short bridge lines such as "先别急着背结论，我们先看画面", "现在公式只是把刚才的画面写下来".
- End each section with a one-sentence takeaway a good teacher would actually say.

IMPORTANT: The visual+formula side-by-side approach is what makes
animation BETTER than a textbook.  A student can read formulas anywhere -
what they need from YOUR animation is seeing the math CONNECTED to visuals.

------------------------------------------------------------
PART 2: MANIM CODE RULES (avoid crashes and visual bugs)
------------------------------------------------------------

"""
    + _COMMON_RUNTIME_SAFETY_RULES
    + "\n\n"
    + _PAGE_BLOCK_LAYOUT_CONTRACT
    + "\n\n"
    + _VISUAL_CLARITY_CONTRACT
    + """

LAYOUT RULES (canvas is 14.2 x 8 units, safe area +/-6.0 x +/-3.3):
- HARD RULES (must follow):
  - Think in terms of pages and blocks:
    1. decide the page regions,
    2. build the stable blocks,
    3. arrange leaf objects inside each block,
    4. clamp the finished block safely.
  - Every page uses a fixed `top band + body band + subtitle band` structure.
  - Put only title/badge content in the top band.
  - Put only subtitle content in the subtitle band.
  - Put all teaching content in the body band. This includes graph/diagram
    blocks, formula blocks, explanation panels, task rows, prompt panels,
    roadmap/promise/takeaway/mechanism/misconception/summary strips,
    queue/stack/table/card blocks, example rows, and persistent local teaching
    text.
  - Any standalone sentence-like teaching text belongs to the body band by
    default, even if it is only one line. Do not classify sentence-like text as
    a local overlay.
  - Visual graphics and text blocks must not overlap each other. Body blocks
    must not overlap other body blocks.
  - Use `self.safe_top_title(...)` or `self.fit_to_top_band(...)` for title-like
    objects in the top band.
  - Use `self.fit_group(block, max_width=..., max_height=..., center=...)`
    only for finished body blocks in the body band.
  - `self.fit_group(...)` is a body-band safety helper, not a layout author.
  - In most pages, call `self.fit_group(...)` once on the main body block before
    the first reveal, not repeatedly on later small text panels.
  - After calling `self.fit_group(...)` on a whole body block, do NOT call
    `.move_to()`, `.shift()`, or `.to_edge()` on that same whole block again.
  - Never use `.to_edge(UP)` on its own for page titles. Put title-like objects
    in the top band.
  - If a section already uses `self.show_section_header(...)`, do NOT create
    another large page title with the same wording inside the page body.
  - ALWAYS reserve the bottom band for subtitles. Do NOT place formulas,
    diagrams, captions, or explanatory text in the subtitle band.
  - If you are unsure where something belongs, default to the body band unless
    it is literally the page title/badge or the subtitle module.
  - If a page needs roadmap text, promise text, a takeaway, or a summary line
    that should persist on that page, include it in a preplanned body-band block
    instead of attaching it ad hoc after reveal.
  - Only symbolic labels such as `A`, `B`, `C`, `D`, `T`, `x`, `y`, `q1`, or
    similarly short object identifiers may stay as local labels near graphics.
  - Use `next_to(...)` for those symbolic labels and for non-text geometric
    overlays only. Do not use `next_to(...)` to place sentence-like teaching
    text.
  - Bad pattern: `prompt_panel.next_to(body, DOWN, ...); self.fit_group(prompt_panel, ...)`.
    If the prompt should persist, include it in the preplanned body block. If it
    is local text, it still belongs in a body block unless it is only a
    symbolic label.
  - Bad pattern: `takeaway_panel.move_to(DOWN * ...)`.
    Persistent takeaway text belongs in the planned body layout or on a new page,
    not as late absolute-positioned loose text.
  - BETWEEN CONCEPTS: use `self.clear_scene_keep_bg()` so the persistent
    background stays visible across section transitions.
- SOFT PREFERENCES (follow unless content clearly needs otherwise):
  - Font sizes: titles 26-32, body 18-24, formulas 24-30, labels 16-20.
    Smaller is better than clipped. When in doubt, reduce font size.
  - Prefer a clear page structure: top band for title/badge, body band for all
    teaching blocks, subtitle band reserved below.
  - For side-by-side pages, a good default is
    `Group(left, right).arrange(RIGHT, buff=0.5)` and then `self.fit_group(...)`
    with an explicit center if needed.
  - For top-down pages, a good default is title at top, visual in middle,
    formula or short text below.
  - Avoid duplicate title rendering: one section marker is enough. Use either the
    shrinking section header badge or a local page heading, not both with the
    same text on screen at the same time.
  - Keep a dedicated title row above the content so the title does not visually
    collide with the graph or diagram below it.
  - Do NOT default every section to left graphic + right text.
  - For graph + explanation slides, keep the graph fully in one region and the
    explanation in another region. That explanation region may be below, above,
    or beside the graph depending on the scene.
  - If a visual needs extra explanation, prefer a caption BELOW the visual or a
    separate text panel rather than floating paragraph text over the diagram.
  - Across a full lesson, vary layouts naturally: some sections can be top-down,
    some full-width visual, some two-panel, some centered formula focus.
  - Do not repeat the exact same layout pattern for 3 or more consecutive
    sections unless the content truly requires it.
  - If the bottom area starts feeling crowded, move content upward or split the
    current teaching point into the next slide.

VECTOR DIAGRAM RULES:
- Prefer self-drawn vector diagrams with Manim primitives such as Rectangle,
    RoundedRectangle, Circle, Line, Arrow, Axes, Polygon, and VGroup.
- Build diagrams progressively. Show the core object, axis, path, or shape
    first; add labels, arrows, highlighted regions, comparisons, and formulas
    only when that exact teaching beat is being explained.
- Do NOT reveal a fully annotated finished diagram at the start of the section
    if the explanation will unfold step by step.
- But do NOT add arrows or connector lines by default. Only add them when they
    are essential to the explanation and can be anchored unambiguously.
- Do NOT rely on large text placed inside shapes as the main explanation.
    Draw the object first, then explain it beside or below the object.
- If a diagram has several moving parts, keep the base geometry stable and add
    one explanatory layer at a time instead of redrawing the whole figure.
- Use outline-only shapes (`fill_opacity=0`) unless a filled region is truly
    necessary.  This reduces false overlap detections and keeps the scene clean.
- Avoid dark decorative panels, empty filled boxes, or black blocks that do not
    carry teaching information.
- On graphs, keep only essential short labels near lines and points.  Put long
    explanations, causal arrows with sentences, and conclusions outside the axes.
- For multi-step graphs, reveal them in teaching order: base axes and baseline
    curve first, then the changed curve or marked point, then the annotation or
    takeaway. Do NOT pre-place all graph labels and callouts at once.
- Never draw decorative divider lines in the explanation panel.
- Never draw custom long horizontal or vertical lines that extend from the graph
    into the explanation panel.

AVAILABLE LAYOUT HELPERS (already defined on AI4LearningBaseScene):
- `self.fit_group(group, max_width=12, max_height=None, center=None)`
- `self.fit_to_body_band(group, max_width=12, max_height=None, center=None)`
- `self.fit_to_top_band(group, max_width=11.8, max_height=None, center=None)`
- `self.safe_top_title("Title", font_size=34, max_width=11.4)`
- `self.clear_scene_keep_bg(run_time=0.7, wait_time=0.3)`
- `self.show_section_header("片段标题")`
- `self.make_subtitle_panel("字幕内容")`
- `self.set_subtitle("字幕内容")`
- `self.clear_subtitle()`
- `self.speak_with_subtitle("旁白文本", *animations, run_time=...)`
- `self.make_page(title, body, buff=0.35)`
- `self.make_two_panel_page(title, left, right, panel_gap=0.8)`
- `self.make_graph_text_page(title, graph_group, text_group, panel_gap=1.0)`
- `self.limit_text_block(block, max_width=4.0, max_height=4.5)`
- `self.stack_panel(top, bottom, buff=0.18, max_width=5.4, max_height=4.2)`
- `self.connect_side(source, target, direction=RIGHT, buff=0.12, **kwargs)`
- `self.connect_vertical(source, target, buff=0.12, **kwargs)`
- `self.load_local_icon("calculator.png", height=0.9)`
- `self.get_secondary_text("标签", font_size=20)`
- `self.get_muted_text("注释", font_size=18)`
- `self.get_warning_text("易错点", font_size=20)`
- `self.get_success_text("结论", font_size=20)`
- `self.get_highlighted_math(r"...", level="primary")`
- `self.highlight_formula_parts(formula, primary=["x"], secondary=["y"])`
- `self.make_panel(content, padding=0.25)`
- `self.make_panel_style()`
- `self.get_warning_color()`, `self.get_success_color()`
- `self.get_border_color()`, `self.get_axis_color()`
Use `self.safe_top_title(...)` / `self.fit_to_top_band(...)` for top-band
titles, and `self.fit_group(...)` / `self.fit_to_body_band(...)` for body-band
blocks. Do not fit first and then manually move the same whole block again.
For graph + explanation pages, prefer `self.make_graph_text_page(...)`.
You do NOT need to use the same helper for every section.

SECTION TITLE RULES:
- Keep the section-title behavior: a large title should appear first, then
    shrink and remain at the top-right as the section marker.
- Use `self.show_section_header(...)` for major teaching segments.
- Before each new major teaching segment, show a clear center title first,
    then let it shrink to the top-right badge.
- Do NOT call `self.show_section_header(...)` twice in a row with the same
    title unless you have already cleared the whole section and intentionally
    started a new segment.
- Keep section titles short, usually 2-6 words in English or 4-10 Chinese characters.
- Title names should be informative and teacher-like, not vague slogans.
- Prefer titles that tell the student what this step is for, such as
    "先看每一步加了什么", "为什么它还不是乱噪声", "把图像翻译成公式".
- Avoid empty labels like "只看一步", "继续推导", "再看一个" unless they are
    expanded into a concrete learning goal.
- Once the badge is in the top-right, treat that corner as reserved space:
    do not place formulas, graph labels, captions, or text blocks under it.

SUBTITLE RULES:
- Keep a bottom subtitle module during explanation-heavy beats.
- Prefer `self.speak_with_subtitle(...)` so subtitle, narration, and animation
    stay aligned.
- Subtitle changes must follow semantic pauses that a human reader can track:
    prefer one natural clause per `self.speak_with_subtitle(...)`, instead of
    one long sentence covering multiple ideas.
- Prefer a single-line subtitle whenever possible. If narration is too long for
    one bottom line, split it into multiple explanation beats instead of forcing
    multi-line subtitles.
- Subtitle changes should be visually quiet. Use simple fade-in / fade-out
    behavior only. Avoid flashy transforms, sliding subtitles, or morphing text.
- Subtitle text must match the spoken TTS content for that beat. Do not
    paraphrase the subtitle into different wording than the narration.
- Update subtitles when the spoken focus changes, and clear them before dense
    transitions if necessary.
- Nothing except the subtitle module itself should occupy the subtitle band.
- Leave extra vertical breathing room above the subtitle band; do not place
    low formulas, captions, or diagram labels close to it.

CONTENT DENSITY RULES:
- Do NOT try to fit all explanation text on one slide.
- Do NOT use a fixed cap on block count. Judge the page by readability,
  hierarchy, and overlap.
- If the page loses a clear focal structure, split it into another page or a
  cleaner follow-up beat instead of cramming more into the current page.
- On any single slide, an explanation panel may contain at most 1 short heading
    plus 3 short body lines.
- If a concept needs more text, split it into the next slide while preserving
    the same teaching content.
- It is GOOD to make the video longer if this avoids crowding.
- Preserving all content across more slides is better than squeezing content
    into one crowded slide.
- If subtitles are present, treat the bottom band as unavailable space when
  planning every page.

ANIMATION RULES:
- Use `Write()` for formulas, `Create()` for shapes, `FadeIn(shift=DOWN*0.2)`
  for text, `GrowArrow()` for arrows.
- To clear a section: prefer `self.clear_scene_keep_bg()`. Do NOT use
  `FadeOut(Group(*self.mobjects))`, because it removes the persistent background.
- Each major section should contain multiple meaningful visual beats, not just
  one static page with narration on top of it.
- In most sections, include at least 2-4 visible changes that help the student
  see the idea develop over time.
- Prefer animations that change the state of the current visual, not just add
  more text beside it.

ANIMATION VARIETY RULES:
- Across a full lesson, include a mix of animation types instead of relying on
  only `FadeIn(...)` and static holds.
- Good animation motifs include:
  - building a diagram piece by piece,
  - moving a point, marker, or object along a path,
  - changing a graph from one state to another,
  - highlighting one part while dimming another,
  - turning a visual relationship into a formula,
  - comparing two nearby cases through a staged change,
  - transforming one formula line into the next with structural continuity.
- Prefer `Transform(...)`, `ReplacementTransform(...)`,
  `TransformMatchingTex(...)`, `LaggedStart(...)`, `AnimationGroup(...)`,
  `Indicate(...)`, `Circumscribe(...)`, `Flash(...)`, `MoveAlongPath(...)`,
  and `ValueTracker` + `always_redraw` when they clarify the idea.
- Do NOT add motion just for decoration. Every animation should teach a
  relation, change, comparison, buildup, or consequence.

STATE CHANGE RULES:
- At least half of the major sections should include a genuine state change in
  the visual itself: not only new text appearing, but the diagram, graph,
  marker, region, or formula evolving.
- For graphs, prefer a progression such as base axes -> base curve -> changed
  curve -> marked point/intersection -> takeaway.
- For geometry or process diagrams, prefer object construction, part-by-part
  highlighting, motion along a path, or before/after comparison.
- For formulas, prefer deriving or transforming from the previous line rather
  than showing isolated final equations with no visual transition.
- If a visual stays on screen for several narration beats, make it evolve in at
  least one meaningful way during those beats.

STABILITY RULES (reduce messy motion):
- Once a page layout appears, keep its title, panels, and axes FIXED in place.
- Do NOT animate whole pages with `.animate.shift(...)` or move large groups
    around after they are already on screen.
- Do NOT call `self.fit_group(...)` again on an already visible whole page just
  because a late takeaway, note, or other persistent element appears.
- Reveal new information in place with FadeIn, Write, Create, or small local
    transforms.
- If a section needs a new layout, FadeOut the old page and build a new stable
    page, instead of dragging old elements across the screen.
- Axes should enter once and then stay anchored; only curves, dots, arrows,
    or highlights should change.
- Text blocks should appear at their final positions. Avoid long sliding text.

REVEAL RHYTHM RULES:
- Think like a teacher building the board live.
- Define the page objects before the first reveal of that page. Then reveal
  them in the order the narration needs.
- At the start of a section, show only the minimum needed to begin the
  explanation.
- When narration says "now look at this label / this step / this formula",
  that specific object should appear at that beat, not earlier.
- Do NOT pre-place a full explanation panel if its lines will be explained one
  by one. Reveal those lines progressively.
- Do NOT pre-place the final formula before the intuition or derivation has
  happened.
- Do NOT pre-place a late persistent note or takeaway by repacking the current
  visible page. If it needs its own stable region, plan it as part of the page
  before reveal or move it to the next page.
- If a section has 3 teaching beats, implement 3 reveals, not one full-page
  reveal plus 3 repeated explanations.
- Page/layout helpers are for positioning and stable composition, not for
  dumping all content on screen at once.

VOICE NARRATION (audio-synced pacing):
- Your Scene class MUST inherit from `AI4LearningBaseScene`.
  Write: `class MyScene(AI4LearningBaseScene):` instead of `class MyScene(Scene):`.
- Use `dur = self.speak("旁白文本")` to play TTS audio.
  It returns the audio duration in seconds.  Use this to pace animations:

    dur = self.speak("现在我们来看导数的几何意义")
    self.play(Create(graph), run_time=dur)

  Or for multiple animations during one narration:

    dur = self.speak("这两条线会逐渐靠拢，最终达到同速")
    self.play(Create(line1), run_time=dur * 0.5)
    self.play(Create(line2), run_time=dur * 0.5)

  Or for pausing while narration plays:

    dur = self.speak("请注意这个关键公式")
    self.wait(dur)

- Call self.speak() BEFORE or AT THE SAME TIME as the animation it describes.
- Use SHORT sentences (roughly 6-16 English words or 15-30 Chinese characters per speak call).
- One speak() per visual "step" - don't narrate everything at once.
- For transitions (FadeOut), do NOT add narration - keep them silent and fast.
- MATCH narration length to animation complexity:
  If speak() returns 3 seconds but you only have a simple FadeIn, split it:
    dur = self.speak("...")
    self.play(FadeIn(element), run_time=min(dur, 1.5))
    self.wait(max(0, dur - 1.5))
  This avoids long freezes on simple animations.
- Section titles: narrate them!  Don't show a silent title.
  dur = self.speak("下面来看第二步")
  self.play(FadeIn(title), run_time=dur)
- Prefer `self.speak_with_subtitle(...)` over raw `self.speak(...)` during
    explanation beats so the subtitle module stays synchronized.

GRAPH ANNOTATION RULES:
- The most important rule: every line or arrow must point to a real visual
    target with a stable anchor point. If you cannot anchor it cleanly, do not
    draw that arrow on this page.
- When in doubt, prefer no arrow at all. A nearby label plus a staged reveal is
    better than a wrong pointer.
- Prefer short arrows between nearby objects. Avoid long cross-screen arrows,
    diagonal arrows across crowded regions, or arrows that pass over text.
- For left/right layouts, keep arrows fully inside the left visual panel or
    fully inside the right explanation panel. Do not let arrows cross the gutter.
- Use `self.connect_side(...)` or `self.connect_vertical(...)` for pointer-style
    arrows instead of hand-written start/end coordinates whenever possible.
- Arrow labels must sit next to the arrow they describe and must not overlap the
    arrow shaft, the target object, or another label.
- Straight lines used as connectors must be anchored to object edges, not drawn
    approximately by eye.
- When labelling regions on a graph (e.g. shortage arrows between curves),
  place annotations ABOVE or BELOW the graph area, not overlapping curves.
- Use `.next_to(axes, DOWN)` or `.next_to(axes, UP)` for annotation text.
- Alternatively, put annotations in the RIGHT panel, not on the graph itself.
- Never place long titles, sentences, or multi-word explanations inside the
    axes region.
- A line intersection between supply and demand curves is normal; avoid adding
    extra decorative shapes at the intersection.
- When showing cause/effect on a graph, animate one change at a time: first
    reveal the base graph, then the shifted curve, then the explanation text.
- Right-side graph labels such as `D_1`, `S_1`, `E_2` must stay fully inside the
    graph area and must never intrude into the text panel.
- If graph labels and explanation text compete for space, keep the graph labels
    minimal and move the sentence-level explanation to a separate follow-up slide.
- If an arrow, brace, or pointer would make the page crowded or ambiguous,
    split the explanation into a follow-up slide rather than forcing the pointer in.

PACING RULES:
- Let the speak() duration drive the timing.  Do NOT add extra self.wait()
  after a speak-synced animation unless you need a deliberate pause.
- VISUAL ANIMATIONS: run_time = dur (from speak).
- TRANSITIONS: run_time=0.7, no speak, self.wait(0.3).
- KEY INSIGHTS: speak() + self.wait(dur) to let student absorb.

GEOMETRY RULES:
- Shapes on polygon edges MUST extend OUTWARD (check normal direction).
- Group all geometry -> `scale_to_fit_width(10)` to prevent overflow.
- No two filled shapes should overlap.

Output ONLY the Python code inside a ```python``` block.
"""
)

_SYSTEM_FIX = (
_CLAUDE_REVIEW_NOTICE
    + """\
You are an expert Manim debugger.  The code below failed to render.
Fix ALL errors so it renders successfully.

STEP 1 - Before even reading the error log, scan the ENTIRE code against the
shared runtime safety rules below and fix every violation first:
"""
    + _COMMON_RUNTIME_SAFETY_RULES
    + """

STEP 2 - Read the error log and fix any remaining issues:
- Attribute errors -> check Manim CE v0.18+ API.
- Type errors -> check argument types.
- `get_tangent_line` does NOT accept `color` keyword. Create tangent manually:
    tangent = Line(start, end, color=GREEN)
- `VMobject` does NOT provide `get_tangent_vector(...)` here. Use
  `angle_of_vector(path.get_end() - path.point_from_proportion(0.92))`
  or animate the path/tip separately.
- `unexpected keyword argument` -> remove the bad kwarg or replace the method.

Preserve the original animation intent.
Output ONLY the corrected Python code inside a ```python``` block.
"""
)

_SYSTEM_IMPROVE = (
_CLAUDE_REVIEW_NOTICE
    + """\
You are an expert Manim quality engineer AND educational designer.
The code below rendered but the QA pipeline found problems.

I am showing you the code, the specific problems, AND keyframe screenshots.

------------------------------------------------------------
RULE #1: PRESERVE the teaching structure!
------------------------------------------------------------

The original code likely has a good educational arc (motivation -> intuition ->
calculation).  Your job is to FIX VISUAL BUGS while KEEPING the teaching flow.

Specifically, you MUST preserve:
- The opening hook style and the lesson-specific roadmap style
- A roadmap that explains how THIS lesson proceeds, not a generic numbered template
- The Phase A opening hook and lesson roadmap, not a fixed numbered template
- The Phase B visual intuition (diagrams, graphs, animations)
- The Phase C formula derivation steps
- The overall order and pacing
- The section-title rhythm where the title appears large first and then stays
    at the top-right.
- The bottom subtitle module when present, and add it if the scene lacks a
    clear subtitle band during explanations.

Do NOT simplify the teaching just to avoid overlap.  Instead, fix the overlap
by adjusting positions and sizes.

------------------------------------------------------------
RULE #2: Fix visual bugs surgically
------------------------------------------------------------

## "overlap" / "truncated" / "cut off":
- LOOK at the keyframe images - identify WHICH specific elements overflow.
- FIX only those elements: shrink them, reposition them, or add spacing.
- Rebuild the affected stable block, then use
  `self.fit_group(block, max_width=..., max_height=..., center=...)`.
- If a block was already fitted, do NOT fix it by fitting and then moving the
  same whole block again.
- FadeOut old elements before showing new ones in the same area.
- For physics diagrams where block sits ON board: make block thinner or use
  outline-only (fill_opacity=0) so the overlap is not flagged.
- If text overlaps a diagram, separate them into different panels instead of
    squeezing both into the same region.
- If a section mixes title + diagram + explanation, rebuild it as a top title
    row plus a lower two-panel row.
- Replace text-inside-shape layouts with self-drawn vector objects plus a
    nearby caption or right-side explanation block.
- If a graph page is crowded, preserve the content but split it across two
    consecutive slides instead of forcing the text to remain next to the graph.
- The bottom subtitle band must stay clear; move any low-placed content upward
    or split the page rather than letting it collide with subtitles.
- If arrows, braces, or pointer lines are misaligned, rebuild them using stable
    object-edge anchors rather than tweaking raw coordinates by eye.
- If a symbolic label is awkwardly placed, rebuild it from a stable anchor with
  explicit side choice plus fine alignment/offset. Do not leave it at a bare
  default `next_to(...)` position in a crowded area.
- If the text is not a symbolic label, do not keep it as a local overlay during
  fixes. Move it into a real body block or a new page.
- If a late persistent note, takeaway, or summary caused the page to repack,
  turn it into a preplanned note block for that page or move it to a new page.
- That note block is part of the body band, not a subtitle replacement and not
  floating late-added text.
- If you see a pattern like `something.next_to(...); self.fit_group(something, ...)`
  on a prompt/callout/takeaway strip, remove that pattern. Fold the text into
  the preplanned body layout before reveal, unless it is only a symbolic label.
- If you see late `.move_to(DOWN * ...)` placement for a takeaway/prompt panel,
  replace it with a planned body-band block or move that teaching point to a
  new page.

## "layout" / "dense":
- Break crowded sections into sub-stages with FadeOut between them.
- But keep the CONTENT the same - just spread it across more slides.
- A section may become multiple pages. When the page structure changes, clear
  the old page and rebuild the next one instead of squeezing all persistent
  content into one fitted layout.
- If a figure is hard to read because it is overly complete or visually busy,
  simplify the figure itself before adding more spacing hacks. Keep only the
  structure needed for the current teaching point.
- If a section currently appears as a full finished page before the narration
  explains it, rebuild it as a staged reveal: keep the layout stable, but let
  labels, formulas, bullets, and takeaways appear only when that beat is
  narrated.
- Do NOT solve pacing problems by showing the same content twice. If something
  is already on screen, keep it and highlight it, or add only the missing part.

## "animation" / "motion":
- Add self.wait(0.3) between rapid animations, use longer run_time.

------------------------------------------------------------
RULE #3: Never introduce new crashes
------------------------------------------------------------

"""
    + _COMMON_RUNTIME_SAFETY_RULES
    + "\n\n"
    + _PAGE_BLOCK_LAYOUT_CONTRACT
    + "\n\n"
    + _VISUAL_CLARITY_CONTRACT
    + """
- Keep body text and formula base in theme-driven defaults such as
  `self.get_text(...)`, `self.get_math(...)`, `self.theme_token("text_main")`,
  `self.theme_token("text_secondary")`, and `self.theme_token("formula_base")`.
- Reserve accent and warning tokens for highlights, warnings, structure, and
  selected emphasis rather than large default body text.
- Use the available AI4LearningBaseScene layout helpers to rebuild crowded scenes
    instead of stacking manual `.shift()` calls.
- Preserve or introduce varied layouts instead of collapsing everything into
    the same left-visual/right-text template.
- Prefer `self.show_section_header(...)` and `self.speak_with_subtitle(...)`
    when revising scenes so title markers and subtitle rhythm remain consistent.
- Keep the top-right title badge clear of other content, and split long
    narration into shorter subtitle-sized beats when needed.
- Treat takeaways and notes as blocks when they occupy their own stable page
  region, not as floating late-added loose text.
- Use simple subtitle fade-in/fade-out only; avoid flashy subtitle transitions.
- Prefer helper-based anchored connectors such as `self.connect_side(...)` and
    `self.connect_vertical(...)` when fixing arrow direction or pointer drift.
- Remove dark empty panels or filled black shapes that do not carry teaching
    meaning; prefer clean outlines or no panel at all.
- If an arrow remains ambiguous after repositioning, remove it and explain the
    target using a nearby label or a separate follow-up beat instead.

Output ONLY the improved Python code in a ```python``` block.
"""
)


# ---------------------------------------------------------------------------
# Code extraction
# ---------------------------------------------------------------------------

def _extract_code(text: str) -> str:
    """Extract the first ```python ... ``` block from LLM output."""
    pattern = r"```python\s*\n(.*?)```"
    m = re.search(pattern, text, re.DOTALL)
    if m:
        return m.group(1).strip()
    pattern2 = r"```\s*\n(.*?)```"
    m2 = re.search(pattern2, text, re.DOTALL)
    if m2:
        return m2.group(1).strip()
    return text.strip()


def _image_to_data_url(path: Path) -> str:
    raw = path.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    suffix = path.suffix.lower().lstrip(".")
    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "gif": "image/gif", "webp": "image/webp"}.get(suffix, "image/png")
    return f"data:{mime};base64,{b64}"


def _build_actionable_feedback(eval_report: Dict) -> str:
    """Translate the new eval_pipeline report into concrete repair guidance."""

    def _as_float(value) -> Optional[float]:
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        if isinstance(value, (int, float)):
            return float(value)
        return None

    def _metric_map(dimension: Dict) -> Dict[str, Dict]:
        metrics = dimension.get("metrics", [])
        result: Dict[str, Dict] = {}
        if not isinstance(metrics, list):
            return result
        for metric in metrics:
            if not isinstance(metric, dict):
                continue
            name = str(metric.get("name", "")).strip()
            if name:
                result[name] = metric
        return result

    def _metric_note(metric: Dict) -> str:
        details = str(metric.get("details", "") or "").strip()
        if details:
            return details
        return str(metric.get("description", "") or "").strip()

    lines: List[str] = []
    score = float(eval_report.get("overall_score", 0) or 0)
    lines.append(
        f"Overall score: {score:.2f} / 1.00 - "
        f"{'PASS' if eval_report.get('overall_passed') else 'FAIL'}\n"
    )

    dimensions = eval_report.get("dimensions", [])
    dimension_map: Dict[str, Dict] = {}
    if isinstance(dimensions, list):
        for dim in dimensions:
            if not isinstance(dim, dict):
                continue
            name = str(dim.get("name", "")).strip()
            if name:
                dimension_map[name] = dim

    lines.append("### Problem summary from evaluation dimensions:\n")

    visual_dim = dimension_map.get("Visual Quality")
    if visual_dim:
        visual_metrics = _metric_map(visual_dim)

        overlap = visual_metrics.get("overlap")
        overlap_score = _as_float(overlap.get("value")) if overlap else None
        if overlap and overlap_score is not None and overlap_score < 0.70:
            lines.append(
                f"**VISUAL QUALITY / OVERLAP (score {overlap_score:.2f})**: {_metric_note(overlap)}\n"
                "  -> Visual elements are colliding, occluding text, or pushing off canvas.\n"
                "  -> FIX: reduce simultaneous objects, increase spacing, and split crowded beats into more pages.\n"
                "  -> Use staged FadeOut/FadeIn so old content leaves before new content enters.\n"
            )

        layout = visual_metrics.get("layout")
        layout_score = _as_float(layout.get("value")) if layout else None
        if layout and layout_score is not None and layout_score < 0.60:
            lines.append(
                f"**VISUAL QUALITY / LAYOUT (score {layout_score:.2f})**: {_metric_note(layout)}\n"
                "  -> The frame is too dense or poorly organized.\n"
                "  -> FIX: show fewer objects at once, keep one clear focal region, and avoid squeezing two heavy panels into one shot.\n"
                "  -> Simplify the figure itself when possible: remove unnecessary branches, labels, copies, arrows, or decorative blocks that do not teach the current beat.\n"
            )

        animation = visual_metrics.get("animation_continuity")
        animation_score = _as_float(animation.get("value")) if animation else None
        if animation and animation_score is not None and animation_score < 0.60:
            lines.append(
                f"**VISUAL QUALITY / ANIMATION CONTINUITY (score {animation_score:.2f})**: {_metric_note(animation)}\n"
                "  -> Motion pacing is jerky or visually discontinuous.\n"
                "  -> FIX: add short pauses between transitions, avoid moving whole layouts after entry, and use longer run_time for dense transformations.\n"
            )

        consistency = visual_metrics.get("visual_content_consistency")
        consistency_score = _as_float(consistency.get("value")) if consistency else None
        if consistency and consistency_score is not None and consistency_score < 0.60:
            lines.append(
                f"**VISUAL QUALITY / CONTENT CONSISTENCY (score {consistency_score:.2f})**: {_metric_note(consistency)}\n"
                "  -> The video does not clearly cover the intended teaching sections or drifts away from the lesson plan.\n"
                "  -> FIX: make each scene map to a teaching-plan section and ensure every key takeaway appears on screen explicitly.\n"
            )

    task_dim = dimension_map.get("Task Correctness")
    if task_dim:
        task_metrics = _metric_map(task_dim)

        accuracy = task_metrics.get("content_accuracy")
        if accuracy and accuracy.get("value") is False:
            lines.append(
                f"**TASK CORRECTNESS / CONTENT ACCURACY**: {_metric_note(accuracy)}\n"
                "  -> Some explanation, formula, label, or conclusion is incorrect or does not answer the requested lesson properly.\n"
                "  -> FIX: correct the math/science content first, and align every major claim with the teaching plan and topic.\n"
            )

        clarity = task_metrics.get("pedagogical_clarity")
        clarity_score = _as_float(clarity.get("value")) if clarity else None
        if clarity and clarity_score is not None and clarity_score < 0.60:
            lines.append(
                f"**TASK CORRECTNESS / PEDAGOGICAL CLARITY (score {clarity_score:.2f})**: {_metric_note(clarity)}\n"
                "  -> The explanation order is unclear or too jumpy.\n"
                "  -> FIX: simplify the narrative arc, make each shot do one teaching job, and tighten transitions between idea -> visual -> conclusion.\n"
                "  -> If the visual itself is too busy, simplify it before adding more explanatory text.\n"
            )

        engagement = task_metrics.get("engagement")
        engagement_score = _as_float(engagement.get("value")) if engagement else None
        if engagement and engagement_score is not None and engagement_score < 0.60:
            lines.append(
                f"**TASK CORRECTNESS / ENGAGEMENT (score {engagement_score:.2f})**: {_metric_note(engagement)}\n"
                "  -> The pacing or visual storytelling is too flat.\n"
                "  -> FIX: reduce dead time, reveal information progressively, and make the key insight appear through motion rather than static dumping.\n"
                "  -> Prefer one strong visual idea at a time over a crowded frame with many competing details.\n"
            )

    audio_dim = dimension_map.get("Audio Quality")
    if audio_dim:
        audio_metrics = _metric_map(audio_dim)
        alignment = audio_metrics.get("av_temporal_alignment")
        alignment_score = _as_float(alignment.get("value")) if alignment else None
        if alignment and alignment_score is not None and alignment_score < 0.60:
            lines.append(
                f"**AUDIO QUALITY / AV TEMPORAL ALIGNMENT (score {alignment_score:.2f})**: {_metric_note(alignment)}\n"
                "  -> Narration timing does not match on-screen changes well enough.\n"
                "  -> FIX: align spoken beats with visual reveals, avoid long stretches of speech with static visuals, and avoid large visual jumps before narration catches up.\n"
            )

    for issue in eval_report.get("issues", []):
        vlm_reason = issue.get("vlm_reason", "")
        cv_reason = issue.get("cv_reason", "")
        if vlm_reason:
            lines.append(f"\n### VLM reviewer said:\n\"{vlm_reason}\"\n")
        if cv_reason:
            lines.append(f"CV analysis: {cv_reason}\n")
        tr = issue.get("time_range", "")
        if tr:
            lines.append(f"Time range affected: {tr}\n")

    return "\n".join(lines)


def _build_local_asset_prompt(teaching_plan: Optional[Dict]) -> str:
    if not teaching_plan:
        return (
            "## Local icons\n"
            "No local icons were selected for this lesson. Do not invent icon "
            "filenames, image paths, URLs, or external assets."
        )

    selected_assets = teaching_plan.get("selected_assets", [])
    if not selected_assets:
        return (
            "## Local icons\n"
            "No local icons were selected for this lesson. Do not invent icon "
            "filenames, image paths, URLs, or external assets."
        )

    return (
        "## Local icons selected for this lesson\n"
        + json.dumps(selected_assets, ensure_ascii=False, indent=2)
        + "\n\nUse only these filenames. Load them only with "
        "`self.load_local_icon(\"filename.png\", height=...)`. "
        "Do not invent more icons or switch to raw `ImageMobject(...)` paths."
    )


def _build_opening_prompt(teaching_plan: Optional[Dict]) -> str:
    if not teaching_plan:
        return ""

    opening = teaching_plan.get("opening")
    if not isinstance(opening, dict):
        return ""

    style = str(opening.get("style", "")).strip()
    roadmap_style = str(opening.get("roadmap_style", "")).strip()
    hook_line = str(opening.get("hook_line", "")).strip()
    if not (style or roadmap_style or hook_line):
        return ""

    opening_summary = {
        "style": style,
        "hook_line": hook_line,
        "roadmap_style": roadmap_style,
    }

    return (
        "## Opening plan for this lesson\n"
        + json.dumps(opening_summary, ensure_ascii=False, indent=2)
        + "\n\nThis opening plan is already fixed for the lesson.\n"
        + "- The opening must follow `opening.style`.\n"
        + "- The first spoken or visual beat should cash out `opening.hook_line`.\n"
        + "- The lesson roadmap must follow `opening.roadmap_style`.\n"
        + "- Every roadmap style must explain how THIS lesson will proceed.\n"
        + "- Do NOT write empty slogans or generic motivation lines.\n"
        + "- Do NOT default to a fixed numbered outline like `我们将看懂三件事` unless the roadmap style is explicitly `classic_outline`.\n"
    )


def _build_selected_theme_prompt(teaching_plan: Optional[Dict]) -> str:
    if not teaching_plan:
        return ""

    selected_theme = teaching_plan.get("selected_theme")
    if not isinstance(selected_theme, dict):
        return ""

    theme_id = str(selected_theme.get("theme_id", "")).strip()
    if not theme_id:
        return ""

    theme_summary = {
        "theme_id": theme_id,
        "display_name": selected_theme.get("display_name"),
        "brightness": selected_theme.get("brightness"),
        "notes": selected_theme.get("notes"),
        "recommended_opening_style": selected_theme.get("recommended_opening_style", []),
        "recommended_scene_density": selected_theme.get("recommended_scene_density"),
        "preferred_topics": selected_theme.get("preferred_topics", []),
        "reason": selected_theme.get("reason"),
    }

    return (
        "## Selected theme for this run\n"
        + json.dumps(theme_summary, ensure_ascii=False, indent=2)
        + "\n\nThis theme choice is already fixed for the lesson.\n"
        + f'- Your Scene class MUST set `theme_id = "{theme_id}"`.\n'
        + "OVERRIDE any older legacy-palette examples in the generic prompt.\n"
        + "For new code, prefer theme-aware APIs:\n"
        + "- `self.get_text(...)` for default text\n"
        + "- `self.get_math(...)` for default formulas\n"
        + "- `self.theme_token(\"text_secondary\")` / `self.theme_token(\"text_muted\")` for softer labels\n"
        + "- `self.theme_token(\"accent_primary\")` / `self.theme_token(\"accent_secondary\")` for highlights\n"
        + "- `self.theme_token(\"warning_color\")` / `self.theme_token(\"success_color\")` for warning and success states\n"
        + "- `self.theme_token(\"panel_stroke\")`, `self.theme_token(\"panel_fill_color\")`, and `self.theme_token(\"panel_fill_opacity\")` for panels\n"
        + "- `self.theme_token(\"grid_or_axis_color\")` for axes and grids\n"
        + "Do not import or rely on legacy palette names like `BLUE_100` or `CYAN_400` in newly generated scenes unless you are preserving existing code."
    )


def _build_output_language_prompt(output_language: str) -> str:
    language = normalize_output_language(output_language)
    language_name = output_language_name(language)
    if language == "zh":
        return (
            "## Output language\n"
            "The final video must use Chinese for all user-facing natural language.\n"
            "- All titles, labels, captions, section headers, subtitles, and narration must be in natural Chinese.\n"
            "- If the teaching plan or request contains English teaching text, translate its meaning into Chinese before putting it on screen.\n"
            "- Only formulas, variable names, standard math symbols, units, file names, and truly necessary abbreviations may remain non-Chinese.\n"
            "- If an abbreviation is important, prefer translated Chinese plus the abbreviation in parentheses.\n"
        )

    return (
        "## Output language\n"
        f"The final video must use {language_name} for all user-facing natural language.\n"
        "- All titles, labels, captions, section headers, subtitles, and narration must be in clear classroom English.\n"
        "- The teaching plan may be written in Chinese; translate its teacher intent into English instead of copying Chinese wording into the video.\n"
        "- Only formulas, variable names, standard math symbols, units, file names, and truly necessary abbreviations may remain non-English.\n"
        "- If a translated term benefits from an abbreviation, write the English term first and keep the abbreviation short.\n"
    )


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class CodeGenAgent:
    """LLM-backed agent for Manim code generation / repair / improvement."""

    def __init__(
        self,
        api_key: str | LLMConfig,
        base_url: str = "https://api.tabcode.cc/openai",
        model: str = "gpt-5.4",
    ):
        if isinstance(api_key, LLMConfig):
            llm_config = api_key
            self.model = llm_config.model
            self.client = LLMClient(llm_config)
        else:
            self.model = model
            self.client = LLMClient(
                LLMConfig(
                    stage="adhoc",
                    model=model,
                    api_key=api_key,
                    base_url=base_url,
                ),
            )

    def _call(self, system: str, user_content: list, max_retries: int = 3) -> str:
        import time as _time
        for attempt in range(max_retries):
            try:
                text = self.client.generate_text(
                    system,
                    user_content,
                    max_retries=1,
                )
                if text.strip():
                    return text.strip()
                raise TimeoutError("Empty response from API")
            except Exception as exc:
                if attempt < max_retries - 1:
                    wait = 5 * (attempt + 1)
                    print(f"  API error (attempt {attempt+1}/{max_retries}): {exc}")
                    print(f"  Retrying in {wait}s ...")
                    _time.sleep(wait)
                else:
                    raise

    def generate(
        self,
        request_text: str,
        image_path: Optional[Path] = None,
        teaching_plan: Optional[Dict] = None,
        output_language: str = "en",
    ) -> str:
        """Generate Manim code from a student request (text, optionally image)."""
        prompt_parts = [f"## Student request\n{request_text}"]
        prompt_parts.append(_build_output_language_prompt(output_language))
        if teaching_plan:
            prompt_parts.append(
                "## Teaching plan\n" + json.dumps(teaching_plan, ensure_ascii=False, indent=2)
            )
            prompt_parts.append(
                "## Required teaching-plan execution\n"
                "Turn the plan into actual teaching behavior. The opening must cash out the hook, teaching_promise, and opening plan. "
                "Use `opening.style` to decide how the lesson starts, use `opening.hook_line` as the opening beat, and realize the lesson roadmap through `opening.roadmap_style` rather than a fixed template. "
                "All roadmap styles must describe how this lesson will proceed; do not write empty slogans. "
                "For each section, reflect teacher_move, answer student_question, include the concrete_example or visual_strategy, "
                "and end with key_takeaway or check_for_understanding. Use the listed misconceptions to design at least one explicit "
                "'you may think X, but actually Y' correction moment. Use transitions so the lesson feels continuous rather than segmented."
            )
            opening_prompt = _build_opening_prompt(teaching_plan)
            if opening_prompt:
                prompt_parts.append(opening_prompt)
            prompt_parts.append(_build_local_asset_prompt(teaching_plan))
            theme_prompt = _build_selected_theme_prompt(teaching_plan)
            if theme_prompt:
                prompt_parts.append(theme_prompt)
        prompt_parts.append(
            "## Implementation priority\n"
            "Plan each section as one or more stable pages. Compose each page before its first reveal. "
            "Use blocks as the page layout units, place those blocks explicitly, and arrange leaf objects inside each block. "
            "Use `self.safe_top_title(...)` or `self.fit_to_top_band(...)` for top-band title objects, and use "
            "`self.fit_group(...)` only as the final body-band clamp for finished body blocks. Keep each page visually stable after it appears. "
            "Use `next_to(...)` only for symbolic labels or non-text geometric overlays; all sentence-like teaching text must be real body blocks. "
            "If a new persistent element would change the page structure, start a new page instead of repacking the current one. "
            "Use teacher-like sequencing, self-drawn vector diagrams, varied layouts chosen by content, and stable section markers/subtitles. "
            "Reserve the bottom band for subtitles only, keep the top-right badge area clear, use a centered section title before each major segment, "
            "give sections informative titles rather than vague labels, and only use arrows/lines when they can be cleanly anchored to nearby objects."
        )
        content: list = [{"type": "input_text", "text": "\n\n".join(prompt_parts)}]
        if image_path and image_path.exists():
            content.append({
                "type": "input_image",
                "image_url": _image_to_data_url(image_path),
            })
        raw = self._call(_SYSTEM_GENERATE, content)
        return _extract_code(raw)

    def fix(self, code: str, error_log: str, output_language: str = "en") -> str:
        """Fix code that failed to render, given the error output."""
        content: list = [{
            "type": "input_text",
            "text": (
                _build_output_language_prompt(output_language)
                + "\n\n"
                f"## Original code\n```python\n{code}\n```\n\n"
                f"## Render error\n```\n{error_log[-3000:]}\n```"
            ),
        }]
        raw = self._call(_SYSTEM_FIX, content)
        return _extract_code(raw)

    def narrate(self, code: str, request_text: str, output_language: str = "en") -> List[str]:
        """Generate a narration script (list of paragraphs) for the video."""
        language = normalize_output_language(output_language)
        language_name = output_language_name(language)
        sentence_hint = (
            "15-40 Chinese characters"
            if language == "zh"
            else "1-2 short sentences, usually 6-18 English words total"
        )
        content: list = [{
            "type": "input_text",
            "text": (
                _build_output_language_prompt(language)
                + "\n\n"
                f"## Student request\n{request_text}\n\n"
                f"## Manim code\n```python\n{code}\n```"
            ),
        }]
        system = (
            f"You are a warm, clear {language_name}-speaking teacher narrating an educational "
            "animation video.  Based on the Manim code and the student's question, "
            f"write a narration script in {language_name}.\n\n"
            "Rules:\n"
            "- Write 5-10 short paragraphs, one for each visual section.\n"
            f"- Each paragraph should be {sentence_hint}.\n"
            "- Match the pacing of the animation: brief for visual parts, detailed "
            "for formula/concept explanations.\n"
            "- Use conversational, encouraging tone (like talking to a student).\n"
            "- Do NOT include timestamps, stage directions, or code references.\n"
            "- Output ONLY a JSON array of strings, like:\n"
            '  ["First narration beat", "Second narration beat", ...]\n'
        )
        raw = self._call(system, content)
        try:
            import json as _json
            text = raw.strip()
            if text.startswith("```"):
                text = text.strip("`").strip()
                if text.lower().startswith("json"):
                    text = text[4:].strip()
            left = text.find("[")
            right = text.rfind("]")
            if left >= 0 and right > left:
                return _json.loads(text[left:right + 1])
        except Exception:
            pass
        return [p.strip() for p in raw.split("\n") if p.strip()]

    def improve(
        self,
        code: str,
        eval_report: Dict,
        keyframe_paths: Optional[List[Path]] = None,
        teaching_plan: Optional[Dict] = None,
        output_language: str = "en",
    ) -> str:
        """Improve code based on evaluation feedback + optional keyframe images."""
        feedback = _build_actionable_feedback(eval_report)
        prompt_parts = [
            f"## Original code\n```python\n{code}\n```\n\n## Evaluation feedback\n{feedback}"
        ]
        prompt_parts.append(_build_output_language_prompt(output_language))
        if teaching_plan:
            prompt_parts.append(
                "## Teaching plan to preserve\n"
                + json.dumps(teaching_plan, ensure_ascii=False, indent=2)
            )
            opening_prompt = _build_opening_prompt(teaching_plan)
            if opening_prompt:
                prompt_parts.append(opening_prompt)
            prompt_parts.append(_build_local_asset_prompt(teaching_plan))
            theme_prompt = _build_selected_theme_prompt(teaching_plan)
            if theme_prompt:
                prompt_parts.append(theme_prompt)
        content: list = [{
            "type": "input_text",
            "text": "\n\n".join(prompt_parts),
        }]
        if keyframe_paths:
            content.append({
                "type": "input_text",
                "text": "## Keyframe screenshots (so you can SEE the problems):",
            })
            for kf in keyframe_paths:
                if kf.exists():
                    content.append({
                        "type": "input_image",
                        "image_url": _image_to_data_url(kf),
                    })
        raw = self._call(_SYSTEM_IMPROVE, content)
        return _extract_code(raw)

