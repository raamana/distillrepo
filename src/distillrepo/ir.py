from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .models import AnalysisResult, FileInfo, FunctionInfo


def write_ir_bundle(result: AnalysisResult, bundle_path: Path, ir_dir: Path) -> dict[str, Path]:
    ir_dir.mkdir(parents=True, exist_ok=True)
    original_tokens = result.original_tokens
    bundle_tokens = result.bundle_tokens
    saved_tokens = max(0, original_tokens - bundle_tokens)
    saved_percent = (saved_tokens / original_tokens * 100.0) if original_tokens else 0.0
    retained_percent = (bundle_tokens / original_tokens * 100.0) if original_tokens else 0.0
    compression_ratio = (original_tokens / bundle_tokens) if bundle_tokens else 0.0
    artifacts = {
        "summary": ir_dir / "repo_summary.md",
        "modules": ir_dir / "modules.json",
        "symbols": ir_dir / "symbols.json",
        "relationships": ir_dir / "relationships.json",
        "entrypoints": ir_dir / "entrypoints.json",
        "chunks": ir_dir / "chunks.json",
        "hotspots": ir_dir / "hotspots.json",
        "unused_candidates": ir_dir / "unused_candidates.json",
    }

    _write_json(artifacts["modules"], _modules_payload(result))
    _write_json(artifacts["symbols"], _symbols_payload(result))
    _write_json(artifacts["relationships"], _relationships_payload(result))
    _write_json(artifacts["entrypoints"], _entrypoints_payload(result))
    _write_json(artifacts["chunks"], _chunks_payload(result))
    _write_json(artifacts["hotspots"], _hotspots_payload(result))
    _write_json(artifacts["unused_candidates"], _unused_candidates_payload(result))
    artifacts["summary"].write_text(_repo_summary_markdown(result), encoding="utf-8")

    manifest_path = ir_dir / "manifest.json"
    manifest = {
        "format_version": "0.1",
        "repo_name": result.config.package_name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "languages": ["python"],
        "entrypoints": ["entrypoints.json"],
        "bundle": bundle_path.name,
        "artifacts": {key: path.name for key, path in artifacts.items()},
        "stats": {
            "files_analyzed": len(result.files),
            "symbols": sum(len(file_info.functions) for file_info in result.files.values()),
            "modules": len(result.files),
            "reached_from_roots": result.reachable_count,
            "not_reached_from_roots": result.unreachable_count,
            "cycles": len(result.cycles),
            "unused_candidates": len(result.unused_candidates),
            "original_tokens_estimate": original_tokens,
            "bundle_tokens_estimate": bundle_tokens,
            "saved_tokens_estimate": saved_tokens,
            "saved_percent_estimate": round(saved_percent, 1),
            "retained_percent_estimate": round(retained_percent, 1),
            "compression_ratio_estimate": round(compression_ratio, 1) if compression_ratio else 0.0,
        },
        "analysis": {
            "analysis_kind": result.config.analysis_kind,
            "root_modules": result.root_modules,
            "root_kinds": result.root_kinds,
            "root_reachability": result.root_reachability,
            "entry_point_module": result.config.entry_point_module,
            "entry_point_function": result.config.entry_point_function,
            "review_mode": result.config.review_mode,
            "enabled_enrichers": {
                "jedi": result.config.use_jedi,
                "radon": result.config.use_radon,
                "vulture": result.config.use_vulture,
            },
            "heuristic_outputs": [
                "hotspots",
                "importance_score",
                "not_reached_from_roots",
                "unused_candidates",
            ],
            "known_limits": [
                "dynamic imports may be underrepresented",
                "lazy exports may be underrepresented",
                "plugin registration may be underrepresented",
                "runtime-only behavior is not executed",
                "token counts are heuristic estimates based on text length, not model-specific tokenization",
            ],
        },
    }
    _write_json(manifest_path, manifest)
    artifacts["manifest"] = manifest_path
    return artifacts


