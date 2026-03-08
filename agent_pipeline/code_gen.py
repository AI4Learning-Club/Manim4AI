"""
Agent for generating, fixing, and improving Manim scene code via LLM.

Supports text and image inputs.  Uses the OpenAI-compatible API with
streaming to handle long responses.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Dict, List, Optional

from openai import OpenAI

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

_SYSTEM_GENERATE = """\
You are an expert educational animation designer AND Manim CE (v0.18+) developer.
Your job is to create animations that help students truly UNDERSTAND math/physics
concepts — not just show formulas.

═══════════════════════════════════════════════════════
PART 1: PEDAGOGICAL DESIGN (think like a great teacher)
═══════════════════════════════════════════════════════

Before writing any code, plan a multi-step teaching flow:

STEP 1 — MOTIVATION & ROADMAP (5-10 seconds):
  What is the problem?  Why should the student care?
  Show the concrete problem statement clearly.
  Then LIST what the student will learn, like a table of contents:
    "我们将看懂 3 件事：
     1. ...
     2. ...
     3. ..."
  This gives the student a mental roadmap before diving in.
  Use simple language.  Make the student feel "I want to know the answer."

STEP 2+ — TEACH EACH CONCEPT with VISUAL + FORMULA SIDE-BY-SIDE:
  This is the CORE of the animation.  For EACH concept in the roadmap:

  Show visuals and formulas ON SCREEN TOGETHER so the student sees the
  connection.  Choose the layout that best fits the content:

  LAYOUT OPTIONS (pick the best one for each concept):
  A) TOP title + MIDDLE visual + BOTTOM formula/text
     Best for: wide diagrams, process flows, timelines
  B) LEFT visual + RIGHT formula/text (each ~half width)
     Best for: a single diagram that needs explanation
  C) TOP title + FULL-WIDTH visual, then formula overlaid or below
     Best for: graphs with labels, network diagrams
  D) FULL-WIDTH formula slide (no visual)
     Best for: pure derivation steps with no diagram needed

  Example for "diffusion forward process":
    TOP: title  MIDDLE: row of images (noise -> clean)  BOTTOM: formula
  Example for "forces on sliding block":
    LEFT: block diagram  RIGHT: equations
  Example for "Punnett square":
    TOP: title  CENTER: 4x4 grid  BOTTOM: ratio summary

  KEY PRINCIPLE: never show a formula without context.  The student should
  see what the formula describes — either a visual next to it, or a clear
  text explanation of what each symbol means.

  Between major concepts: FadeOut everything, then build the next
  LEFT+RIGHT pair.

FINAL STEP — CONCLUSION (5-8 seconds):
  Summarize the key result with a highlighted box.
  Can be full-screen centered (no need for left/right split here).

IMPORTANT: The visual+formula side-by-side approach is what makes
animation BETTER than a textbook.  A student can read formulas anywhere —
what they need from YOUR animation is seeing the math CONNECTED to visuals.

═══════════════════════════════════════════════════════
PART 2: MANIM CODE RULES (avoid crashes and visual bugs)
═══════════════════════════════════════════════════════

LANGUAGE RULES:
- `from manim import *` at the top.  Exactly ONE Scene subclass.
- Chinese text: ALWAYS `Text("中文", font_size=...)`.
  NEVER `Tex(r"\\text{中文}")` or put Chinese inside MathTex.
- Pure math: `MathTex(r"...", font_size=...)`.
- MIXED Chinese + math: split into parts and use VGroup.arrange(RIGHT).
  Example: "这里 x_T 是纯噪声" should be written as:
    VGroup(
        Text("这里", font_size=20),
        MathTex(r"x_T", font_size=22),
        Text("是纯噪声", font_size=20),
    ).arrange(RIGHT, buff=0.1)
  NEVER write `Text("这里 x_T 是纯噪声")` — the subscript won't render!
  Any variable with subscripts/superscripts (x_t, alpha_bar, epsilon_theta)
  MUST use MathTex, not Text.

LAYOUT RULES (canvas is 14.2 x 8 units, safe area +/-6.0 x +/-3.3):
- ALWAYS group ALL elements of a "page" into one VGroup and THEN:
    group.arrange(DOWN, buff=0.3)  # or RIGHT for side-by-side
    if group.width > 12: group.scale_to_fit_width(12)
    if group.height > 6.5: group.scale_to_fit_height(6.5)
    group.move_to(ORIGIN)
  This guarantees nothing goes off-screen.
- For side-by-side: use VGroup(left, right).arrange(RIGHT, buff=0.5)
  then apply the same width/height check.
- For top-down: title at top, visual in middle, formula at bottom.
- BETWEEN CONCEPTS: FadeOut everything with `Group(*self.mobjects)`.
- Font sizes: titles 26-32, body 18-24, formulas 24-30, labels 16-20.
  SMALLER is better than clipped!  When in doubt, reduce font size.
- Never use `.to_edge(UP)` on its own — put the title inside the
  VGroup so it scales together with everything else.

