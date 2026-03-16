from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .analysis import analyze_files, module_path_for_file
from .discovery import discover_python_files
from .enrich import apply_radon_metrics, detect_unused_candidates
from .graphs import build_import_graph, compute_reachability, detect_cycles, order_modules, render_call_graph, render_import_forest, render_import_tree
from .ir import write_ir_bundle
from .models import AnalysisResult, Config
from .ranking import assign_inclusion_modes, collect_hotspots, generate_observations, score_files
from .render import render_bundle
from .resolution import resolve_calls


def analyze(config: Config) -> AnalysisResult:
    """Analyze a package and return the canonical structured review result.

    The returned object mixes directly extracted facts with heuristic judgments.

    High-confidence fields are based on static parsing and graph extraction:
    module paths, symbols, signatures, imports, source spans, and extracted
    relationships.

    Heuristic fields include root selection, pooled reachability, hotspot
    scores, importance ranking, unused-code candidates, and source inclusion
    choices for the derived review bundle.
    """
    paths = discover_python_files(config)
    files = analyze_files(paths, config)
    warnings = apply_radon_metrics(files, config)
    entry_relative = Path(config.entry_point_module)
    entry_module = module_path_for_file(entry_relative, config.package_name)
    if entry_module not in files:
        raise ValueError(f"Entry module not found: {config.entry_point_module}")

    root_modules = derive_root_modules(files, config, entry_module)
    root_kinds = classify_root_modules(files, config, entry_module, root_modules)
    if config.entry_point_function:
        entry_file = files[entry_module]
        if not any(
            function.qualified_name == config.entry_point_function or function.name == config.entry_point_function
            for function in entry_file.functions.values()
        ):
            raise ValueError(f"Entry function not found: {config.entry_point_function} in {config.entry_point_module}")

    warnings.extend(resolve_calls(files, config))
    import_graph = build_import_graph(files)
    cycles = detect_cycles(import_graph)
    reachable_count, unreachable_count, root_reachability = compute_reachability(files, import_graph, root_modules)
    score_files(files, cycles, entry_module, config.entry_point_function)
    hotspots = collect_hotspots(files, config.complexity_hotspot_threshold)
    ordered_modules = order_modules(files, import_graph)
    assign_inclusion_modes(files, ordered_modules, config)
    observations = generate_observations(files, cycles, hotspots)
    total_lines = sum(file_info.lines for file_info in files.values())
    total_sloc = sum(file_info.sloc for file_info in files.values())
    unused_candidates, vulture_warnings = detect_unused_candidates(files, config)
    warnings.extend(vulture_warnings)
    import_tree_lines = (
        render_import_tree(import_graph, files, entry_module)
        if config.analysis_kind == "application"
        else render_import_forest(import_graph, files, root_modules)
    )
    call_graph_lines = (
        render_call_graph(files, entry_module, config.entry_point_function, config.call_graph_depth)
        if config.entry_point_function
        else ["- n/a (library mode: no single function entrypoint)"]
    )
    return AnalysisResult(
        config=config,
        files=files,
        import_graph=import_graph,
        call_graph_lines=call_graph_lines,
        import_tree_lines=import_tree_lines,
        cycles=cycles,
        ordered_modules=ordered_modules,
        root_modules=root_modules,
        root_kinds=root_kinds,
        root_reachability=root_reachability,
        hotspots=hotspots,
        observations=observations,
        total_lines=total_lines,
        total_sloc=total_sloc,
        reachable_count=reachable_count,
        unreachable_count=unreachable_count,
        warnings=warnings,
        unused_candidates=unused_candidates,
    )


def bundle(config: Config) -> str:
    """Render the single-file LLM review bundle from the canonical analysis IR.

    This artifact is optimized for quick review, not as the source of truth.
    The underlying IR remains the authoritative structured output for agents and
    for auditing the tool's heuristic choices.
    """
    return render_bundle(analyze(config))


def write_outputs(config: Config) -> dict[str, object]:
    """Write both default outputs: the LLM bundle and the `.distillrepo/` IR.

    The single-file bundle is a compressed, review-oriented view.
    The `.distillrepo/` directory contains the fuller machine-readable
    representation, including pooled multi-root metadata and other analysis
    artifacts that may be too verbose for direct LLM consumption.
    """
    result = analyze(config)
    bundle_text = render_bundle(result)
    date_label = datetime.now().strftime("%b%d%Y")
    bundle_path = config.output_path or (config.package_root / f"distilled.{config.package_name}.{date_label}.py")
    bundle_path.write_text(bundle_text, encoding="utf-8")
    ir_dir = config.package_root / ".distillrepo"
    artifacts = write_ir_bundle(result, bundle_path, ir_dir)
    return {
        "result": result,
        "bundle_path": bundle_path,
        "ir_dir": ir_dir,
        "artifacts": artifacts,
    }


def derive_root_modules(files, config: Config, entry_module: str) -> list[str]:
    """Choose a small root set for pooled review analysis.

    The policy is intentionally conservative: enough roots to cover likely
    public or runnable surfaces, but not so many that every module becomes
    equally "important" and the review bundle stops compressing usefully.
    """
    roots: list[str] = []
    package_root_module = f"{config.package_name}"
    if package_root_module in files:
        roots.append(package_root_module)
    if entry_module in files:
        roots.append(entry_module)

    package_init = files.get(package_root_module)
    if package_init is not None:
        roots.extend(package_init.imports[:4] if config.analysis_kind == "library" else package_init.imports[:2])

    direct_children = []
    for module_path, file_info in files.items():
        if not file_info.relative_path.endswith("__init__.py"):
            continue
        if module_path == package_root_module:
            continue
        suffix = module_path.removeprefix(f"{config.package_name}.")
        if suffix and "." not in suffix:
            direct_children.append(module_path)
    roots.extend(sorted(direct_children)[:4 if config.analysis_kind == "library" else 2])

    roots = [module for module in roots if module in files]
    ordered_roots = []
    for module in roots:
        if module not in ordered_roots:
            ordered_roots.append(module)
    return ordered_roots[:8 if config.analysis_kind == "library" else 4]


def classify_root_modules(files, config: Config, entry_module: str, root_modules: list[str]) -> dict[str, str]:
    """Label inferred roots so users can inspect why each root was included."""
    root_kinds: dict[str, str] = {}
    package_root_module = config.package_name
    for module in root_modules:
        if config.entry_point_function and module == entry_module:
            root_kinds[module] = "cli_entrypoint"
        elif module == package_root_module:
            root_kinds[module] = "package_root"
        elif files[module].relative_path.endswith("__init__.py"):
            root_kinds[module] = "subpackage_root"
        else:
            root_kinds[module] = "module_root"
    return root_kinds
