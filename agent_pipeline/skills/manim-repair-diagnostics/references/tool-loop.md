# Tool Loop Repair Workflow

## Mandatory workflow

The system prompt names the single writable target file. Use that exact path in
every file tool call; never substitute a placeholder or another file.

1) `read_file(path=<target_file>)` - use start_line/end_line when the error cites line numbers.
2) `search_file(path=<target_file>, pattern=...)` - locate strings or symbols (set use_regex=true only when needed).
3) `apply_patch(path=<target_file>, old_text=..., new_text=...)` - old_text must match EXACTLY once in the file.
4) When done, call finish_repair(fallback_required=false, summary="...")
If the problem needs a whole-file rewrite, call finish_repair(fallback_required=true).

## Few-shot examples
1) SyntaxError at line N: read_file with start_line=N-3, end_line=N+3, apply_patch the smallest fix, finish_repair(fallback_required=false).
2) LaTeX in plain text helper: search_file for the snippet, apply_patch to split into get_math + natural language, finish_repair(fallback_required=false).
3) code_eval: read_file around cited line, apply_patch minimal structural fix, finish_repair(fallback_required=false).
4) If stuck after several patches: finish_repair(fallback_required=true).
