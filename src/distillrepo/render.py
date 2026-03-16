from __future__ import annotations

from .analysis import estimate_tokens
from .models import AnalysisResult, FileInfo, FunctionInfo


def render_bundle(result: AnalysisResult) -> str:
    if result.config.review_mode == "concat":
        rendered = _render_concat_bundle(result)
        result.original_tokens = sum(file_info.estimated_tokens for file_info in result.files.values())
        result.bundle_tokens = estimate_tokens(rendered)
        return rendered
    if result.config.review_mode == "plain_concat":
        rendered = _render_plain_concat_bundle(result)
        result.original_tokens = sum(file_info.estimated_tokens for file_info in result.files.values())
        result.bundle_tokens = estimate_tokens(rendered)
        return rendered
    ordered_files = _apply_budgets(result)
    lines: list[str] = []
    lines.extend(_header_lines(result, ordered_files))
    lines.append("")
    lines.extend(_guidance_lines(result))
    lines.append("")
    lines.extend(_module_inventory_lines(ordered_files))
    lines.append("")
    lines.append("# Import Dependency Tree")
    lines.extend(result.import_tree_lines)
    lines.append("")
    lines.append("# Call Graph")
    lines.extend(result.call_graph_lines)
    lines.append("")
    lines.append("# Complexity Hotspots")
    lines.extend(_hotspot_lines(result.hotspots))
    lines.append("")
    lines.append("# Circular Dependencies")
    lines.extend(_cycle_lines(result.cycles))
    lines.append("")
    lines.append("# Key Observations")
    for observation in result.observations:
        lines.append(f"- {observation}")
    if result.warnings:
        lines.append("")
        lines.append("# Warnings")
        for warning in result.warnings:
            lines.append(f"- {warning}")
    lines.append("")
    lines.append("# Source Material")
    for file_info in ordered_files:
        if file_info.inclusion_mode == "excluded":
            continue
        lines.extend(_file_block(file_info))
        lines.append("")
    rendered = "\n".join(lines).rstrip() + "\n"
    result.original_tokens = sum(file_info.estimated_tokens for file_info in result.files.values())
    result.bundle_tokens = estimate_tokens(rendered)
    return rendered


def _render_concat_bundle(result: AnalysisResult) -> str:
    ordered_files = [result.files[module_path] for module_path in result.ordered_modules]
    lines = [
        "# Distillrepo Concat Bundle",
        f"# Package: {result.config.package_name}",
        "# Mode: concat",
        "# Contents: cleaned source files concatenated after discovery/exclusion rules.",
        "# Notes: lightweight file headers are added; leading generated headers are stripped; blank lines are removed.",
        "",
    ]
    included = 0
    for file_info in ordered_files:
        if file_info.inclusion_mode == "excluded":
            continue
        compact_source = _concat_source(file_info.cleaned_source)
        if not compact_source:
            continue
        included += 1
        lines.append(f"# FILE: {file_info.relative_path}")
        lines.extend(compact_source)
        lines.append("")
    if included == 0:
        lines.append("# No files included.")
    return "\n".join(lines).rstrip() + "\n"


def _render_plain_concat_bundle(result: AnalysisResult) -> str:
    ordered_files = [result.files[module_path] for module_path in result.ordered_modules]
    chunks: list[str] = []
    for file_info in ordered_files:
        if file_info.inclusion_mode == "excluded":
            continue
        compact_source = "\n".join(_concat_source(file_info.cleaned_source)).strip()
        if compact_source:
            chunks.append(compact_source)
    if not chunks:
        return ""
    return "\n".join(chunks).rstrip() + "\n"


def _apply_budgets(result: AnalysisResult) -> list[FileInfo]:
    ordered = [result.files[module_path] for module_path in result.ordered_modules]
    remaining_chars = result.config.max_chars
    remaining_lines = result.config.max_lines
    remaining_tokens = result.config.max_tokens
    remaining_files = result.config.max_files
    for file_info in ordered:
        if file_info.inclusion_mode == "excluded":
            continue
        candidate = _render_file_body(file_info)
        body_text = "\n".join(candidate)
        current_tokens = estimate_tokens(body_text)
        current_lines = len(candidate)
        current_chars = len(body_text)
        while file_info.inclusion_mode in {"full", "summary"} and _exceeds(
            remaining_chars, current_chars, remaining_lines, current_lines, remaining_tokens, current_tokens, remaining_files
        ):
            file_info.inclusion_mode = "summary" if file_info.inclusion_mode == "full" else "signature"
            candidate = _render_file_body(file_info)
            body_text = "\n".join(candidate)
            current_tokens = estimate_tokens(body_text)
            current_lines = len(candidate)
            current_chars = len(body_text)
        if _exceeds(remaining_chars, current_chars, remaining_lines, current_lines, remaining_tokens, current_tokens, remaining_files):
            file_info.inclusion_mode = "excluded"
            continue
        if remaining_chars is not None:
            remaining_chars -= current_chars
        if remaining_lines is not None:
            remaining_lines -= current_lines
        if remaining_tokens is not None:
            remaining_tokens -= current_tokens
        if remaining_files is not None:
            remaining_files -= 1
    return ordered


