# Reveal / Narration Protocol

REVEAL / NARRATION PROTOCOL:

This file owns progressive reveal, narration chunking, and subtitle alignment. Motion timing details are owned by `motion-transitions.md`; page/body placement is owned by `page-body-layout.md`.

## Reveal Order

- Do not put future text, formulas, arrows, labels, captions, examples, and conclusions on screen at the start of a section.
- For each page, define persistent page objects before the first reveal, then reveal those objects beat by beat.
- A section may reserve stable final positions, but only the elements being discussed right now should be visible.
- Reveal in teaching order: usually page title or main visual first, then local labels, formulas, explanation lines, and takeaway.
- Do not reveal an entire page/layout container such as `Group(title, bodyN)` at once.
- Avoid `self.play(FadeIn(page))`, `self.play(Write(page))`, `self.play(Create(page))`, or `self.speak_with_subtitle(..., FadeIn(page))` when `page` is a layout container.
- If an object is already visible, do not show it again. Highlight it, transform it, or add only the new local element.
- If a later persistent element would change the current page structure, start a new page instead of repacking the old one.

## Narration And Subtitles

- Prefer `self.speak_with_subtitle(...)` for explanation beats.
- TTS extraction contract: every spoken beat must directly call `self.speak_with_subtitle("literal text", ...)` or `self.speak("literal text", ...)` inside the section method.
- Do not wrap TTS in helper methods such as `narrate`, `say`, `_say`, or `voiceover`; do not pass variables, f-strings, constants, or helper-returned strings as the first argument.
- Subtitle text must match the spoken TTS content for that beat; do not paraphrase it into different wording.
- Use one natural clause per narration/subtitle beat when possible.
- Keep subtitles single-line when possible. If narration is too long, split it into multiple beats instead of forcing dense multi-line subtitles.
- Call narration before or at the same time as the animation it describes.
- Use short narration chunks: roughly 6-16 English words or 15-30 Chinese characters per call.
- Use one speak call per visual step; do not narrate everything at once.
- Section titles should be narrated rather than appearing as silent cards.
- Let narration duration drive pacing; do not add extra waits after speak-synced animation unless the pause has teaching value.

## Subtitle Band

- Keep a bottom subtitle module during explanation-heavy beats.
- Nothing except the subtitle module may occupy the subtitle band.
- Keep the subtitle margin tight; do not invent oversized empty bottom margins.
- Subtitle changes should be visually quiet: simple fade-in/fade-out only, no flashy transforms, sliding subtitles, or morphing text.
- Clear subtitles before dense transitions if needed.

## Teaching Rhythm

- Build the board like a teacher in real time, not like a finished slide that gets explained afterward.
- At the start of a section, show only the minimum needed to begin the explanation.
- When narration says "now look at this label / this step / this formula", that specific object should appear at that beat, not earlier.
- Do not pre-place a full explanation panel if its lines will be explained one by one.
- Do not pre-place the final formula before the intuition or derivation has happened.
- If a section has three teaching beats, implement three reveals, not one full-page reveal plus repeated explanations.
- For transitions such as `FadeOut`, keep them silent and fast unless the teaching goal truly needs narrated emphasis.
