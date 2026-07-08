# Coordinate System Skill

Use inside `LessonBase(AI4LearningBaseScene)` for axes, graphs, coordinate transforms, geometry, optimization trajectories, tangents, secants, shaded regions, and point projections.

## When to use

- Use axes when the relation is numeric, functional, spatial, or changing over a parameter.
- Use a clean base graph first, then reveal moving points, projections, tangents, or areas.
- Use anchor builders for dots, labels, tangent lines, secants, and shaded regions.
- Use explanatory panels outside the axes for anything sentence-like.

## Aesthetic rule

Keep the coordinate plane quiet. The graph should do the reasoning, while text outside the axes names the idea. Use theme helpers and no hardcoded colors.

## Safety rule

Axes and primary curves are structural children of `graph_block`. Dependent overlays must either be structural children before `fit_body` or be created through `build_on_anchor` from the fitted axes/curve.

## Anti-patterns

- Do not call `axes.get_grid()`.
- Do not place long explanations inside axes.
- Do not rebuild a second axes object after `fit_body` to obtain a new tangent, dot, or label.
- Do not use Scene-bound callback methods inside `axes.plot`.

## Snippet

```python
@staticmethod
def sample_curve(x):
    return 0.25 * (x - 1.0) ** 2 + 0.5


def build_point_on_axes(self, axes, x_value):
    y_value = self.sample_curve(x_value)
    return Dot(axes.c2p(x_value, y_value), radius=0.06, color=self.get_accent_color("primary"))


def build_projection_on_axes(self, axes, x_value):
    y_value = self.sample_curve(x_value)
    point = axes.c2p(x_value, y_value)
    base = axes.c2p(x_value, 0)
    return DashedLine(base, point, color=self.get_axis_color(), stroke_width=2)


def section_coordinate_example(self):
    axes = Axes(
        x_range=[-1, 4, 1],
        y_range=[0, 4, 1],
        x_length=5.4,
        y_length=3.4,
        axis_config={"color": self.get_axis_color(), "stroke_width": 2},
    )
    curve = axes.plot(lambda x: 0.25 * (x - 1.0) ** 2 + 0.5, x_range=[-1, 4], color=self.get_accent_color("secondary"))
    graph_block = Group(axes, curve)
    point = self.build_on_anchor("build_point_on_axes", axes, 2.5)
    projection = self.build_on_anchor("build_projection_on_axes", axes, 2.5)
    note = self.make_panel(self.get_text("The point reads the curve at one input.", font_size=22), padding=0.18)
    body1 = Group(Group(graph_block, point, projection), note).arrange(RIGHT, buff=0.55, aligned_edge=UP)
    self.fit_body(body1, max_width=11.6, center=UP * 0.15)
    self.play(Create(axes), Create(curve), run_time=0.75)
    self.play(FadeIn(point), Create(projection), FadeIn(note), run_time=0.6)
```

