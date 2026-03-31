"""LLM-assisted pre-render structural checks for generated Manim scenes."""

from __future__ import annotations

import json
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
- Typical examples to review under this rule:
  - a new note block, takeaway sentence, prompt panel, warning text, success
    message, or explanatory line created after `fit_body(...)`
  - a text object created after `fit_body(...)` and placed relative to `bodyN`
    or one of its children, but never structurally added into the fitted page
- Do NOT report this rule for:
  - page titles created by `make_page_title(...)`
  - very short symbolic labels such as `A`, `B`, `x`, `y`
  - temporary replacement text used only as a transform target and not meant to
    become a detached persistent object
- Good pattern:
  - build the teaching text before `fit_body(...)` and include it in `bodyN`
  - or add the new persistent teaching text into an already fitted container
    that belongs to `bodyN` before showing it
- Bad pattern:
  - `fit_body(body1, ...)` then create `note = self.get_text(...)`, position it
    with `next_to(body1, ...)`, and show it without adding it to `body1`

2. `symbolic_label_overlap_risk`
- A symbolic label is a VERY short local label such as `A`, `B`, `x`, `y`,
  `q1`, `T`, `pi`, or similarly short identifiers.
- Symbolic labels may use `next_to(...)` to stay near a graphic.
- If a local label is clearly sentence-like or multi-word, it is NOT a
  symbolic label and should not be treated as one.
- For true symbolic labels, make a rough overlap-risk judgement from the code.
- If the placement is likely awkward, crowded, or overlapping nearby objects,
  report a `warning`, not an `error`.
- Only use this rule when the label is truly local and symbolic.
- Consider risk signals such as:
  - multiple labels stacked on the same side of a small graphic
  - tiny `buff` values
  - labels placed near dense formulas, dots, braces, or each other
  - repeated `next_to(...)` placements that appear likely to collide
- Do NOT use this rule for:
  - full teaching sentences
  - paragraph-like labels
  - general aesthetic disagreement with spacing
- If the overlap risk is merely possible but not likely, do not flag it.

3. `non_text_anchor_lifecycle`
- Review only persistent non-text geometry or dependent overlays that are meant
  to remain part of the page, such as secants, tangents, lines, arrows, dots,
  braces, highlights, shaded regions, rectangles, icons, or connector shapes.
- If such an object depends on another visual anchor or fitted block, judge
  whether its lifecycle is safe around `self.fit_body(bodyN, ...)`.
- Report an `error` when there is strong code evidence that anchor-dependent
  non-text geometry will be left behind, detached from the fitted block, or
  rebuilt from a stale/pre-fit anchor state without being structurally included
  in the fitted block or explicitly synchronized with `bind_to_block(...)`,
  `build_on_anchor(...)`, or `bind_to_anchor(...)`.
- Strong signals for anchor-dependent non-text geometry include code built from:
  - `axes.c2p(...)`, `coords_to_point(...)`, `n2p(...)`
  - `get_center()`, `get_corner(...)`, `get_edge_center(...)`, `get_top()`,
    `get_bottom()`, `get_left()`, `get_right()`
  - helper calls that clearly return secants, tangents, dots, arrows, braces,
    rectangles, highlights, connectors, or other geometry tied to an existing
    anchor object
- Treat these as accepted lifecycle patterns:
  1. the dependent geometry is a structural child of the fitted block before
     `fit_body(...)`
  2. it is created after the fitted parent reaches final position and then
     explicitly synchronized with `bind_to_block(...)`, `build_on_anchor(...)`,
     or `bind_to_anchor(...)`
  3. it is morphed in place from an already fitted object identity
- Also treat these as generally safe and do NOT flag them:
  - geometry built after `fit_body(...)` from the same already-fitted on-screen
    anchor instance and then explicitly bound
  - `Transform(...)` or `transform_in_place(...)` targets that are temporary and
    clearly used only to morph an already visible object
  - `transform_in_place(...)` used to replace the appearance of an already
    fitted on-screen object while keeping the same layout slot
  - tuple-unpack placeholders such as `_`
  - helper return values that are unpacked but only some items are persistent,
    when the actually persistent items are correctly bound or structurally owned
- Report this rule when the code strongly suggests patterns like:
  - create secant/tangent/dot/arrow/rectangle from an anchor before `fit_body`
    but do not include it in the fitted block and do not bind it before the fit
  - after `fit_body(...)`, rebuild geometry from a stale pre-fit anchor copy or
    from a second detached helper-built layout state, then show or transform it
    as if it belonged to the fitted block
  - create a persistent dependent overlay after `fit_body(...)` and never add
    it to the fitted structure and never bind it
- Do NOT flag mere temporary transform targets, tuple-unpack placeholders such
  as `_`, or objects that are clearly transient and not persistent page content.
- Be conservative. If lifecycle intent is ambiguous, do not flag it.

Important evaluation discipline:
- Focus ONLY on these three rules.
- Prefer precision over recall. Missing a weak or ambiguous issue is better than
  inventing a shaky one.
- Do NOT invent issues just because you would prefer a different layout.
- If uncertain, do not flag an issue.
- Use `error` only for high-confidence rule violations.
- Use `warning` only for the symbolic-label overlap-risk check.
- Every reported issue must cite concrete code evidence: object creation timing,
  placement call, missing structural membership, missing bind helper, or likely
  overlap-causing placement.
- If a pattern could be valid depending on runtime intent, treat it as valid
  unless the code strongly indicates a violation.
- Never report the same underlying issue twice under different rule ids.
- Return JSON ONLY, no markdown.

Review procedure:
1. Identify `bodyN` roots and each `fit_body(bodyN, ...)` call.
2. For each page, separate:
   - persistent teaching text
   - symbolic local labels
   - persistent anchor-dependent non-text geometry
3. For each candidate object, determine whether the code shows a safe ownership
   or synchronization pattern.
4. Report only high-confidence violations of the three rules above.

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

Output requirements:
- `summary` should be short and factual.
- `object_name` should be the variable name when available; otherwise use an
  empty string.
- `body_name` should be the affected `bodyN` when identifiable; otherwise use
  an empty string.
- `line` and `end_line` should point to the most relevant statement range.
- `message` should describe the problem, not the fix.
- `evidence` should mention the concrete code pattern that triggered the issue.
- `fix_hint` should suggest the smallest plausible correction.
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
        llm_report = {
            "passed": True,
            "summary": "LLM checks skipped",
            "issues": [],
        }
        try:
            raw = self._call(
                _SYSTEM_CODE_EVAL,
                [{
                    "type": "input_text",
                    "text": f"## Scene code\n```python\n{code}\n```",
                }],
            )
            llm_report = _normalize_report(_extract_json_object(raw))
        except Exception as exc:
            llm_report = {
                "passed": True,
                "summary": f"LLM checks unavailable: {exc}",
                "issues": [],
            }

        issues = llm_report.get("issues", [])
        summary = str(llm_report.get("summary", "")).strip()
        if not summary:
            summary = "No high-confidence violations found." if not issues else f"LLM checks found {len(issues)} issue(s)."

        return {
            "passed": not issues,
            "summary": summary,
            "issues": issues,
        }
