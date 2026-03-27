"""LLM-assisted pre-render structural checks for generated Manim scenes."""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List

from .llm import LLMClient, LLMConfig

_SYSTEM_CODE_EVAL = """\
You are a strict pre-render code evaluator for AI4Learning Manim scenes.

Review the Python code ONLY against these three rules:

1. `body_membership_post_fit`
- After `self.fit_body(bodyN, ...)`, any newly created persistent sentence-like
  teaching object must already belong to that page's fitted `bodyN`.
- Persistent sentence-like objects include note panels, takeaway lines, prompt
  blocks, summary lines, warning text, success text, explanatory text, and
  other natural-language teaching text that is meant to stay visible on that
  page.
- If such an object is created after `fit_body(bodyN, ...)` and then positioned
  with `next_to(...)`, `align_to(...)`, `move_to(...)`, or otherwise shown on
  screen without being part of `bodyN`, report an `error`.

2. `symbolic_label_overlap_risk`
- A symbolic label is a VERY short local label such as `A`, `B`, `x`, `y`,
  `q1`, `T`, `pi`, or similarly short identifiers.
- Symbolic labels may use `next_to(...)` to stay near a graphic.
- If a local label is clearly sentence-like or multi-word, it is NOT a
  symbolic label and should not be treated as one.
- For true symbolic labels, make a rough overlap-risk judgement from the code.
  If the placement is likely awkward, crowded, or overlapping nearby objects,
  report a `warning`, not an `error`.

3. `non_text_anchor_lifecycle`
- For any non-text geometric object whose location is supposed to relate to
  another visual structure, its final position must be determined by an
  explicit anchor or coordinate system, regardless of whether it is created
  before or after `fit_body(...)`.
- Examples: arrows, braces, highlights, circles, connector lines, helper lines,
  threshold lines, stars, dots, point rows, bars, rings, icons, rectangles,
  and replacement / transform targets.
- Good anchor patterns include `axes.c2p(...)`, `graph.point_from_proportion(...)`,
  `obj.get_center()`, `obj.get_right()`, `obj.get_corner(...)`,
  `next_to(anchor, ...)`, `move_to(anchor)`, or a helper that consumes the real
  on-screen anchor instance and returns geometry for that exact anchor.
- Anchor existence is NOT enough by itself. The non-text object must also share
  the same positioning lifecycle as its anchor.
- If a non-text object is created before `fit_body(...)` from an anchor that
  later gets fitted or moved, that object should usually be included in the
  same fitted visual block, or rebuilt after fitting, or made to follow the
  anchor dynamically.
- Report an `error` when geometry is created from an anchor before
  `fit_body(...)`, but the geometry itself is not part of the fitted block and
  therefore will be left behind when the anchor moves.
- If a new target object is going to be used in `Transform(...)`,
  `ReplacementTransform(...)`, or a similar replacement animation, its final
  position must be fully anchored to an existing object or coordinate system.
- A full anchor usually means `move_to(existing_object)` or an equivalent
  complete placement that fixes both axes from the intended anchor.
- A single-axis adjustment such as only `align_to(..., LEFT)`, only
  `align_to(..., UP)`, only `match_x(...)`, or only `match_y(...)` is NOT
  enough unless the remaining axis is also clearly fixed by the same anchor
  setup.
- If dots, point rows, markers, or threshold guides are meant to live on an
  axes / graph / number line, they should be derived from that SAME axes /
  graph / number line anchor, not from ad-hoc floating coordinates.
- If the code appears to rebuild a fresh axes/graph/block/helper copy after
  `fit_body(...)` and then uses that stale copy to position geometry, report an
  `error`.
- If a line, dot, point row, marker, threshold guide, or icon is created from
  `axes.c2p(...)`, `axes.plot(...)`, `graph.point_from_proportion(...)`, or a
  similar anchor expression before the page is fitted, but that geometry is not
  placed inside the same `graph_block` / `bodyN` that later gets fitted, report
  an `error`.
- If a non-text object is only partially anchored or appears to float away from
  the intended visual structure, report an `error`.

Important evaluation discipline:
- Focus ONLY on these three rules.
- Do NOT invent issues just because you would prefer a different layout.
- If uncertain, do not flag an issue.
- Use `error` only for high-confidence rule violations.
- Use `warning` only for the symbolic-label overlap-risk check.
- Return JSON ONLY, no markdown.

Output schema:
{
  "passed": true,
  "summary": "short summary",
  "issues": [
    {
      "rule_id": "body_membership_post_fit | symbolic_label_overlap_risk | non_text_anchor_lifecycle",
      "severity": "error | warning",
      "body_name": "bodyN or empty string",
      "object_name": "variable name or empty string",
      "line": 123,
      "end_line": 124,
      "message": "what is wrong",
      "evidence": "brief code evidence",
      "fix_hint": "specific minimal fix"
    }
  ]
}
"""