def _repo_summary_markdown(result: AnalysisResult) -> str:
    original_tokens = result.original_tokens
    bundle_tokens = result.bundle_tokens
    saved_tokens = max(0, original_tokens - bundle_tokens)
    saved_percent = (saved_tokens / original_tokens * 100.0) if original_tokens else 0.0
    retained_percent = (bundle_tokens / original_tokens * 100.0) if original_tokens else 0.0
    compression_ratio = (original_tokens / bundle_tokens) if bundle_tokens else 0.0
    lines = [
        "# Repository Summary",
        "",
        "## Purpose",
        f"{result.config.package_name} analyzed by distillrepo.",
        "",
        "## Review Guidance",
        "- Static-analysis-derived artifact optimized for review and navigation.",
        "- Treat signatures, paths, spans, and extracted relationships as high-confidence facts.",
        "- Treat hotspots, not-reached-from-roots, and unused candidates as heuristics.",
        "- Dynamic imports, lazy exports, and plugin registration may be underrepresented.",
        "- Token counts are heuristic estimates based on text length, not model-specific tokenization.",
        "",
        "## Run Summary",
        f"- Files analyzed: {len(result.files)}",
        f"- Symbols discovered: {sum(len(file_info.functions) for file_info in result.files.values())}",
        f"- Modules analyzed: {len(result.files)}",
        f"- Reached from roots / not reached: {result.reachable_count} / {result.unreachable_count}",
        f"- Import cycles: {len(result.cycles)}",
        f"- Possible unused symbols: {len(result.unused_candidates)}",
        f"- Original size (estimated tokens): {original_tokens:,}",
        f"- Distilled size (estimated tokens): {bundle_tokens:,}",
        f"- Saved (estimated tokens): {saved_tokens:,} ({saved_percent:.1f}%)",
        f"- Retained estimated tokens: {retained_percent:.1f}%",
        f"- Compression ratio: {compression_ratio:.1f}x" if compression_ratio else "- Compression ratio: n/a",
    ]
    lines.extend([
        "",
        "## Analysis mode",
        f"- `{result.config.analysis_kind}`",
        "",
        "## Root modules",
    ])
    for root in result.root_modules[:10]:
        kind = result.root_kinds.get(root, "root")
        coverage = result.root_reachability.get(root, 0)
        lines.append(f"- `{root}` ({kind}, reaches {coverage} modules)")
    if result.config.entry_point_function:
        lines.extend(
            [
                "",
                "## Key entrypoint",
                f"- `{result.config.package_name}.{_module_stem(result.config.entry_point_module)}:{result.config.entry_point_function}`",
            ]
        )
    lines.extend(["", "## Main modules"])
    for file_info in _top_modules(result, limit=6):
        lines.append(
            f"- `{file_info.module_path}`: score={file_info.importance_score:.2f}, "
            f"fan-in={file_info.fan_in}, max_cc={file_info.max_complexity}"
        )
    lines.extend(
        [
            "",
            "## High-complexity areas",
        ]
    )
    for hotspot in result.hotspots[:5]:
        lines.append(f"- `{hotspot.module_path}:{hotspot.qualified_name}` (cc={hotspot.cyclomatic_complexity})")
    if result.observations:
        lines.extend(["", "## Observations"])
        for observation in result.observations[:6]:
            lines.append(f"- {observation}")
    if result.unused_candidates:
        lines.extend(["", "## Possible Unused Symbols"])
        for candidate in result.unused_candidates[:6]:
            location = f"{Path(candidate.path).name}:{candidate.first_lineno}"
            lines.append(f"- `{candidate.name}` ({candidate.typ}, confidence={candidate.confidence}) at {location}")
    return "\n".join(lines).rstrip() + "\n"


def _modules_payload(result: AnalysisResult) -> list[dict]:
    payload = []
    for module_path in result.ordered_modules:
        file_info = result.files[module_path]
        payload.append(
            {
                "id": f"module:{module_path}",
                "path": file_info.relative_path,
                "kind": "module",
                "exports": sorted(_exported_symbols(file_info)),
                "imports": sorted(f"module:{item}" for item in file_info.imports),
                "tags": _module_tags(file_info, result),
                "loc": file_info.lines,
                "sloc": file_info.sloc,
                "complexity": file_info.max_complexity,
                "fan_in": file_info.fan_in,
                "fan_out": file_info.fan_out,
                "depth": file_info.depth_from_entry,
                "reached_by_roots": file_info.reached_by_roots,
                "root_count": len(file_info.reached_by_roots),
                "importance_score": file_info.importance_score,
                "inclusion_mode": file_info.inclusion_mode,
            }
        )
    return payload


