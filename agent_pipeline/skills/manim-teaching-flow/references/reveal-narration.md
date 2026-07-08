# Reveal / Narration Protocol

> Migrated verbatim from the former CodeGen prompt contract.

REVEAL / NARRATION PROTOCOL:
- Do NOT put all future text, formulas, arrows, labels, captions, examples,
  and conclusions on screen at the start of a section.
- For each page, define the persistent page objects before the first reveal of
  that page. Then reveal those objects beat by beat.
- A section may reserve stable final positions, but only the elements being
  discussed right now may be visible.
- Reveal each teaching beat in sync with narration: usually main visual or
  title first, then local labels, then formulas, then the takeaway.
- Do NOT reveal an entire page container such as `Group(title, bodyN)` at
  once. Reveal the page title and the body's internal teaching beats in order.
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
- Keep the subtitle-safe margin tight. Leave only a small visual buffer above
  the subtitle band; do not invent oversized empty bottom margins.
- Think like a teacher building the board live.
- At the start of a section, show only the minimum needed to begin the
  explanation.
- When narration says "now look at this label / this step / this formula",
  that specific object should appear at that beat, not earlier.
- Do NOT pre-place a full explanation panel if its lines will be explained one
  by one. Reveal those lines progressively.
- Do NOT pre-place the final formula before the intuition or derivation has
  happened.
- If a section has 3 teaching beats, implement 3 reveals, not one full-page
  reveal plus 3 repeated explanations.
- Page/layout helpers are for positioning and stable composition, not for
  dumping all content on screen at once.
- Use `dur = self.speak("...")` or `self.speak_with_subtitle(...)` to pace
  explanation beats, and call narration BEFORE or AT THE SAME TIME as the
  animation it describes.
- Use short narration chunks: roughly 6-16 English words or 15-30 Chinese
  characters per speak call.
- One speak() per visual step. Do not narrate everything at once.
- For transitions such as `FadeOut`, keep them silent and fast unless the
  teaching goal truly needs narrated emphasis.
- Let narration duration drive pacing; do NOT add extra `self.wait()` after a
  speak-synced animation unless you need a deliberate pause.
- If a speak call is longer than the simple animation it describes, split the
  beat so the scene does not freeze on a trivial visual.
- Section titles should be narrated rather than appearing as silent cards.
