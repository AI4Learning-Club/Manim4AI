"""LLM-assisted pre-render structural checks for generated Manim scenes."""

from __future__ import annotations

import ast
import json
import re
import time
from typing import Any, Dict, List

from .llm import LLMClient, LLMConfig

_SYSTEM_CODE_EVAL = """\
You are a strict pre-render code evaluator for AI4Learning Manim scenes.

Review the Python code ONLY against these two rules:

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

Do NOT review `non_text_anchor_lifecycle` here.
- Anchor lifecycle is enforced separately by deterministic static analysis.
- Even if you notice a lifecycle problem in the code, do not report it in this
  LLM pass.

Important evaluation discipline:
- Focus ONLY on these two rules.
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
      "rule_id": "body_membership_post_fit | symbolic_label_overlap_risk",
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


_TEXT_LIKE_CALLS = {
    "MarkupText",
    "MathTex",
    "Paragraph",
    "Tex",
    "Text",
    "Title",
    "get_highlighted_math",
    "get_math",
    "get_muted_text",
    "get_secondary_text",
    "get_success_text",
    "get_text",
    "get_warning_text",
    "make_page_title",
    "make_subtitle_panel",
    "set_subtitle",
}

_CONTAINER_BASE_CALLS = {
    "Group",
    "VGroup",
    "make_panel",
}

_EXPLICIT_BIND_HELPERS = {
    "bind_to_anchor",
    "bind_many_to_anchor",
    "bind_to_block",
    "bind_many_to_block",
}

_EXPLICIT_BUILD_HELPERS = {
    "build_on_anchor",
    "bind_many_to_anchor",
}

_GEOMETRY_CONSTRUCTORS = {
    "Annulus",
    "Arc",
    "ArcBetweenPoints",
    "Arrow",
    "Brace",
    "BraceBetweenPoints",
    "Circle",
    "Cross",
    "CurvedArrow",
    "DashedLine",
    "Dot",
    "DoubleArrow",
    "Ellipse",
    "LabeledDot",
    "Line",
    "NumberLine",
    "Polygon",
    "Rectangle",
    "RoundedRectangle",
    "Square",
    "Star",
    "SurroundingRectangle",
    "Underline",
    "Vector",
    "VMobject",
}

_ANCHOR_METHODS = {
    "c2p",
    "coords_to_point",
    "get_bottom",
    "get_center",
    "get_corner",
    "get_critical_point",
    "get_edge_center",
    "get_left",
    "get_right",
    "get_top",
    "n2p",
    "point_from_proportion",
}

_SCENE_METHOD_HINTS = {
    "clear_scene_keep_bg",
    "fit_body",
    "make_page_title",
    "play",
    "show_section_badge_once",
    "speak",
    "speak_with_subtitle",
}

_ANCHOR_NAME_RE = re.compile(
    r"(^|_)(axis|axes|graph|curve|line|plane|number|number_line|path|"
    r"point|dot|node|anchor|panel|block|rect|rects|bar|bars|marker|"
    r"threshold|secant|tangent|brace|arrow|strip)(_|$)"
)

_GEOMETRY_HELPER_RE = re.compile(
    r"(^|_)(area|riemann|plot|curve|point|dot|line|arrow|brace|rect|rectangle|"
    r"bar|ring|highlight|threshold|marker|secant|slope|tangent|connector|"
    r"connect|strip|icon)s?(_|$)|"
    r"_on_(axes|axis|graph|curve|line|plane|path|block|panel)$"
)

_ANCHOR_DERIVED_METHODS = {
    "get_area",
    "get_riemann_rectangles",
    "get_secant_slope_group",
    "get_tangent_line",
    "get_vertical_line",
    "get_horizontal_line",
    "get_vertical_lines_to_graph",
    "plot",
    "plot_line_graph",
    "plot_parametric_curve",
}


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


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _base_call_name(node: ast.AST) -> str:
    expr = node
    while isinstance(expr, ast.Call):
        func = expr.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Call):
            expr = func.value
            continue
        return _call_name(func)
    return ""


def _target_names(target: ast.AST) -> List[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        names: List[str] = []
        for elt in target.elts:
            names.extend(_target_names(elt))
        return names
    return []


def _load_names(node: ast.AST) -> List[str]:
    names: List[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
            if child.id != "self":
                names.append(child.id)
    return names


def _is_anchorish_name(name: str) -> bool:
    return bool(_ANCHOR_NAME_RE.search(name))


def _contains_anchorish_expression(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute) and child.attr in _ANCHOR_METHODS:
            return True
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
            if _is_anchorish_name(child.id):
                return True
    return False


def _is_text_like_expr(node: ast.AST) -> bool:
    base_name = _base_call_name(node)
    return base_name in _TEXT_LIKE_CALLS


def _is_container_expr(node: ast.AST) -> bool:
    base_name = _base_call_name(node)
    return base_name in _CONTAINER_BASE_CALLS


def _expr_uses_explicit_builder(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and _call_name(child.func) in _EXPLICIT_BUILD_HELPERS:
            return True
    return False


def _is_anchor_derived_method_call(call: ast.Call) -> bool:
    if not isinstance(call.func, ast.Attribute):
        return False

    attr = call.func.attr
    if attr in _EXPLICIT_BIND_HELPERS or attr in _TEXT_LIKE_CALLS or attr in _CONTAINER_BASE_CALLS:
        return False

    if attr in _ANCHOR_DERIVED_METHODS or bool(_GEOMETRY_HELPER_RE.search(attr)):
        return _contains_anchorish_expression(call.func.value)
    return False


def _is_geometry_like_call(call: ast.Call) -> bool:
    name = _call_name(call.func)
    if not name:
        return False
    if name in _TEXT_LIKE_CALLS or name in _CONTAINER_BASE_CALLS or name in _EXPLICIT_BIND_HELPERS:
        return False
    return (
        name in _GEOMETRY_CONSTRUCTORS
        or bool(_GEOMETRY_HELPER_RE.search(name))
        or _is_anchor_derived_method_call(call)
    )


def _geometry_call_inputs(call: ast.Call) -> List[ast.AST]:
    inputs = list(call.args) + [kw.value for kw in call.keywords]
    if isinstance(call.func, ast.Attribute):
        inputs.insert(0, call.func.value)
    return inputs


def _expr_is_anchor_dependent_non_text(node: ast.AST) -> bool:
    if _is_text_like_expr(node) or _is_container_expr(node):
        return False
    if _expr_uses_explicit_builder(node):
        return True

    for child in ast.walk(node):
        if isinstance(child, ast.Call) and _is_geometry_like_call(child):
            if any(_contains_anchorish_expression(arg) for arg in _geometry_call_inputs(child)):
                return True
    return False


def _looks_like_scene_method(func: ast.FunctionDef) -> bool:
    for child in ast.walk(func):
        if isinstance(child, ast.Call) and _call_name(child.func) in _SCENE_METHOD_HINTS:
            return True
    return False


def _iter_nested_statements(statements: List[ast.stmt]):
    for stmt in statements:
        yield stmt
        if isinstance(stmt, (ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith)):
            yield from _iter_nested_statements(stmt.body)
            yield from _iter_nested_statements(stmt.orelse)
        elif isinstance(stmt, ast.If):
            yield from _iter_nested_statements(stmt.body)
            yield from _iter_nested_statements(stmt.orelse)
        elif isinstance(stmt, ast.Try):
            yield from _iter_nested_statements(stmt.body)
            for handler in stmt.handlers:
                yield from _iter_nested_statements(handler.body)
            yield from _iter_nested_statements(stmt.orelse)
            yield from _iter_nested_statements(stmt.finalbody)


def _mark_candidate_valid(
    candidates: Dict[str, Dict[str, Any]],
    name: str,
    *,
    via: str,
) -> None:
    record = candidates.get(name)
    if record is None:
        return
    record["valid"] = True
    record["valid_via"] = via


def _body_closure(body_name: str, deps: Dict[str, set[str]]) -> set[str]:
    seen = {body_name}
    stack = [body_name]
    while stack:
        current = stack.pop()
        for dep in deps.get(current, set()):
            if dep in seen:
                continue
            seen.add(dep)
            stack.append(dep)
    return seen


def _make_lifecycle_issue(
    *,
    name: str,
    line: int,
    end_line: int,
    body_name: str,
    evidence: str,
    message: str,
) -> Dict[str, Any]:
    return {
        "rule_id": "non_text_anchor_lifecycle",
        "severity": "error",
        "body_name": body_name,
        "object_name": name,
        "line": line,
        "end_line": end_line,
        "message": message,
        "evidence": evidence,
        "fix_hint": (
            f"Make `{name}` one of the accepted lifecycle patterns: "
            "either include it as a structural child of the fitted block, "
            "or call `self.bind_to_block(...)` if it should follow a parent "
            "block transform, or create/synchronize it with "
            "`self.build_on_anchor(...)` / `self.bind_to_anchor(...)`."
        ),
    }


def _deterministic_anchor_lifecycle_issues(code: str) -> List[Dict[str, Any]]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []

    issues: List[Dict[str, Any]] = []

    for func in ast.walk(tree):
        if not isinstance(func, ast.FunctionDef) or not _looks_like_scene_method(func):
            continue

        deps: Dict[str, set[str]] = {}
        candidates: Dict[str, Dict[str, Any]] = {}
        explicit_bind_lines: Dict[str, int] = {}
        fitted_members: set[str] = set()
        fitted_body_name = ""
        reported_names: set[str] = set()

        def report_invalid_pending(*, fit_line: int, body_name: str) -> None:
            for name, record in list(candidates.items()):
                if record["valid"] or name in reported_names:
                    continue
                if record["line"] >= fit_line:
                    continue
                if explicit_bind_lines.get(name, 10**9) < fit_line:
                    _mark_candidate_valid(candidates, name, via="explicit bind helper")
                    continue
                issue = _make_lifecycle_issue(
                    name=name,
                    line=record["line"],
                    end_line=record["end_line"],
                    body_name=body_name,
                    evidence=(
                        f"`{name}` is created before `{body_name}` is fitted, but it is not "
                        "a structural child of that fitted block and is not explicitly "
                        "synchronized with a bind helper before the fit."
                    ),
                    message=(
                        f"`{name}` is an anchor-dependent non-text object that violates the "
                        "hard lifecycle rule: before the fitted layout moves, it must already "
                        "be a structural child of the fitted block or be explicitly bound "
                        "with `bind_to_block(...)` or `build_on_anchor(...)` / `bind_to_anchor(...)`."
                    ),
                )
                issues.append(issue)
                reported_names.add(name)

        def finalize_unbound_tail() -> None:
            if not fitted_members and not fitted_body_name:
                return
            for name, record in list(candidates.items()):
                if record["valid"] or name in reported_names:
                    continue
                if explicit_bind_lines.get(name, -1) > record["line"]:
                    _mark_candidate_valid(candidates, name, via="explicit bind helper")
                    continue
                issue = _make_lifecycle_issue(
                    name=name,
                    line=record["line"],
                    end_line=record["end_line"],
                    body_name=fitted_body_name,
                    evidence=(
                        f"`{name}` is anchor-dependent geometry, but after the fitted body "
                        "is established it is never made structural to that fitted block and "
                        "never explicitly synchronized with a bind helper."
                    ),
                    message=(
                        f"`{name}` violates the hard lifecycle rule: anchor-dependent "
                        "non-text geometry must either be a structural child of the fitted "
                        "block or use `bind_to_block(...)` / `build_on_anchor(...)` / "
                        "`bind_to_anchor(...)`."
                    ),
                )
                issues.append(issue)
                reported_names.add(name)

        for stmt in _iter_nested_statements(func.body):
            if isinstance(stmt, ast.Assign):
                names: List[str] = []
                for target in stmt.targets:
                    names.extend(_target_names(target))

                if names:
                    refs = {name for name in _load_names(stmt.value) if name not in names}
                    for name in names:
                        deps[name] = set(refs)

                    if (
                        len(names) >= 2
                        and any(name.endswith(("_block", "_panel", "_group")) for name in names)
                    ):
                        container_name = next(
                            name for name in names if name.endswith(("_block", "_panel", "_group"))
                        )
                        deps.setdefault(container_name, set()).update(
                            other for other in names if other != container_name
                        )
                        if container_name in fitted_members:
                            fitted_members.update(other for other in names if other != container_name)

                    base_name = _base_call_name(stmt.value)
                    if base_name in _EXPLICIT_BUILD_HELPERS:
                        for name in names:
                            explicit_bind_lines[name] = stmt.lineno
                            _mark_candidate_valid(candidates, name, via=base_name)
                        continue

                    if _expr_is_anchor_dependent_non_text(stmt.value):
                        for name in names:
                            if name in fitted_members:
                                continue
                            candidates[name] = {
                                "line": stmt.lineno,
                                "end_line": getattr(stmt, "end_lineno", stmt.lineno),
                                "valid": False,
                                "valid_via": "",
                            }
                            if explicit_bind_lines.get(name, -1) > stmt.lineno:
                                _mark_candidate_valid(candidates, name, via="explicit bind helper")

            elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                call = stmt.value
                call_name = _call_name(call.func)

                if call_name == "fit_body" and call.args:
                    body_arg = call.args[0]
                    if isinstance(body_arg, ast.Name):
                        body_name = body_arg.id
                        closure = _body_closure(body_name, deps)
                        fitted_body_name = body_name
                        fitted_members = set(closure)
                        for member in closure:
                            _mark_candidate_valid(candidates, member, via=f"structural child of `{body_name}`")
                        report_invalid_pending(fit_line=stmt.lineno, body_name=body_name)
                    continue

                if call_name == "clear_scene_keep_bg":
                    finalize_unbound_tail()
                    deps = {}
                    candidates = {}
                    explicit_bind_lines = {}
                    fitted_members = set()
                    fitted_body_name = ""
                    reported_names = set()
                    continue

                if call_name in {"bind_to_block", "bind_to_anchor"} and call.args:
                    first = call.args[0]
                    if isinstance(first, ast.Name):
                        explicit_bind_lines[first.id] = stmt.lineno
                        _mark_candidate_valid(candidates, first.id, via=call_name)
                    continue

                if call_name == "bind_many_to_block" and len(call.args) >= 2:
                    for arg in call.args[1:]:
                        if isinstance(arg, ast.Name):
                            explicit_bind_lines[arg.id] = stmt.lineno
                            _mark_candidate_valid(candidates, arg.id, via=call_name)
                    continue

                if call_name == "add" and isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name):
                    container_name = call.func.value.id
                    child_names = [arg.id for arg in call.args if isinstance(arg, ast.Name)]
                    if child_names:
                        deps.setdefault(container_name, set()).update(child_names)
                        if container_name in fitted_members or container_name == fitted_body_name:
                            fitted_members.update(child_names)
                            for child_name in child_names:
                                _mark_candidate_valid(
                                    candidates,
                                    child_name,
                                    via=f"structural child of fitted `{container_name}`",
                                )
                    continue

        finalize_unbound_tail()

    deduped: List[Dict[str, Any]] = []
    seen_keys = set()
    for issue in issues:
        key = (issue.get("rule_id"), issue.get("object_name"), issue.get("line"))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(issue)
    return deduped


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
        deterministic_issues = _deterministic_anchor_lifecycle_issues(code)

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

        llm_issues = [
            issue
            for issue in llm_report.get("issues", [])
            if issue.get("rule_id") != "non_text_anchor_lifecycle"
        ]
        issues = deterministic_issues + llm_issues

        summary_parts: List[str] = []
        if deterministic_issues:
            summary_parts.append(
                f"Deterministic anchor lifecycle scan found {len(deterministic_issues)} hard violation(s)."
            )
        if llm_issues:
            summary_parts.append(
                f"LLM checks found {len(llm_issues)} additional issue(s)."
            )
        if not summary_parts:
            llm_summary = str(llm_report.get("summary", "")).strip()
            summary_parts.append(llm_summary or "No high-confidence violations found.")

        return {
            "passed": not issues,
            "summary": " ".join(summary_parts).strip(),
            "issues": issues,
        }
