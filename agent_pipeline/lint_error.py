"""Section-local static lint checks for Manim streaming sections."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Iterable

from .section_validation import ValidationIssue, ValidationReport


_BODY_NAME_RE = re.compile(r"body\d+$")
_SECTION_METHOD_RE = re.compile(r"^(opening_page|closing_page|section_[a-z0-9_]+)$")
_PLAIN_TEXT_HELPERS = {
    "get_text",
    "get_secondary_text",
    "make_page_title",
    "show_page_title_chip",
}
_MATH_HELPERS = {
    "get_math",
    "get_highlighted_math",
    "MathTex",
    "Tex",
}
_DEPENDENT_ATTRS = {
    "get_area",
    "get_riemann_rectangles",
    "get_secant_slope_group",
}
_BINDING_CALLS = {
    "bind_to_block",
    "bind_to_anchor",
    "build_on_anchor",
}
_LATEX_RISK_PATTERNS = (
    r"\\[A-Za-z]+",
    r"\$[^$]+\$",
)
_PLAIN_TEXT_MATH_PATTERNS = (
    r"\\frac",
    r"\\Delta",
    r"\\left",
    r"\\right",
    r"\\Phi",
    r"\\theta",
    r"\\alpha",
    r"\\beta",
    r"\\gamma",
    r"\\mathrm",
    r"\\text",
    r"\\cdot",
    r"\\times",
    r"\\sum",
    r"\\int",
    r"\$[^$]+\$",
)
_GEOMETRY_LEAF_NAME_RE = re.compile(
    r"(?:^|_)(axes|axis|curve|graph|plot|dot|dots|point|points|marker|markers|"
    r"tangent|tangents|secant|secants|arrow|arrows|brace|braces|line|lines)(?:$|_)"
)
_LAYOUT_BLOCK_NAME_RE = re.compile(
    r"(?:^|_)(panel|note|prompt|question|takeaway|caption|formula|text|explain|"
    r"meaning|summary|col|column|block)(?:$|_)"
)
_CJK_CHAR_RE = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff\uff00-\uffef]")
_FOLLOWER_MEASUREMENT_ATTRS = {"get_center", "get_left", "get_right", "get_top", "get_bottom"}
_ANCHOR_POINT_ATTRS = {
    "c2p",
    "coords_to_point",
    "n2p",
    "point_from_proportion",
    "get_center",
    "get_corner",
    "get_edge_center",
    "get_top",
    "get_bottom",
    "get_left",
    "get_right",
}
_GEOMETRY_CONSTRUCTOR_NAMES = {
    "Dot",
    "Line",
    "Arrow",
    "Brace",
    "BraceBetweenPoints",
    "DashedLine",
    "Polygon",
    "Rectangle",
    "SurroundingRectangle",
}


@dataclass
class _PageState:
    index: int
    start_lineno: int
    statements: list[ast.stmt] = field(default_factory=list)
    assignments: dict[str, ast.Assign | ast.AnnAssign] = field(default_factory=dict)
    assignment_rhs_refs: dict[str, set[str]] = field(default_factory=dict)
    body_assignments: dict[str, ast.Assign | ast.AnnAssign] = field(default_factory=dict)
    body_rhs_refs: dict[str, set[str]] = field(default_factory=dict)
    fit_body_calls: list[ast.Call] = field(default_factory=list)
    long_title_calls: list[ast.Call] = field(default_factory=list)
    chip_title_calls: list[ast.Call] = field(default_factory=list)
    bound_names: set[str] = field(default_factory=set)
    built_on_anchor_names: set[str] = field(default_factory=set)
    dependent_assignments: list[tuple[str, ast.Assign | ast.AnnAssign]] = field(default_factory=list)
    anchor_leaf_assignments_pre_fit: list[tuple[str, ast.Assign | ast.AnnAssign]] = field(default_factory=list)


def lint_section_code(code: str, *, filename: str = "scene.py") -> dict[str, object]:
    try:
        module = ast.parse(code, filename=filename)
    except SyntaxError as exc:
        issue = ValidationIssue(
            tool="lint_error",
            severity="error",
            category="syntax",
            message=f"Python syntax invalid: {exc.msg}",
            evidence=f"{filename}:{exc.lineno}:{exc.offset}",
            fix_hint="Repair the Python syntax before section validation continues.",
            line=exc.lineno,
        )
        return ValidationReport(
            passed=False,
            summary="Validation failed with 1 issue(s): 1 error, 0 warning, 0 info.",
            issues=(issue,),
        ).to_dict()

    issues: list[ValidationIssue] = []
    issues.extend(_lint_math_call_cjk_risk(module))
    for class_def in [node for node in module.body if isinstance(node, ast.ClassDef)]:
        for func in [node for node in class_def.body if isinstance(node, ast.FunctionDef)]:
            if not _SECTION_METHOD_RE.match(func.name):
                continue
            issues.extend(_lint_section_method(func))

    report = ValidationReport.from_dict(
        {
            "issues": [issue.to_dict() for issue in issues],
        }
    )
    return report.to_dict()


def _lint_section_method(func: ast.FunctionDef) -> list[ValidationIssue]:
    pages = _split_pages(func)
    issues: list[ValidationIssue] = []
    for page in pages:
        if not _page_has_content(page):
            continue
        issues.extend(_lint_page(func.name, page))
    return issues


def _split_pages(func: ast.FunctionDef) -> list[_PageState]:
    pages: list[_PageState] = []
    current = _PageState(index=1, start_lineno=func.lineno)
    seen_fit_body = False

    for stmt in func.body:
        if _is_clear_scene_stmt(stmt):
            pages.append(current)
            current = _PageState(index=current.index + 1, start_lineno=getattr(stmt, "lineno", func.lineno))
            seen_fit_body = False
            continue

        current.statements.append(stmt)

        call = _extract_call(stmt)
        if call is not None:
            attr_name = _call_attr_name(call)
            if attr_name == "fit_body":
                current.fit_body_calls.append(call)
                seen_fit_body = True
            elif attr_name in {"make_page_title", "fit_to_top_band"}:
                current.long_title_calls.append(call)
            elif attr_name == "show_page_title_chip":
                current.chip_title_calls.append(call)
            elif attr_name in _BINDING_CALLS:
                if attr_name == "build_on_anchor":
                    built_name = _assigned_name(stmt)
                    if built_name:
                        current.built_on_anchor_names.add(built_name)
                else:
                    bound_name = _first_name_arg(call)
                    if bound_name:
                        current.bound_names.add(bound_name)

        body_name = _assigned_body_name(stmt)
        assigned_name = _assigned_name(stmt)
        if assigned_name:
            current.assignments[assigned_name] = stmt
            current.assignment_rhs_refs[assigned_name] = _referenced_names(_assigned_value(stmt))
        if body_name:
            current.body_assignments[body_name] = stmt
            current.body_rhs_refs[body_name] = _referenced_names(_assigned_value(stmt))

        dep_name = _dependent_assignment_name(stmt)
        if dep_name and not seen_fit_body:
            current.dependent_assignments.append((dep_name, stmt))

        anchor_leaf_name = _anchor_leaf_assignment_name(stmt)
        if anchor_leaf_name and not seen_fit_body:
            current.anchor_leaf_assignments_pre_fit.append((anchor_leaf_name, stmt))

    pages.append(current)
    return pages


def _page_has_content(page: _PageState) -> bool:
    return bool(page.statements)


def _lint_page(method_name: str, page: _PageState) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    page_label = f"{method_name}:page_{page.index}"

    if not page.fit_body_calls:
        issues.append(
            ValidationIssue(
                tool="lint_error",
                severity="error",
                category="fit_body_missing",
                message="Page is missing a self.fit_body(bodyN, ...) call.",
                evidence=f"{page_label} has no fit_body call.",
                fix_hint="Create exactly one bodyN root for the page and call self.fit_body(bodyN, ...) once.",
                line=page.start_lineno,
                symbol=page_label,
            )
        )
    if len(page.fit_body_calls) > 1:
        issues.append(
            ValidationIssue(
                tool="lint_error",
                severity="error",
                category="fit_body_multiple_calls_same_page",
                message="Page calls self.fit_body(...) more than once.",
                evidence=", ".join(f"line {call.lineno}" for call in page.fit_body_calls),
                fix_hint="Keep exactly one fit_body call for each page.",
                line=page.fit_body_calls[1].lineno,
                symbol=page_label,
            )
        )

    fit_targets = [_first_name_arg(call) for call in page.fit_body_calls]
    valid_fit_targets = [name for name in fit_targets if name and _BODY_NAME_RE.match(name)]
    if page.fit_body_calls and not valid_fit_targets:
        issues.append(
            ValidationIssue(
                tool="lint_error",
                severity="error",
                category="body_root_missing",
                message="fit_body(...) does not target a bodyN root.",
                evidence=", ".join(
                    f"line {call.lineno}: {ast.unparse(call.args[0]) if call.args else 'missing-arg'}"
                    for call in page.fit_body_calls
                ),
                fix_hint="Assign the page root to a variable named bodyN and pass that bodyN to self.fit_body(...).",
                line=page.fit_body_calls[0].lineno,
                symbol=page_label,
            )
        )

    if len(page.body_assignments) > 1:
        details = ", ".join(
            f"{name}@line {stmt.lineno}"
            for name, stmt in sorted(page.body_assignments.items(), key=lambda item: item[1].lineno)
        )
        issues.append(
            ValidationIssue(
                tool="lint_error",
                severity="error",
                category="multiple_body_roots_same_page",
                message="Page defines more than one bodyN root.",
                evidence=details,
                fix_hint="Keep exactly one bodyN root per page; split into a new page if the layout changes materially.",
                line=min(stmt.lineno for stmt in page.body_assignments.values()),
                symbol=page_label,
            )
        )

    if page.long_title_calls and page.chip_title_calls:
        line = min(call.lineno for call in [*page.long_title_calls, *page.chip_title_calls])
        issues.append(
            ValidationIssue(
                tool="lint_error",
                severity="error",
                category="title_style_mixed_on_page",
                message="Page mixes long top title and title chip styles.",
                evidence=(
                    f"long_title_lines={[call.lineno for call in page.long_title_calls]}, "
                    f"chip_title_lines={[call.lineno for call in page.chip_title_calls]}"
                ),
                fix_hint="Choose exactly one page-title style for the page.",
                line=line,
                symbol=page_label,
            )
        )

    issues.extend(_lint_page_anchor_contract(method_name, page))
    issues.extend(_lint_page_anchor_leaf_contract(method_name, page))
    issues.extend(_lint_page_geometry_layout_mixing(method_name, page))
    issues.extend(_lint_page_follower_measurement_risk(method_name, page))
    issues.extend(_lint_page_latex_text_risk(method_name, page))
    return issues


def _lint_math_call_cjk_risk(module: ast.AST) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for node in ast.walk(module):
        if not isinstance(node, ast.Call):
            continue
        call_name = _call_attr_name(node)
        if call_name not in _MATH_HELPERS:
            continue
        string_args = [
            arg for arg in [*node.args, *(kw.value for kw in node.keywords)]
            if _literal_str(arg)
        ]
        for arg in string_args:
            raw = _literal_str(arg)
            line = getattr(arg, "lineno", getattr(node, "lineno", None))
            evidence = f"line {line}: {call_name}({raw})" if line else f"{call_name}({raw})"
            if not _contains_cjk(raw):
                pass
            else:
                issues.append(
                    ValidationIssue(
                        tool="lint_error",
                        severity="error",
                        category="latex_cjk_in_math",
                        message="Math helper contains Chinese/CJK text inside a LaTeX string.",
                        evidence=evidence,
                        fix_hint=(
                            "Remove Chinese/CJK text from get_math/get_highlighted_math/MathTex/Tex strings. "
                            "Keep math helpers pure LaTeX, and render natural-language Chinese with get_text(...) "
                            "or a separate Text block outside the formula."
                        ),
                        line=line,
                        symbol=call_name,
                    )
                )
            if not _latex_braces_balanced(raw):
                issues.append(
                    ValidationIssue(
                        tool="lint_error",
                        severity="error",
                        category="latex_brace_mismatch",
                        message="Math helper contains unbalanced LaTeX braces.",
                        evidence=evidence,
                        fix_hint=(
                            "Repair the LaTeX grouping so every `{` has a matching `}`. "
                            "Pay special attention to nested groups like `F_{\\mathrm{...}}`, subscripts, superscripts, and fractions."
                        ),
                        line=line,
                        symbol=call_name,
                    )
                )
            if not _latex_frac_well_formed(raw):
                issues.append(
                    ValidationIssue(
                        tool="lint_error",
                        severity="error",
                        category="latex_frac_malformed",
                        message="Math helper contains a malformed \\frac expression.",
                        evidence=evidence,
                        fix_hint=(
                            "Rewrite every fraction as `\\frac{numerator}{denominator}` with two complete brace groups."
                        ),
                        line=line,
                        symbol=call_name,
                    )
                )
    return issues


def _lint_page_anchor_contract(method_name: str, page: _PageState) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    structural_names = _structural_names(page)

    page_label = f"{method_name}:page_{page.index}"
    for name, stmt in page.dependent_assignments:
        if name in structural_names or name in page.bound_names or name in page.built_on_anchor_names:
            continue
        issues.append(
            ValidationIssue(
                tool="lint_error",
                severity="warning",
                category="anchor_dependent_before_fit_without_binding",
                message="Anchor-dependent geometry is created before fit_body(...) but is neither structural nor explicitly bound.",
                evidence=f"line {stmt.lineno}: {ast.unparse(_assigned_value(stmt))}",
                fix_hint="Include the dependent geometry in the body block or rewrite it as a local builder plus build_on_anchor(...).",
                line=stmt.lineno,
                symbol=f"{page_label}:{name}",
            )
        )
    return issues


def _lint_page_anchor_leaf_contract(method_name: str, page: _PageState) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    structural_names = _structural_names(page)

    page_label = f"{method_name}:page_{page.index}"
    for name, stmt in page.anchor_leaf_assignments_pre_fit:
        if name in structural_names or name in page.built_on_anchor_names:
            continue
        issues.append(
            ValidationIssue(
                tool="lint_error",
                severity="error",
                category="pre_fit_anchor_leaf_detached",
                message="Anchor leaf is created before fit_body(...) but is neither structural nor built via build_on_anchor(...).",
                evidence=f"line {stmt.lineno}: {ast.unparse(_assigned_value(stmt))}",
                fix_hint="Make the object a structural child of the fitted visual block, or rewrite it as a local builder plus build_on_anchor(...).",
                line=stmt.lineno,
                symbol=f"{page_label}:{name}",
            )
        )
    return issues


def _lint_page_geometry_layout_mixing(method_name: str, page: _PageState) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    page_label = f"{method_name}:page_{page.index}"
    for body_name, stmt in page.body_assignments.items():
        refs = page.body_rhs_refs.get(body_name, set())
        geometry_refs = sorted(name for name in refs if _looks_like_geometry_leaf_name(name))
        layout_refs = sorted(name for name in refs if _looks_like_layout_block_name(name))
        if not geometry_refs or not layout_refs:
            continue
        issues.append(
            ValidationIssue(
                tool="lint_error",
                severity="warning",
                category="geometry_layout_mixing",
                message="A page body appears to mix geometry leaf objects with layout blocks in the same layout composition.",
                evidence=(
                    f"line {stmt.lineno}: body={body_name}, "
                    f"geometry={geometry_refs}, layout={layout_refs}"
                ),
                fix_hint=(
                    "Wrap coordinate-based visuals as a graph/diagram block first, then compose that block with "
                    "note/prompt/formula blocks at the page layout level."
                ),
                line=stmt.lineno,
                symbol=page_label,
            )
        )
    return issues


def _lint_page_follower_measurement_risk(method_name: str, page: _PageState) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    structural_names = _structural_names(page)

    page_label = f"{method_name}:page_{page.index}"
    for stmt in page.statements:
        if not isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            continue
        name = _assigned_name(stmt)
        if not name:
            continue
        if name in page.bound_names or name in structural_names:
            continue
        value = _assigned_value(stmt)
        if not _contains_measurement_anchor_call(value):
            continue
        issues.append(
            ValidationIssue(
                tool="lint_error",
                severity="warning",
                category="follower_positioned_by_measurement",
                message="A likely follower object is positioned from one-off geometric measurements without an explicit stable anchor lifecycle.",
                evidence=f"line {stmt.lineno}: {ast.unparse(value)}",
                fix_hint=(
                    "Prefer a local builder plus build_on_anchor(...) for persistent followers, or make the object a structural child "
                    "of the owning graph block instead of relying on get_center/get_left/get_right style measurements."
                ),
                line=stmt.lineno,
                symbol=f"{page_label}:{name}",
            )
        )
    return issues


def _lint_page_latex_text_risk(method_name: str, page: _PageState) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    page_label = f"{method_name}:page_{page.index}"
    for stmt in _iter_stmt_calls(page):
        attr_name = _call_attr_name(stmt)
        if attr_name not in _PLAIN_TEXT_HELPERS or not stmt.args:
            continue
        raw = _literal_str(stmt.args[0])
        if not raw:
            continue
        if any(re.search(pattern, raw) for pattern in _PLAIN_TEXT_MATH_PATTERNS):
            issues.append(
                ValidationIssue(
                    tool="lint_error",
                    severity="error",
                    category="plain_text_math_misuse",
                    message="Plain-text helper contains math content that should be rendered with MathTex.",
                    evidence=f"line {stmt.lineno}: {raw}",
                    fix_hint=(
                        "Do not place formulas in get_text/get_secondary_text/make_page_title/show_page_title_chip. "
                        "Rewrite it by splitting the sentence into a plain-text prefix and a math helper, for example: "
                        "`prefix = self.get_text(\"...:\")`, `formula = self.get_math(r\"...\")`, then "
                        "`line = VGroup(prefix, formula).arrange(RIGHT, buff=0.12, aligned_edge=DOWN)`. "
                        "If the whole string is formula-only, move the full content into get_math/get_highlighted_math instead."
                    ),
                    line=stmt.lineno,
                    symbol=page_label,
                )
            )
            continue
        if any(re.search(pattern, raw) for pattern in _LATEX_RISK_PATTERNS):
            issues.append(
                ValidationIssue(
                    tool="lint_error",
                    severity="warning",
                    category="latex_text_risk",
                    message="Plain-text helper appears to contain LaTeX-like markup.",
                    evidence=f"line {stmt.lineno}: {raw}",
                    fix_hint="Use a math helper for LaTeX content, or escape/remove LaTeX-like markup from plain text helpers.",
                    line=stmt.lineno,
                    symbol=page_label,
                )
            )
    return issues


def _iter_stmt_calls(page: _PageState) -> Iterable[ast.Call]:
    for stmt in page.statements:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Call):
                yield node


def _is_clear_scene_stmt(stmt: ast.stmt) -> bool:
    call = _extract_call(stmt)
    return call is not None and _call_attr_name(call) == "clear_scene_keep_bg"


def _extract_call(stmt: ast.stmt) -> ast.Call | None:
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        return stmt.value
    if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Call):
        return stmt.value
    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.value, ast.Call):
        return stmt.value
    return None


def _call_attr_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _assigned_body_name(stmt: ast.stmt) -> str | None:
    name = _assigned_name(stmt)
    if name and _BODY_NAME_RE.match(name):
        return name
    return None


def _dependent_assignment_name(stmt: ast.stmt) -> str | None:
    value = _assigned_value(stmt)
    if not isinstance(value, ast.Call):
        return None
    if _call_attr_name(value) not in _DEPENDENT_ATTRS:
        return None
    return _assigned_name(stmt)


def _anchor_leaf_assignment_name(stmt: ast.stmt) -> str | None:
    value = _assigned_value(stmt)
    if not _looks_like_anchor_leaf_value(value):
        return None
    return _assigned_name(stmt)


def _assigned_name(stmt: ast.stmt) -> str | None:
    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
        return stmt.targets[0].id
    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
        return stmt.target.id
    return None


def _assigned_value(stmt: ast.stmt) -> ast.AST:
    if isinstance(stmt, ast.Assign):
        return stmt.value
    if isinstance(stmt, ast.AnnAssign):
        return stmt.value if stmt.value is not None else ast.Constant(value=None)
    return ast.Constant(value=None)


def _first_name_arg(call: ast.Call) -> str | None:
    if not call.args:
        return None
    first = call.args[0]
    if isinstance(first, ast.Name):
        return first.id
    return None


def _referenced_names(node: ast.AST) -> set[str]:
    return {child.id for child in ast.walk(node) if isinstance(child, ast.Name)}


def _literal_str(node: ast.AST) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ""


def _contains_cjk(text: str) -> bool:
    return bool(_CJK_CHAR_RE.search(str(text or "")))


def _latex_braces_balanced(text: str) -> bool:
    depth = 0
    escaped = False
    for char in str(text or ""):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _latex_frac_well_formed(text: str) -> bool:
    raw = str(text or "")
    idx = 0
    while True:
        pos = raw.find(r"\frac", idx)
        if pos < 0:
            return True
        cursor = pos + len(r"\frac")
        cursor = _skip_spaces(raw, cursor)
        first_end = _consume_brace_group(raw, cursor)
        if first_end < 0:
            return False
        cursor = _skip_spaces(raw, first_end)
        second_end = _consume_brace_group(raw, cursor)
        if second_end < 0:
            return False
        idx = second_end


def _skip_spaces(text: str, idx: int) -> int:
    while idx < len(text) and text[idx].isspace():
        idx += 1
    return idx


def _consume_brace_group(text: str, idx: int) -> int:
    if idx >= len(text) or text[idx] != "{":
        return -1
    depth = 0
    escaped = False
    cursor = idx
    while cursor < len(text):
        char = text[cursor]
        if escaped:
            escaped = False
            cursor += 1
            continue
        if char == "\\":
            escaped = True
            cursor += 1
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return cursor + 1
            if depth < 0:
                return -1
        cursor += 1
    return -1


def _looks_like_geometry_leaf_name(name: str) -> bool:
    return bool(_GEOMETRY_LEAF_NAME_RE.search(str(name or "")))


def _looks_like_layout_block_name(name: str) -> bool:
    return bool(_LAYOUT_BLOCK_NAME_RE.search(str(name or "")))


def _contains_measurement_anchor_call(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        if _call_attr_name(child) in _FOLLOWER_MEASUREMENT_ATTRS:
            return True
    return False


def _structural_names(page: _PageState) -> set[str]:
    seeds: set[str] = set()
    for refs in page.body_rhs_refs.values():
        seeds.update(refs)

    structural = set(seeds)
    queue = list(seeds)
    while queue:
        name = queue.pop()
        for ref in page.assignment_rhs_refs.get(name, set()):
            if ref in structural:
                continue
            structural.add(ref)
            queue.append(ref)
    return structural


def _looks_like_anchor_leaf_value(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False

    attr_name = _call_attr_name(node)
    if attr_name in _DEPENDENT_ATTRS:
        return True
    if attr_name == "build_on_anchor":
        return True
    if attr_name == "plot":
        return True
    if attr_name in _GEOMETRY_CONSTRUCTOR_NAMES and _contains_anchor_point_call(node):
        return True
    return False


def _contains_anchor_point_call(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        if _call_attr_name(child) in _ANCHOR_POINT_ATTRS:
            return True
    return False


__all__ = ["lint_section_code"]
