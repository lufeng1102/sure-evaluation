"""Node registry: discover and resolve builtin and external (plugin) nodes.

Phase 1 scope: discovery and metadata (``manifest`` / ``node_env`` / node-id
enumeration) route through this registry, so external nodes can be found via
entry points or local paths.  Callable construction (``build``) lands in Phase 2.
"""

from __future__ import annotations

import importlib.util
import warnings
from functools import lru_cache
from importlib import import_module
from importlib.metadata import entry_points
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable

import yaml

from sure_eval.evaluation.core.node_protocol import (
    ENTRY_POINT_GROUP,
    NodeRegistration,
    registration_from_module,
)

EVALUATION_ROOT = Path(__file__).resolve().parent
NODES_ROOT = EVALUATION_ROOT / "nodes"

NODE_MANIFEST_ALIASES = {
    "scoring/wenet_cer": "scoring/wenet_wer",
    "scoring/wenet_mer": "scoring/wenet_wer",
}


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise TypeError(f"Expected a YAML mapping at {path}")
    return data


def _check_artifacts(payload: Any, keys: tuple[str, ...], *, node_id: str, phase: str) -> None:
    """Validate that a ``NodePayload`` carries the artifact keys a node declares."""
    if not keys or not hasattr(payload, "artifacts"):
        return
    missing = [key for key in keys if key not in payload.artifacts]
    if missing:
        raise ValueError(
            f"Node {node_id} {phase} missing artifact(s): {', '.join(missing)}"
        )


