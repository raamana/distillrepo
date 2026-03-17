from __future__ import annotations

from .models import Config, FileInfo, ResolvedCall


def resolve_calls(files: dict[str, FileInfo], config: Config) -> list[str]:
    warnings: list[str] = []
    jedi = _load_jedi(config)
    project = None
    script_by_path: dict[str, object] = {}
    if jedi is not None:
        try:
            project = jedi.Project(path=str(config.package_root.parent))
        except Exception as exc:
            project = None
            warnings.append(f"Jedi project initialization failed; using heuristic call resolution only ({exc.__class__.__name__}).")
    for file_info in files.values():
        script = None
        if jedi is not None and project is not None:
            cache_key = str(file_info.path)
            script = script_by_path.get(cache_key)
            if script is None:
                try:
                    script = jedi.Script(code=file_info.source, path=cache_key, project=project)
                except Exception:
                    script = None
                if script is not None:
                    script_by_path[cache_key] = script
        for function in file_info.functions.values():
            resolved: list[ResolvedCall] = []
            seen: set[tuple[str | None, str | None, str]] = set()
            for call_site in function.call_sites:
                result = None
                if script is not None:
                    result = _resolve_with_jedi(script, call_site.lineno, call_site.col_offset, config)
                if result is None:
                    result = _resolve_heuristic(files, file_info, function.class_name, call_site.raw_name)
                key = (result.target_module_path, result.target_qualified_name, result.resolution_kind)
                if key in seen:
                    continue
                seen.add(key)
                resolved.append(result)
            function.resolved_calls = resolved
    if config.use_jedi and jedi is None:
        warnings.append("Jedi unavailable; using heuristic call resolution only.")
    return warnings


def _load_jedi(config: Config):
    if not config.use_jedi:
        return None
    try:
        import jedi  # type: ignore
    except ImportError:
        return None
    return jedi


def _resolve_with_jedi(script: object, lineno: int, col_offset: int, config: Config) -> ResolvedCall | None:
    try:
        goto = getattr(script, "goto", None)
        if goto is None:
            return None
        names = goto(line=lineno, column=col_offset, follow_imports=True)
    except Exception:
        return None
    for name in names:
        module_path = getattr(name, "module_name", None)
        if not module_path or not module_path.startswith(config.package_name):
            continue
        qualified_name = getattr(name, "full_name", None) or getattr(name, "name", None)
        return ResolvedCall(
            source_raw_name=name.name,
            target_module_path=module_path,
            target_qualified_name=qualified_name,
            resolution_kind="jedi",
            confidence=0.95,
        )
    return None


def _resolve_heuristic(
    files: dict[str, FileInfo], file_info: FileInfo, class_name: str | None, raw_name: str
) -> ResolvedCall:
    if raw_name.startswith("self.") and class_name:
        method_name = raw_name.split(".", 1)[1]
        target_qname = f"{class_name}.{method_name}"
        if target_qname in file_info.functions:
            return ResolvedCall(raw_name, file_info.module_path, target_qname, "self-method", 0.8)

    if "." in raw_name:
        left, right = raw_name.split(".", 1)
        bound = file_info.import_bindings.get(left)
        if bound:
            target_module = _closest_module(files, bound)
            if target_module:
                resolved_qname = _find_target_qualified_name(files, target_module, right)
                return ResolvedCall(raw_name, target_module, resolved_qname or right, "import-binding", 0.72)

    bound = file_info.import_bindings.get(raw_name)
    if bound:
        target_module = _closest_module(files, bound)
        if target_module:
            target_name = bound.rsplit(".", 1)[-1]
            resolved_qname = _find_target_qualified_name(files, target_module, target_name)
            return ResolvedCall(raw_name, target_module, resolved_qname or target_name, "import-binding", 0.7)

    if raw_name in file_info.functions:
        return ResolvedCall(raw_name, file_info.module_path, raw_name, "same-module", 0.68)

    candidate = _find_function_by_simple_name(files, raw_name)
    if candidate is not None:
        return ResolvedCall(raw_name, candidate.module_path, candidate.qualified_name, "same-name-heuristic", 0.42)

    return ResolvedCall(raw_name, None, None, "unresolved", 0.0)


def _closest_module(files: dict[str, FileInfo], target: str) -> str | None:
    candidate = target
    while candidate:
        if candidate in files:
            return candidate
        if "." not in candidate:
            break
        candidate = candidate.rsplit(".", 1)[0]
    return None


def _find_target_qualified_name(files: dict[str, FileInfo], module_path: str, simple_name: str) -> str | None:
    file_info = files.get(module_path)
    if file_info is None:
        return None
    if simple_name in file_info.functions:
        return simple_name
    for qualified_name, function in file_info.functions.items():
        if function.name == simple_name or qualified_name.endswith(f".{simple_name}"):
            return qualified_name
    return None


def _find_function_by_simple_name(files: dict[str, FileInfo], simple_name: str):
    candidates = []
    for file_info in files.values():
        for function in file_info.functions.values():
            if function.name == simple_name:
                candidates.append(function)
    candidates.sort(key=lambda item: (item.module_path, item.qualified_name))
    return candidates[0] if candidates else None