def _symbols_payload(result: AnalysisResult) -> list[dict]:
    unused_ids = {candidate.symbol_id for candidate in result.unused_candidates if candidate.symbol_id}
    called_by_index = _build_called_by_index(result)
    payload: list[dict] = []
    for module_path in result.ordered_modules:
        file_info = result.files[module_path]
        for function in sorted(file_info.functions.values(), key=lambda item: (item.lineno, item.qualified_name)):
            symbol_id = _symbol_id(function)
            payload.append(
                {
                    "id": symbol_id,
                    "qualified_name": f"{function.module_path}.{function.qualified_name}",
                    "kind": "method" if function.is_method else "function",
                    "path": file_info.relative_path,
                    "span": {"start": function.lineno, "end": function.end_lineno},
                    "signature": function.signature_text,
                    "doc": _short_text(function.docstring),
                    "tags": _function_tags(function, result, symbol_id in unused_ids),
                    "root_count": len(file_info.reached_by_roots),
                    "reached_by_roots": file_info.reached_by_roots,
                    "calls": [
                        _resolved_symbol_ref(resolved.target_module_path, resolved.target_qualified_name)
                        for resolved in function.resolved_calls
                        if resolved.target_module_path and resolved.target_qualified_name
                    ],
                    "called_by": sorted(called_by_index.get(symbol_id, [])),
                }
            )
    return payload


def _relationships_payload(result: AnalysisResult) -> dict[str, list[dict]]:
    edges: list[dict] = []
    for module_path, file_info in result.files.items():
        for dep in file_info.imports:
            edges.append({"type": "imports", "from": f"module:{module_path}", "to": f"module:{dep}"})
        for function in file_info.functions.values():
            edges.append({"type": "defines", "from": f"module:{module_path}", "to": _symbol_id(function)})
            for resolved in function.resolved_calls:
                if resolved.target_module_path and resolved.target_qualified_name:
                    edges.append(
                        {
                            "type": "calls",
                            "from": _symbol_id(function),
                            "to": _resolved_symbol_ref(resolved.target_module_path, resolved.target_qualified_name),
                            "resolution_kind": resolved.resolution_kind,
                            "confidence": resolved.confidence,
                        }
                    )
    return {"edges": edges}


def _entrypoints_payload(result: AnalysisResult) -> list[dict]:
    if not result.config.entry_point_function:
        return [
            {
                "id": f"module:{root}",
                "kind": result.root_kinds.get(root, "library_root"),
                "reason": "Derived from review root set",
                "module_path": root,
                "coverage": result.root_reachability.get(root, 0),
                "coverage_ratio": round(result.root_reachability.get(root, 0) / max(1, len(result.files)), 3),
            }
            for root in result.root_modules
        ]
    return [
        {
            "id": f"symbol:{result.config.package_name}.{_module_stem(result.config.entry_point_module)}.{result.config.entry_point_function}",
            "kind": result.root_kinds.get(result.root_modules[0], "cli_entrypoint"),
            "reason": "Configured or inferred entrypoint within review root set",
            "module_path": result.config.entry_point_module,
            "function": result.config.entry_point_function,
            "coverage": result.root_reachability.get(result.root_modules[0], 0) if result.root_modules else 0,
            "coverage_ratio": round(
                (result.root_reachability.get(result.root_modules[0], 0) if result.root_modules else 0)
                / max(1, len(result.files)),
                3,
            ),
        }
    ]


def _chunks_payload(result: AnalysisResult) -> list[dict]:
    chunks: list[dict] = []
    for index, file_info in enumerate(_top_modules(result, limit=8), start=1):
        members = [_symbol_id(function) for function in _top_functions(file_info, limit=6)]
        chunks.append(
            {
                "chunk_id": f"chunk:{file_info.module_path}",
                "kind": "module_summary",
                "tokens_estimate": file_info.estimated_tokens,
                "members": members,
                "summary": _chunk_summary(file_info),
                "rank": index,
            }
        )
    return chunks