class NodeRegistry:
    """Aggregated view over builtin, entry-point, and local-path nodes."""

    def __init__(self, nodes_root: Path = NODES_ROOT) -> None:
        self.nodes_root = nodes_root
        # Session-scoped extra node paths (``--extra-node-path``).  Once set on
        # the ``get_registry()`` singleton, every ``resolve``/``build``/
        # ``find_by_selector``/``find_node_by_name`` call resolves against them
        # without executors passing the paths explicitly.
        self.local_paths: tuple[str | Path, ...] = ()
        self.project_local_paths: tuple[str | Path, ...] = ()
        self._project_dir: Path | None = None
        self._conflict_paths: tuple[str, ...] | None = None

    def ensure_project_plugins(self, project_dir: str | Path | None = None) -> None:
        """Load project-local plugin paths once for the selected project directory."""

        from sure_eval.evaluation.plugin_management import configure_registry_paths, project_root

        root = project_root(project_dir) if project_dir is not None else (self._project_dir or project_root())
        if self._project_dir == root:
            return
        self.project_local_paths = configure_registry_paths(root)
        self._project_dir = root
        self._conflict_paths = None

    def invalidate_project_plugins(self) -> None:
        """Drop cached project-plugin paths so the next access reloads config.

        ``plugin add``/``remove`` mutate the on-disk declaration inside the same
        process (notably under ``CliRunner``), so a subsequent resolve/route load
        must re-read the project instead of reusing stale paths.
        """

        self._project_dir = None
        self.project_local_paths = ()
        self._conflict_paths = None

    def _effective_local_paths(self) -> tuple[str | Path, ...]:
        self.ensure_project_plugins(self._project_dir)
        return self.project_local_paths + self.local_paths

    def _check_external_conflicts(self) -> None:
        """Reject duplicate external node identities before resolution."""

        path_signature = tuple(str(path) for path in self.project_local_paths + self.local_paths)
        if self._conflict_paths == path_signature:
            return
        builtin_ids = set(self.discover_builtin_node_ids())
        sources: dict[str, list[str]] = {}
        for node_id, _ in self.iter_entry_point_specs():
            sources.setdefault(node_id, []).append("entry_point")
        for path in self._effective_local_paths():
            reg = self._local_registration(path)
            if reg is not None:
                sources.setdefault(reg.node_id, []).append(str(path))
        for node_id, source_list in sources.items():
            if len(source_list) < 2:
                continue
            if node_id in builtin_ids:
                warnings.warn(
                    f"External node {node_id!r} is shadowed by builtin; using builtin implementation",
                    RuntimeWarning,
                    stacklevel=3,
                )
                continue
            raise ValueError(
                f"Duplicate external node_id {node_id!r} from: {', '.join(source_list)}"
            )
        self._conflict_paths = path_signature

    # ---- builtin discovery ----

    def discover_builtin_node_ids(self) -> tuple[str, ...]:
        ids: list[str] = []
        for manifest_path in sorted(self.nodes_root.glob("*/*/manifest.yaml")):
            stage = manifest_path.parent.parent.name
            name = manifest_path.parent.name
            ids.append(f"{stage}/{name}")
        return tuple(ids)

    def _builtin_manifest_path(self, node_id: str) -> Path | None:
        manifest_id = NODE_MANIFEST_ALIASES.get(node_id, node_id)
        if "/" not in manifest_id:
            return None
        stage, name = manifest_id.split("/", 1)
        path = self.nodes_root / stage / name / "manifest.yaml"
        return path if path.exists() else None

    # ---- entry point discovery ----

    @staticmethod
    def iter_entry_point_specs() -> Iterable[tuple[str, str]]:
        eps = entry_points()
        group = (
            eps.select(group=ENTRY_POINT_GROUP)
            if hasattr(eps, "select")
            else eps.get(ENTRY_POINT_GROUP, ())
        )
        for ep in group:
            yield ep.name, ep.value

    # ---- metadata ----

    def manifest(self, node_id: str) -> dict[str, Any]:
        self.ensure_project_plugins()
        self._check_external_conflicts()
        path = self._builtin_manifest_path(node_id)
        if path is not None:
            return _load_yaml(path)
        for name, value in self.iter_entry_point_specs():
            if name == node_id:
                return self._module_registration(value, node_id, source="entry_point").manifest
        for reg in self._local_registrations():
            if reg.node_id == node_id:
                return reg.manifest
        raise KeyError(f"Unknown node: {node_id!r}")

    def manifest_path(self, node_id: str) -> Path:
        self.ensure_project_plugins()
        self._check_external_conflicts()
        path = self._builtin_manifest_path(node_id)
        if path is not None:
            return path
        for name, value in self.iter_entry_point_specs():
            if name == node_id:
                module = _import_cached(value)
                return Path(module.__file__).resolve()
        for local_path in self._effective_local_paths():
            reg = self._local_registration(local_path)
            if reg is not None and reg.node_id == node_id:
                p = Path(local_path)
                return (p / "node.py" if p.is_dir() else p).resolve()
        raise KeyError(f"Unknown node: {node_id!r}")

    def node_env(self, node_id: str) -> dict[str, Any] | None:
        self.ensure_project_plugins()
        self._check_external_conflicts()
        path = self._builtin_manifest_path(node_id)
        if path is not None:
            env_path = path.parent / "node_env.yaml"
            return _load_yaml(env_path) if env_path.exists() else None
        for name, value in self.iter_entry_point_specs():
            if name == node_id:
                return self._module_registration(value, node_id, source="entry_point").node_env
        for reg in self._local_registrations():
            if reg.node_id == node_id:
                return reg.node_env
        return None

    def iter_node_ids(self) -> tuple[str, ...]:
        self.ensure_project_plugins()
        self._check_external_conflicts()
        ids = set(self.discover_builtin_node_ids())
        ids.update(name for name, _ in self.iter_entry_point_specs())
        ids.update(reg.node_id for reg in self._local_registrations())
        return tuple(sorted(ids))

    def iter_env_node_ids(self) -> tuple[str, ...]:
        """Node ids that declare a ``node_env`` (need environment visibility)."""

        self.ensure_project_plugins()
        self._check_external_conflicts()
        ids: set[str] = set()
        for env_path in sorted(self.nodes_root.glob("*/*/node_env.yaml")):
            stage = env_path.parent.parent.name
            name = env_path.parent.name
            ids.add(f"{stage}/{name}")
        for name, value in self.iter_entry_point_specs():
            reg = self._module_registration(value, name, source="entry_point")
            if reg.node_env is not None:
                ids.add(name)
        for reg in self._local_registrations():
            if reg.node_env is not None:
                ids.add(reg.node_id)
        return tuple(sorted(ids))

    def find_by_selector(self, stage: str, key: str, value: Any) -> str | None:
        """Resolve an external node id by a selector declaration.

        External nodes may declare ``SELECTORS = {"normalizer": "my_norm"}``
        (or ``{"scorer": "my_scorer"}``).  This lets a task dispatch an unknown
        selector string to the right node without hardcoding it.  Local-path
        nodes are considered after entry-point nodes.
        """

        self.ensure_project_plugins()
        self._check_external_conflicts()
        for name, module_path in self.iter_entry_point_specs():
            reg = self._module_registration(module_path, name, source="entry_point")
            if reg.stage == stage and reg.selectors.get(key) == value:
                return name
        for reg in self._local_registrations():
            if reg.stage == stage and reg.selectors.get(key) == value:
                return reg.node_id
        return None

    def find_node_by_name(self, stage: str, name: str) -> str | None:
        """Resolve a node id from its stage and name (builtin, entry point, or local path)."""

        self.ensure_project_plugins()
        self._check_external_conflicts()
        node_id = f"{stage}/{name}"
        if self._builtin_manifest_path(node_id) is not None:
            return node_id
        for ep_name, _ in self.iter_entry_point_specs():
            if ep_name == node_id:
                return node_id
        for reg in self._local_registrations():
            if reg.node_id == node_id:
                return node_id
        return None

    # ---- resolution ----

    def resolve(
        self,
        node_ref: str,
        *,
        local_paths: list[str | Path] | None = None,
    ) -> NodeRegistration:
        self.ensure_project_plugins()
        self._check_external_conflicts()
        # ① builtin
        if self._builtin_manifest_path(node_ref) is not None:
            return self._builtin_registration(node_ref)
        # ② entry point
        for name, value in self.iter_entry_point_specs():
            if name == node_ref:
                return self._module_registration(value, node_ref, source="entry_point")
        # ③ local paths (explicit argument wins over the session-scoped set)
        paths = local_paths if local_paths is not None else self._effective_local_paths()
        for path in paths:
            reg = self._local_registration(path)
            if reg is not None and reg.node_id == node_ref:
                return reg
        raise KeyError(f"Unknown node reference: {node_ref!r}")

    def build(self, node_id: str, *, local_paths: list[str | Path] | None = None, **config: Any) -> Any:
        reg = self.resolve(node_id, local_paths=local_paths)
        if reg.build is None:
            raise NotImplementedError(f"Node {node_id} has no 'build' factory")
        node = reg.build(**config)
        if not reg.consumes and not reg.produces:
            return node
        return self._checked_node(node, node_id, reg.consumes, reg.produces)

    @staticmethod
    def _checked_node(node: Any, node_id: str, consumes: tuple[str, ...], produces: tuple[str, ...]) -> Any:
        def checked(payload: Any) -> Any:
            _check_artifacts(payload, consumes, node_id=node_id, phase="consumes")
            new_payload, result = node(payload)
            _check_artifacts(new_payload, produces, node_id=node_id, phase="produces")
            return new_payload, result

        return checked

    # ---- helpers ----

    def _builtin_registration(self, node_id: str) -> NodeRegistration:
        path = self._builtin_manifest_path(node_id)
        assert path is not None
        manifest = _load_yaml(path)
        stage = str(manifest.get("stage") or node_id.split("/", 1)[0])
        version = str(manifest.get("version") or "v1")
        env_path = path.parent / "node_env.yaml"
        node_env = _load_yaml(env_path) if env_path.exists() else None
        return NodeRegistration(
            node_id=node_id,
            stage=stage,
            version=version,
            manifest=manifest,
            build=self._builtin_build(node_id, manifest),
            node_env=node_env,
            module="",
            source="builtin",
            consumes=tuple(manifest.get("consumes") or ()),
            produces=tuple(manifest.get("produces") or ()),
        )

    def _builtin_build(self, node_id: str, manifest: dict[str, Any]) -> Any:
        """Load a builtin node's ``build`` factory from its node module, if any."""
        impl = manifest.get("implementation")
        if impl:
            module_name = impl
        else:
            stage, name = node_id.split("/", 1)
            module_name = f"sure_eval.evaluation.nodes.{stage}.{name}.node"
        try:
            module = import_module(module_name)
        except ImportError:
            return None
        return getattr(module, "build", None)

    @staticmethod
    def _module_registration(
        module_name: str,
        node_id: str,
        source: str,
    ) -> NodeRegistration:
        module = _import_cached(module_name)
        return registration_from_module(module, node_id=node_id, source=source)

    @staticmethod
    def _local_registration(path: str | Path) -> NodeRegistration | None:
        try:
            module = _import_path(path)
        except (ImportError, OSError, ValueError):
            return None
        return registration_from_module(module, source="local")

    def _local_registrations(self) -> list[NodeRegistration]:
        """Registration for every session-scoped local node path (skipping invalid)."""
        regs: list[NodeRegistration] = []
        for path in self._effective_local_paths():
            reg = self._local_registration(path)
            if reg is not None:
                regs.append(reg)
        return regs

    def iter_local_route_modules(self) -> list[ModuleType]:
        """Route modules discovered from session-scoped local paths.

        A local path may be a directory containing ``routes.py``, or a ``.py``
        file that exposes a ``ROUTES`` list.  This mirrors the ``sure_eval.routes``
        entry point so a zero-install directory can register both nodes and
        routes.
        """
        self.ensure_project_plugins()
        modules: list[ModuleType] = []
        for path in self._effective_local_paths():
            module = _import_route_module(path)
            if module is not None:
                modules.append(module)
        return modules