ANIMATION RULES:
- Use `Write()` for formulas, `Create()` for shapes, `FadeIn(shift=DOWN*0.2)`
  for text, `GrowArrow()` for arrows.
- To fade out everything: use `Group(*self.mobjects)` NOT `VGroup(...)`.

VOICE NARRATION (audio-synced pacing):
- Your Scene class MUST inherit from NarratedScene (already available).
  Write: `class MyScene(NarratedScene):` instead of `class MyScene(Scene):`.
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
- Use SHORT sentences (15-30 Chinese characters per speak call).
- One speak() per visual "step" — don't narrate everything at once.
- For transitions (FadeOut), do NOT add narration — keep them silent and fast.
- MATCH narration length to animation complexity:
  If speak() returns 3 seconds but you only have a simple FadeIn, split it:
    dur = self.speak("...")
    self.play(FadeIn(element), run_time=min(dur, 1.5))
    self.wait(max(0, dur - 1.5))
  This avoids long freezes on simple animations.
- Section titles: narrate them!  Don't show a silent title.
  dur = self.speak("下面来看第二步")
  self.play(FadeIn(title), run_time=dur)

GRAPH ANNOTATION RULES:
- When labelling regions on a graph (e.g. shortage arrows between curves),
  place annotations ABOVE or BELOW the graph area, not overlapping curves.
- Use `.next_to(axes, DOWN)` or `.next_to(axes, UP)` for annotation text.
- Alternatively, put annotations in the RIGHT panel, not on the graph itself.

PACING RULES:
- Let the speak() duration drive the timing.  Do NOT add extra self.wait()
  after a speak-synced animation unless you need a deliberate pause.
- VISUAL ANIMATIONS: run_time = dur (from speak).
- TRANSITIONS: run_time=0.7, no speak, self.wait(0.3).
- KEY INSIGHTS: speak() + self.wait(dur) to let student absorb.

GEOMETRY RULES:
- Shapes on polygon edges MUST extend OUTWARD (check normal direction).
- Group all geometry → `scale_to_fit_width(10)` to prevent overflow.
- No two filled shapes should overlap.

Output ONLY the Python code inside a ```python``` block.
"""

_SYSTEM_FIX = """\
You are an expert Manim debugger.  The code below failed to render.
Fix ALL errors so it renders successfully.

STEP 1 — Before even reading the error log, scan the ENTIRE code for these
guaranteed-crash patterns and fix them ALL:
- Any Chinese character inside MathTex() or Tex() → CRASH.
  This includes `\\mathrm{中文}`, `\\text{中文}`, `\\mathrm{共同}`, etc.
  Fix: replace with `Text("中文", font_size=...)` and arrange with VGroup.
- `VGroup(*self.mobjects)` → may crash. Use `Group(*self.mobjects)`.
- Missing `from manim import *`.

STEP 2 — Read the error log and fix any remaining issues:
- Attribute errors → check Manim CE v0.18+ API.
- Type errors → check argument types.
- `get_tangent_line` does NOT accept `color` keyword. Create tangent manually:
    tangent = Line(start, end, color=GREEN)
- `unexpected keyword argument` → remove the bad kwarg or replace the method.

Preserve the original animation intent.
Output ONLY the corrected Python code inside a ```python``` block.
"""

_SYSTEM_IMPROVE = """\
You are an expert Manim quality engineer AND educational designer.
The code below rendered but the QA pipeline found problems.

I am showing you the code, the specific problems, AND keyframe screenshots.

═══════════════════════════════════════════════════════
RULE #1: PRESERVE the teaching structure!
═══════════════════════════════════════════════════════

The original code likely has a good educational arc (motivation → intuition →
calculation).  Your job is to FIX VISUAL BUGS while KEEPING the teaching flow.

Specifically, you MUST preserve:
- The Phase A motivation/roadmap (problem statement + "我们将看懂N件事" list)
- The Phase B visual intuition (diagrams, graphs, animations)
- The Phase C formula derivation steps
- The overall order and pacing

Do NOT simplify the teaching just to avoid overlap.  Instead, fix the overlap
by adjusting positions and sizes.

═══════════════════════════════════════════════════════
RULE #2: Fix visual bugs surgically
═══════════════════════════════════════════════════════

## "overlap" / "truncated" / "cut off":
- LOOK at the keyframe images — identify WHICH specific elements overflow.
- FIX only those elements: shrink them, reposition them, or add spacing.
- Group elements → scale_to_fit_height(6.5) → move_to(ORIGIN).
- FadeOut old elements before showing new ones in the same area.
- For physics diagrams where block sits ON board: make block thinner or use
  outline-only (fill_opacity=0) so the overlap is not flagged.

## "layout" / "dense":
- Break crowded sections into sub-stages with FadeOut between them.
- But keep the CONTENT the same — just spread it across more slides.

## "animation" / "motion":
- Add self.wait(0.3) between rapid animations, use longer run_time.

═══════════════════════════════════════════════════════
RULE #3: Never introduce new crashes
═══════════════════════════════════════════════════════

