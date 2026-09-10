"""Node registry: discover and resolve builtin and external (plugin) nodes.

Phase 1 scope: discovery and metadata (``manifest`` / ``node_env`` / node-id
enumeration) route through this registry, so external nodes can be found via
entry points or local paths.  Callable construction (``build``) lands in Phase 2.
"""

from __future__ import annotations

import importlib.util
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


class NodeRegistry:
    """Aggregated view over builtin, entry-point, and local-path nodes."""

    def __init__(self, nodes_root: Path = NODES_ROOT) -> None:
        self.nodes_root = nodes_root

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
        path = self._builtin_manifest_path(node_id)
        if path is not None:
            return _load_yaml(path)
        for name, value in self.iter_entry_point_specs():
            if name == node_id:
                return self._module_registration(value, node_id, source="entry_point").manifest
        raise KeyError(f"Unknown node: {node_id!r}")

    def manifest_path(self, node_id: str) -> Path:
        path = self._builtin_manifest_path(node_id)
        if path is not None:
            return path
        for name, value in self.iter_entry_point_specs():
            if name == node_id:
                module = _import_cached(value)
                return Path(module.__file__).resolve()
        raise KeyError(f"Unknown node: {node_id!r}")

    def node_env(self, node_id: str) -> dict[str, Any] | None:
        path = self._builtin_manifest_path(node_id)
        if path is not None:
            env_path = path.parent / "node_env.yaml"
            return _load_yaml(env_path) if env_path.exists() else None
        for name, value in self.iter_entry_point_specs():
            if name == node_id:
                return self._module_registration(value, node_id, source="entry_point").node_env
        return None

    def iter_node_ids(self) -> tuple[str, ...]:
        ids = set(self.discover_builtin_node_ids())
        ids.update(name for name, _ in self.iter_entry_point_specs())
        return tuple(sorted(ids))

    def iter_env_node_ids(self) -> tuple[str, ...]:
        """Node ids that declare a ``node_env`` (need environment visibility)."""

        ids: set[str] = set()
        for env_path in sorted(self.nodes_root.glob("*/*/node_env.yaml")):
            stage = env_path.parent.parent.name
            name = env_path.parent.name
            ids.add(f"{stage}/{name}")
        for name, value in self.iter_entry_point_specs():
            reg = self._module_registration(value, name, source="entry_point")
            if reg.node_env is not None:
                ids.add(name)
        return tuple(sorted(ids))

    # ---- resolution ----

    def resolve(
        self,
        node_ref: str,
        *,
        local_paths: list[str | Path] | None = None,
    ) -> NodeRegistration:
        # ① builtin
        if self._builtin_manifest_path(node_ref) is not None:
            return self._builtin_registration(node_ref)
        # ② entry point
        for name, value in self.iter_entry_point_specs():
            if name == node_ref:
                return self._module_registration(value, node_ref, source="entry_point")
        # ③ local paths
        for path in local_paths or ():
            reg = self._local_registration(path)
            if reg is not None and reg.node_id == node_ref:
                return reg
        raise KeyError(f"Unknown node reference: {node_ref!r}")

    def build(self, node_id: str, **config: Any) -> Any:
        reg = self.resolve(node_id)
        if reg.build is None:
            raise NotImplementedError(f"Node {node_id} has no 'build' factory (Phase 2)")
        return reg.build(**config)

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
            build=None,
            node_env=node_env,
            module="",
            source="builtin",
        )

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