_registry: NodeRegistry | None = None


def get_registry() -> NodeRegistry:
    global _registry
    if _registry is None:
        _registry = NodeRegistry()
    return _registry


@lru_cache(maxsize=None)
def _import_cached(module_name: str) -> ModuleType:
    return import_module(module_name)


def _import_path(path: str | Path) -> ModuleType:
    """Import a node module from a ``.py`` file or a directory with ``node.py``."""

    path = Path(path)
    if path.is_dir():
        node_file = path / "node.py"
        if not node_file.exists():
            raise ImportError(f"No node.py in {path}")
        path = node_file
    if not path.exists():
        raise FileNotFoundError(path)
    if path.suffix != ".py":
        raise ImportError(f"Expected a .py file, got {path}")

    resolved = path.resolve()
    module_name = f"_sure_eval_local_node_{resolved.stem}_{abs(hash(str(resolved))):x}"
    spec = importlib.util.spec_from_file_location(module_name, resolved)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load node module from {resolved}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _import_route_module(path: str | Path) -> ModuleType | None:
    """Import a local route module (``dir/routes.py`` or a ``.py`` exposing ``ROUTES``)."""

    p = Path(path)
    if p.is_dir():
        route_file = p / "routes.py"
        if not route_file.exists():
            return None
        p = route_file
    if not p.exists() or p.suffix != ".py":
        return None
    module = _import_path(p)
    return module if hasattr(module, "ROUTES") else None
