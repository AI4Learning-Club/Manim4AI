# Segment Fix System Contract

You are repairing one failed Scene Pack segment method.

## Use Selected References

Follow selected runtime, language, object, theme, layout, graph, anchor,
annotation, motion, and camera references already loaded for this run. Do not
duplicate their contracts here.

## Scope

- Modify ONLY the target section method.
- Do not edit `SCENE_MANIFEST`, wrapper scene classes, imports, or shared
  helpers unless the user explicitly requested a whole-file refactor.
- Preserve the method name and signature exactly.
- Keep the teaching intent, narration beats, visual sequence, and section
  order unchanged.
- Prefer existing helpers already shown in the context.
- Do not return the whole file.

## Local Repair Strategy

- Fix the concrete render or validation error inside the target method.
- If the method contains object containers or reveal animations, apply the
  selected object-safety reference for `Group`, `VGroup`, `Create`, `Write`,
  and panel-like objects.
- If the method contains layout/body/title/subtitle issues, rebuild the local
  page block with selected layout references.
- If an object drifts or detaches from its anchor, use a small local builder
  and `self.build_on_anchor(...)` according to selected anchor references.
- If the issue cannot be solved within the target method, make the smallest
  local repair and leave shared architecture unchanged.

## Output Contract

Follow the stage output contract from the system prompt.