def _hotspots_payload(result: AnalysisResult) -> list[dict]:
    payload = []
    for hotspot in result.hotspots[:20]:
        payload.append(
            {
                "target": _symbol_id(hotspot),
                "score": round(min(1.0, hotspot.cyclomatic_complexity / max(1, result.config.complexity_hotspot_threshold * 2)), 2),
                "reasons": ["high complexity"],
                "complexity": hotspot.cyclomatic_complexity,
                "path": next(file_info.relative_path for file_info in result.files.values() if file_info.module_path == hotspot.module_path),
            }
        )
    return payload


def _unused_candidates_payload(result: AnalysisResult) -> list[dict]:
    return [
        {
            "name": candidate.name,
            "type": candidate.typ,
            "path": candidate.path,
            "first_lineno": candidate.first_lineno,
            "last_lineno": candidate.last_lineno,
            "size": candidate.size,
            "confidence": candidate.confidence,
            "message": candidate.message,
            "module_path": candidate.module_path,
            "qualified_name": candidate.qualified_name,
            "symbol_id": candidate.symbol_id,
        }
        for candidate in result.unused_candidates
    ]


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _module_tags(file_info: FileInfo, result: AnalysisResult) -> list[str]:
    tags: list[str] = []
    if file_info.module_path in result.root_modules:
        tags.append("entrypoint")
    if file_info.depth_from_entry >= 0:
        tags.append("reachable")
    if file_info.fan_in >= 3:
        tags.append("dependency_hub")
    if file_info.max_complexity >= result.config.complexity_hotspot_threshold:
        tags.append("hotspot")
    return tags


def _function_tags(function: FunctionInfo, result: AnalysisResult, possibly_unused: bool) -> list[str]:
    tags: list[str] = []
    if result.config.entry_point_function and function.module_path == f"{result.config.package_name}.{_module_stem(result.config.entry_point_module)}" and (
        function.qualified_name == result.config.entry_point_function or function.name == result.config.entry_point_function
    ):
        tags.append("entrypoint")
    if function.cyclomatic_complexity >= result.config.complexity_hotspot_threshold:
        tags.append("hotspot")
    if not function.is_method:
        tags.append("module_api")
    if possibly_unused:
        tags.append("unused_candidate")
    return tags


def _build_called_by_index(result: AnalysisResult) -> dict[str, list[str]]:
    """Build reverse call index for IR payloads.

    This avoids an O(symbols^2) scan when populating `called_by`.
    """
    index: dict[str, list[str]] = {}
    for file_info in result.files.values():
        for function in file_info.functions.values():
            caller_id = _symbol_id(function)
            for resolved in function.resolved_calls:
                if not (resolved.target_module_path and resolved.target_qualified_name):
                    continue
                target_id = _resolved_symbol_ref(resolved.target_module_path, resolved.target_qualified_name)
                index.setdefault(target_id, []).append(caller_id)
    return index


def _resolved_symbol_ref(module_path: str, qualified_name: str) -> str:
    return f"symbol:{module_path}.{qualified_name}"


def _symbol_id(function: FunctionInfo) -> str:
    return f"symbol:{function.module_path}.{function.qualified_name}"


def _exported_symbols(file_info: FileInfo) -> list[str]:
    return [f"{file_info.module_path}.{function.qualified_name}" for function in file_info.functions.values() if not function.name.startswith("_")]


def _top_modules(result: AnalysisResult, limit: int) -> list[FileInfo]:
    modules = [result.files[module_path] for module_path in result.ordered_modules]
    return sorted(modules, key=lambda item: (-item.importance_score, item.module_path))[:limit]


def _top_functions(file_info: FileInfo, limit: int) -> list[FunctionInfo]:
    return sorted(
        file_info.functions.values(),
        key=lambda item: (-item.cyclomatic_complexity, item.lineno, item.qualified_name),
    )[:limit]


def _chunk_summary(file_info: FileInfo) -> str:
    return (
        f"{file_info.module_path} with fan-in {file_info.fan_in}, "
        f"fan-out {file_info.fan_out}, max complexity {file_info.max_complexity}."
    )


def _module_stem(relative_module_path: str) -> str:
    path = Path(relative_module_path)
    return "__init__" if path.name == "__init__.py" else path.stem


def _short_text(text: str | None, limit: int = 160) -> str | None:
    if not text:
        return None
    single_line = " ".join(text.strip().splitlines())
    if len(single_line) <= limit:
        return single_line
    return single_line[: limit - 3].rstrip() + "..."