def _exceeds(
    remaining_chars: int | None,
    current_chars: int,
    remaining_lines: int | None,
    current_lines: int,
    remaining_tokens: int | None,
    current_tokens: int,
    remaining_files: int | None,
) -> bool:
    if remaining_chars is not None and current_chars > remaining_chars:
        return True
    if remaining_lines is not None and current_lines > remaining_lines:
        return True
    if remaining_tokens is not None and current_tokens > remaining_tokens:
        return True
    if remaining_files is not None and remaining_files <= 0:
        return True
    return False


def _header_lines(result: AnalysisResult, ordered_files: list[FileInfo]) -> list[str]:
    original_tokens = sum(file_info.estimated_tokens for file_info in result.files.values())
    bundled_tokens = estimate_tokens(
        "".join("\n".join(_render_file_body(file_info)) for file_info in ordered_files if file_info.inclusion_mode != "excluded")
    )
    compression = f"{(original_tokens / bundled_tokens):.1f}x" if bundled_tokens else "n/a"
    return [
        "# Distillrepo Review Bundle",
        f"# Package: {result.config.package_name}",
        f"# Analysis kind: {result.config.analysis_kind}",
        f"# Roots analyzed: {len(result.root_modules)}",
        f"# Root summary: {_root_coverage_text(result)}",
        f"# Entry point: {_entrypoint_label(result)}",
        f"# Review mode: {result.config.review_mode}",
        f"# Files analyzed: {len(result.files)}",
        f"# Reached from root set / not reached: {result.reachable_count} / {result.unreachable_count}",
        f"# Lines / SLOC: {result.total_lines} / {result.total_sloc}",
        f"# Original repo size (est tokens): {original_tokens}",
        f"# Bundle size (est tokens): {bundled_tokens}",
        f"# Compression: {compression}",
    ]


def _entrypoint_label(result: AnalysisResult) -> str:
    if result.config.entry_point_function:
        return f"{result.config.entry_point_module}:{result.config.entry_point_function}"
    return "n/a (library mode)"


def _guidance_lines(result: AnalysisResult) -> list[str]:
    return [
        "# Review Guidance",
        "# This bundle is a static-analysis-derived review artifact optimized for LLM review and agent navigation.",
        "# Treat file paths, source spans, signatures, imports, and directly extracted symbol relationships as high-confidence facts.",
        "# Treat hotspots, importance scores, not-reached-from-roots results, and unused-code candidates as heuristics.",
        "# Not reached from roots does not mean dead code; dynamic imports, lazy exports, plugin registration, and reflection may be underrepresented.",
        f"# Analysis roots were selected for {result.config.analysis_kind} review and consolidated across {len(result.root_modules)} roots.",
        "# Compressed or omitted files may still matter; verify critical conclusions against the original source when needed.",
    ]


def _root_coverage_text(result: AnalysisResult) -> str:
    parts = []
    for root in result.root_modules[:4]:
        kind = result.root_kinds.get(root, "root")
        coverage = result.root_reachability.get(root, 0)
        parts.append(f"{root} [{kind}]={coverage}")
    if len(result.root_modules) > 4:
        parts.append(f"... {len(result.root_modules) - 4} more")
    return ", ".join(parts)


def _module_inventory_lines(ordered_files: list[FileInfo]) -> list[str]:
    lines = ["# Module Inventory"]
    displayed = ordered_files[:20]
    for index, file_info in enumerate(displayed, start=1):
        lines.append(
            "- "
            + " | ".join(
                [
                    str(index),
                    file_info.relative_path,
                    f"cc={file_info.max_complexity}",
                    f"depth={file_info.depth_from_entry if file_info.depth_from_entry >= 0 else 'unreachable'}",
                    f"root_count={len(file_info.reached_by_roots)}",
                    f"mode={file_info.inclusion_mode}",
                ]
            )
        )
    omitted = len(ordered_files) - len(displayed)
    if omitted > 0:
        lines.append(f"- ... {omitted} more modules")
    return lines


