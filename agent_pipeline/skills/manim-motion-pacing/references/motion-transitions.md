# Motion / Transition / Voice Timing Rules

> Migrated verbatim from the former CodeGen prompt contract.

ANIMATION / STABILITY / REVEAL RULES:
- Use animation to teach change, comparison, buildup, or consequence; avoid
  decorative motion.
- Prefer `Write()` for formulas, `Create()` for shapes, `FadeIn(shift=DOWN*0.2)`
  for text, and `GrowArrow()` for arrows unless a stronger transition is truly
  explanatory.
- Each major section should have multiple meaningful visual beats, not one
  static page with narration pasted on top.
- Once a page layout appears, keep its title, panels, and axes fixed in place.
- Reveal new information in place; do not drag whole page groups around.
- Define page objects before the first reveal of that page, then reveal them in
  the order the narration needs.
- Do not pre-place late explanation panels, final formulas, or takeaways if
  they should only appear after the relevant teaching beat.

VOICE NARRATION (audio-synced pacing):
- New code MUST follow the Scene Pack contract.
- Use the `LessonBase` inheritance required by the selected Scene Pack/runtime
  skills, preserving any mixins required by selected skills.
- Renderable wrapper scenes must inherit from `LessonBase`, and each wrapper
  `construct()` should simply call its one section method.
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
- Let `self.speak()` / `self.speak_with_subtitle(...)` drive timing.
- Keep transitions short and mostly silent.
- Let key insight beats breathe long enough to read and hear clearly.
