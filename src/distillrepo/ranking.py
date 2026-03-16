from __future__ import annotations

from .models import Config, FileInfo, FunctionInfo


def score_files(files: dict[str, FileInfo], cycles: list[list[str]], entry_module: str, entry_function: str | None) -> None:
    cycle_members = {member for cycle in cycles for member in cycle}
    entry_targets = _entry_call_targets(files, entry_module, entry_function)
    for module_path, file_info in files.items():
        score = 0.0
        reasons: list[str] = []
        if module_path == entry_module:
            score += 8
            reasons.append("entry module")
        if file_info.depth_from_entry >= 0:
            score += 5
            if file_info.depth_from_entry == 0:
                reasons.append("entry path")
            else:
                score += max(0.0, 3.5 - file_info.depth_from_entry)
                reasons.append("reachable from entry")
        if module_path in cycle_members:
            score += 2.5
            reasons.append("participates in cycle")
        if module_path in entry_targets:
            score += 4
            reasons.append("called from entry")
        root_count = len(file_info.reached_by_roots)
        if root_count:
            score += min(root_count * 1.8, 7)
            reasons.append("review root coverage")
        if root_count >= 2:
            score += min((root_count - 1) * 1.4, 4)
            reasons.append("shared across roots")
        score += min(file_info.fan_in * 1.5, 8)
        score += min(file_info.max_complexity * 0.7, 10)
        score += min(file_info.sloc / 120, 4)
        score += max(0.0, (70 - file_info.maintainability_index) / 12)
        if file_info.fan_in >= 3:
            reasons.append("high fan-in")
        if file_info.max_complexity >= 10:
            reasons.append("complexity hotspot")
        file_info.importance_score = round(score, 2)
        file_info.why_important = sorted(dict.fromkeys(reasons))


def collect_hotspots(files: dict[str, FileInfo], threshold: int) -> list[FunctionInfo]:
    hotspots: list[FunctionInfo] = []
    for file_info in files.values():
        for function in file_info.functions.values():
            function.hotspot_score = _hotspot_score(file_info, function)
            if function.hotspot_score >= threshold:
                hotspots.append(function)
    hotspots.sort(key=lambda item: (-item.hotspot_score, -item.cyclomatic_complexity, item.module_path, item.qualified_name))
    return hotspots


def assign_inclusion_modes(files: dict[str, FileInfo], ordered_modules: list[str], config: Config) -> None:
    for index, module_path in enumerate(ordered_modules):
        file_info = files[module_path]
        if not config.include_unreachable and file_info.depth_from_entry < 0:
            file_info.inclusion_mode = "excluded"
            continue
        file_info.inclusion_mode = _mode_for_rank(index, file_info, config)


def _mode_for_rank(index: int, file_info: FileInfo, config: Config) -> str:
    if config.review_mode in {"concat", "plain_concat"}:
        return "full"
    if config.review_mode == "review":
        if file_info.depth_from_entry < 0:
            return "signature"
        if index < 2 or (len(file_info.reached_by_roots) >= 2 and index < 3):
            return "full"
        if index < 5:
            return "summary"
        if index < 10:
            return "signature"
        return "excluded"
    if config.review_mode == "architecture":
        return "summary" if index < 5 else "signature"
    if config.review_mode == "hotspots":
        return "full" if _file_has_hotspot(file_info, config.complexity_hotspot_threshold) else "summary"
    if config.review_mode == "entrypath":
        if file_info.depth_from_entry < 0:
            return "signature"
        return "full" if file_info.depth_from_entry <= 1 else "summary"
    if config.review_mode == "budgeted":
        if file_info.depth_from_entry < 0:
            return "excluded"
        if index < 2:
            return "summary"
        if index < 6:
            return "signature"
        return "excluded"
    return "full"


def generate_observations(files: dict[str, FileInfo], cycles: list[list[str]], hotspots: list[FunctionInfo]) -> list[str]:
    observations: list[str] = []
    if not files:
        return observations
    most_imported = max(files.values(), key=lambda item: (item.fan_in, item.module_path))
    observations.append(f"Highest fan-in module: {most_imported.module_path} ({most_imported.fan_in}).")
    most_complex = max(files.values(), key=lambda item: (item.max_complexity, item.module_path))
    observations.append(
        f"Highest complexity module: {most_complex.module_path} ({most_complex.max_complexity} in {most_complex.max_complexity_func or 'n/a'})."
    )
    lowest_mi = min(files.values(), key=lambda item: (item.maintainability_index, item.module_path))
    observations.append(f"Lowest maintainability module: {lowest_mi.module_path} ({lowest_mi.maintainability_index:.1f}).")
    unreachable = sorted(file_info.module_path for file_info in files.values() if file_info.depth_from_entry < 0)
    if unreachable:
        observations.append(f"Not reached from root set: {', '.join(unreachable[:6])}.")
    if cycles:
        observations.append(f"Import cycles detected: {len(cycles)}.")
    if hotspots:
        top = hotspots[0]
        observations.append(
            f"Top hotspot: {top.module_path}:{top.qualified_name} "
            f"(score={top.hotspot_score:.1f}, cc={top.cyclomatic_complexity})."
        )
    return observations


def _entry_call_targets(files: dict[str, FileInfo], entry_module: str, entry_function: str | None) -> set[str]:
    if not entry_function:
        return set()
    file_info = files.get(entry_module)
    if file_info is None:
        return set()
    entry = file_info.functions.get(entry_function)
    if entry is None:
        for function in file_info.functions.values():
            if function.name == entry_function:
                entry = function
                break
    if entry is None:
        return set()
    return {resolved.target_module_path for resolved in entry.resolved_calls if resolved.target_module_path is not None}


def _hotspot_score(file_info: FileInfo, function: FunctionInfo) -> float:
    score = 0.0
    score += function.cyclomatic_complexity * 0.45
    score += min(file_info.fan_in, 8) * 0.9
    score += min(file_info.fan_out, 8) * 0.35
    score += min(len(function.resolved_calls), 10) * 0.25
    score += max(0, 3 - max(file_info.depth_from_entry, 0)) * 1.0 if file_info.depth_from_entry >= 0 else 0.0
    score += 1.0 if not function.name.startswith("_") else 0.0
    score += 0.6 if function.docstring else 0.0
    if _is_parser_boilerplate(function):
        score *= 0.45
    if function.name in {"__init__", "__repr__", "__str__"}:
        score *= 0.65
    return round(score, 2)


def _is_parser_boilerplate(function: FunctionInfo) -> bool:
    lowered_name = function.name.lower()
    parserish_names = {"parse_args", "parse_common_args", "build_parser", "get_parser", "cli_parser"}
    if lowered_name in parserish_names:
        return True
    raw_calls = " ".join(function.raw_calls).lower()
    parserish_markers = ("add_argument", "add_parser", "set_defaults", "argumentparser", "parse_args")
    return sum(marker in raw_calls for marker in parserish_markers) >= 2


def _file_has_hotspot(file_info: FileInfo, threshold: int) -> bool:
    return any(function.hotspot_score >= threshold for function in file_info.functions.values())
