# Graph Dynamics Skill

> Restored verbatim from the former math-to-manim visual-pattern reference.

# Graph Dynamics Skill

Use inside `LessonBase` section methods for dynamic function and physics visuals: moving points, secant-to-tangent transitions, parameter sliders, accumulated area, trajectory traces, and function-family changes.

## When to use

- Use for derivatives, integrals, optimization, motion, and parameterized functions.
- Show the stable axes and baseline curve first.
- Add one dynamic layer at a time: point, projection, secant, tangent, shaded area, or trace.
- Keep explanatory text outside the axes.

## Aesthetic rule

The graph should be the proof. Keep axes quiet, motion slow enough to track, and labels local. Use theme helpers and no hardcoded colors.

## Safety rule

Use structural ownership or `build_on_anchor` for every graph-dependent overlay. Do not rebuild a second axes/curve after `fit_body` to obtain new dots, tangents, or shaded regions.

## Anti-patterns

- Do not place long sentences inside the coordinate system.
- Do not pre-place every dynamic overlay before the student knows what to watch.
- Do not use Scene-bound methods as plot callbacks.
- Do not call `axes.get_grid()`.

## Snippet

```python
@staticmethod
def derivative_curve(x):
    return 0.25 * (x - 1.0) ** 2 + 0.5


def build_dot_on_curve(self, axes, x_value):
    return Dot(
        axes.c2p(x_value, self.derivative_curve(x_value)),
        radius=0.06,
        color=self.get_accent_color("primary"),
    )


def build_secant_on_curve(self, axes, x_left, x_right):
    left = axes.c2p(x_left, self.derivative_curve(x_left))
    right = axes.c2p(x_right, self.derivative_curve(x_right))
    return Line(left, right, color=self.get_formula_highlight_color("secondary"), stroke_width=3)


def build_tangent_on_curve(self, axes, x_value):
    y_value = self.derivative_curve(x_value)
    slope = 0.5 * (x_value - 1.0)
    x_span = [x_value - 0.9, x_value + 0.9]
    return axes.plot(
        lambda x: y_value + slope * (x - x_value),
        x_range=x_span,
        color=self.get_formula_highlight_color("primary"),
    )


def section_graph_dynamics_example(self):
    title = self.make_page_title("割线靠近，切线出现", font_size=30)
    axes = Axes(
        x_range=[-1, 4, 1],
        y_range=[0, 4, 1],
        x_length=5.4,
        y_length=3.4,
        axis_config={"color": self.get_axis_color(), "stroke_width": 2},
    )
    curve = axes.plot(lambda x: 0.25 * (x - 1.0) ** 2 + 0.5, x_range=[-1, 4], color=self.get_accent_color("secondary"))
    graph_block = Group(axes, curve)
    fixed_dot = self.build_on_anchor("build_dot_on_curve", axes, 2.0)
    moving_dot = self.build_on_anchor("build_dot_on_curve", axes, 3.4)
    secant = self.build_on_anchor("build_secant_on_curve", axes, 2.0, 3.4)
    tangent = self.build_on_anchor("build_tangent_on_curve", axes, 2.0)
    note = self.make_panel(self.get_text("Q 越靠近 P，割线越像切线。", font_size=22), padding=0.18)
    body1 = Group(Group(graph_block, fixed_dot, moving_dot, secant, tangent), note).arrange(RIGHT, buff=0.55, aligned_edge=UP)
    self.fit_body(body1, max_width=11.6, center=UP * 0.1)
    self.add(title)
    self.play(Create(axes), Create(curve), run_time=0.8)
    self.play(FadeIn(fixed_dot), FadeIn(moving_dot), Create(secant), run_time=0.65)
    self.play(FadeOut(secant), FadeOut(moving_dot), Create(tangent), FadeIn(note), run_time=0.85)
```