def _hotspot_lines(hotspots: list[FunctionInfo]) -> list[str]:
    if not hotspots:
        return ["- none"]
    return [
        f"- {item.module_path}:{item.qualified_name} | score={item.hotspot_score:.1f} | cc={item.cyclomatic_complexity}"
        for item in hotspots[:12]
    ]


def _cycle_lines(cycles: list[list[str]]) -> list[str]:
    if not cycles:
        return ["- none"]
    rendered = [f"- {' -> '.join(cycle)}" for cycle in cycles[:5]]
    if len(cycles) > 5:
        rendered.append(f"- ... {len(cycles) - 5} more cycles")
    return rendered


def _file_block(file_info: FileInfo) -> list[str]:
    body = _render_file_body(file_info)
    header = [
        f"# FILE: {file_info.relative_path}",
        f"# MODE: {file_info.inclusion_mode}",
        f"# WHY IMPORTANT: {', '.join(file_info.why_important) if file_info.why_important else 'n/a'}",
    ]
    if file_info.inclusion_mode != "full":
        header.append(f"# KEY FUNCTIONS: {_function_preview(file_info)}")
    return header + [""] + body


def _render_file_body(file_info: FileInfo) -> list[str]:
    if file_info.inclusion_mode == "full":
        return file_info.cleaned_source.splitlines() or [""]
    if file_info.inclusion_mode == "summary":
        return _summary_lines(file_info)
    return _signature_lines(file_info)


def _summary_lines(file_info: FileInfo) -> list[str]:
    lines = ["# Summary"]
    if file_info.module_docstring:
        lines.append(f'"""{_short_doc(file_info.module_docstring, limit=90, max_lines=2)}"""')
    if file_info.imports:
        lines.append(f"# Internal imports: {', '.join(file_info.imports[:4])}")
    class_count = 0
    method_count = 0
    for class_name in file_info.classes[:6]:
        bases = ", ".join(file_info.class_bases.get(class_name, [])) or "object"
        lines.append(f"class {class_name}({bases}): ...")
        class_count += 1
        for function in sorted(file_info.functions.values(), key=lambda item: (item.class_name or "", item.lineno)):
            if function.class_name == class_name and method_count < 10:
                lines.append(f"    {function.signature_text}: ...")
                if function.docstring:
                    lines.append(f'    """{_short_doc(function.docstring, limit=72, max_lines=1)}"""')
                method_count += 1
    standalone_count = 0
    for function in sorted(file_info.functions.values(), key=lambda item: item.lineno):
        if function.class_name is None and standalone_count < 12:
            lines.append(f"{function.signature_text}: ...")
            if function.docstring:
                lines.append(f'    """{_short_doc(function.docstring, limit=72, max_lines=1)}"""')
            standalone_count += 1
    omitted = len(file_info.classes) - class_count + sum(
        1 for function in file_info.functions.values() if function.class_name is None
    ) - standalone_count
    if omitted > 0:
        lines.append(f"# ... {omitted} more symbols omitted")
    return lines


def _signature_lines(file_info: FileInfo) -> list[str]:
    lines = ["# Signatures"]
    if file_info.module_docstring:
        lines.append(f'"""{_short_doc(file_info.module_docstring, limit=70, max_lines=1)}"""')
    shown = 0
    for class_name in file_info.classes[:8]:
        bases = ", ".join(file_info.class_bases.get(class_name, [])) or "object"
        lines.append(f"class {class_name}({bases}): ...")
        shown += 1
    for function in sorted(file_info.functions.values(), key=lambda item: item.lineno)[:20]:
        lines.append(f"{function.signature_text}: ...")
        shown += 1
    total_symbols = len(file_info.classes) + len(file_info.functions)
    if total_symbols > shown:
        lines.append(f"# ... {total_symbols - shown} more symbols omitted")
    return lines


def _function_preview(file_info: FileInfo) -> str:
    names = sorted(file_info.functions)[:5]
    if not names:
        return "none"
    if len(file_info.functions) > len(names):
        names.append(f"... {len(file_info.functions) - len(names)} more")
    return ", ".join(names)


def _short_doc(docstring: str, limit: int = 120, max_lines: int = 1) -> str:
    kept_lines: list[str] = []
    for line in docstring.strip().splitlines():
        stripped = line.strip()
        if not stripped:
            if kept_lines:
                break
            continue
        kept_lines.append(stripped)
        if len(kept_lines) >= max_lines:
            break
    text = " ".join(kept_lines)
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _concat_source(source: str) -> list[str]:
    return [line.rstrip() for line in source.splitlines() if line.strip()]