- Chinese text: ALWAYS `Text("中文")`.  NEVER inside MathTex or Tex.
  `\\mathrm{中文}`, `\\text{中文}` inside MathTex → instant LaTeX crash.
- To fade all: use `Group(*self.mobjects)`, NOT `VGroup(...)`.

Output ONLY the improved Python code in a ```python``` block.
"""


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
    """Translate evaluation metrics into concrete, actionable instructions."""
    lines: List[str] = []
    score = eval_report.get("overall_score", 0)
    lines.append(f"Overall score: {score:.2f} / 1.00 — "
                 f"{'PASS' if eval_report.get('overall_passed') else 'FAIL'}\n")

    lines.append("### Problem summary per dimension:\n")
    for dim in eval_report.get("dimensions", []):
        name = dim["name"]
        dscore = dim["score"]
        passed = dim.get("passed", False)
        details = dim.get("details", "")
        if passed:
            continue

        if name == "overlap":
            lines.append(
                f"**OVERLAP (score {dscore:.2f})**: {details}\n"
                "  → Elements are covering each other or extending off-screen.\n"
                "  → FIX: Scale down, add spacing, FadeOut before new elements.\n"
            )
        elif name == "layout":
            lines.append(
                f"**LAYOUT (score {dscore:.2f})**: {details}\n"
                "  → Screen is too crowded.\n"
                "  → FIX: Show fewer elements simultaneously, use FadeOut stages.\n"
            )
        elif name == "color_consistency":
            lines.append(
                f"**COLOR (score {dscore:.2f})**: {details}\n"
                "  → Abrupt colour jumps.\n"
                "  → FIX: Use fewer colours, gradual transitions.\n"
            )
        elif name == "animation":
            lines.append(
                f"**ANIMATION (score {dscore:.2f})**: {details}\n"
                "  → Motion is jerky.\n"
                "  → FIX: Add self.wait() between animations, longer run_time.\n"
            )
        elif name == "vlm_semantic":
            lines.append(
                f"**VLM JUDGMENT (score {dscore:.2f})**: {details}\n"
                "  → Vision model found visual problems.\n"
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


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class CodeGenAgent:
    """LLM-backed agent for Manim code generation / repair / improvement."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.tabcode.cc/openai",
        model: str = "gpt-5.4",
    ):
        self.model = model
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=180.0)

    def _call(self, system: str, user_content: list, max_retries: int = 3) -> str:
        full_content = [{"type": "input_text", "text": system}] + user_content
        import time as _time
        for attempt in range(max_retries):
            try:
                resp = self.client.responses.create(
                    model=self.model,
                    input=[{"role": "user", "content": full_content}],
                    stream=True,
                )
                text = ""
                start = _time.time()
                for event in resp:
                    if _time.time() - start > 180:
                        print("  Stream timeout (180s) — aborting this attempt")
                        break
                    if hasattr(event, "type") and event.type == "response.output_text.delta":
                        text += event.delta
                        start = _time.time()
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
    ) -> str:
        """Generate Manim code from a student request (text, optionally image)."""
        content: list = [{"type": "input_text", "text": request_text}]
        if image_path and image_path.exists():
            content.append({
                "type": "input_image",
                "image_url": _image_to_data_url(image_path),
            })
        raw = self._call(_SYSTEM_GENERATE, content)
        return _extract_code(raw)

    def fix(self, code: str, error_log: str) -> str:
        """Fix code that failed to render, given the error output."""
        content: list = [{
            "type": "input_text",
            "text": (
                f"## Original code\n```python\n{code}\n```\n\n"
                f"## Render error\n```\n{error_log[-3000:]}\n```"
            ),
        }]
        raw = self._call(_SYSTEM_FIX, content)
        return _extract_code(raw)

    def narrate(self, code: str, request_text: str) -> List[str]:
        """Generate a narration script (list of paragraphs) for the video."""
        content: list = [{
            "type": "input_text",
            "text": (
                f"## Student request\n{request_text}\n\n"
                f"## Manim code\n```python\n{code}\n```"
            ),
        }]
        system = (
            "You are a warm, clear Chinese-speaking teacher narrating an educational "
            "animation video.  Based on the Manim code and the student's question, "
            "write a narration script in Chinese.\n\n"
            "Rules:\n"
            "- Write 5-10 short paragraphs, one for each visual section.\n"
            "- Each paragraph should be 1-2 sentences (15-40 Chinese characters).\n"
            "- Match the pacing of the animation: brief for visual parts, detailed "
            "for formula/concept explanations.\n"
            "- Use conversational, encouraging tone (like talking to a student).\n"
            "- Do NOT include timestamps, stage directions, or code references.\n"
            "- Output ONLY a JSON array of strings, like:\n"
            '  ["第一段旁白", "第二段旁白", ...]\n'
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
    ) -> str:
        """Improve code based on evaluation feedback + optional keyframe images."""
        feedback = _build_actionable_feedback(eval_report)
        content: list = [{
            "type": "input_text",
            "text": (
                f"## Original code\n```python\n{code}\n```\n\n"
                f"## Evaluation feedback\n{feedback}"
            ),
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
