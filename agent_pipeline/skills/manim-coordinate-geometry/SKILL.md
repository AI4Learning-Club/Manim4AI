---
name: manim-coordinate-geometry
description: Coordinate, graph, and anchor lifecycle guidance for Manim. Use for axes, plots, functions, derivatives, integrals, slopes, coordinate labels, anchor-bound followers, drifting geometry, and build_on_anchor patterns.
---

# Manim Coordinate Geometry

Use this skill for visuals whose meaning depends on coordinates, graphs, geometric anchors, or fitted visual blocks.

- Keep axes, curves, dots, tangents, shaded regions, and coordinate labels in the same lifecycle as their owner block.
- Treat dependent geometry as anchor followers.
- Prefer structural ownership inside the fitted visual block or `self.build_on_anchor(...)` for persistent detached leaves.
- Do not compute followers from stale pre-fit coordinates.
- Load:
  - `references/coordinate-systems.md`
  - `references/graph-dynamics.md`
  - `references/anchor-lifecycle.md`
