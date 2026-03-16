from __future__ import annotations

from pathlib import Path

from .models import Config, FileInfo, UnusedCandidate


def apply_radon_metrics(files: dict[str, FileInfo], config: Config) -> list[str]:
    if not config.use_radon:
        return []
    try:
        from radon.complexity import cc_visit
        from radon.metrics import mi_visit
    except ImportError:
        return ["Radon unavailable; using internal complexity heuristic."]

    for file_info in files.values():
        if file_info.parse_error:
            continue
        try:
            blocks = cc_visit(file_info.source)
        except Exception:
            continue
        complexity_by_name: dict[str, int] = {}
        for block in blocks:
            if getattr(block, "classname", None):
                qualified_name = f"{block.classname}.{block.name}"
            else:
                qualified_name = block.name
            complexity_by_name[qualified_name] = int(block.complexity)
        for qualified_name, function in file_info.functions.items():
            if qualified_name in complexity_by_name:
                function.cyclomatic_complexity = complexity_by_name[qualified_name]
            else:
                fallback = next(
                    (
                        complexity
                        for name, complexity in complexity_by_name.items()
                        if name.endswith(f".{function.name}") or name == function.name
                    ),
                    None,
                )
                if fallback is not None:
                    function.cyclomatic_complexity = fallback
        try:
            file_info.maintainability_index = round(float(mi_visit(file_info.source, multi=True)), 1)
        except Exception:
            pass
        _recompute_file_metrics(file_info)
    return []


def detect_unused_candidates(files: dict[str, FileInfo], config: Config) -> tuple[list[UnusedCandidate], list[str]]:
    if not getattr(config, "use_vulture", True):
        return [], []
    try:
        from vulture import Vulture
    except ImportError:
        return [], ["Vulture unavailable; skipping unused-code candidates."]

    vulture = Vulture(verbose=False)
    try:
        vulture.scavenge([str(file_info.path) for file_info in files.values()])
        raw_items = vulture.get_unused_code()
    except Exception:
        return [], ["Vulture failed; skipping unused-code candidates."]

    path_to_module = {str(file_info.path): file_info.module_path for file_info in files.values()}
    results: list[UnusedCandidate] = []
    for item in raw_items:
        path = str(getattr(item, "filename", ""))
        candidate = UnusedCandidate(
            name=getattr(item, "name", ""),
            typ=getattr(item, "typ", ""),
            path=path,
            first_lineno=int(getattr(item, "first_lineno", 0) or 0),
            last_lineno=getattr(item, "last_lineno", None),
            size=getattr(item, "size", None),
            confidence=getattr(item, "confidence", None),
            message=getattr(item, "message", None),
            module_path=path_to_module.get(path),
        )
        if candidate.module_path and candidate.typ in {"function", "method"}:
            candidate.qualified_name, candidate.symbol_id = _match_symbol(files[candidate.module_path], candidate.name, candidate.first_lineno)
        if _keep_unused_candidate(candidate):
            results.append(candidate)
    results.sort(key=lambda item: (-(item.confidence or 0), item.path, item.first_lineno, item.name))
    return results, []


def _recompute_file_metrics(file_info: FileInfo) -> None:
    complexities = [function.cyclomatic_complexity for function in file_info.functions.values()]
    if complexities:
        file_info.avg_complexity = sum(complexities) / len(complexities)
        file_info.max_complexity = max(complexities)
        for function in sorted(file_info.functions.values(), key=lambda item: (item.lineno, item.qualified_name)):
            if function.cyclomatic_complexity == file_info.max_complexity:
                file_info.max_complexity_func = function.qualified_name
                break


def _match_symbol(file_info: FileInfo, name: str, lineno: int) -> tuple[str | None, str | None]:
    for function in sorted(file_info.functions.values(), key=lambda item: abs(item.lineno - lineno)):
        if function.name == name or function.qualified_name.endswith(f".{name}"):
            qualified_name = function.qualified_name
            return qualified_name, f"symbol:{function.module_path}.{qualified_name}"
    return None, None


def _keep_unused_candidate(candidate: UnusedCandidate) -> bool:
    if candidate.typ not in {"function", "method", "class", "property"}:
        return False
    return (candidate.confidence or 0) >= 60
