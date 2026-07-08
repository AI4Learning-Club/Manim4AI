# Callback / Deepcopy Safety

> Migrated verbatim from the former CodeGen prompt contract.

CALLBACK / DEEPCOPY SAFETY:
- Never pass a bound Scene method such as `self._position_func` or
  `self.some_helper` into Manim objects that may store callbacks and later get
  copied, including `axes.plot(...)`, `FunctionGraph(...)`,
  `ParametricFunction(...)`, `always_redraw(...)`, and updater callbacks.
- In particular, do NOT write `axes.plot(self.func, ...)`, `axes.plot(self.some_curve, ...)`,
  or helper methods that return `axes.plot(...)` from a lambda/inner function
  that still closes over `self`. Those patterns later trigger deep-copy /
  pickle failures in Manim.
- Avoid callbacks or lambdas that capture `self` when those callbacks are stored
  on mobjects, graphs, or animations.
- Bad pattern: `axes.plot(self._position_func, ...)`.
- Bad pattern: `always_redraw(lambda: Dot(self.axes.c2p(...)))`.
- Bad pattern: `mob.add_updater(lambda m: m.move_to(self.some_anchor(...)))`.
- Prefer a module-level function, a `@staticmethod`, or a local pure function
  that depends only on plain numeric values, not on `self`.
- If you only need a static curve, compute it from a pure function and build
  the mobject once. Do not keep a Scene-bound callback attached to the mobject.
- If you truly need dynamic redraw behavior, the callback must avoid capturing
  `self`; capture only stable numeric parameters or already-built anchor
  mobjects, and rebuild from those.
- Any callback stored on a mobject must remain deep-copy-safe. Never let it
  close over the live `Scene`, renderer, audio client, locks, threads, or
  other non-picklable runtime state.

STATE / CLEARING SAFETY:
- If the scene inherits from `AI4LearningBaseScene`, prefer
  `self.clear_scene_keep_bg()` over `FadeOut(Group(*self.mobjects))` so the
  persistent background is not removed.
- Use the available `AI4LearningBaseScene` layout helpers before stacking many
  manual `.shift()` / `.to_edge()` calls.
- Use `self.make_page_title(...)`, `self.show_page_title_chip(...)`, and
  `self.fit_body(...)`.