def _extract_json_object(text: str) -> Dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    left = cleaned.find("{")
    right = cleaned.rfind("}")
    if left < 0 or right <= left:
        raise ValueError("No JSON object found in code_eval response")
    return json.loads(cleaned[left : right + 1])


def _normalize_issue(issue: Dict[str, Any]) -> Dict[str, Any]:
    severity = str(issue.get("severity", "error")).strip().lower()
    if severity not in {"error", "warning"}:
        severity = "error"
    rule_id = str(issue.get("rule_id", "")).strip()
    if rule_id not in {
        "body_membership_post_fit",
        "symbolic_label_overlap_risk",
        "non_text_anchor_lifecycle",
        "non_text_anchor_binding",
        "dependent_overlay_anchor_binding",
    }:
        rule_id = "body_membership_post_fit"
    if rule_id == "non_text_anchor_binding":
        rule_id = "non_text_anchor_lifecycle"
    if rule_id == "dependent_overlay_anchor_binding":
        rule_id = "non_text_anchor_lifecycle"
    return {
        "rule_id": rule_id,
        "severity": severity,
        "body_name": str(issue.get("body_name", "")).strip(),
        "object_name": str(issue.get("object_name", "")).strip(),
        "line": issue.get("line"),
        "end_line": issue.get("end_line"),
        "message": str(issue.get("message", "")).strip(),
        "evidence": str(issue.get("evidence", "")).strip(),
        "fix_hint": str(issue.get("fix_hint", "")).strip(),
    }


def _normalize_report(data: Dict[str, Any]) -> Dict[str, Any]:
    issues_raw = data.get("issues") if isinstance(data.get("issues"), list) else []
    issues = [_normalize_issue(issue) for issue in issues_raw if isinstance(issue, dict)]
    passed = bool(data.get("passed", not issues))
    if issues:
        passed = False
    return {
        "passed": passed,
        "summary": str(data.get("summary", "")).strip(),
        "issues": issues,
    }


class CodeEvalAgent:
    """LLM-backed structural scene checker used before render."""

    def __init__(
        self,
        api_key: str | LLMConfig,
        base_url: str = "https://api2.tabcode.cc/openai",
        model: str = "gpt-4o",
    ):
        if isinstance(api_key, LLMConfig):
            llm_config = api_key
            self.client = LLMClient(llm_config)
        else:
            self.client = LLMClient(
                LLMConfig(
                    stage="code_eval",
                    model=model,
                    api_key=api_key,
                    base_url=base_url,
                )
            )

    def _call(self, system: str, user_content: List[Dict[str, Any]], max_retries: int = 3) -> str:
        for attempt in range(max_retries):
            try:
                text = self.client.generate_text(system, user_content, max_retries=1)
                if text.strip():
                    return text.strip()
                raise TimeoutError("Empty response from API")
            except Exception:
                if attempt >= max_retries - 1:
                    raise
                time.sleep(5 * (attempt + 1))
        raise RuntimeError("LLM call failed")

    def review(self, code: str) -> Dict[str, Any]:
        raw = self._call(
            _SYSTEM_CODE_EVAL,
            [{
                "type": "input_text",
                "text": f"## Scene code\n```python\n{code}\n```",
            }],
        )
        return _normalize_report(_extract_json_object(raw))
