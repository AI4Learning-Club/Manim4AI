# Motion / Transition / Voice Timing Rules

This file owns animation choices, transition pacing, and pointer-style annotations. Progressive reveal semantics are owned by `reveal-narration.md`.

ANIMATION / STABILITY / REVEAL RULES:

- Use animation to teach change, comparison, buildup, or consequence; avoid decorative motion.
- Prefer `Write()` for formulas, `Create()` for shapes, `FadeIn(shift=DOWN*0.2)` for text, and `GrowArrow()` for arrows unless a stronger transition is explanatory.
- Each major section should have multiple meaningful visual beats, not one static page with narration pasted on top.
- Once a page layout appears, keep its title, panels, axes, and body slots fixed.
- Reveal new information in place; do not drag whole page groups around.
- Define page objects before the first reveal of that page, then reveal internal elements in narration order.
- Do not pre-place late explanation panels, final formulas, or takeaways if they should only appear after their teaching beat.

VOICE NARRATION (audio-synced pacing):

- New code must follow the selected Scene Pack contract.
- Renderable wrapper scenes should inherit from `LessonBase`, and each wrapper `construct()` should call its one section method.
- Keep TTS calls extractable: use direct string-literal calls in the section method, not helper wrappers like `self.narrate(...)`, `self.say(...)`, or `self._say(...)`.
- Let `self.speak(...)` or `self.speak_with_subtitle(...)` return the duration that drives the matching animation:
```python
dur = self.speak("short narration")
self.play(Create(graph), run_time=dur)
```
- For multiple animations in one narrated beat, split the returned duration across those animations.
- For a simple animation with a long narration, play the simple animation quickly and wait for the remaining narration:
```python
dur = self.speak("short narration")
self.play(FadeIn(element), run_time=min(dur, 1.5))
self.wait(max(0, dur - 1.5))
```
- Call narration before or at the same time as the animation it describes.
- Use short narration chunks: roughly 6-16 English words or 15-30 Chinese characters per call.
- Use one speak call per visual step.
- Prefer `self.speak_with_subtitle(...)` during explanation beats so subtitle, narration, and animation stay synchronized.
- Narrate section titles instead of showing silent title cards.
- Keep pure transitions such as `FadeOut` short and usually silent.

GRAPH ANNOTATION RULES:

- Every line, arrow, brace, or pointer must target a real visual object with a stable anchor point. If it cannot be anchored cleanly, omit it.
- Prefer no arrow over a misleading or crowded arrow.
- Prefer short nearby arrows. Avoid long cross-screen arrows, diagonal arrows through crowded regions, and arrows that pass over text.
- In left/right layouts, keep arrows inside one panel; do not cross the gutter between visual and explanation regions.
- Use `self.connect_side(...)` or `self.connect_vertical(...)` for pointer-style arrows instead of hand-written approximate coordinates when possible.
- Arrow labels must sit next to their arrow and must not overlap the shaft, target, or other labels.
- Straight connector lines must anchor to object edges.
- On graphs, place region annotations above or below the graph area, or move sentence-level explanation into the side panel.
- Keep graph labels minimal (`D_1`, `S_1`, `E_2`, etc.) and fully inside the graph region.
- When showing cause/effect on a graph, animate one change at a time: base graph, changed curve or point, then explanation text.
- If a pointer would make the page crowded or ambiguous, split the explanation into a follow-up page.

PACING RULES:

- Let narration duration drive timing.
- Keep non-teaching transitions short and mostly silent.
- Give key insight beats enough time to read and hear clearly.
- Do not add extra waits after a speak-synced animation unless the pause itself has teaching value.
