from __future__ import annotations

from collections import deque

from .models import FileInfo, FunctionInfo


def build_import_graph(files: dict[str, FileInfo]) -> dict[str, list[str]]:
    graph: dict[str, list[str]] = {}
    for module_path, file_info in files.items():
        graph[module_path] = sorted(dict.fromkeys(file_info.imports))
    for module_path, deps in graph.items():
        for dep in deps:
            if dep in files:
                files[dep].imported_by.append(module_path)
    for file_info in files.values():
        file_info.imported_by.sort()
        file_info.fan_in = len(file_info.imported_by)
        file_info.fan_out = len(file_info.imports)
    return graph


def detect_cycles(graph: dict[str, list[str]]) -> list[list[str]]:
    index = 0
    stack: list[str] = []
    on_stack: set[str] = set()
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    cycles: list[list[str]] = []

    def strong_connect(node: str) -> None:
        nonlocal index
        indices[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for neighbor in graph.get(node, []):
            if neighbor not in indices:
                strong_connect(neighbor)
                lowlinks[node] = min(lowlinks[node], lowlinks[neighbor])
            elif neighbor in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[neighbor])
        if lowlinks[node] == indices[node]:
            component: list[str] = []
            while stack:
                popped = stack.pop()
                on_stack.remove(popped)
                component.append(popped)
                if popped == node:
                    break
            component.sort()
            if len(component) > 1:
                cycles.append(component)

    for node in sorted(graph):
        if node not in indices:
            strong_connect(node)
    cycles.sort()
    return cycles


def compute_reachability(
    files: dict[str, FileInfo], graph: dict[str, list[str]], root_modules: list[str]
) -> tuple[int, int, dict[str, int]]:
    for file_info in files.values():
        file_info.depth_from_entry = -1
        file_info.reached_by_roots = []
    roots = [module for module in root_modules if module in files]
    if not roots:
        return 0, len(files), {}
    root_reachability: dict[str, int] = {}
    visited_any: set[str] = set()
    for root in roots:
        queue: deque[tuple[str, int]] = deque([(root, 0)])
        visited = {root}
        root_reachability[root] = 0
        while queue:
            node, depth = queue.popleft()
            file_info = files[node]
            if root not in file_info.reached_by_roots:
                file_info.reached_by_roots.append(root)
            if file_info.depth_from_entry < 0 or depth < file_info.depth_from_entry:
                file_info.depth_from_entry = depth
            if node not in visited_any:
                visited_any.add(node)
            root_reachability[root] += 1
            for neighbor in graph.get(node, []):
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                queue.append((neighbor, depth + 1))
    for file_info in files.values():
        file_info.reached_by_roots.sort()
    reachable = len(visited_any)
    return reachable, len(files) - reachable, root_reachability


def order_modules(files: dict[str, FileInfo], graph: dict[str, list[str]]) -> list[str]:
    reverse: dict[str, set[str]] = {node: set() for node in graph}
    indegree: dict[str, int] = {node: 0 for node in graph}
    for node, deps in graph.items():
        for dep in deps:
            reverse.setdefault(dep, set()).add(node)
            indegree[node] += 1
    ready = sorted((node for node, count in indegree.items() if count == 0), key=lambda item: (-files[item].importance_score, item))
    ordered: list[str] = []
    while ready:
        node = ready.pop(0)
        ordered.append(node)
        for dependent in sorted(reverse.get(node, [])):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)
        ready.sort(key=lambda item: (-files[item].importance_score, item))
    remainder = sorted((node for node in graph if node not in ordered), key=lambda item: (-files[item].importance_score, item))
    ordered.extend(remainder)
    return ordered


def render_import_tree(graph: dict[str, list[str]], files: dict[str, FileInfo], entry_module: str) -> list[str]:
    if entry_module not in graph:
        return [f"- missing entry module: {entry_module}"]
    lines: list[str] = []
    visited: set[str] = set()

    def walk(node: str, depth: int, path: set[str]) -> None:
        file_info = files[node]
        prefix = "  " * depth
        suffix = f" [sloc={file_info.sloc}, max_cc={file_info.max_complexity}]"
        if node in path:
            lines.append(f"{prefix}- {node} (cycle)")
            return
        if node in visited and depth > 0:
            lines.append(f"{prefix}- {node} (see above){suffix}")
            return
        visited.add(node)
        lines.append(f"{prefix}- {node}{suffix}")
        next_path = set(path)
        next_path.add(node)
        for child in graph.get(node, []):
            walk(child, depth + 1, next_path)

    walk(entry_module, 0, set())
    return lines


def render_import_forest(graph: dict[str, list[str]], files: dict[str, FileInfo], root_modules: list[str]) -> list[str]:
    roots = [module for module in root_modules if module in files]
    if not roots:
        return ["- missing root modules"]
    lines: list[str] = []
    for index, root in enumerate(sorted(dict.fromkeys(roots))):
        if index:
            lines.append("")
        lines.extend(render_import_tree(graph, files, root))
    return lines


def render_call_graph(files: dict[str, FileInfo], entry_module: str, entry_function: str, max_depth: int) -> list[str]:
    if entry_module not in files:
        return [f"- missing entry module: {entry_module}"]
    entry = _locate_entry_function(files[entry_module], entry_function)
    if entry is None:
        return [f"- missing entry function: {entry_function} in {entry_module}"]

    lines: list[str] = []

    def walk(
        module_path: str,
        qualified_name: str,
        depth: int,
        path_set: set[tuple[str, str]],
        chain: list[str],
    ) -> None:
        marker = f"{module_path}:{qualified_name}"
        chain_text = " -> ".join([*chain, marker])
        if (module_path, qualified_name) in path_set:
            lines.append(f"- {chain_text} (cycle)")
            return
        lines.append(f"- {chain_text}")
        if depth >= max_depth:
            return
        function = files[module_path].functions.get(qualified_name)
        if function is None:
            return
        next_path_set = set(path_set)
        next_path_set.add((module_path, qualified_name))
        next_chain = [*chain, marker]
        for resolved in function.resolved_calls:
            if resolved.target_module_path and resolved.target_module_path in files and resolved.target_qualified_name:
                walk(
                    resolved.target_module_path,
                    resolved.target_qualified_name,
                    depth + 1,
                    next_path_set,
                    next_chain,
                )
            else:
                lines.append(f"- {' -> '.join([*next_chain, f'{resolved.source_raw_name} ({resolved.resolution_kind})'])}")

    walk(entry.module_path, entry.qualified_name, 0, set(), [])
    return lines


def _locate_entry_function(file_info: FileInfo, entry_function: str) -> FunctionInfo | None:
    if entry_function in file_info.functions:
        return file_info.functions[entry_function]
    matches = [function for function in file_info.functions.values() if function.name == entry_function]
    matches.sort(key=lambda item: item.qualified_name)
    return matches[0] if matches else None
