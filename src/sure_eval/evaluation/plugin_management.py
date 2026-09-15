"""Project-local plugin declarations, inspection, locking, and status checks."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import os
import sys
import tempfile
import threading
import warnings
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml

import sure_eval
from sure_eval.evaluation.core.node_protocol import registration_from_module

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10 CI
    import tomli as tomllib

PLUGIN_DIRNAME = ".sure-eval"
CONFIG_FILENAME = "plugins.yaml"
LOCK_FILENAME = "plugins.lock.json"
MANIFEST_FILENAME = "sure_eval_plugin.yaml"
LOCK_FORMAT = "sure-eval.plugins.lock.v1"
HASH_FORMAT = "sure-eval.plugin-tree.v1"
HASH_EXTENSIONS = {".py", ".yaml", ".yml", ".toml", ".json", ".lock", ".txt"}
HASH_EXCLUDED_DIRS = {
    ".git",
    ".venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".sure-eval",
    "__pycache__",
    "__pypackages__",
    "checkpoint",
    "checkpoints",
    "cache",
    "caches",
}
_PACKAGE_IMPORT_LOCK = threading.RLock()


class PluginError(ValueError):
    """A user-facing plugin declaration or lock error."""


@dataclass(frozen=True)
class PluginLayout:
    """Resolved source layout for a local plugin directory."""

    root: Path
    kind: str
    import_root: Path | None = None
    package_module: str = ""
    node_module: str = ""
    route_module: str = ""


@dataclass(frozen=True)
class PluginInspection:
    name: str
    source: str
    path: Path
    node_ids: tuple[str, ...]
    pipeline_ids: tuple[str, ...]
    tasks: tuple[str, ...]
    effective_kind: str
    routes: tuple[dict[str, Any], ...]
    manifest: dict[str, Any]
    content_hash: str
    hash_format: str = HASH_FORMAT
    resource_manifest: str = ""
    resource_hashes: dict[str, str] | None = None

    def lock_entry(self, *, resolved_path: str, portable: bool = True) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "name": self.name,
            "source": self.source,
            "resolved_path": resolved_path,
            "hash_format": self.hash_format,
            "content_hash": self.content_hash,
            "node_ids": list(self.node_ids),
            "pipeline_ids": list(self.pipeline_ids),
            "tasks": list(self.tasks),
            "effective_kind": self.effective_kind,
        }
        if self.resource_manifest:
            entry["resource_manifest"] = self.resource_manifest
            entry["resource_hashes"] = dict(self.resource_hashes or {})
        if not portable:
            entry["portable"] = False
        return entry


def project_root(project_dir: str | Path | None = None) -> Path:
    return Path(project_dir or Path.cwd()).expanduser().resolve()


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise PluginError(f"Cannot read plugin config {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PluginError(f"Plugin config must be a mapping: {path}")
    return value


def _mapping_module(manifest: dict[str, Any], key: str) -> str:
    value = manifest.get(key)
    if value is None:
        return ""
    if not isinstance(value, dict) or not isinstance(value.get("module"), str):
        raise PluginError(f"Manifest {key}.module must be a non-empty string")
    module = value["module"].strip()
    if not module:
        raise PluginError(f"Manifest {key}.module must be a non-empty string")
    return module


def _module_parts(module: str, *, field: str) -> tuple[str, ...]:
    parts = tuple(module.split("."))
    if not parts or any(not part.isidentifier() for part in parts):
        raise PluginError(f"Manifest {field} is not a valid Python module: {module!r}")
    return parts


def _module_source(import_root: Path, module: str, *, field: str) -> Path:
    parts = _module_parts(module, field=field)
    module_base = import_root.joinpath(*parts)
    module_file = module_base.with_suffix(".py")
    package_file = module_base / "__init__.py"
    if module_file.is_file():
        resolved = module_file.resolve()
    elif package_file.is_file():
        resolved = package_file.resolve()
    else:
        raise PluginError(f"Manifest {field} does not resolve inside src/: {module!r}")
    if not _is_within(resolved, import_root.resolve()):
        raise PluginError(f"Manifest {field} resolves outside src/: {module!r}")
    return resolved


def resolve_plugin_layout(
    path: str | Path,
    manifest: dict[str, Any] | None = None,
) -> PluginLayout:
    """Resolve either the legacy root-file layout or the v1 package layout."""

    root = Path(path).expanduser().resolve()
    manifest_path = root / MANIFEST_FILENAME
    payload = manifest
    if payload is None:
        payload = _read_yaml(manifest_path) if manifest_path.exists() else {}
    package_keys = {"package", "node", "route"}
    package_layout = any(key in payload for key in package_keys)
    legacy_layout = "nodes" in payload or "routes" in payload
    if package_layout and legacy_layout:
        raise PluginError(
            "Package-layout module fields cannot be mixed with nodes/routes file fields"
        )
    if (
        not package_layout
        and (root / "src").is_dir()
        and not (root / "node.py").exists()
        and not (root / "routes.py").exists()
    ):
        raise PluginError(f"Package-layout plugin requires {MANIFEST_FILENAME}: {root}")
    if not package_layout:
        return PluginLayout(root=root, kind="legacy")
    if not manifest_path.is_file():
        raise PluginError(f"Package-layout plugin requires {MANIFEST_FILENAME}: {root}")

    package_module = _mapping_module(payload, "package")
    node_module = _mapping_module(payload, "node")
    route_module = _mapping_module(payload, "route")
    if not package_module:
        raise PluginError("Package-layout plugin requires package.module")
    if not node_module and not route_module:
        raise PluginError("Package-layout plugin requires node.module, route.module, or both")
    package_parts = _module_parts(package_module, field="package.module")
    import_root = root / "src"
    package_path = import_root.joinpath(*package_parts)
    if not package_path.is_dir():
        raise PluginError(
            f"Manifest package.module does not resolve below src/: {package_module!r}"
        )
    current = import_root
    for part in package_parts:
        current /= part
        if not _is_within(current.resolve(), import_root.resolve()):
            raise PluginError(f"Manifest package.module resolves outside src/: {package_module!r}")
        if not (current / "__init__.py").is_file():
            raise PluginError(f"Package path requires __init__.py: {current}")
    for field, module in (("node.module", node_module), ("route.module", route_module)):
        if not module:
            continue
        if module != package_module and not module.startswith(package_module + "."):
            raise PluginError(f"Manifest {field} must be inside package.module {package_module!r}")
        _module_source(import_root, module, field=field)
    return PluginLayout(
        root=root,
        kind="package",
        import_root=import_root,
        package_module=package_module,
        node_module=node_module,
        route_module=route_module,
    )


def _module_origin(module: ModuleType) -> Path | None:
    raw = getattr(module, "__file__", None)
    return Path(raw).resolve() if raw else None


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _clear_package_bytecode(layout: PluginLayout) -> None:
    assert layout.import_root is not None
    package_path = layout.import_root.joinpath(*layout.package_module.split("."))
    for bytecode in package_path.rglob("*.pyc"):
        try:
            bytecode.unlink()
        except OSError:
            pass


def _module_from_package(layout: PluginLayout, module_name: str, *, label: str) -> ModuleType:
    assert layout.import_root is not None
    _module_source(layout.import_root, module_name, field=f"{label}.module")
    with _PACKAGE_IMPORT_LOCK:
        importlib.invalidate_caches()
        _clear_package_bytecode(layout)
        for name in tuple(sys.modules):
            if name == layout.package_module or name.startswith(layout.package_module + "."):
                del sys.modules[name]
        sys.path.insert(0, str(layout.import_root))
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            raise PluginError(f"Failed to import plugin {module_name!r}: {exc}") from exc
        finally:
            try:
                sys.path.remove(str(layout.import_root))
            except ValueError:
                pass
    origin = _module_origin(module)
    if origin is None or not _is_within(origin, layout.import_root.resolve()):
        raise PluginError(
            f"Plugin module {module_name!r} resolved outside {layout.import_root}: {origin}"
        )
    return module


def load_local_node_module(path: str | Path) -> ModuleType | None:
    """Load the declared node module from either supported local layout."""

    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        return None
    manifest_path = root / MANIFEST_FILENAME
    manifest = _read_yaml(manifest_path) if manifest_path.exists() else {}
    layout = resolve_plugin_layout(root, manifest)
    if layout.kind == "package":
        return (
            _module_from_package(layout, layout.node_module, label="node")
            if layout.node_module
            else None
        )
    node_path = root / "node.py"
    return _module_from_path(node_path, label="node") if node_path.exists() else None


def load_local_route_module(path: str | Path) -> ModuleType | None:
    """Load the declared route module from either supported local layout."""

    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        return None
    manifest_path = root / MANIFEST_FILENAME
    manifest = _read_yaml(manifest_path) if manifest_path.exists() else {}
    layout = resolve_plugin_layout(root, manifest)
    if layout.kind == "package":
        return (
            _module_from_package(layout, layout.route_module, label="route")
            if layout.route_module
            else None
        )
    route_path = root / "routes.py"
    return _module_from_path(route_path, label="routes") if route_path.exists() else None


def _load_config(project_dir: str | Path | None = None) -> dict[str, Any]:
    root = project_root(project_dir)
    _recover_transaction(root / PLUGIN_DIRNAME)
    path = root / PLUGIN_DIRNAME / CONFIG_FILENAME
    if not path.exists():
        return {"plugins": []}
    payload = _read_yaml(path)
    plugins = payload.get("plugins", [])
    if not isinstance(plugins, list):
        raise PluginError(f"plugins must be a list: {path}")
    if any(not isinstance(entry, dict) for entry in plugins):
        raise PluginError(f"Each plugin entry must be a mapping: {path}")
    names = [str(entry.get("name")) for entry in plugins if isinstance(entry, dict)]
    if len(names) != len(set(names)):
        raise PluginError(f"Plugin names must be unique: {path}")
    return payload


def _load_lock(project_dir: str | Path | None = None) -> dict[str, Any]:
    root = project_root(project_dir)
    _recover_transaction(root / PLUGIN_DIRNAME)
    path = root / PLUGIN_DIRNAME / LOCK_FILENAME
    if not path.exists():
        return {"format": LOCK_FORMAT, "plugins": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PluginError(f"Cannot read plugin lock {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("format") != LOCK_FORMAT:
        raise PluginError(f"Invalid plugin lock format: {path}")
    plugins = payload.get("plugins", [])
    if not isinstance(plugins, list):
        raise PluginError(f"Lock plugins must be a list: {path}")
    if any(not isinstance(entry, dict) for entry in plugins):
        raise PluginError(f"Each lock plugin entry must be a mapping: {path}")
    names = [str(entry.get("name")) for entry in plugins]
    if len(names) != len(set(names)):
        raise PluginError(f"Lock plugin names must be unique: {path}")
    return payload


def _module_from_path(path: Path, *, label: str) -> ModuleType:
    resolved = path.resolve()
    if not resolved.exists() or resolved.suffix != ".py":
        raise PluginError(f"{label} must be an existing .py file: {path}")
    module_name = f"_sure_eval_plugin_{label}_{abs(hash(str(resolved))):x}"
    spec = importlib.util.spec_from_file_location(module_name, resolved)
    if spec is None or spec.loader is None:
        raise PluginError(f"Cannot load plugin module: {resolved}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise PluginError(f"Failed to import plugin {resolved}: {exc}") from exc
    return module


def _node_registration(plugin_path: Path) -> Any | None:
    module = load_local_node_module(plugin_path)
    if module is None:
        return None
    try:
        return registration_from_module(module, source="local")
    except Exception as exc:
        raise PluginError(f"Invalid node module in {plugin_path}: {exc}") from exc


def _route_task(route: dict[str, Any]) -> str | None:
    executor = str(route.get("executor") or "")
    parts = executor.split(".")
    if parts[:3] == ["sure_eval", "evaluation", "tasks"] and len(parts) >= 4:
        return parts[3]
    pipeline_id = str(route.get("pipeline_id") or "")
    return pipeline_id.split(".", 1)[0] if pipeline_id else None


def _route_module(plugin_path: Path) -> ModuleType | None:
    return load_local_route_module(plugin_path)


def _load_pyproject(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            payload = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise PluginError(f"Cannot read pyproject.toml {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise PluginError(f"pyproject.toml must be a mapping: {path}")
    return payload


def _entry_point_group(pyproject: dict[str, Any], group: str) -> dict[str, str]:
    project = pyproject.get("project") or {}
    if not isinstance(project, dict):
        raise PluginError("pyproject.toml project must be a mapping")
    groups = project.get("entry-points") or {}
    if not isinstance(groups, dict):
        raise PluginError("pyproject.toml project.entry-points must be a mapping")
    raw = groups.get(group) or {}
    if not isinstance(raw, dict) or any(not isinstance(value, str) for value in raw.values()):
        raise PluginError(f"pyproject.toml entry point group {group!r} must be a string mapping")
    return {str(name): value for name, value in raw.items()}


def _validate_pyproject_entry_points(
    plugin_path: Path,
    layout: PluginLayout,
    *,
    node_id: str | None,
    tasks: tuple[str, ...],
) -> None:
    pyproject_path = plugin_path / "pyproject.toml"
    if layout.kind != "package" or not pyproject_path.exists():
        return
    pyproject = _load_pyproject(pyproject_path)
    nodes = _entry_point_group(pyproject, "sure_eval.nodes")
    routes = _entry_point_group(pyproject, "sure_eval.routes")
    expected_nodes = {node_id: layout.node_module} if node_id else {}
    expected_routes = {task: layout.route_module for task in tasks} if layout.route_module else {}
    if nodes != expected_nodes:
        raise PluginError(
            "pyproject.toml sure_eval.nodes must match NODE_ID and manifest node.module: "
            f"expected {expected_nodes!r}, got {nodes!r}"
        )
    if routes != expected_routes:
        raise PluginError(
            "pyproject.toml sure_eval.routes must map each route task to manifest "
            f"route.module: expected {expected_routes!r}, got {routes!r}"
        )


def _validate_routes(plugin_path: Path, module: ModuleType | None) -> tuple[dict[str, Any], ...]:
    if module is None:
        return ()
    raw_routes = getattr(module, "ROUTES", None)
    if raw_routes is None:
        raise PluginError(f"routes.py in {plugin_path} must expose ROUTES")
    if not isinstance(raw_routes, (list, tuple)):
        raise PluginError(f"ROUTES in {plugin_path} must be a list")
    routes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_routes:
        if not isinstance(raw, dict):
            raise PluginError(f"Each route in {plugin_path} must be a mapping")
        missing = [
            key
            for key in ("pipeline_id", "metric", "nodes", "input_contract", "executor")
            if not raw.get(key)
        ]
        if missing:
            raise PluginError(f"Route in {plugin_path} is missing: {', '.join(missing)}")
        pipeline_id = str(raw["pipeline_id"])
        if pipeline_id in seen:
            raise PluginError(f"Duplicate pipeline_id in {plugin_path}: {pipeline_id}")
        seen.add(pipeline_id)
        if not isinstance(raw["nodes"], (list, tuple)):
            raise PluginError(f"Route nodes must be a list: {pipeline_id}")
        routes.append(dict(raw))
    return tuple(routes)


def _effective_kind(node_ids: tuple[str, ...], routes: tuple[dict[str, Any], ...]) -> str:
    if node_ids and routes:
        return "node-and-route"
    if node_ids:
        return "node"
    if routes:
        return "route"
    raise PluginError("A plugin must provide node.py, routes.py, or both")


def _validate_declared_manifest(manifest: dict[str, Any], effective_kind: str) -> None:
    declared_kind = manifest.get("kind")
    if declared_kind and declared_kind != effective_kind:
        raise PluginError(f"Plugin kind {declared_kind!r} does not match {effective_kind!r}")
    api = manifest.get("plugin_api")
    if api and api != "sure-eval.plugin.v1":
        raise PluginError(f"Unsupported plugin_api: {api}")
    required = str(manifest.get("requires_sure_eval") or "")
    if required and required.startswith(">="):
        try:
            if tuple(map(int, sure_eval.__version__.split(".")[:2])) < tuple(
                map(int, required[2:].split(".")[:2])
            ):
                raise PluginError(
                    f"Plugin requires SURE-EVAL {required}, current is {sure_eval.__version__}"
                )
        except ValueError as exc:
            raise PluginError(f"Invalid requires_sure_eval: {required}") from exc


def _iter_hash_files(root: Path, include: list[str] | None = None) -> list[Path]:
    included = {str(item).replace("\\", "/") for item in (include or [])}
    files: list[Path] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        parts = relative.parts
        if path.is_dir():
            continue
        if any(part.startswith(".") or part in HASH_EXCLUDED_DIRS for part in parts[:-1]):
            continue
        if path.is_symlink():
            continue
        rel = relative.as_posix()
        if rel in included or path.suffix.lower() in HASH_EXTENSIONS:
            files.append(path)
    return sorted(files, key=lambda item: item.relative_to(root).as_posix())


def content_hash(root: Path, include: list[str] | None = None) -> str:
    digest = hashlib.sha256()
    for path in _iter_hash_files(root, include):
        rel = path.relative_to(root).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def _file_hash(path: Path) -> str:
    """Hash a declared resource manifest with the same newline normalization."""

    digest = hashlib.sha256()
    digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return f"sha256:{digest.hexdigest()}"


def inspect_plugin(path: str | Path, *, name: str | None = None) -> PluginInspection:
    plugin_path = Path(path).expanduser().resolve()
    if not plugin_path.is_dir():
        raise PluginError(f"Plugin path must be a directory: {plugin_path}")
    manifest_path = plugin_path / MANIFEST_FILENAME
    manifest = _read_yaml(manifest_path) if manifest_path.exists() else {}
    if not isinstance(manifest, dict):
        raise PluginError(f"Plugin manifest must be a mapping: {manifest_path}")
    layout = resolve_plugin_layout(plugin_path, manifest)
    plugin_name = str(name or manifest.get("name") or plugin_path.name)
    node = _node_registration(plugin_path)
    node_ids = (node.node_id,) if node else ()
    routes = _validate_routes(plugin_path, _route_module(plugin_path))
    effective_kind = _effective_kind(node_ids, routes)
    _validate_declared_manifest(manifest, effective_kind)
    declared_nodes = manifest.get("nodes")
    if declared_nodes is not None and bool(declared_nodes) != bool(node_ids):
        raise PluginError("Manifest nodes does not match node.py presence")
    if manifest.get("routes") is not None and bool(manifest.get("routes")) != bool(routes):
        raise PluginError("Manifest routes does not match routes.py presence")
    pipeline_ids = tuple(str(route["pipeline_id"]) for route in routes)
    tasks = tuple(sorted({task for task in (_route_task(route) for route in routes) if task}))
    _validate_pyproject_entry_points(
        plugin_path,
        layout,
        node_id=node.node_id if node else None,
        tasks=tasks,
    )
    raw_hash_include = manifest.get("hash_include") or []
    if not isinstance(raw_hash_include, list):
        raise PluginError("hash_include must be a list of relative file paths")
    hash_include = list(raw_hash_include)
    for item in hash_include:
        include_path = (plugin_path / str(item)).resolve()
        if plugin_path not in include_path.parents and include_path != plugin_path:
            raise PluginError(f"hash_include must stay inside plugin: {item}")
        if not include_path.is_file():
            raise PluginError(f"hash_include file does not exist: {item}")
    resource_manifest = str(manifest.get("resource_manifest") or "")
    resource_hashes: dict[str, str] = {}
    if resource_manifest:
        resource_path = (plugin_path / resource_manifest).resolve()
        if plugin_path not in resource_path.parents or not resource_path.exists():
            raise PluginError(f"resource_manifest must point inside plugin: {resource_manifest}")
        if not resource_path.is_file():
            raise PluginError(f"resource_manifest must point to a file: {resource_manifest}")
        resource_hashes[resource_manifest.replace("\\", "/")] = _file_hash(resource_path)
    return PluginInspection(
        name=plugin_name,
        source="path",
        path=plugin_path,
        node_ids=node_ids,
        pipeline_ids=pipeline_ids,
        tasks=tasks,
        effective_kind=effective_kind,
        routes=routes,
        manifest=manifest,
        content_hash=content_hash(plugin_path, hash_include),
        resource_manifest=resource_manifest,
        resource_hashes=resource_hashes,
    )


def _recover_transaction(root: Path) -> None:
    marker = root / ".plugins.transaction.json"
    if not marker.exists():
        return
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
        for key in ("config_tmp", "lock_tmp"):
            temp = Path(payload[key])
            target = root / (CONFIG_FILENAME if key == "config_tmp" else LOCK_FILENAME)
            if temp.exists():
                os.replace(temp, target)
        marker.unlink(missing_ok=True)
    except Exception as exc:
        raise PluginError(f"Cannot recover plugin transaction: {exc}") from exc


def _write_pair(project_dir: Path, config: dict[str, Any], lock: dict[str, Any]) -> None:
    root = project_dir / PLUGIN_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    _recover_transaction(root)
    config_fd, config_name = tempfile.mkstemp(prefix="plugins.", suffix=".yaml.tmp", dir=root)
    lock_fd, lock_name = tempfile.mkstemp(prefix="plugins.", suffix=".json.tmp", dir=root)
    os.close(config_fd)
    os.close(lock_fd)
    config_tmp = Path(config_name)
    lock_tmp = Path(lock_name)
    try:
        config_tmp.write_text(
            yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        lock_tmp.write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for temp in (config_tmp, lock_tmp):
            with temp.open("rb") as handle:
                os.fsync(handle.fileno())
        marker = root / ".plugins.transaction.json"
        marker.write_text(
            json.dumps({"config_tmp": str(config_tmp), "lock_tmp": str(lock_tmp)}), encoding="utf-8"
        )
        with marker.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(config_tmp, root / CONFIG_FILENAME)
        os.replace(lock_tmp, root / LOCK_FILENAME)
        marker.unlink(missing_ok=True)
        fd = os.open(root, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        config_tmp.unlink(missing_ok=True)
        lock_tmp.unlink(missing_ok=True)


def _entry_for_name(entries: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    for entry in entries:
        if entry.get("name") == name:
            return entry
    return None


def add_plugin(
    path: str | Path,
    *,
    project_dir: str | Path | None = None,
    name: str | None = None,
    replace: bool = False,
) -> PluginInspection:
    if isinstance(path, str) and path.startswith("open-bench://"):
        raise PluginError(
            "Open-Bench plugin sources are reserved for the second phase; "
            "add a local plugin directory in phase 1"
        )
    root = project_root(project_dir)
    inspection = inspect_plugin(path, name=name)
    config = _load_config(root)
    lock = _load_lock(root)
    entries = list(config.get("plugins") or [])
    lock_entries = list(lock.get("plugins") or [])
    existing = _entry_for_name(entries, inspection.name)
    if existing:
        existing_path = (
            (root / existing["path"]).resolve()
            if existing.get("path_mode") == "project_relative"
            else Path(existing["path"]).resolve()
        )
        existing_hash = _entry_for_name(lock_entries, inspection.name) or {}
        same = (
            existing.get("source") == "path"
            and existing_path == inspection.path
            and existing_hash.get("content_hash") == inspection.content_hash
        )
        if same:
            from sure_eval.evaluation.node_registry import get_registry

            registry = get_registry()
            registry.invalidate_project_plugins()
            registry.ensure_project_plugins(root)
            return inspection
        if not replace:
            raise PluginError(
                f"Plugin name already exists with a different source or hash: {inspection.name}"
            )
        entries = [entry for entry in entries if entry.get("name") != inspection.name]
        lock_entries = [entry for entry in lock_entries if entry.get("name") != inspection.name]
    try:
        relative = inspection.path.relative_to(root)
        config_path_value = relative.as_posix()
        path_mode = "project_relative"
        portable = True
    except ValueError:
        config_path_value = str(inspection.path)
        path_mode = "absolute"
        portable = False
    entries.append(
        {
            "name": inspection.name,
            "source": "path",
            "path": config_path_value,
            "path_mode": path_mode,
            **({"portable": False} if not portable else {}),
        }
    )
    lock_entries.append(inspection.lock_entry(resolved_path=config_path_value, portable=portable))
    _write_pair(root, {"plugins": entries}, {"format": LOCK_FORMAT, "plugins": lock_entries})
    from sure_eval.evaluation.node_registry import get_registry

    registry = get_registry()
    registry.invalidate_project_plugins()
    registry.ensure_project_plugins(root)
    return inspection


def plugin_records(project_dir: str | Path | None = None) -> list[dict[str, Any]]:
    root = project_root(project_dir)
    config = _load_config(root)
    lock = _load_lock(root)
    records: list[dict[str, Any]] = []
    for entry in config.get("plugins") or []:
        name = str(entry.get("name") or "")
        try:
            path = (
                (root / entry["path"]).resolve()
                if entry.get("path_mode") == "project_relative"
                else Path(entry["path"]).expanduser().resolve()
            )
            inspection = inspect_plugin(path, name=name)
            lock_entry = _entry_for_name(list(lock.get("plugins") or []), name)
            if entry.get("path_mode") == "project_relative":
                try:
                    expected_resolved_path = path.relative_to(root).as_posix()
                except ValueError as exc:
                    raise PluginError("project_relative plugin path escapes project root") from exc
            else:
                expected_resolved_path = str(path)
            if not lock_entry:
                lock_status = "missing"
            elif str(lock_entry.get("resolved_path")) != expected_resolved_path:
                lock_status = "invalid"
            elif lock_entry.get("content_hash") != inspection.content_hash:
                lock_status = "drifted"
            elif (
                inspection.resource_hashes
                and lock_entry.get("resource_hashes") != inspection.resource_hashes
            ):
                lock_status = "drifted"
            else:
                lock_status = "ready"
            payload = {
                "name": name,
                "source": "path",
                "path": str(path),
                "node_ids": list(inspection.node_ids),
                "pipeline_ids": list(inspection.pipeline_ids),
                "tasks": list(inspection.tasks),
                "effective_kind": inspection.effective_kind,
                "lock_status": lock_status,
                "env_status": "unknown",
                "reason": (
                    ""
                    if lock_status == "ready"
                    else f"{lock_status}: lock does not match plugin content"
                ),
            }
        except PluginError as exc:
            lock_entry = _entry_for_name(list(lock.get("plugins") or []), name) or {}
            raw_path = entry.get("path", "")
            resolved_entry_path = (
                (root / raw_path).resolve()
                if entry.get("path_mode") == "project_relative"
                else Path(raw_path).expanduser().resolve()
            )
            payload = {
                "name": name,
                "source": entry.get("source", "path"),
                "path": str(resolved_entry_path),
                "node_ids": list(lock_entry.get("node_ids") or []),
                "pipeline_ids": list(lock_entry.get("pipeline_ids") or []),
                "tasks": list(lock_entry.get("tasks") or []),
                "effective_kind": lock_entry.get("effective_kind", ""),
                "lock_status": "invalid" if resolved_entry_path.exists() else "missing",
                "env_status": "unknown",
                "reason": str(exc),
            }
        records.append(payload)
    return records


def remove_plugin(name: str, *, project_dir: str | Path | None = None) -> bool:
    root = project_root(project_dir)
    config = _load_config(root)
    lock = _load_lock(root)
    found = any(entry.get("name") == name for entry in config.get("plugins") or [])
    if not found:
        return False
    _write_pair(
        root,
        {"plugins": [entry for entry in config.get("plugins") or [] if entry.get("name") != name]},
        {
            "format": LOCK_FORMAT,
            "plugins": [entry for entry in lock.get("plugins") or [] if entry.get("name") != name],
        },
    )
    from sure_eval.evaluation.node_registry import get_registry

    registry = get_registry()
    registry.invalidate_project_plugins()
    registry.ensure_project_plugins(root)
    return True


def sync_plugins(
    name: str | None = None, *, project_dir: str | Path | None = None
) -> list[dict[str, Any]]:
    records = plugin_records(project_dir)
    selected = [record for record in records if name is None or record["name"] == name]
    drifted = [record for record in selected if record["lock_status"] != "ready"]
    if drifted:
        details = ", ".join(f"{record['name']} ({record['lock_status']})" for record in drifted)
        raise PluginError(f"Plugin sync failed: {details}")
    return selected


def configure_registry_paths(project_dir: str | Path | None = None) -> tuple[Path, ...]:
    root = project_root(project_dir)
    paths: list[Path] = []
    for record in plugin_records(root):
        if record["lock_status"] != "ready":
            warnings.warn(
                f"Skipping project plugin {record['name']!r}: {record['lock_status']} ({record.get('reason', '')})",
                RuntimeWarning,
                stacklevel=3,
            )
            continue
        paths.append(Path(record["path"]))
    return tuple(paths)
