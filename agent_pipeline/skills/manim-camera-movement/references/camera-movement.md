# Camera Movement Skill

Use inside `LessonBase(AI4LearningBaseScene, MovingCameraScene)` when a lesson needs zoom, pan, viewport focus, magnification, close-up inspection, or a camera move that follows a graph or geometric idea.

## When to use

- Use camera movement to move from the whole page to one local object, term, point, graph region, or geometric relation.
- Use zoom-in for a short inspection beat, then restore the full view before the next major idea.
- Use pan only when the viewer should compare two nearby graph regions or follow a motion path.
- Use camera focus after the page has a stable `bodyN` and after `fit_body(...)` has fixed the layout.

## Aesthetic rule

Treat the camera as the teacher's gaze. One camera move should serve one teaching intent. Prefer slow, readable moves with a clear before/after state. Use theme helpers and no hardcoded colors.

## Safety rule

Use `class LessonBase(AI4LearningBaseScene, MovingCameraScene):` and keep the inheritance order exactly this way. Build the page normally first, call one `fit_body(...)`, then use `frame = self.camera.frame`, `frame.save_state()`, `frame.animate...`, and `Restore(frame)`. Any point, label, or highlight tied to fitted geometry should still use structural ownership or `build_on_anchor(...)`.

## Anti-patterns

- Do not use rapid repeated zooms or zoom/pan loops.
- Do not move the camera while a subtitle is being introduced or replaced.
- Do not use camera movement to hide crowded layout; split or simplify the page instead.
- Do not leave the camera zoomed in across unrelated teaching beats.
- Do not hardcode colors, stroke colors, text colors, or panel colors.

## Snippet

```python
def build_focus_box_on_object(self, target):
    return SurroundingRectangle(
        target,
        buff=0.08,
        corner_radius=0.08,
        stroke_width=3,
        color=self.get_formula_highlight_color("primary"),
        fill_opacity=0,
    )


def section_camera_zoom_example(self):
    title = self.make_page_title("Zoom from the full formula to one term", font_size=30)
    formula = self.get_math(r"f'(x)=\lim_{h\to 0}\frac{f(x+h)-f(x)}{h}", font_size=34)
    note = self.make_panel(
        self.get_secondary_text("The denominator controls the shrinking interval.", font_size=20),
        padding=0.18,
    )
    body1 = Group(formula, note).arrange(DOWN, buff=0.32)
    self.fit_body(body1, max_width=10.8, center=UP * 0.08)

    focus_box = self.build_on_anchor("build_focus_box_on_object", formula)
    body1.add(focus_box)

    self.add(title)
    self.play(Write(formula), run_time=0.7)
    self.play(Create(focus_box), FadeIn(note), run_time=0.55)

    frame = self.camera.frame
    frame.save_state()
    self.play(
        frame.animate.set(width=formula.width * 1.25).move_to(formula.get_center()),
        run_time=0.8,
    )
    self.play(Circumscribe(formula), run_time=0.45)
    self.play(Restore(frame), run_time=0.75)
```

```python
@staticmethod
def camera_curve(x):
    return 0.25 * (x - 1.0) ** 2 + 0.4


def build_camera_dot_on_curve(self, axes, x_value):
    return Dot(
        axes.c2p(x_value, self.camera_curve(x_value)),
        radius=0.055,
        color=self.get_accent_color("primary"),
    )


def section_camera_pan_graph_example(self):
    title = self.make_page_title("Pan along the curve after the full view is clear", font_size=29)
    axes = Axes(
        x_range=[-1, 4, 1],
        y_range=[0, 4, 1],
        x_length=5.8,
        y_length=3.5,
        axis_config={"color": self.get_axis_color(), "stroke_width": 2},
    )
    curve = axes.plot(
        lambda x: 0.25 * (x - 1.0) ** 2 + 0.4,
        x_range=[-1, 4],
        color=self.get_accent_color("secondary"),
    )
    graph_block = Group(axes, curve)
    left_dot = self.build_on_anchor("build_camera_dot_on_curve", axes, 0.2)
    right_dot = self.build_on_anchor("build_camera_dot_on_curve", axes, 3.2)
    explanation = self.make_panel(
        self.get_secondary_text("The camera follows the local change, then returns.", font_size=20),
        padding=0.18,
    )
    body1 = Group(Group(graph_block, left_dot, right_dot), explanation).arrange(
        RIGHT,
        buff=0.5,
        aligned_edge=UP,
    )
    self.fit_body(body1, max_width=11.5, center=UP * 0.05)

    self.add(title)
    self.play(Create(axes), Create(curve), run_time=0.85)
    self.play(FadeIn(left_dot), FadeIn(right_dot), FadeIn(explanation), run_time=0.55)

    frame = self.camera.frame
    frame.save_state()
    self.play(
        frame.animate.set(width=graph_block.width * 0.55).move_to(left_dot.get_center()),
        run_time=0.8,
    )
    self.play(frame.animate.move_to(right_dot.get_center()), run_time=1.0)
    self.play(Restore(frame), run_time=0.75)
```
