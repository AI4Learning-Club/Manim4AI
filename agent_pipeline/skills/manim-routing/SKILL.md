---
name: manim-routing
description: Hybrid route handling for Manim CodeGen. Use when upstream routing selected specific Manim section ids and the code generator must produce only those routed sections while preserving lesson continuity.
---

# Manim Routing

Use this skill only when `hybrid_routes.manim_section_ids` is present.

- Generate Manim content for the routed section ids.
- Preserve section order from the teaching plan.
- Do not invent extra routed sections.
- Load `references/routing.md` for route-specific behavior.
