# Density / Overlap Skill

- Visual clarity beats maximal completeness.
- Use readable font floors: titles at least 28, body text at least 20, secondary text at least 18, formulas at least 24, symbolic labels at least 16.
- If a page cannot fit without violating font floors, split or simplify it.
- Direct children of `bodyN` must have visible spacing after arrangement.
- Use `arrange(..., buff>=0.14)` for page-level blocks.
- Fold loose notes, prompts, and takeaways back into the planned body layout.
- Do not patch layout with arbitrary `.shift(...)` nudges.
- For overlap diagnostics, rebuild the affected block composition and call one `fit_body`.
- Use more of the body band before deciding content is impossible to fit.
